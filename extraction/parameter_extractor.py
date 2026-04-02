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
from sqlalchemy.orm import joinedload

from processing.document_processor import logger

import asyncio
from typing import Dict, List


class ParameterExtractor:
    """Extract facade parameters using LLM"""

    def __init__(self, pinecone_index, embedding_client, db_session, session_factory=None):
        self.pinecone = pinecone_index
        self.embedder = embedding_client
        self.db = db_session
        self.session_factory = session_factory
        self.gemini = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    # ── LLM call (blocking, safe to run in a thread — no SQLAlchemy) ─────────

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=8, max=60),
        retry=retry_if_exception_type(ClientError)
    )
    def _call_llm(self, param_config: Dict, context: str) -> str:
        """Call Gemini and return raw JSON string. Receives only plain strings."""
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
  "source_numbers": [list of source numbers 1/2/3 that contain relevant information, e.g. [1, 2]],
  "confidence": float between 0.0 and 1.0,
  "explanation": "brief explanation of where/how you found it"
}}

Set "found" to true if ANY relevant information for this parameter is present in the context, even if partial.
Set "found" to false only if the parameter is completely absent from the context.
Include ALL sources in source_numbers that contain relevant information, not just the primary one.
"""
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
            logger.info(f"LLM response for {param_config['display_name']}: {response.text}")
            return response.text

        except ClientError as e:
            if e.status_code == 429:
                logger.info(f"Rate limit hit for {param_config['display_name']}. Retrying...")
                raise e
            raise e

    # ── Sync search (used by legacy sync path) ────────────────────────────────

    def _search_relevant_chunks(self, project_id: str, query: str, top_k: int = 5) -> List[Dict]:
        """Search Pinecone + DB, expand to parent sections, return plain dicts."""
        query_embedding = self.embedder.embed([query])[0]

        results = self.pinecone.query(
            vector=query_embedding,
            top_k=top_k,
            filter={"project_id": project_id},
            include_metadata=True
        )

        chunk_ids = [match['id'] for match in results['matches']]
        if not chunk_ids:
            return []

        scores = {m['id']: m['score'] for m in results['matches']}

        child_chunks = (
            self.db.query(DocumentChunk)
            .options(joinedload(DocumentChunk.document))
            .filter(DocumentChunk.pinecone_id.in_(chunk_ids))
            .all()
        )

        # Expand to parent sections for hierarchical chunks
        parent_ids = [c.parent_chunk_id for c in child_chunks if c.parent_chunk_id]
        parent_map: Dict = {}
        if parent_ids:
            parent_rows = (
                self.db.query(DocumentChunk)
                .options(joinedload(DocumentChunk.document))
                .filter(DocumentChunk.chunk_id.in_(parent_ids))
                .all()
            )
            parent_map = {p.chunk_id: p for p in parent_rows}

        seen: set = set()
        enriched: List[Dict] = []
        for child in child_chunks:
            score = scores.get(child.pinecone_id, 0)
            if child.parent_chunk_id and child.parent_chunk_id in parent_map:
                if child.parent_chunk_id not in seen:
                    seen.add(child.parent_chunk_id)
                    enriched.append(self._chunk_to_dict(parent_map[child.parent_chunk_id], score))
            else:
                enriched.append(self._chunk_to_dict(child, score))

        return sorted(enriched, key=lambda x: x['score'], reverse=True)

    @staticmethod
    def _chunk_to_dict(chunk, score: float) -> Dict:
        """Convert a SQLAlchemy DocumentChunk (with loaded document) to a plain dict."""
        return {
            'chunk_text':       chunk.chunk_text,
            'page_number':      chunk.page_number,
            'section_title':    chunk.section_title,
            'subsection_title': chunk.subsection_title,
            'document_name':    chunk.document.original_filename if chunk.document else None,
            'document_id':      str(chunk.document_id),
            'chunk_id':         str(chunk.chunk_id),
            'chunk_level':      getattr(chunk, 'chunk_level', 1),  # 0=parent, 1=child
            'score':            score,
        }

    # ── Build LLM context from plain dicts ───────────────────────────────────

    @staticmethod
    def _build_context(chunk_dicts: List[Dict]) -> str:
        parts = []
        for i, c in enumerate(chunk_dicts[:3], 1):
            parts.append(
                f"[Source {i}]\n"
                f"Document: {c['document_name']}\n"
                f"Page: {c['page_number']}\n"
                f"Section: {c['section_title'] or 'N/A'}\n"
                f"Subsection: {c['subsection_title'] or 'N/A'}\n"
                f"Content: {c['chunk_text']}\n"
            )
        return "\n\n".join(parts)

    # ── Parse LLM JSON response ───────────────────────────────────────────────

    def _parse_llm_response(self, response_text: str, param_config: Dict, chunk_dicts: List[Dict]) -> Dict:
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            return {'parameter_name': param_config['name'], 'found': False, 'reason': 'JSON parsing failed'}

        if result.get('found'):
            raw_sources = result.get('source_numbers') or (
                [result['source_number']] if result.get('source_number') else []
            )
            try:
                source_idxs = [int(s) - 1 for s in raw_sources if str(s).isdigit()]
            except (ValueError, TypeError):
                source_idxs = []

            primary = next((chunk_dicts[i] for i in source_idxs if 0 <= i < len(chunk_dicts)), None)
            if primary:
                result['source_metadata'] = {
                    'document_id':   primary['document_id'],
                    'document_name': primary['document_name'],
                    'page':          primary['page_number'],
                    'section':       primary['section_title'],
                    'subsection':    primary['subsection_title'],
                    'chunk_id':      primary['chunk_id'],
                }

            all_pages = []
            for i in source_idxs:
                if 0 <= i < len(chunk_dicts):
                    pg = chunk_dicts[i]['page_number']
                    if pg is not None and pg not in all_pages:
                        all_pages.append(pg)
            result['all_pages'] = all_pages

        result['parameter_name'] = param_config['name']
        return result

    # ── Sync public API (legacy) ──────────────────────────────────────────────

    def extract_all_parameters(self, project_id: str) -> List[Dict]:
        results = []
        for param_config in FACADE_PARAMETERS:
            extraction = self.extract_single_parameter(project_id, param_config)
            logger.info(f"Extracted {param_config['name']}: {extraction}")
            results.append(extraction)
            if extraction.get('found'):
                self._store_extraction(project_id, param_config, extraction)
        return results

    def extract_single_parameter(self, project_id: str, param_config: Dict) -> Dict:
        query = f"{param_config['description']} {' '.join(param_config['search_keywords'])}"
        chunk_dicts = self._search_relevant_chunks(project_id, query, top_k=5)
        if not chunk_dicts:
            return {'parameter_name': param_config['name'], 'found': False, 'reason': 'No relevant content found'}
        context = self._build_context(chunk_dicts)
        response_text = self._call_llm(param_config, context)
        return self._parse_llm_response(response_text, param_config, chunk_dicts)

    # ── Async public API ──────────────────────────────────────────────────────

    async def extract_all_parameters_async(
        self,
        project_id: str,
        facade_parameters: List[Dict],
        max_concurrent: int = 10,
    ) -> List[Dict]:
        semaphore = asyncio.Semaphore(max_concurrent)

        tasks = [
            self._extract_single_async(project_id, param, semaphore)
            for param in facade_parameters
        ]

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for param_config, result in zip(facade_parameters, raw_results):
            if isinstance(result, Exception):
                logger.error(f"Failed {param_config['name']}: {result}")
                result = {'parameter_name': param_config['name'], 'found': False, 'reason': str(result)}
            else:
                logger.info(f"Extracted {param_config['name']}: {result}")

            results.append(result)
            if result.get('found'):
                self._store_extraction(project_id, param_config, result)

        return results

    async def _extract_single_async(
        self,
        project_id: str,
        param_config: Dict,
        semaphore: asyncio.Semaphore,
    ) -> Dict:
        async with semaphore:
            loop = asyncio.get_running_loop()
            query = f"{param_config['description']} {' '.join(param_config['search_keywords'])}"

            # Step 1: Embed query in thread (network call, no SQLAlchemy)
            query_embedding = await loop.run_in_executor(
                None, lambda: self.embedder.embed([query])[0]
            )

            # Step 2: Pinecone search in thread (network call, no SQLAlchemy)
            pinecone_results = await loop.run_in_executor(
                None, lambda: self.pinecone.query(
                    vector=query_embedding,
                    top_k=5,
                    filter={"project_id": project_id},
                    include_metadata=True,
                )
            )

            chunk_ids = [m['id'] for m in pinecone_results['matches']]
            logger.info(f"[EXTRACT][{param_config['name']}] Pinecone returned {len(chunk_ids)} ids: {chunk_ids}")
            if not chunk_ids:
                logger.info(f"[EXTRACT][{param_config['name']}] No Pinecone results → skipping")
                return {'parameter_name': param_config['name'], 'found': False, 'reason': 'No relevant content found'}

            # Step 3: DB query — load child chunks matched by Pinecone
            scores = {m['id']: m['score'] for m in pinecone_results['matches']}
            child_chunks = (
                self.db.query(DocumentChunk)
                .options(joinedload(DocumentChunk.document))
                .filter(DocumentChunk.pinecone_id.in_(chunk_ids))
                .all()
            )
            logger.info(f"[EXTRACT][{param_config['name']}] DB found {len(child_chunks)} child chunks")

            if not child_chunks:
                logger.info(f"[EXTRACT][{param_config['name']}] 0 DB chunks despite {len(chunk_ids)} Pinecone hits — project_id filter mismatch?")
                return {'parameter_name': param_config['name'], 'found': False, 'reason': 'No chunks found in DB'}

            # ── Hierarchical context expansion ────────────────────────────────
            # For each child chunk that has a parent (section-level chunk), load
            # the parent instead.  The parent contains the FULL section text, so
            # the LLM sees complete information rather than a mid-sentence fragment.
            # Legacy chunks (parent_chunk_id=None) fall back to using themselves.
            parent_ids = [
                c.parent_chunk_id for c in child_chunks
                if c.parent_chunk_id is not None
            ]
            parent_map: Dict = {}
            if parent_ids:
                parent_rows = (
                    self.db.query(DocumentChunk)
                    .options(joinedload(DocumentChunk.document))
                    .filter(DocumentChunk.chunk_id.in_(parent_ids))
                    .all()
                )
                parent_map = {p.chunk_id: p for p in parent_rows}
                logger.info(
                    f"[EXTRACT][{param_config['name']}] Loaded {len(parent_map)} parent sections"
                )

            # Build deduplicated context list — one entry per unique parent section
            seen_parent_ids: set = set()
            enriched: List[Dict] = []
            for child in child_chunks:
                score = scores.get(child.pinecone_id, 0)
                if child.parent_chunk_id and child.parent_chunk_id in parent_map:
                    if child.parent_chunk_id not in seen_parent_ids:
                        seen_parent_ids.add(child.parent_chunk_id)
                        parent = parent_map[child.parent_chunk_id]
                        enriched.append(self._chunk_to_dict(parent, score))
                else:
                    # Legacy chunk or orphaned child — use as-is
                    enriched.append(self._chunk_to_dict(child, score))

            chunk_dicts = sorted(enriched, key=lambda x: x['score'], reverse=True)
            context = self._build_context(chunk_dicts)

            # Step 4: LLM call in thread — only plain strings, no SQLAlchemy
            try:
                response_text = await loop.run_in_executor(
                    None, self._call_llm, param_config, context
                )
                logger.info(f"[EXTRACT][{param_config['name']}] LLM raw response: {response_text}")
            except Exception as e:
                logger.error(f"[EXTRACT][{param_config['name']}] LLM call failed: {e}")
                return {'parameter_name': param_config['name'], 'found': False, 'reason': f'LLM error: {e}'}

            result = self._parse_llm_response(response_text, param_config, chunk_dicts)
            logger.info(f"[EXTRACT][{param_config['name']}] parsed result found={result.get('found')} value={result.get('value')}")
            return result

    # ── Store to DB ───────────────────────────────────────────────────────────

    def _store_extraction(self, project_id: str, param_config: Dict, extraction: Dict):
        source_meta = extraction.get('source_metadata', {})
        all_pages = extraction.get('all_pages', [])

        try:
            # Upsert: update existing row if present, otherwise insert a new one.
            # This is safe under concurrent async extraction because the UniqueConstraint
            # (project_id, parameter_name) prevents duplicate rows — we just update in place.
            existing = self.db.query(ExtractedParameter).filter(
                ExtractedParameter.project_id == project_id,
                ExtractedParameter.parameter_name == param_config['name']
            ).with_for_update().first()

            if existing:
                existing.parameter_display_name = param_config['display_name']
                existing.value_text            = extraction.get('value')
                existing.value_numeric         = extraction.get('value_numeric')
                existing.unit                  = extraction.get('unit')
                existing.source_document_id    = source_meta.get('document_id')
                existing.source_page_number    = source_meta.get('page')
                existing.source_pages          = json.dumps(all_pages) if all_pages else None
                existing.source_section        = source_meta.get('section')
                existing.source_subsection     = source_meta.get('subsection')
                existing.source_chunk_id       = source_meta.get('chunk_id')
                existing.confidence_score      = extraction.get('confidence', 0.0)
                existing.extraction_method     = 'llm_extraction'
                existing.notes                 = extraction.get('explanation')
            else:
                record = ExtractedParameter(
                    project_id=project_id,
                    parameter_name=param_config['name'],
                    parameter_display_name=param_config['display_name'],
                    value_text=extraction.get('value'),
                    value_numeric=extraction.get('value_numeric'),
                    unit=extraction.get('unit'),
                    source_document_id=source_meta.get('document_id'),
                    source_page_number=source_meta.get('page'),
                    source_pages=json.dumps(all_pages) if all_pages else None,
                    source_section=source_meta.get('section'),
                    source_subsection=source_meta.get('subsection'),
                    source_chunk_id=source_meta.get('chunk_id'),
                    confidence_score=extraction.get('confidence', 0.0),
                    extraction_method='llm_extraction',
                    notes=extraction.get('explanation'),
                )
                self.db.add(record)

            self.db.commit()
            logger.info(f"[STORE] Saved parameter '{param_config['name']}' value='{extraction.get('value')}'")
        except Exception as e:
            self.db.rollback()
            logger.error(f"[STORE] Failed to save '{param_config['name']}': {e}")
