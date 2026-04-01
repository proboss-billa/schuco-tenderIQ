# extraction/parameter_extractor.py

from typing import List, Dict, Optional
import json

from config.parameters import FACADE_PARAMETERS
from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter

import os
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError

from processing.document_processor import logger

class ParameterExtractor:
    """Extract facade parameters using LLM"""

    def __init__(self, pinecone_index, embedding_client, llm_client, db_session):
        self.pinecone = pinecone_index
        self.embedder = embedding_client
        self.llm = llm_client
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

        prompt = f"""
Extract the following parameter from the document context below.

**Parameter:** {param_config['display_name']}
**Description:** {param_config['description']}
**Expected Units:** {', '.join(param_config['expected_units'])}

**Document Context:**
{context}

**Instructions:**
Return a JSON object with EXACTLY these fields:
{{
  "found": true or false,
  "value": "extracted value as string, or null if not found",
  "value_numeric": numeric value as a number or null,
  "unit": "unit string or null",
  "source_number": 1, 2, or 3 (which source contained the value),
  "confidence": float between 0.0 and 1.0,
  "explanation": "brief explanation of where/how you found it"
}}

Set "found" to true only if the parameter is clearly present in the context.
"""
        logger.info(prompt)
        try:
            response = self.gemini.models.generate_content(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_instr,
                    # Forces valid JSON and removes the need for "No prose" in prompt
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
                contents=prompt
            )
            logger.info(response.text)

            # Gemini returns the string in .text; you can use json.loads() here if needed
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

        # response = self.extract_facade_parameter(param_config, context)
        #
        # # Parse JSON response
        # try:
        #     result = json.loads(response.content[0].text)
        #
        #     # Add source metadata
        #     if result.get('found') and result.get('source_number'):
        #         source_idx = result['source_number'] - 1
        #         if source_idx < len(relevant_chunks):
        #             source_chunk = relevant_chunks[source_idx]['chunk']
        #             result['source_metadata'] = {
        #                 'document_id': str(source_chunk.document_id),
        #                 'document_name': source_chunk.document.original_filename,
        #                 'page': source_chunk.page_number,
        #                 'section': source_chunk.section_title,
        #                 'subsection': source_chunk.subsection_title,
        #                 'chunk_id': str(source_chunk.chunk_id)
        #             }
        #
        #     result['parameter_name'] = param_config['name']
        #     return result
        #
        # except json.JSONDecodeError:
        #     return {
        #         'parameter_name': param_config['name'],
        #         'found': False,
        #         'reason': 'LLM response parsing failed'
        #     }

        try:
            # 1. Call the updated extraction method (using gemini-3.1-flash-preview)
            # Note: In 2026, 'gemini-3.1-flash-preview' is the current performance standard.
            response_text = self.extract_facade_parameter(param_config, context)

            # 2. Parse JSON response
            # Gemini with response_mime_type="application/json" returns a clean string
            result = json.loads(response_text)

            # 3. Add source metadata logic
            # Gemini is usually better at returning integers for 'source_number' than Claude
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
                'reason': 'Gemini JSON parsing failed'
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