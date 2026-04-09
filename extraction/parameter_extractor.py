# extraction/parameter_extractor.py
import json
import time
import re

from config.parameters import FACADE_PARAMETERS
from config.models import AVAILABLE_MODELS, DEFAULT_MODEL, get_model_config
from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter

import os
import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from sqlalchemy.orm import joinedload

from processing.document_processor import logger

import asyncio
from typing import Dict, List, Optional

# ── Constants ────────────────────────────────────────────────────────────────
BATCH_SIZE       = 10    # params per LLM call for vector-search fallback (Pass 2)
SCORE_THRESHOLD  = 0.05  # low threshold — let Claude decide relevance, not vector similarity

# ── Full-context extraction constants (Pass 1) ──────────────────────────────
FULL_CONTEXT_OVERLAP       = 3         # parent chunks overlap between windows
TOKENS_PER_WORD            = 1.35      # average tokens per word estimate
FULL_CONTEXT_PARAM_BATCH   = 25        # max params per LLM call in full-context mode

# ── Module-level LLM rate limiter ─────────────────────────────────────────────
# Caps total concurrent Anthropic calls across ALL simultaneous pipeline runs.
# Lazy-init avoids event-loop mismatch on startup.
_LLM_SEMAPHORE: Optional[asyncio.Semaphore] = None

def _get_llm_semaphore() -> asyncio.Semaphore:
    """Return module-level LLM semaphore, creating it on first call in the running loop."""
    global _LLM_SEMAPHORE
    if _LLM_SEMAPHORE is None:
        _LLM_SEMAPHORE = asyncio.Semaphore(14)
    return _LLM_SEMAPHORE


class QuotaExhaustedError(Exception):
    """Raised when the LLM API key quota is exhausted (e.g. Gemini 429 / RESOURCE_EXHAUSTED)."""
    pass


