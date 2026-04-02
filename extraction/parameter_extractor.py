# extraction/parameter_extractor.py
import json

from config.parameters import FACADE_PARAMETERS
from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter

import os
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from processing.document_processor import logger

import asyncio
from typing import Dict, List

class ParameterExtractor:
    """Extract facade parameters using LLM"""

    def __init__(self, pinecone_index, embedding_client, db_session):
        self.pinecone = pinecone_index
        self.embedder = embedding_client
        # self.llm = llm_client
        self.db = db_session
        self.gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=8, max=60),
        retry=retry_if_exception_type(ClientError)
    )
    def extract_facade_parameter(self, param_config, context):
        # System instructions keep the "Analyst" persona consistent
        system_instr = "You are an expert extracting technical parameters from facade/curtain wall specifications."

        value_type = param_config.get('value_type', 'text')
        value_guidance = (
            "For numeric parameters: set value to the number as a string and value_numeric to the number. "
            "For text/composite parameters (e.g. seismic zone, performance class, multi-part values): "
            "set value to a concise summary of ALL relevant sub-values found (e.g. 'Zone IV, Z=0.24, I=1.2, R=5'), "
            "and set value_numeric to null. "
            "Do NOT require an exact unit match — if the information is present, extract it."
        )

        prompt = f"""
Extract the following parameter from the document context below.

**Parameter:** {param_config['display_name']}
**Description:** {param_config['description']}
**Expected Units / Format:** {', '.join(param_config['expected_units'])} (use best match or describe if composite)
**Value Type:** {value_type}

**Document Context:**
{context}

**Instructions:**
{value_guidance}

Return a JSON object with EXACTLY these fields:
{{
  "found": true or false,
  "value": "extracted value as string (summarise all relevant sub-values), or null if not found",
  "value_numeric": numeric value as a number or null (null for composite/text values),
  "unit": "unit string or null",
  "source_number": 1, 2, or 3 (which source contained the value),
  "confidence": float between 0.0 and 1.0,
  "explanation": "brief explanation of where/how you found it"
}}

Set "found" to true if ANY relevant information for this parameter is present in the context, even if partial.
Set "found" to false only if the parameter is completely absent from the context.
"""
        logger.info(prompt)
        try:
            response = self.gemini.models.generate_content(
                model="gemini-3-flash-preview",
                config=types.GenerateContentConfig(
                    system_instruction=system_instr,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                contents=prompt
            )
            logger.info(response.text)
            return response.text

        except ClientError as e:
            if e.status_code == 429:
                logger.info(f"Rate limit hit for {param_config['display_name']}. Retrying...")
                raise e
            raise e

    def extract_all_parameters(self, project_id: str) -> List[Dict]:
        """Extract all 25 parameters for a project"""

        results = []

        for param_config in FACADE_PARAMETERS:
            extraction = self.extract_single_parameter(project_id, param_config)
            logger.info(f"Extracted {param_config['name']}: {extraction}")
            results.append(extraction)

            # Store in database
            if extraction.get('found'):
                self._store_extraction(project_id, param_config, extraction)

        return results

    def extract_single_parameter(self, project_id: str, param_config: Dict) -> Dict:
        """Extract one parameter"""

        # Step 1: Semantic search for relevant chunks
        query = f"{param_config['description']} {' '.join(param_config['search_keywords'])}"
        relevant_chunks = self._search_relevant_chunks(project_id, query, top_k=5)

        if not relevant_chunks:
            return {
                'parameter_name': param_config['name'],
                'found': False,
                'reason': 'No relevant content found'
            }

        # Step 2: LLM extraction with structured prompt
        extraction_result = self._llm_extract(param_config, relevant_chunks)

        return extraction_result

    def _search_relevant_chunks(self, project_id: str, query: str, top_k: int = 5) -> List[Dict]:
        """Search Pinecone for relevant chunks"""

        # Generate query embedding
        query_embedding = self.embedder.embed([query])[0]

        # Search Pinecone with project filter
        results = self.pinecone.query(
            vector=query_embedding,
            top_k=top_k,
            filter={"project_id": project_id},
            include_metadata=True
        )

        # Fetch full chunk details from PostgreSQL
        chunk_ids = [match['id'] for match in results['matches']]

        chunks = self.db.query(DocumentChunk).filter(
            DocumentChunk.pinecone_id.in_(chunk_ids)
        ).all()

        # Combine with scores
        chunks_with_scores = []
        for chunk in chunks:
            score = next((m['score'] for m in results['matches'] if m['id'] == chunk.pinecone_id), 0)
            chunks_with_scores.append({
                'chunk': chunk,
                'score': score
            })

        # Sort by score
        chunks_with_scores.sort(key=lambda x: x['score'], reverse=True)

        return chunks_with_scores


    # --- Async versions of your two I/O-bound methods ---

    async def _search_relevant_chunks_async(
            self, project_id: str, query: str, top_k: int = 5
    ) -> List[Dict]:
        """Async wrapper — run the blocking DB/vector search in a thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,  # uses default ThreadPoolExecutor
            self._search_relevant_chunks,  # your existing sync method
            project_id, query, top_k
        )

    async def _llm_extract_async(
            self, param_config: Dict, relevant_chunks: List[Dict]
    ) -> Dict:
        """Async wrapper — run the blocking LLM call in a thread."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._llm_extract,  # your existing sync method
            param_config, relevant_chunks
        )

    # --- Core: one param end-to-end, async ---

    async def extract_single_parameter_async(
            self,
            project_id: str,
            param_config: Dict,
            semaphore: asyncio.Semaphore,
    ) -> Dict:
        async with semaphore:  # rate-limit concurrent LLM calls
            query = (
                f"{param_config['description']} "
                f"{' '.join(param_config['search_keywords'])}"
            )

            relevant_chunks = await self._search_relevant_chunks_async(
                project_id, query, top_k=5
            )

            if not relevant_chunks:
                return {
                    "parameter_name": param_config["name"],
                    "found": False,
                    "reason": "No relevant content found",
                }

            return await self._llm_extract_async(param_config, relevant_chunks)

    # --- Public entry point: replaces your sequential for-loop ---

    async def extract_all_parameters_async(
            self,
            project_id: str,
            facade_parameters: List[Dict],
            max_concurrent: int = 5,  # tune to your LLM rate limit
    ) -> List[Dict]:
        semaphore = asyncio.Semaphore(max_concurrent)

        tasks = [
            self.extract_single_parameter_async(project_id, param, semaphore)
            for param in facade_parameters
        ]

        # return_exceptions=True prevents one failure killing all others
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for param_config, result in zip(facade_parameters, raw_results):
            if isinstance(result, Exception):
                logger.error(f"Failed {param_config['name']}: {result}")
                result = {
                    "parameter_name": param_config["name"],
                    "found": False,
                    "reason": f"Exception: {result}",
                }
            else:
                logger.info(f"Extracted {param_config['name']}: {result}")

            results.append(result)

            if result.get("found"):
                self._store_extraction(project_id, param_config, result)

        return results

    def _llm_extract(self, param_config: Dict, relevant_chunks: List[Dict]) -> Dict:
        """Use LLM to extract parameter value"""

        # Build context from chunks
        context_parts = []
        for i, item in enumerate(relevant_chunks[:3], 1):  # Use top 3
            chunk = item['chunk']
            context_parts.append(
                f"[Source {i}]\n"
                f"Document: {chunk.document.original_filename}\n"
                f"Page: {chunk.page_number}\n"
                f"Section: {chunk.section_title or 'N/A'}\n"
                f"Subsection: {chunk.subsection_title or 'N/A'}\n"
                f"Content: {chunk.chunk_text}\n"
            )

        context = "\n\n".join(context_parts)

        try:
            response_text = self.extract_facade_parameter(param_config, context)

            # 2. Parse JSON response
            # Gemini with response_mime_type="application/json" returns a clean string
            result = json.loads(response_text)

            # 3. Add source metadata logic
            if result.get('found') and result.get('source_number'):
                try:
                    # Ensure it's an int and adjust for 0-based indexing
                    source_idx = int(result['source_number']) - 1

                    if 0 <= source_idx < len(relevant_chunks):
                        # Mapping the extracted value to your specific document chunk
                        source_chunk = relevant_chunks[source_idx]['chunk']
                        result['source_metadata'] = {
                            'document_id': str(source_chunk.document_id),
                            'document_name': source_chunk.document.original_filename,
                            'page': source_chunk.page_number,
                            'section': source_chunk.section_title,
                            'subsection': source_chunk.subsection_title,
                            'chunk_id': str(source_chunk.chunk_id)
                        }
                except (ValueError, TypeError):
                    # Handle cases where LLM might return a string like "1" or "Unknown"
                    pass

            result['parameter_name'] = param_config['name']
            return result

        except json.JSONDecodeError:
            return {
                'parameter_name': param_config['name'],
                'found': False,
                'reason': 'Claude JSON parsing failed'
            }
        except Exception as e:
            return {
                'parameter_name': param_config['name'],
                'found': False,
                'reason': f'Unexpected error: {str(e)}'
            }

    def _store_extraction(self, project_id: str, param_config: Dict, extraction: Dict):
        """Store extraction in database"""

        source_meta = extraction.get('source_metadata', {})

        record = ExtractedParameter(
            project_id=project_id,
            parameter_name=param_config['name'],
            parameter_display_name=param_config['display_name'],
            value_text=extraction.get('value'),
            value_numeric=extraction.get('value_numeric'),
            unit=extraction.get('unit'),
            source_document_id=source_meta.get('document_id'),
            source_page_number=source_meta.get('page'),
            source_section=source_meta.get('section'),
            source_subsection=source_meta.get('subsection'),
            source_chunk_id=source_meta.get('chunk_id'),
            confidence_score=extraction.get('confidence', 0.0),
            extraction_method='llm_extraction',
            notes=extraction.get('explanation')
        )

        # Upsert (replace if exists)
        existing = self.db.query(ExtractedParameter).filter(
            ExtractedParameter.project_id == project_id,
            ExtractedParameter.parameter_name == param_config['name']
        ).first()

        if existing:
            self.db.delete(existing)

        self.db.add(record)
        self.db.commit()