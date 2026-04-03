# extraction/parameter_extractor.py
import json
import time
import re

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
from typing import Dict, List, Optional

# ── Constants ────────────────────────────────────────────────────────────────
BATCH_SIZE       = 8     # params per LLM call
SCORE_THRESHOLD  = 0.10  # discard Pinecone hits below this relevance score
MODEL            = "gemini-3-flash-preview"


class ParameterExtractor:
    """Extract facade parameters using batched LLM calls for speed and quality."""

    def __init__(self, pinecone_index, embedding_client, db_session, session_factory=None):
        self.pinecone        = pinecone_index
        self.embedder        = embedding_client
        self.db              = db_session
        self.session_factory = session_factory
        self.gemini          = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_to_dict(chunk, score: float) -> Dict:
        return {
            'chunk_text':       chunk.chunk_text,
            'page_number':      chunk.page_number,
            'section_title':    chunk.section_title,
            'subsection_title': chunk.subsection_title,
            'document_name':    chunk.document.original_filename if chunk.document else None,
            'document_id':      str(chunk.document_id),
            'chunk_id':         str(chunk.chunk_id),
            'chunk_level':      getattr(chunk, 'chunk_level', 1),
            'score':            score,
        }

    @staticmethod
    def _build_context(chunk_dicts: List[Dict], max_sources: int = 6) -> str:
        parts = []
        for i, c in enumerate(chunk_dicts[:max_sources], 1):
            parts.append(
                f"[Source {i} | Doc: {c['document_name']} | Pg.{c['page_number']} | "
                f"Section: {c['section_title'] or 'N/A'}]\n"
                f"{c['chunk_text']}"
            )
        return "\n\n---\n\n".join(parts)

    def _fetch_chunks_from_db(self, session, chunk_ids: List[str], scores: Dict) -> List[Dict]:
        """Fetch chunks from DB, expand to parents, return sorted by score."""
        if not chunk_ids:
            return []
        child_chunks = (
            session.query(DocumentChunk)
            .options(joinedload(DocumentChunk.document))
            .filter(DocumentChunk.pinecone_id.in_(chunk_ids))
            .all()
        )
        if not child_chunks:
            return []

        parent_ids = [c.parent_chunk_id for c in child_chunks if c.parent_chunk_id]
        parent_map: Dict = {}
        if parent_ids:
            parent_rows = (
                session.query(DocumentChunk)
                .options(joinedload(DocumentChunk.document))
                .filter(DocumentChunk.chunk_id.in_(parent_ids))
                .all()
            )
            parent_map = {p.chunk_id: p for p in parent_rows}

        seen_parents: set = set()
        enriched: List[Dict] = []
        for child in child_chunks:
            score = scores.get(child.pinecone_id, 0)
            if child.parent_chunk_id and child.parent_chunk_id in parent_map:
                if child.parent_chunk_id not in seen_parents:
                    seen_parents.add(child.parent_chunk_id)
                    enriched.append(self._chunk_to_dict(parent_map[child.parent_chunk_id], score))
            else:
                enriched.append(self._chunk_to_dict(child, score))

        return sorted(enriched, key=lambda x: x['score'], reverse=True)

    def _parse_sources(self, source_numbers, chunk_dicts: List[Dict]) -> tuple:
        """Return (source_metadata dict, all_sources list, all_pages list)."""
        try:
            idxs = [int(s) - 1 for s in (source_numbers or []) if str(s).isdigit()]
        except (ValueError, TypeError):
            idxs = []

        primary = next((chunk_dicts[i] for i in idxs if 0 <= i < len(chunk_dicts)), chunk_dicts[0] if chunk_dicts else None)
        source_meta = {}
        if primary:
            source_meta = {
                'document_id':   primary['document_id'],
                'document_name': primary['document_name'],
                'page':          primary['page_number'],
                'section':       primary['section_title'],
                'subsection':    primary['subsection_title'],
                'chunk_id':      primary['chunk_id'],
            }

        doc_sources: dict = {}
        for i in idxs:
            if 0 <= i < len(chunk_dicts):
                c = chunk_dicts[i]
                did = c['document_id']
                pg  = c['page_number']
                if did not in doc_sources:
                    doc_sources[did] = {'document_id': did, 'document': c['document_name'], 'pages': [], 'section': c['section_title']}
                if pg is not None and pg not in doc_sources[did]['pages']:
                    doc_sources[did]['pages'].append(pg)

        all_sources = list(doc_sources.values())
        all_pages   = [pg for src in all_sources for pg in src['pages']]
        return source_meta, all_sources, all_pages

    # ── Batch LLM call ────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=6, max=45),
        retry=retry_if_exception_type(ClientError)
    )
    def _call_llm_batch(self, batch_params: List[Dict], context: str) -> str:
        """Single LLM call to extract multiple parameters from shared context."""
        param_list = "\n".join(
            f'{i+1}. [{p["name"]}] {p["display_name"]}\n'
            f'   Description: {p["description"]}\n'
            f'   Expected format/units: {", ".join(p["expected_units"])}\n'
            f'   Look for: {", ".join(p["search_keywords"][:6])}'
            for i, p in enumerate(batch_params)
        )

        prompt = f"""You are a specialist in facade and curtain wall engineering documents.
Carefully read the document sources below and extract EVERY parameter listed.

PARAMETERS TO EXTRACT:
{param_list}

DOCUMENT CONTEXT:
{context}

EXTRACTION RULES:
- Search ALL sources thoroughly for each parameter before marking as not found
- found=true if ANY relevant data exists, even partial or implied
- value = concise string with ALL relevant sub-values (e.g. "Zone IV, Z=0.24, I=1.2" or "8+16+8 IGU Low-E")
- confidence: 0.9+ if explicitly stated, 0.7-0.9 if inferred, 0.5-0.7 if partial, <0.5 if uncertain
- source_numbers: ALL source numbers [1,2,3...] that contain relevant info (can be multiple)
- For yes/no parameters: value="Yes" or value="No" with evidence in explanation
- Do NOT mark as not-found just because exact wording differs — use domain knowledge

Return ONLY a valid JSON object with each parameter name as a key:
{{
  "wind_load": {{
    "found": true,
    "value": "2.2 kN/m² (Zone III, Basic Wind Speed 44 m/s, IS 875 Part 3)",
    "value_numeric": 2.2,
    "unit": "kN/m²",
    "confidence": 0.95,
    "source_numbers": [1, 2],
    "explanation": "Wind load of 2.2 kN/m² stated in Section 3.1 of the structural brief"
  }},
  "water_tightness": {{
    "found": false,
    "value": null,
    "value_numeric": null,
    "unit": null,
    "confidence": 0.0,
    "source_numbers": [],
    "explanation": "No water tightness requirement found in the provided sources"
  }},
  ... (one entry per parameter listed above)
}}"""

        try:
            response = self.gemini.models.generate_content(
                model=MODEL,
                config=types.GenerateContentConfig(
                    system_instruction="You are an expert facade engineer extracting technical parameters. Always return valid JSON.",
                    response_mime_type="application/json",
                    temperature=0.05,
                ),
                contents=prompt
            )
            return response.text
        except ClientError as e:
            if e.status_code == 429:
                logger.warning(f"Rate limit hit — retrying batch of {len(batch_params)} params")
                raise
            raise

    def _parse_batch_response(
        self,
        response_text: str,
        batch_params: List[Dict],
        chunk_dicts: List[Dict],
    ) -> List[Dict]:
        """Parse a multi-parameter JSON response into individual result dicts."""
        try:
            # Strip markdown code fences if present
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip(), flags=re.MULTILINE)
            # Some models wrap the response in a top-level key — unwrap if needed
            parsed = json.loads(clean)
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected dict, got {type(parsed)}")
            # If all keys are nested under a single wrapper key, unwrap it
            if len(parsed) == 1:
                only_key = next(iter(parsed))
                if only_key not in [p['name'] for p in batch_params] and isinstance(parsed[only_key], dict):
                    parsed = parsed[only_key]
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Batch JSON parse error: {e} | raw: {response_text[:400]}")
            # Fall back: mark all as not-found — individual retry will handle them
            return [{'parameter_name': p['name'], 'found': False, 'reason': 'JSON parse failed'} for p in batch_params]

        results = []
        for param in batch_params:
            raw = parsed.get(param['name'])
            if not isinstance(raw, dict):
                results.append({'parameter_name': param['name'], 'found': False, 'reason': 'Missing in response'})
                continue

            result = {
                'parameter_name':  param['name'],
                'found':           bool(raw.get('found')),
                'value':           raw.get('value'),
                'value_numeric':   raw.get('value_numeric'),
                'unit':            raw.get('unit'),
                'confidence':      float(raw.get('confidence', 0.0)),
                'explanation':     raw.get('explanation', ''),
            }

            if result['found']:
                source_meta, all_sources, all_pages = self._parse_sources(
                    raw.get('source_numbers', []), chunk_dicts
                )
                result['source_metadata'] = source_meta
                result['all_sources']     = all_sources
                result['all_pages']       = all_pages
            else:
                result['source_metadata'] = {}
                result['all_sources']     = []
                result['all_pages']       = []

            results.append(result)
        return results

    # ── Single-param LLM call (kept for fallback / legacy) ───────────────────

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=6, max=45),
        retry=retry_if_exception_type(ClientError)
    )
    def _call_llm(self, param_config: Dict, context: str) -> str:
        prompt = f"""You are an expert extracting technical parameters from facade/curtain wall specifications.

Extract: {param_config['display_name']}
Description: {param_config['description']}
Expected units/format: {', '.join(param_config['expected_units'])}
Keywords: {', '.join(param_config['search_keywords'][:8])}

DOCUMENT CONTEXT:
{context}

Return ONLY JSON:
{{
  "found": true or false,
  "value": "extracted value string or null",
  "value_numeric": numeric or null,
  "unit": "unit string or null",
  "source_numbers": [1, 2, ...],
  "confidence": 0.0-1.0,
  "explanation": "brief explanation"
}}

Set found=true if ANY relevant information exists. Include all source numbers with relevant info."""

        try:
            response = self.gemini.models.generate_content(
                model=MODEL,
                config=types.GenerateContentConfig(
                    system_instruction="You are an expert facade engineer. Return valid JSON only.",
                    response_mime_type="application/json",
                    temperature=0.05,
                ),
                contents=prompt
            )
            return response.text
        except ClientError as e:
            if e.status_code == 429:
                raise
            raise

    def _parse_llm_response(self, response_text: str, param_config: Dict, chunk_dicts: List[Dict]) -> Dict:
        try:
            clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip(), flags=re.MULTILINE)
            result = json.loads(clean)
        except json.JSONDecodeError:
            return {'parameter_name': param_config['name'], 'found': False, 'reason': 'JSON parsing failed'}

        result['parameter_name'] = param_config['name']
        if result.get('found'):
            source_meta, all_sources, all_pages = self._parse_sources(
                result.get('source_numbers', []), chunk_dicts
            )
            result['source_metadata'] = source_meta
            result['all_sources']     = all_sources
            result['all_pages']       = all_pages
        return result

    # ── Store to DB ───────────────────────────────────────────────────────────

    def _store_extraction(self, project_id: str, param_config: Dict, extraction: Dict):
        t0 = time.perf_counter()
        source_meta = extraction.get('source_metadata', {})
        all_pages   = extraction.get('all_pages', [])

        store_session = self.session_factory() if self.session_factory else self.db
        try:
            existing = store_session.query(ExtractedParameter).filter(
                ExtractedParameter.project_id    == project_id,
                ExtractedParameter.parameter_name == param_config['name']
            ).with_for_update().first()

            fields = dict(
                parameter_display_name = param_config['display_name'],
                value_text             = extraction.get('value'),
                value_numeric          = extraction.get('value_numeric'),
                unit                   = extraction.get('unit'),
                source_document_id     = source_meta.get('document_id'),
                source_page_number     = source_meta.get('page'),
                source_pages           = json.dumps(all_pages) if all_pages else None,
                source_section         = source_meta.get('section'),
                source_subsection      = source_meta.get('subsection'),
                source_chunk_id        = source_meta.get('chunk_id'),
                confidence_score       = extraction.get('confidence', 0.0),
                extraction_method      = 'llm_batch',
                notes                  = extraction.get('explanation'),
                all_sources            = json.dumps(extraction.get('all_sources', [])),
            )

            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
            else:
                record = ExtractedParameter(project_id=project_id, parameter_name=param_config['name'], **fields)
                store_session.add(record)

            store_session.commit()
            logger.info(f"[TIMING][STORE] '{param_config['name']}': {time.perf_counter()-t0:.2f}s")
        except Exception as e:
            store_session.rollback()
            logger.error(f"[STORE] Failed '{param_config['name']}': {e}")
        finally:
            if self.session_factory and store_session is not self.db:
                store_session.close()

    # ── Async search helper ───────────────────────────────────────────────────

    async def _search_pinecone_async(
        self,
        loop,
        query: str,
        project_id: str,
        top_k: int,
    ) -> List[Dict]:
        """Embed query + Pinecone search + DB fetch, return scored chunk dicts."""
        embedding = await loop.run_in_executor(None, lambda: self.embedder.embed([query])[0])

        pinecone_results = await loop.run_in_executor(
            None, lambda: self.pinecone.query(
                vector=embedding,
                top_k=top_k,
                filter={"project_id": project_id},
                include_metadata=True,
            )
        )

        matches = [m for m in pinecone_results['matches'] if m['score'] >= SCORE_THRESHOLD]
        if not matches:
            return []

        chunk_ids = [m['id'] for m in matches]
        scores    = {m['id']: m['score'] for m in matches}

        task_session = self.session_factory() if self.session_factory else self.db
        try:
            chunk_dicts = self._fetch_chunks_from_db(task_session, chunk_ids, scores)
        finally:
            if self.session_factory and task_session is not self.db:
                task_session.close()

        return chunk_dicts

    # ── Async batch extraction ────────────────────────────────────────────────

    async def _extract_batch_async(
        self,
        project_id: str,
        batch_params: List[Dict],
        semaphore: asyncio.Semaphore,
        num_docs: int,
    ) -> List[Dict]:
        """Extract a batch of params with ONE search + ONE LLM call."""
        async with semaphore:
            batch_names = [p['name'] for p in batch_params]
            t_start = time.perf_counter()
            loop = asyncio.get_running_loop()

            # Build a combined search query from all params in the batch
            keywords: list = []
            for p in batch_params:
                keywords.append(p['display_name'])
                keywords.extend(p['search_keywords'][:4])
            # Deduplicate while preserving order
            seen_kw: set = set()
            unique_kw = []
            for kw in keywords:
                if kw.lower() not in seen_kw:
                    seen_kw.add(kw.lower())
                    unique_kw.append(kw)
            query = ' '.join(unique_kw[:25])

            # Scale top_k with doc count — more docs → more chunks to retrieve
            top_k = min(25, max(10, 5 * num_docs))

            # ── Search + fetch ──
            t0 = time.perf_counter()
            chunk_dicts = await self._search_pinecone_async(loop, query, project_id, top_k)
            logger.info(
                f"[TIMING][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"search: {time.perf_counter()-t0:.2f}s → {len(chunk_dicts)} chunks"
            )

            if not chunk_dicts:
                logger.info(f"[BATCH] No chunks for batch starting {batch_names[0]} → all not-found")
                return [{'parameter_name': p['name'], 'found': False, 'reason': 'No relevant content'} for p in batch_params]

            # ── Build context — give the LLM enough to cover all 8 params ──
            # Each chunk ~500-1000 tokens; 12 sources ≈ 8-12k tokens — well within
            # Gemini Flash's 1M context window and gives good per-param coverage.
            max_sources = min(12, max(8, num_docs * 3))
            context = self._build_context(chunk_dicts, max_sources=max_sources)

            # ── LLM call ──
            t0 = time.perf_counter()
            try:
                response_text = await loop.run_in_executor(None, self._call_llm_batch, batch_params, context)
            except Exception as e:
                logger.error(f"[BATCH] LLM failed for batch starting {batch_names[0]}: {e}")
                return [{'parameter_name': p['name'], 'found': False, 'reason': f'LLM error: {e}'} for p in batch_params]

            logger.info(
                f"[TIMING][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"LLM: {time.perf_counter()-t0:.2f}s"
            )

            results = self._parse_batch_response(response_text, batch_params, chunk_dicts)

            # ── Targeted retry for not-found params ──────────────────────────
            # The broad combined query may miss niche params. Give each not-found
            # param its own focused search + single-param LLM call.
            not_found = [(p, r) for p, r in zip(batch_params, results) if not r.get('found')]
            if not_found:
                logger.info(
                    f"[BATCH] {len(not_found)} not-found in batch — retrying individually: "
                    f"{[p['name'] for p,_ in not_found]}"
                )
                for param, old_result in not_found:
                    try:
                        focused_query = f"{param['display_name']} {' '.join(param['search_keywords'][:6])}"
                        focused_chunks = await self._search_pinecone_async(loop, focused_query, project_id, top_k=12)
                        if focused_chunks:
                            focused_context = self._build_context(focused_chunks, max_sources=6)
                            retry_text = await loop.run_in_executor(None, self._call_llm, param, focused_context)
                            retry_result = self._parse_llm_response(retry_text, param, focused_chunks)
                            if retry_result.get('found'):
                                logger.info(f"[BATCH][RETRY] {param['name']} → found on retry ✓")
                                # Replace the not-found entry
                                idx = next(i for i, r in enumerate(results) if r.get('parameter_name') == param['name'])
                                results[idx] = retry_result
                    except Exception as e:
                        logger.warning(f"[BATCH][RETRY] {param['name']} retry failed: {e}")

            logger.info(
                f"[TIMING][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"total: {time.perf_counter()-t_start:.2f}s | "
                f"found: {sum(1 for r in results if r.get('found'))}/{len(results)}"
            )
            return results

    # ── Main async entry point ────────────────────────────────────────────────

    async def extract_all_parameters_async(
        self,
        project_id: str,
        facade_parameters: List[Dict],
        max_concurrent: int = 6,
        num_docs: int = 1,
    ) -> List[Dict]:
        """Extract all parameters using batched LLM calls (BATCH_SIZE params per call)."""
        t_all_start = time.perf_counter()

        # Split into batches
        batches = [
            facade_parameters[i:i + BATCH_SIZE]
            for i in range(0, len(facade_parameters), BATCH_SIZE)
        ]
        n_batches = len(batches)
        logger.info(
            f"[TIMING][EXTRACT_ALL] {len(facade_parameters)} params → "
            f"{n_batches} batches of ≤{BATCH_SIZE} | max_concurrent={max_concurrent}"
        )

        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = [
            self._extract_batch_async(project_id, batch, semaphore, num_docs)
            for batch in batches
        ]

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Flatten and store
        all_results: List[Dict] = []
        for batch_params, batch_result in zip(batches, batch_results):
            if isinstance(batch_result, Exception):
                logger.error(f"Batch exception: {batch_result}")
                for p in batch_params:
                    all_results.append({'parameter_name': p['name'], 'found': False, 'reason': str(batch_result)})
            else:
                all_results.extend(batch_result)

        # Persist found results
        found_count = 0
        param_map = {p['name']: p for p in facade_parameters}
        for result in all_results:
            if result.get('found'):
                found_count += 1
                pname = result.get('parameter_name')
                param_config = param_map.get(pname)
                if param_config:
                    self._store_extraction(project_id, param_config, result)

        logger.info(
            f"[TIMING][EXTRACT_ALL] Done — {found_count}/{len(all_results)} found — "
            f"total: {time.perf_counter()-t_all_start:.2f}s"
        )
        return all_results

    # ── Sync public API (legacy / fallback) ───────────────────────────────────

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
        query = f"{param_config['display_name']} {' '.join(param_config['search_keywords'][:8])}"
        top_k = 10
        query_embedding = self.embedder.embed([query])[0]
        results = self.pinecone.query(
            vector=query_embedding, top_k=top_k,
            filter={"project_id": project_id}, include_metadata=True
        )
        matches = [m for m in results['matches'] if m['score'] >= SCORE_THRESHOLD]
        if not matches:
            return {'parameter_name': param_config['name'], 'found': False, 'reason': 'No relevant content found'}

        scores    = {m['id']: m['score'] for m in matches}
        chunk_ids = [m['id'] for m in matches]
        chunk_dicts = self._fetch_chunks_from_db(self.db, chunk_ids, scores)
        if not chunk_dicts:
            return {'parameter_name': param_config['name'], 'found': False, 'reason': 'No chunks in DB'}

        context      = self._build_context(chunk_dicts, max_sources=5)
        response_text = self._call_llm(param_config, context)
        return self._parse_llm_response(response_text, param_config, chunk_dicts)