class ParameterExtractor:
    """Extract facade parameters using batched LLM calls for speed and quality."""

    def __init__(self, pinecone_index, embedding_client, db_session, session_factory=None, model_key=None):
        self.pinecone        = pinecone_index
        self.embedder        = embedding_client
        self.db              = db_session
        self.session_factory = session_factory
        self.anthropic       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        # Model configuration — can be switched per extraction run
        self.model_key = model_key or DEFAULT_MODEL
        self.model_config = get_model_config(self.model_key)
        self.model_id = self.model_config["model_id"]
        self.provider = self.model_config["provider"]
        self.max_response_tokens = self.model_config["max_response_tokens"]
        self.context_window_tokens = self.model_config["context_window_tokens"]

        # Gemini client for Google models
        if self.provider == "google":
            from core.clients import gemini_client
            self.gemini = gemini_client
        # OpenAI client for GPT models
        elif self.provider == "openai":
            from core.clients import openai_client
            self.openai = openai_client

        # Token usage counter — accumulated across all _call_provider calls
        self._extraction_tokens_used = 0

        logger.info(
            f"[EXTRACTOR] Using model: {self.model_config['display_name']} "
            f"({self.model_id}, {self.provider})"
        )

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _chunk_to_dict(chunk, score: float) -> Dict:
        from services.file_classifier import get_document_role
        doc_name = chunk.document.original_filename if chunk.document else None
        file_type = chunk.document.file_type if chunk.document else "unknown"
        doc_role = get_document_role(doc_name, file_type=file_type) if doc_name else "unknown"
        return {
            'chunk_text':       chunk.chunk_text,
            'page_number':      chunk.page_number,
            'section_title':    chunk.section_title,
            'subsection_title': chunk.subsection_title,
            'document_name':    doc_name,
            'document_id':      str(chunk.document_id),
            'chunk_id':         str(chunk.chunk_id),
            'chunk_level':      getattr(chunk, 'chunk_level', 1),
            'file_type':        file_type,
            'doc_role':         doc_role,
            'score':            score,
        }

    @staticmethod
    def _build_context(chunk_dicts: List[Dict], max_sources: int = 6) -> str:
        parts = []
        for i, c in enumerate(chunk_dicts[:max_sources], 1):
            role_tag = f" | Type: {c.get('doc_role', 'spec').upper()}" if c.get('doc_role') else ""
            parts.append(
                f"[Source {i} | Doc: {c['document_name']}{role_tag} | Pg.{c['page_number']} | "
                f"Section: {c['section_title'] or 'N/A'}]\n"
                f"{c['chunk_text']}"
            )
        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _build_full_context(chunk_dicts: List[Dict]) -> str:
        """Context for full-document mode — includes document role for multi-doc awareness."""
        # Group chunks by document for a clear document map
        doc_chunks: Dict[str, List] = {}
        for c in chunk_dicts:
            doc_name = c['document_name'] or 'Unknown'
            if doc_name not in doc_chunks:
                doc_chunks[doc_name] = []
            doc_chunks[doc_name].append(c)

        # Build document inventory header
        doc_inventory = []
        for doc_name, chunks in doc_chunks.items():
            role = chunks[0].get('doc_role', 'specification')
            file_type = chunks[0].get('file_type', 'unknown')
            doc_inventory.append(f"  - {doc_name} [{role.upper()}] ({len(chunks)} sections)")

        header = "DOCUMENT INVENTORY:\n" + "\n".join(doc_inventory) + "\n\n---\n\n"

        # Build content with role tags
        parts = []
        for i, c in enumerate(chunk_dicts, 1):
            role = c.get('doc_role', 'spec').upper()
            page = c['page_number'] or 'N/A'
            tag = f"[{i}|{c['document_name']}|{role}|p.{page}]"
            parts.append(f"{tag}\n{c['chunk_text']}")

        return header + "\n\n".join(parts)

    def _compute_param_batch_size(self) -> int:
        """Calculate optimal number of params per LLM call based on model's output limit."""
        tokens_per_param = 150  # avg JSON output per parameter
        safety = 0.80
        max_batch = int((self.max_response_tokens * safety) / tokens_per_param)
        return max(15, min(max_batch, FULL_CONTEXT_PARAM_BATCH))

    def _call_provider(self, system: str, prompt: str, max_tokens: int) -> str:
        """Route LLM call to the configured provider (Anthropic, Google, or OpenAI).
        Also tracks token usage on self._extraction_tokens_used.
        Raises QuotaExhaustedError if the Gemini API key quota is exceeded."""
        tokens = 0
        if self.provider == "anthropic":
            response = self.anthropic.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            if hasattr(response, "usage") and response.usage:
                tokens = getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0)
        elif self.provider == "google":
            from google import genai
            try:
                response = self.gemini.models.generate_content(
                    model=self.model_id,
                    contents=prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=0.0,
                        max_output_tokens=max_tokens,
                        system_instruction=system,
                    ),
                )
            except Exception as e:
                err_str = str(e).lower()
                if "resource_exhausted" in err_str or "429" in err_str or "quota" in err_str:
                    raise QuotaExhaustedError("Gemini API quota exhausted") from e
                raise
            text = response.text
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                tokens = (
                    getattr(response.usage_metadata, "prompt_token_count", 0)
                    + getattr(response.usage_metadata, "candidates_token_count", 0)
                )
        elif self.provider == "openai":
            if self.openai is None:
                raise RuntimeError("OpenAI client not initialized. Check OPENAI_API_KEY.")
            response = self.openai.chat.completions.create(
                model=self.model_id,
                max_tokens=max_tokens,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            text = response.choices[0].message.content
            if hasattr(response, "usage") and response.usage:
                tokens = response.usage.total_tokens or 0
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        self._extraction_tokens_used += tokens
        return text

    def _fetch_chunks_from_db(self, session, chunk_ids: List[str], scores: Dict) -> List[Dict]:
        """Fetch chunks from DB, expand to parents, return sorted by score."""
        if not chunk_ids:
            return []
        child_chunks = (
            session.query(DocumentChunk)
            .options(joinedload(DocumentChunk.document))
            .filter(DocumentChunk.pinecone_id.in_(chunk_ids))
            .order_by(DocumentChunk.chunk_id)
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
                .order_by(DocumentChunk.chunk_id)
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
        """Return (source_metadata dict, all_sources list, all_pages list).

        If the LLM omits source_numbers (common on high-confidence Gemini
        responses), returns empty source_meta / all_sources rather than
        guessing chunk_dicts[0] — a plausible-but-wrong citation is worse
        than an honest "not cited".
        """
        try:
            idxs = [int(s) - 1 for s in (source_numbers or []) if str(s).isdigit()]
        except (ValueError, TypeError):
            idxs = []

        primary = next((chunk_dicts[i] for i in idxs if 0 <= i < len(chunk_dicts)), None)
        if primary is None and chunk_dicts:
            logger.warning(
                f"[SOURCES] LLM returned no valid source_numbers (raw={source_numbers!r}) "
                f"— recording empty source_meta rather than guessing chunk_dicts[0]"
            )
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
                    doc_sources[did] = {
                        'document_id': did,
                        'document':    c['document_name'],
                        'pages':       [],
                        'sections':    [],
                    }
                if pg is not None and pg not in doc_sources[did]['pages']:
                    doc_sources[did]['pages'].append(pg)
                section = c['section_title']
                if section and section not in doc_sources[did]['sections']:
                    doc_sources[did]['sections'].append(section)

        all_sources = list(doc_sources.values())
        all_pages   = [pg for src in all_sources for pg in src['pages']]
        return source_meta, all_sources, all_pages

    # ══════════════════════════════════════════════════════════════════════════
    # PASS 1: FULL-CONTEXT EXTRACTION (like Claude Web — zero retrieval loss)
    # ══════════════════════════════════════════════════════════════════════════

    def _fetch_all_parent_chunks(self, project_id: str) -> List[Dict]:
        """Fetch ALL level-0 parent chunks for a project from ALL document types.

        Level-0 parents are created for:
        - PDF specs/drawings: full section text (~3000 words each)
        - BOQ spreadsheets: consolidated per-sheet text (equal weight to spec sections)
        - DOCX specs: full section text

        If no level-0 parents exist (legacy data), falls back to level-1 children.

        Returns chunks interleaved by document to ensure all document types
        get fair representation across context windows (no document is bunched
        at the end where it might be ignored).
        """
        from models.document import Document

        # 1. Fetch ALL level-0 parents (specs, drawings, docx, BOQ — all have them now)
        all_parents = (
            self.db.query(DocumentChunk)
            .options(joinedload(DocumentChunk.document))
            .filter(
                DocumentChunk.project_id == project_id,
                DocumentChunk.chunk_level == 0,
            )
            .order_by(DocumentChunk.document_id, DocumentChunk.page_number)
            .all()
        )

        if not all_parents:
            # Fallback: if no level-0 parents exist (legacy data), fetch all level-1 children
            logger.warning(f"[FULL_CONTEXT] No level-0 parents found — falling back to level-1 children")
            all_parents = (
                self.db.query(DocumentChunk)
                .options(joinedload(DocumentChunk.document))
                .filter(
                    DocumentChunk.project_id == project_id,
                    DocumentChunk.chunk_level == 1,
                )
                .order_by(DocumentChunk.document_id, DocumentChunk.page_number)
                .all()
            )
            return [self._chunk_to_dict(p, score=1.0) for p in all_parents]

        # 2. Interleave chunks from different documents for balanced representation.
        #    Without interleaving, BOQ parents (appended last) end up in the final
        #    context window and can be ignored by the LLM.
        from collections import defaultdict
        doc_groups = defaultdict(list)
        for chunk in all_parents:
            doc_id = str(chunk.document_id)
            doc_groups[doc_id].append(chunk)

        interleaved = []
        group_lists = [doc_groups[k] for k in sorted(doc_groups.keys())]
        max_len = max(len(g) for g in group_lists) if group_lists else 0

        for i in range(max_len):
            for group in group_lists:
                if i < len(group):
                    interleaved.append(group[i])

        # Log document coverage
        doc_types = {}
        doc_names = {}
        for c in interleaved:
            ft = c.document.file_type if c.document else "unknown"
            fn = c.document.original_filename if c.document else "unknown"
            doc_types[ft] = doc_types.get(ft, 0) + 1
            doc_names[fn] = doc_names.get(fn, 0) + 1
        logger.info(
            f"[FULL_CONTEXT] {len(interleaved)} parent chunks from {len(doc_groups)} documents. "
            f"Types: {doc_types} | Files: {doc_names}"
        )

        return [self._chunk_to_dict(p, score=1.0) for p in interleaved]

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token count: words × 1.35."""
        return int(len(text.split()) * TOKENS_PER_WORD)

    def _build_context_windows(
        self, parent_dicts: List[Dict], max_tokens: int = None
    ) -> List[List[Dict]]:
        """Split parent chunks into context windows that fit within token budget.

        Returns list of windows, each a list of chunk dicts.
        Consecutive windows overlap by FULL_CONTEXT_OVERLAP chunks so no
        content falls through boundary cracks.
        """
        if max_tokens is None:
            max_tokens = self.context_window_tokens

        if not parent_dicts:
            return []

        windows: List[List[Dict]] = []
        current_window: List[Dict] = []
        current_tokens = 0

        for chunk in parent_dicts:
            chunk_tokens = self._estimate_tokens(chunk['chunk_text'])
            if current_window and (current_tokens + chunk_tokens) > max_tokens:
                windows.append(current_window)
                # Overlap: start next window with last N chunks of current
                overlap = current_window[-FULL_CONTEXT_OVERLAP:]
                current_window = list(overlap)
                current_tokens = sum(self._estimate_tokens(c['chunk_text']) for c in overlap)
            current_window.append(chunk)
            current_tokens += chunk_tokens

        if current_window:
            windows.append(current_window)

        return windows

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=10, max=60),
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError, Exception))
    )
    def _call_llm_full_context(self, all_params: List[Dict], context: str) -> str:
        """LLM call with FULL document context and ALL parameters.

        Unlike _call_llm_batch which sees only vector-searched chunks,
        this sees the complete document text — matching Claude Web behavior.
        """
        # Compact param list — save tokens in prompt
        param_list = "\n".join(
            f'- [{p["name"]}] {p["display_name"]} ({", ".join(p["expected_units"][:3])})'
            for p in all_params
        )

        prompt = f"""Extract ALL {len(all_params)} parameters from the COMPLETE project documents below.

This project contains MULTIPLE document types (specifications, drawings, BOQ/spreadsheets, GCC, etc.).
Each source is tagged with its document type. Search ALL documents — parameters may be spread across different files:
- SPECIFICATION docs: technical requirements, performance values, material specs
- DRAWING docs: dimensions, profiles, glass sizes, mullion spacing, sill heights
- BOQ docs: quantities, material descriptions, item specifications, rates
- GCC docs: warranty terms, testing requirements, compliance standards
- MATRIX docs: compliance data, comparison values, checklists

Use domain expertise: recognize different terminology (e.g. "60mm visible profile" = Face Width of Mullion).

PARAMETERS:
{param_list}

DOCUMENT TEXT:
{context}

RULES:
- found=true if ANY relevant data exists in ANY document: explicit, partial, implied, derivable, or equivalent terminology
- found=false ONLY when NO document has ANY information about this parameter
- When uncertain between found and not-found → choose found=true with confidence 0.5-0.6 (let the user decide)
- value: concise string with key sub-values
- confidence: 0.9+ explicit, 0.7-0.9 inferred/derived, 0.5-0.7 partial/implied
- source_numbers: REQUIRED whenever found=true. The number is the leading
  integer in each `[N|Doc|ROLE|p.X]` tag above (so `[3|GCC.pdf|...]` is 3).
  Include EVERY tag you used — missing citations break downstream source tracking.
  Return [] ONLY when found=false.
- explanation: MAX 15 words — keep very brief to avoid response truncation
- EVERY parameter MUST appear in output

Return ONLY valid JSON — one key per parameter:
{{"param_name": {{"found":true,"value":"val","value_numeric":null,"unit":"u","confidence":0.9,"source_numbers":[1],"explanation":"brief"}}, ...}}"""

        system = (
            "You are an expert facade engineer extracting technical parameters "
            "from complete tender documents. You have the FULL document text.\n\n"
            "EXTRACTION PHILOSOPHY: Your job is to FIND information, not gatekeep. "
            "If a value can be derived, inferred, or is partially present → found=true. "
            "'Not stated in exact words' is NOT a reason for found=false — use domain "
            "expertise to recognize equivalent terminology. Only mark found=false when "
            "the document contains absolutely NO related information. "
            "When in doubt → found=true with lower confidence (0.5-0.7).\n"
            "Always return valid JSON."
        )
        # Scale response tokens to batch size — avoid requesting more than needed
        tokens_needed = min(len(all_params) * 180 + 500, self.max_response_tokens)
        return self._call_provider(system, prompt, tokens_needed)

    async def _extract_full_context_async(
        self,
        project_id: str,
        facade_parameters: List[Dict],
    ) -> List[Dict]:
        """Pass 1: Full-context extraction — send ALL document text to the LLM.

        Fetches all parent chunks from PostgreSQL (no Pinecone), splits into
        context windows, and makes one LLM call per window with ALL parameters.
        Merges results across windows keeping highest-confidence per param.
        """
        t_start = time.perf_counter()
        loop = asyncio.get_running_loop()

        # ── Fetch all parent chunks from DB ──
        parent_dicts = await loop.run_in_executor(
            None, self._fetch_all_parent_chunks, project_id
        )
        total_tokens = sum(self._estimate_tokens(c['chunk_text']) for c in parent_dicts)
        logger.info(
            f"[FULL_CONTEXT] {len(parent_dicts)} parent chunks, "
            f"~{total_tokens:,} tokens total"
        )

        if not parent_dicts:
            logger.warning("[FULL_CONTEXT] No chunks found — skipping full-context pass")
            return [{'parameter_name': p['name'], 'found': False, 'reason': 'No document content'} for p in facade_parameters]

        # ── Split into context windows ──
        windows = self._build_context_windows(parent_dicts)
        logger.info(
            f"[FULL_CONTEXT] Split into {len(windows)} context windows "
            f"(~{self.context_window_tokens:,} tokens each, {FULL_CONTEXT_OVERLAP} overlap)"
        )

        # ── Fire LLM calls for all windows in parallel ──
        semaphore = _get_llm_semaphore()

        async def _process_window(window_idx: int, window_chunks: List[Dict]) -> List[Dict]:
            context = self._build_full_context(window_chunks)
            param_batch_size = self._compute_param_batch_size()
            param_batches = [
                facade_parameters[i:i + param_batch_size]
                for i in range(0, len(facade_parameters), param_batch_size)
            ]

            all_window_results = []
            for batch_idx, param_batch in enumerate(param_batches):
                batch_label = f"Window {window_idx+1}/{len(windows)} batch {batch_idx+1}/{len(param_batches)}"
                t0 = time.perf_counter()
                try:
                    async with semaphore:
                        response_text = await asyncio.wait_for(
                            loop.run_in_executor(
                                None, self._call_llm_full_context, param_batch, context
                            ),
                            timeout=240.0,
                        )
                except asyncio.TimeoutError:
                    logger.error(f"[FULL_CONTEXT] {batch_label} timed out (240s)")
                    all_window_results.extend([
                        {'parameter_name': p['name'], 'found': False, 'reason': 'LLM timeout'}
                        for p in param_batch
                    ])
                    continue
                except Exception as e:
                    logger.error(f"[FULL_CONTEXT] {batch_label} LLM error: {e}")
                    all_window_results.extend([
                        {'parameter_name': p['name'], 'found': False, 'reason': f'LLM error: {e}'}
                        for p in param_batch
                    ])
                    continue

                elapsed = time.perf_counter() - t0
                logger.info(
                    f"[FULL_CONTEXT] {batch_label} "
                    f"LLM: {elapsed:.2f}s | response: {len(response_text)} chars"
                )

                # ── Parse + truncation detection ──
                results = self._parse_batch_response(response_text, param_batch, window_chunks)

                # Check if majority of params failed — indicates truncation
                parse_failures = sum(1 for r in results if r.get('reason') in ('JSON parse failed', 'Missing in response'))
                if parse_failures > len(param_batch) * 0.5:
                    logger.warning(
                        f"[FULL_CONTEXT] {batch_label} — {parse_failures}/{len(param_batch)} params "
                        f"missing/failed. Attempting JSON recovery…"
                    )
                    recovered = self._recover_truncated_json(response_text, param_batch, window_chunks)
                    # Merge: keep recovered found results over failed ones
                    recovered_map = {r['parameter_name']: r for r in recovered if r.get('found')}
                    if recovered_map:
                        results = [
                            recovered_map.get(r['parameter_name'], r)
                            if r.get('reason') in ('JSON parse failed', 'Missing in response')
                            else r
                            for r in results
                        ]

                    # If still many missing, retry just the missing params
                    still_missing = [
                        p for p in param_batch
                        if any(r['parameter_name'] == p['name'] and r.get('reason') in ('JSON parse failed', 'Missing in response', 'JSON truncated')
                               for r in results)
                    ]
                    if still_missing and len(still_missing) <= len(param_batch) * 0.6:
                        logger.info(f"[FULL_CONTEXT] {batch_label} — retrying {len(still_missing)} missing params")
                        try:
                            async with semaphore:
                                retry_text = await asyncio.wait_for(
                                    loop.run_in_executor(
                                        None, self._call_llm_full_context, still_missing, context
                                    ),
                                    timeout=240.0,
                                )
                            retry_results = self._parse_batch_response(retry_text, still_missing, window_chunks)
                            retry_map = {r['parameter_name']: r for r in retry_results if r.get('found')}
                            if retry_map:
                                results = [retry_map.get(r['parameter_name'], r) for r in results]
                                logger.info(f"[FULL_CONTEXT] {batch_label} — recovered {len(retry_map)} params on retry")
                        except Exception as e:
                            logger.warning(f"[FULL_CONTEXT] {batch_label} — retry failed: {e}")

                all_window_results.extend(results)

            return all_window_results

        window_tasks = [
            _process_window(i, w) for i, w in enumerate(windows)
        ]
        window_results = await asyncio.gather(*window_tasks, return_exceptions=True)

        # ── Merge across windows: keep highest-confidence found=true per param ──
        best: Dict[str, Dict] = {}
        for wr in window_results:
            if isinstance(wr, Exception):
                logger.error(f"[FULL_CONTEXT] Window exception: {wr}")
                continue
            for result in wr:
                pname = result.get('parameter_name')
                if not pname:
                    continue
                existing = best.get(pname)
                if not existing:
                    best[pname] = result
                elif result.get('found') and (
                    not existing.get('found')
                    or result.get('confidence', 0) > existing.get('confidence', 0)
                ):
                    best[pname] = result

        # Build final list preserving parameter order
        all_results = []
        for p in facade_parameters:
            r = best.get(p['name'])
            if r:
                all_results.append(r)
            else:
                all_results.append({'parameter_name': p['name'], 'found': False, 'reason': 'Not in any window response'})

        found_count = sum(1 for r in all_results if r.get('found'))
        logger.info(
            f"[FULL_CONTEXT] Done — {found_count}/{len(all_results)} found — "
            f"{time.perf_counter()-t_start:.2f}s"
        )
        return all_results

    # ══════════════════════════════════════════════════════════════════════════
    # PASS 2: VECTOR-SEARCH FALLBACK (for params missed by full-context)
    # ══════════════════════════════════════════════════════════════════════════

    # ── Batch LLM call ────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=6, max=45),
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError, Exception))
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
- source_numbers: REQUIRED whenever found=true. The integer shown in each
  `[Source N | Doc: ...]` tag above — list EVERY tag you pulled info from.
  Missing citations break downstream source tracking. Return [] ONLY when found=false.
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
    "explanation": "Not specified in any of the provided documents. Related terms searched: watertight, water resistance, EN 12155."
  }},
  ... (one entry per parameter listed above)
}}

IMPORTANT for found=false: explanation MUST state (a) what terms were searched for, (b) whether a related value was seen but couldn't be confirmed, or (c) clearly say "Not specified in documents" — never leave explanation vague."""

        system = (
            "You are an expert facade engineer extracting technical parameters. "
            "Your job is to FIND information — err on the side of found=true. "
            "Use domain expertise to recognize equivalent terminology. "
            "Only mark found=false when absolutely NO related info exists. "
            "Always return valid JSON."
        )
        try:
            return self._call_provider(system, prompt, 4096)
        except anthropic.RateLimitError:
            logger.warning(f"Rate limit hit — retrying batch of {len(batch_params)} params")
            raise
        except Exception as e:
            logger.error(f"LLM API error in batch: {e}")
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

            raw_conf = float(raw.get('confidence', 0.0))
            explanation_text = raw.get('explanation', '').lower()
            # Cap confidence when LLM signals inference rather than explicit statement
            _inference_signals = ('inferred', 'assumed', 'estimated', 'approximately',
                                  'likely', 'probably', 'implied', 'not explicitly')
            if raw_conf > 0.75 and any(sig in explanation_text for sig in _inference_signals):
                raw_conf = 0.75
            # If found=true but value is null/empty, set confidence to 0
            if raw.get('found') and not raw.get('value'):
                raw_conf = 0.0

            result = {
                'parameter_name':  param['name'],
                'found':           bool(raw.get('found')),
                'value':           raw.get('value'),
                'value_numeric':   raw.get('value_numeric'),
                'unit':            raw.get('unit'),
                'confidence':      raw_conf,
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

    def _recover_truncated_json(
        self,
        response_text: str,
        batch_params: List[Dict],
        chunk_dicts: List[Dict],
    ) -> List[Dict]:
        """Attempt to recover params from a truncated JSON response.

        When the LLM runs out of output tokens, the JSON is cut off mid-object.
        Strategy: find the last complete parameter entry and parse up to that point.
        """
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text.strip(), flags=re.MULTILINE)

        # Try progressively trimming from the end to find valid JSON
        # Look for the last complete "}" that closes a parameter entry
        recovered: Dict = {}
        last_brace = len(clean)
        for _ in range(50):  # try up to 50 trim attempts
            last_brace = clean.rfind("}", 0, last_brace)
            if last_brace <= 0:
                break
            # Try closing the outer object
            candidate = clean[:last_brace + 1]
            # Count braces to see if we need to close
            open_braces = candidate.count("{") - candidate.count("}")
            candidate += "}" * open_braces  # close any unclosed braces
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    # Unwrap single wrapper key if present
                    if len(parsed) == 1:
                        only_key = next(iter(parsed))
                        if only_key not in [p['name'] for p in batch_params] and isinstance(parsed[only_key], dict):
                            parsed = parsed[only_key]
                    recovered = parsed
                    break
            except json.JSONDecodeError:
                continue

        if not recovered:
            logger.error(f"[RECOVERY] Could not recover any params from truncated JSON")
            return [{'parameter_name': p['name'], 'found': False, 'reason': 'JSON truncated'} for p in batch_params]

        logger.info(f"[RECOVERY] Recovered {len(recovered)} params from truncated response")

        # Parse recovered params through the standard logic
        results = []
        for param in batch_params:
            raw = recovered.get(param['name'])
            if not isinstance(raw, dict):
                results.append({'parameter_name': param['name'], 'found': False, 'reason': 'Missing (truncated)'})
                continue

            raw_conf = float(raw.get('confidence', 0.0))
            explanation_text = raw.get('explanation', '').lower()
            _inference_signals = ('inferred', 'assumed', 'estimated', 'approximately',
                                  'likely', 'probably', 'implied', 'not explicitly')
            if raw_conf > 0.75 and any(sig in explanation_text for sig in _inference_signals):
                raw_conf = 0.75
            if raw.get('found') and not raw.get('value'):
                raw_conf = 0.0

            result = {
                'parameter_name': param['name'],
                'found':          bool(raw.get('found')),
                'value':          raw.get('value'),
                'value_numeric':  raw.get('value_numeric'),
                'unit':           raw.get('unit'),
                'confidence':     raw_conf,
                'explanation':    raw.get('explanation', ''),
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
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError, Exception))
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

Set found=true if ANY relevant information exists. source_numbers is REQUIRED
whenever found=true — use the integer shown in each `[Source N | Doc: ...]` tag
above, and include EVERY tag you used (missing citations break source tracking).
If found=false, source_numbers=[], and explanation must clearly state: what terms
were searched, what (if anything) was found nearby, and confirm "Not specified in
documents" if truly absent."""

        system = (
            "You are an expert facade engineer. Your job is to FIND information — "
            "err on the side of found=true. When in doubt, choose found=true with "
            "lower confidence. Return valid JSON only."
        )
        return self._call_provider(system, prompt, 2048)

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
        found = extraction.get('found', False)
        source_meta = extraction.get('source_metadata', {}) if found else {}
        all_pages   = extraction.get('all_pages', []) if found else []

        from sqlalchemy.dialects.postgresql import insert as pg_insert

        fields = dict(
            parameter_display_name = param_config['display_name'],
            value_text             = extraction.get('value') if found else None,
            value_numeric          = extraction.get('value_numeric') if found else None,
            unit                   = extraction.get('unit') if found else None,
            source_document_id     = source_meta.get('document_id'),
            source_page_number     = source_meta.get('page'),
            source_pages           = json.dumps(all_pages) if all_pages else None,
            source_section         = source_meta.get('section'),
            source_subsection      = source_meta.get('subsection'),
            source_chunk_id        = source_meta.get('chunk_id'),
            confidence_score       = extraction.get('confidence', 0.0) if found else 0.0,
            extraction_method      = 'llm_full_context' if found else 'llm_full_context',
            notes                  = extraction.get('explanation') or extraction.get('reason'),
            all_sources            = json.dumps(extraction.get('all_sources', [])),
        )

        store_session = self.session_factory() if self.session_factory else self.db
        try:
            # Atomic UPSERT — no pessimistic lock needed, eliminates deadlock risk.
            # uq_project_param = UNIQUE(project_id, parameter_name) in extracted_parameter.py
            stmt = (
                pg_insert(ExtractedParameter)
                .values(project_id=project_id, parameter_name=param_config['name'], **fields)
                .on_conflict_do_update(constraint="uq_project_param", set_=fields)
            )
            store_session.execute(stmt)
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
        file_types: Optional[List[str]] = None,
    ) -> List[Dict]:
        """Embed query + Pinecone search + DB fetch, return scored chunk dicts.

        file_types: when provided, restricts the Pinecone search to chunks whose
        'file_type' metadata matches one of the listed values (e.g. ['pdf_drawing',
        'dxf_drawing']). If the filtered search returns no results it automatically
        retries without the type filter so no content is ever silently missed.
        """
        embedding = await loop.run_in_executor(None, lambda: self.embedder.embed([query])[0])

        pinecone_filter: Dict = {"project_id": project_id}
        if file_types:
            pinecone_filter["file_type"] = {"$in": file_types}

        pinecone_results = await loop.run_in_executor(
            None, lambda: self.pinecone.query(
                vector=embedding,
                top_k=top_k,
                filter=pinecone_filter,
                include_metadata=True,
            )
        )

        matches = [m for m in pinecone_results['matches'] if m['score'] >= SCORE_THRESHOLD]

        # Fallback: if type-filtered search returns nothing, search all types.
        # This handles projects where drawing PDFs haven't been uploaded yet, or
        # where the file_type label didn't match the expected type.
        if not matches and file_types:
            logger.info(
                f"[SEARCH] No results with file_type filter {file_types} — retrying without filter"
            )
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

        # Reuse self.db — _fetch_chunks_from_db is fully synchronous (no await),
        # so it executes atomically in the event-loop thread. The embed and
        # pinecone.query steps above run in executors and never touch the DB,
        # so there is no concurrent session access. This eliminates ~50 new
        # session creations per extraction run, keeping the pool healthy.
        chunk_dicts = self._fetch_chunks_from_db(self.db, chunk_ids, scores)
        return chunk_dicts

    # ── Async batch extraction ────────────────────────────────────────────────

    async def _search_single_param(
        self,
        loop,
        param: Dict,
        project_id: str,
        top_k: int,
    ) -> List[Dict]:
        """Per-parameter focused Pinecone search — returns scored chunk dicts."""
        query = f"{param['display_name']} {' '.join(param['search_keywords'][:6])}"
        file_types = param.get('source_types') or None
        return await self._search_pinecone_async(loop, query, project_id, top_k, file_types=file_types)

    async def _extract_batch_async(
        self,
        project_id: str,
        batch_params: List[Dict],
        semaphore: asyncio.Semaphore,
        num_docs: int,
    ) -> List[Dict]:
        """Per-param searches (parallel) → merge chunks → ONE LLM call."""
        async with semaphore:
            batch_names = [p['name'] for p in batch_params]
            t_start = time.perf_counter()
            loop = asyncio.get_running_loop()

            # ── Per-parameter Pinecone searches in parallel ──
            # Each param gets its own focused search query → much better recall
            # than one diluted combined query. Searches are fast (~100ms each).
            # Generous top_k: retrieve broadly, let Claude decide what's relevant.
            top_k_per_param = min(25, max(10, 3 * num_docs))

            t0 = time.perf_counter()
            search_tasks = [
                self._search_single_param(loop, p, project_id, top_k_per_param)
                for p in batch_params
            ]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            # ── Merge & deduplicate chunks across all param searches ──
            # Keep highest score per chunk, preserving the best matches from each search.
            merged_chunks: Dict[str, Dict] = {}  # pinecone_id → chunk_dict
            for result in search_results:
                if isinstance(result, Exception):
                    logger.warning(f"[BATCH] Search error in batch: {result}")
                    continue
                for chunk in result:
                    cid = chunk.get('pinecone_id') or chunk.get('chunk_id', '')
                    if cid not in merged_chunks or chunk.get('score', 0) > merged_chunks[cid].get('score', 0):
                        merged_chunks[cid] = chunk

            # Sort by score descending
            chunk_dicts = sorted(merged_chunks.values(), key=lambda c: c.get('score', 0), reverse=True)

            logger.info(
                f"[TIMING][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"per-param search: {time.perf_counter()-t0:.2f}s → "
                f"{len(chunk_dicts)} unique chunks from {len(batch_params)} searches"
            )

            if not chunk_dicts:
                logger.info(f"[BATCH] No chunks for batch starting {batch_names[0]} → all not-found")
                return [{'parameter_name': p['name'], 'found': False, 'reason': 'No relevant content'} for p in batch_params]

            # ── Build context — generous limit since Claude Opus handles 200k context ──
            # Each param contributed ~10-25 chunks; after dedup we might have 40-100 unique.
            # Cap at 80 sources × ~500 words avg ≈ 40k tokens — well within Opus limits.
            max_sources = min(80, max(15, len(chunk_dicts)))
            context = self._build_context(chunk_dicts, max_sources=max_sources)

            # ── LLM call — guarded by global rate limiter across all projects ──
            t0 = time.perf_counter()
            try:
                async with _get_llm_semaphore():
                    response_text = await asyncio.wait_for(
                        loop.run_in_executor(None, self._call_llm_batch, batch_params, context),
                        timeout=120.0,
                    )
            except asyncio.TimeoutError:
                logger.error(f"[BATCH] LLM timed out (120s) for batch starting {batch_names[0]} — marking not found")
                return [{'parameter_name': p['name'], 'found': False, 'reason': 'LLM timeout'} for p in batch_params]
            except Exception as e:
                logger.error(f"[BATCH] LLM failed for batch starting {batch_names[0]}: {e}")
                return [{'parameter_name': p['name'], 'found': False, 'reason': f'LLM error: {e}'} for p in batch_params]

            logger.info(
                f"[TIMING][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"LLM: {time.perf_counter()-t0:.2f}s"
            )

            results = self._parse_batch_response(response_text, batch_params, chunk_dicts)

            # Log not-found params (no retry — user can re-extract individually)
            not_found = [r.get('parameter_name') for r in results if not r.get('found')]
            if not_found:
                logger.info(
                    f"[BATCH] {len(not_found)} not-found in batch (no retry): {not_found}"
                )

            logger.info(
                f"[TIMING][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"total: {time.perf_counter()-t_start:.2f}s | "
                f"found: {sum(1 for r in results if r.get('found'))}/{len(results)}"
            )
            return results

    # ── Retry variant: broader search, no file-type filter ─────────────────────

    async def _extract_batch_async_retry(
        self,
        project_id: str,
        batch_params: List[Dict],
        semaphore: asyncio.Semaphore,
        num_docs: int,
    ) -> List[Dict]:
        """Retry pass for not-found params: broader search, all file types, more keywords."""
        async with semaphore:
            batch_names = [p['name'] for p in batch_params]
            t_start = time.perf_counter()
            loop = asyncio.get_running_loop()

            # Broader search: more keywords, ALL search_keywords, no file_type filter
            top_k_retry = min(30, max(15, 4 * num_docs))

            async def _search_broad(param: Dict) -> List[Dict]:
                # Use full keyword list + description for broader matching
                query = (
                    f"{param['display_name']} {param['description']} "
                    f"{' '.join(param['search_keywords'])}"
                )
                # No file_type filter — search everything
                return await self._search_pinecone_async(
                    loop, query, project_id, top_k_retry, file_types=None
                )

            search_tasks = [_search_broad(p) for p in batch_params]
            search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

            merged_chunks: Dict[str, Dict] = {}
            for result in search_results:
                if isinstance(result, Exception):
                    continue
                for chunk in result:
                    cid = chunk.get('pinecone_id') or chunk.get('chunk_id', '')
                    if cid not in merged_chunks or chunk.get('score', 0) > merged_chunks[cid].get('score', 0):
                        merged_chunks[cid] = chunk

            chunk_dicts = sorted(merged_chunks.values(), key=lambda c: c.get('score', 0), reverse=True)

            logger.info(
                f"[RETRY][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"broad search → {len(chunk_dicts)} unique chunks"
            )

            if not chunk_dicts:
                return [{'parameter_name': p['name'], 'found': False, 'reason': 'No content (retry)'} for p in batch_params]

            max_sources = min(80, max(15, len(chunk_dicts)))
            context = self._build_context(chunk_dicts, max_sources=max_sources)

            try:
                async with _get_llm_semaphore():
                    response_text = await asyncio.wait_for(
                        loop.run_in_executor(None, self._call_llm_batch, batch_params, context),
                        timeout=120.0,
                    )
            except (asyncio.TimeoutError, Exception) as e:
                logger.error(f"[RETRY] LLM failed for retry batch: {e}")
                return [{'parameter_name': p['name'], 'found': False, 'reason': f'Retry LLM error: {e}'} for p in batch_params]

            results = self._parse_batch_response(response_text, batch_params, chunk_dicts)

            retry_found = sum(1 for r in results if r.get('found'))
            logger.info(
                f"[RETRY][BATCH] {batch_names[0]}…{batch_names[-1]} "
                f"total: {time.perf_counter()-t_start:.2f}s | "
                f"recovered: {retry_found}/{len(results)}"
            )
            return results

    # ══════════════════════════════════════════════════════════════════════════
    # MAIN ENTRY POINT: Three-pass extraction
    # ══════════════════════════════════════════════════════════════════════════

    async def extract_all_parameters_async(
        self,
        project_id: str,
        facade_parameters: List[Dict],
        max_concurrent: int = 6,
        num_docs: int = 1,
    ) -> List[Dict]:
        """Extract parameters using a three-pass strategy for maximum accuracy.

        Pass 1: VECTOR SEARCH — per-param Pinecone search, batched LLM calls (primary pass)
        Pass 2: FULL CONTEXT — fallback for params vector search didn't find
        Pass 3: STORE — merge and persist all results
        """
        t_all_start = time.perf_counter()
        logger.info(
            f"[EXTRACT_ALL] Starting 3-pass extraction for {len(facade_parameters)} params "
            f"| {num_docs} docs"
        )

        # ── Create extraction run record ──
        self._current_run_id = None
        try:
            import uuid as _uuid
            from models.extraction_run import ExtractionRun
            run_id = _uuid.uuid4()
            run_session = self.session_factory() if self.session_factory else self.db
            try:
                run = ExtractionRun(
                    run_id=run_id,
                    project_id=project_id,
                    total_params=len(facade_parameters),
                    status="running",
                )
                run_session.add(run)
                run_session.commit()
                self._current_run_id = run_id
                logger.info(f"[EXTRACT_ALL] Created extraction run {run_id}")
            finally:
                if self.session_factory and run_session is not self.db:
                    run_session.close()
        except Exception as e:
            logger.warning(f"[EXTRACT_ALL] Could not create extraction run record: {e}")

        # ═══════════ PASS 1: Vector search — primary extraction for all params ═══════════
        all_results = [
            {'parameter_name': p['name'], 'found': False, 'reason': 'Pending vector search'}
            for p in facade_parameters
        ]

        # Small batches keep per-param context focused; concurrency is bounded
        # by the semaphore, not the batch count.
        pass1_batch_size = 5
        pass1_batches = [
            facade_parameters[i:i + pass1_batch_size]
            for i in range(0, len(facade_parameters), pass1_batch_size)
        ]
        semaphore = asyncio.Semaphore(max_concurrent)
        pass1_tasks = [
            self._extract_batch_async(project_id, batch, semaphore, num_docs)
            for batch in pass1_batches
        ]
        pass1_results_raw = await asyncio.gather(*pass1_tasks, return_exceptions=True)

        pass1_map: Dict[str, Dict] = {}
        for pb, pr in zip(pass1_batches, pass1_results_raw):
            if isinstance(pr, Exception):
                logger.error(f"[EXTRACT_ALL] Pass 1 batch exception: {pr}")
                continue
            for r in pr:
                if r.get('found'):
                    pass1_map[r['parameter_name']] = r

        for i, result in enumerate(all_results):
            pname = result.get('parameter_name')
            if pname in pass1_map:
                all_results[i] = pass1_map[pname]

        pass1_found = sum(1 for r in all_results if r.get('found'))
        logger.info(
            f"[EXTRACT_ALL] Pass 1 (vector search): {pass1_found}/{len(all_results)} found — "
            f"{time.perf_counter()-t_all_start:.2f}s"
        )

        # ═══════════ PASS 2: Full-context fallback for not-found params ═══════════
        not_found_names = sorted({r['parameter_name'] for r in all_results if not r.get('found')})
        missing_params = [p for p in facade_parameters if p['name'] in not_found_names]

        if missing_params:
            logger.info(
                f"[EXTRACT_ALL] Pass 2 (full context): {len(missing_params)} not-found params"
            )
            try:
                fallback_results = await self._extract_full_context_async(
                    project_id, missing_params
                )
            except Exception as e:
                logger.error(f"[EXTRACT_ALL] Pass 2 full-context exception: {e}")
                fallback_results = []

            fallback_map = {
                r['parameter_name']: r
                for r in fallback_results
                if r.get('found')
            }
            if fallback_map:
                for i, result in enumerate(all_results):
                    pname = result.get('parameter_name')
                    if pname in fallback_map:
                        all_results[i] = fallback_map[pname]
                logger.info(
                    f"[EXTRACT_ALL] Pass 2 recovered {len(fallback_map)} params: "
                    f"{list(fallback_map.keys())}"
                )

        # ═══════════ PASS 3: Persist ALL results (found + not-found) ═══════════
        found_count = 0
        store_failures = []
        param_map = {p['name']: p for p in facade_parameters}
        for result in all_results:
            pname = result.get('parameter_name')
            param_config = param_map.get(pname)
            if param_config:
                try:
                    self._store_extraction(project_id, param_config, result)
                    if result.get('found'):
                        found_count += 1
                except Exception as e:
                    store_failures.append(pname)
                    logger.error(f"[EXTRACT_ALL] Store failed for {pname}: {e}")

        if store_failures:
            logger.error(
                f"[EXTRACT_ALL] {len(store_failures)} params failed to persist: "
                f"{store_failures}"
            )

        pass2_recovered = found_count - pass1_found
        extraction_time = time.perf_counter() - t_all_start

        # ── Update extraction run record if present ──
        if hasattr(self, '_current_run_id') and self._current_run_id:
            try:
                from models.extraction_run import ExtractionRun
                from datetime import datetime
                run_session = self.session_factory() if self.session_factory else self.db
                try:
                    run = run_session.query(ExtractionRun).filter(
                        ExtractionRun.run_id == self._current_run_id
                    ).first()
                    if run:
                        run.completed_at = datetime.utcnow()
                        run.total_params = len(all_results)
                        run.found_count = found_count
                        run.not_found_count = len(all_results) - found_count
                        run.pass1_found = pass1_found
                        run.pass2_found = pass2_recovered
                        run.extraction_time_seconds = round(extraction_time, 2)
                        run.status = "completed"
                        run_session.commit()
                finally:
                    if self.session_factory and run_session is not self.db:
                        run_session.close()
            except Exception as e:
                logger.warning(f"[EXTRACT_ALL] Failed to update extraction run: {e}")

        logger.info(
            f"[EXTRACT_ALL] Done — {found_count}/{len(all_results)} found "
            f"(pass1: {pass1_found}, pass2: +{pass2_recovered}) — "
            f"total: {extraction_time:.2f}s"
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
