# processing/document_processor.py
from __future__ import annotations

import logging
import time
from typing import List, Dict, Any
import uuid
from pathlib import Path

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from google import genai
from google.genai import types
import os

from models.boq_item import BOQItem
from models.document import Document

import json
from docx import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph

from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter
from parsing.excel_parser import ExcelBOQParser
from parsing.pdf_parser import PDFParser

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────
# Hierarchical chunking sizes (words, not tokens — a reasonable proxy)
CHILD_SIZE        = 200   # level-1 child chunk: ~200 words, precise for retrieval
PARENT_MAX_WORDS  = 3000  # level-0 parent cap: long sections are split here
EMBED_BATCH_SIZE  = 64    # max texts per embedding API call
PARAM_BATCH_SIZE  = 1800  # chunks sent per LLM parameter-extraction call

# Streaming processing: number of sections processed per batch.
# Each section typically contributes 1 parent + a few children.
# At SECTION_BATCH=50 with ~3 children/section → ~150 child chunks per DB commit.
# This keeps peak memory per batch small regardless of document size.
SECTION_BATCH = 50

# Legacy constants kept for reference — no longer used in new pipeline
CHUNK_SIZE    = 512
CHUNK_OVERLAP = 64

class DocumentProcessor:
    """Main processing orchestrator"""

    def __init__(self, project_id: uuid.UUID, db_session, pinecone_index, embedding_client):
        self.project_id = project_id
        self.db = db_session
        self.pinecone = pinecone_index
        self.embedder = embedding_client
        self.gemini_llm_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))



    # ── missing methods on DocumentProcessor ─────────────────────────────────────

    def _parse_pdf(self, file_path: str) -> List[Dict]:
        """
        Delegate to PDFParser.
        Returns raw block list produced by PDFParser.parse().
        """
        parser = PDFParser()
        return parser.parse(file_path)

    def _parse_docx(self, file_path: str) -> List[Dict]:
        """
        Extract text blocks from a DOCX file using python-docx,
        mimicking the block schema produced by PDFParser so that
        _chunk_with_metadata can treat both identically.

        Schema per block:
            {
                "type":       "text" | "table",
                "text":       str,
                "page":       int | None,   # DOCX has no pages; use paragraph index
                "section":    str | None,
                "subsection": str | None,
                "font_size":  float | None,
                "is_heading": bool,
            }
        """
        doc = DocxDocument(file_path)
        blocks: List[Dict] = []
        current_section: str | None = None
        current_subsection: str | None = None
        para_index = 0

        for element in doc.element.body:
            tag = element.tag.split("}")[-1]  # strip namespace

            # ── paragraphs ────────────────────────────────────────────────────
            if tag == "p":
                para = Paragraph(element, doc)
                text = para.text.strip()
                if not text:
                    continue

                style_name = para.style.name or ""
                # Heading 1 / Title → section;  Heading 2-3 → subsection
                is_h1 = style_name.lower() in ("heading 1", "title")
                is_h2 = style_name.lower() in ("heading 2", "heading 3")
                is_heading = is_h1 or is_h2

                if is_h1:
                    current_section = text
                    current_subsection = None
                elif is_h2:
                    current_subsection = text

                # Approximate font size from the first run (may be None)
                font_size: float | None = None
                if para.runs:
                    pt = para.runs[0].font.size
                    font_size = pt.pt if pt else None

                blocks.append({
                    "type": "text",
                    "text": text,
                    "page": para_index,  # logical index, not a real page
                    "section": current_section,
                    "subsection": current_subsection,
                    "font_size": font_size,
                    "is_heading": is_heading,
                })
                para_index += 1

            # ── tables ────────────────────────────────────────────────────────
            elif tag == "tbl":
                table = DocxTable(element, doc)
                rows_text: List[str] = []

                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    rows_text.append(" | ".join(cells))

                table_text = "\n".join(rows_text)
                if table_text.strip():
                    blocks.append({
                        "type": "table",
                        "text": table_text,
                        "page": None,
                        "section": current_section,
                        "subsection": current_subsection,
                        "font_size": None,
                        "is_heading": False,
                    })

        return blocks

    # ── Streaming chunking pipeline ───────────────────────────────────────────
    #
    # Large documents (300+ pages) previously failed because:
    #   1. ALL blocks were kept in memory simultaneously.
    #   2. ALL child chunks were embedded in one pass before any DB writes.
    #   3. One giant DB transaction held thousands of unsaved rows.
    #
    # The new approach splits the work into three clearly-separated phases:
    #   Phase 1  _group_into_sections()       — lightweight, no UUIDs
    #   Phase 2  _build_chunks_for_sections() — UUIDs, wiring, one section-batch
    #   Phase 3  embed + _store_chunks()      — per-batch API calls + DB commit
    #
    # _process_specification_document() drives them in a loop of SECTION_BATCH
    # sections at a time.  At any point, only ONE batch of parents + children +
    # embeddings is in memory.

    @staticmethod
    def _group_into_sections(parsed_content: List[Dict]) -> List[Dict]:
        """
        Phase 1 — Group raw parsed blocks into contiguous section objects.

        Each section dict:
            {sec_key, section, subsection, page_start, page_end,
             all_words: List[str], is_table: bool}

        This is a lightweight pass — no UUIDs, no embedding.  It converts
        blocks (which include headings) into text-bearing section buckets.
        """
        sections: List[Dict] = []
        current: Dict | None = None

        for block in parsed_content:
            if block.get("is_heading"):
                continue

            sec_key = (block.get("section"), block.get("subsection"))
            if current is None or current["sec_key"] != sec_key:
                current = {
                    "sec_key":    sec_key,
                    "section":    block.get("section"),
                    "subsection": block.get("subsection"),
                    "page_start": block.get("page"),
                    "page_end":   block.get("page"),
                    "all_words":  [],
                    "is_table":   block["type"] == "table",
                    # source_type drives Pinecone routing — 'pdf_drawing' wins over
                    # 'pdf_spec' within a section so drawing pages are not diluted
                    # by neighbouring text-extraction blocks.
                    "source_type": block.get("source_type"),
                }
                sections.append(current)

            current["all_words"].extend(block["text"].split())
            if block.get("page") is not None:
                current["page_end"] = block.get("page")
            if block["type"] != "table":
                current["is_table"] = False   # mixed → not a pure table section
            # Escalate to pdf_drawing if ANY block in this section is from Vision
            if block.get("source_type") == "pdf_drawing":
                current["source_type"] = "pdf_drawing"

        return sections

    def _build_chunks_for_sections(
            self,
            sections: List[Dict],
            document,
            chunk_idx_start: int = 0,
    ) -> tuple[List[Dict], List[Dict], int]:
        """
        Phase 2 — Build parent + child chunk dicts for a batch of sections.

        Returns (parents, children, next_chunk_idx).

        ── Level 0  (parent / section) ─────────────────────────────────────────
        One parent per contiguous (section, subsection) group (or per
        PARENT_MAX_WORDS window if the section is very long).  Stored in
        PostgreSQL ONLY — not embedded or indexed in Pinecone.  Acts as the
        rich context window given to the LLM.

        ── Level 1  (child / paragraph) ────────────────────────────────────────
        ~CHILD_SIZE-word slices within each parent window, embedded and
        indexed in Pinecone for precise retrieval.  Each child stores
        parent_chunk_id, prev_chunk_id, next_chunk_id.
        """
        parents:  List[Dict] = []
        children: List[Dict] = []
        chunk_idx = chunk_idx_start

        for sec in sections:
            all_words = sec["all_words"]
            if not all_words:
                continue

            for p_start in range(0, len(all_words), PARENT_MAX_WORDS):
                parent_words = all_words[p_start : p_start + PARENT_MAX_WORDS]
                parent_id    = uuid.uuid4()

                parents.append({
                    "chunk_id":        parent_id,
                    "chunk_index":     chunk_idx,
                    "chunk_level":     0,
                    "text":            " ".join(parent_words),
                    "document_id":     document.document_id,
                    "file_type":       document.file_type,
                    "source_type":     sec.get("source_type", document.file_type),
                    "page_start":      sec["page_start"],
                    "page_end":        sec["page_end"],
                    "section":         sec["section"],
                    "subsection":      sec["subsection"],
                    "is_table":        sec["is_table"],
                    "parent_chunk_id": None,
                    "prev_chunk_id":   None,
                    "next_chunk_id":   None,
                })
                chunk_idx += 1

                section_children: List[Dict] = []
                for c_start in range(0, len(parent_words), CHILD_SIZE):
                    child_words = parent_words[c_start : c_start + CHILD_SIZE]
                    if not child_words:
                        continue
                    section_children.append({
                        "chunk_id":        uuid.uuid4(),
                        "chunk_index":     chunk_idx,
                        "chunk_level":     1,
                        "text":            " ".join(child_words),
                        "document_id":     document.document_id,
                        "file_type":       document.file_type,
                        "source_type":     sec.get("source_type", document.file_type),
                        "page_start":      sec["page_start"],
                        "page_end":        sec["page_end"],
                        "section":         sec["section"],
                        "subsection":      sec["subsection"],
                        "is_table":        False,
                        "parent_chunk_id": parent_id,
                        "prev_chunk_id":   None,
                        "next_chunk_id":   None,
                    })
                    chunk_idx += 1

                # Wire doubly-linked list within the parent window
                for j, child in enumerate(section_children):
                    if j > 0:
                        child["prev_chunk_id"] = section_children[j - 1]["chunk_id"]
                        section_children[j - 1]["next_chunk_id"] = child["chunk_id"]

                children.extend(section_children)

        return parents, children, chunk_idx

    @retry(
        retry=retry_if_exception_type(Exception),
        wait=wait_exponential(multiplier=1, min=4, max=30),
        stop=stop_after_attempt(3),
        before_sleep=lambda rs: logger.warning(
            f"[EMBED] Retry {rs.attempt_number} after error: {rs.outcome.exception()}"
        ),
    )
    def _embed_with_retry(self, texts: List[str]) -> List[List[float]]:
        """Single embedding batch call with tenacity retry."""
        return self.embedder.embed(texts)

    def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Call the embedding client in batches to avoid hitting API size limits.
        Assumes self.embedder exposes:
            embedder.embed(texts: List[str]) -> List[List[float]]
        Each batch is retried up to 3× with exponential backoff on any error.
        """
        all_embeddings: List[List[float]] = []
        t0 = time.perf_counter()

        for batch_num, start in enumerate(range(0, len(texts), EMBED_BATCH_SIZE), 1):
            batch = texts[start: start + EMBED_BATCH_SIZE]
            bt = time.perf_counter()
            batch_embeddings = self._embed_with_retry(batch)
            logger.info(
                f"[TIMING][EMBED] batch {batch_num} ({len(batch)} texts): "
                f"{time.perf_counter() - bt:.2f}s"
            )
            all_embeddings.extend(batch_embeddings)

        logger.info(
            f"[TIMING][EMBED] total {len(texts)} texts in "
            f"{len(all_embeddings)} embeddings: {time.perf_counter() - t0:.2f}s"
        )
        return all_embeddings

    def _store_chunks(
            self,
            parent_chunks: List[Dict],
            child_chunks: List[Dict],
            child_embeddings: List[List[float]],
            document,
    ) -> None:
        """
        Persist to PostgreSQL and Pinecone.

        • Level-0 parents  → PostgreSQL only  (no Pinecone entry)
        • Level-1 children → PostgreSQL + Pinecone vector

        Pinecone vector id format: "{document_id}_{chunk_index}"
        """
        t0 = time.perf_counter()
        # ── Insert level-0 parent chunks (DB only) ────────────────────────────
        for chunk in parent_chunks:
            db_chunk = DocumentChunk(
                chunk_id=chunk["chunk_id"],
                document_id=document.document_id,
                project_id=self.project_id,
                chunk_index=chunk["chunk_index"],
                chunk_level=0,
                chunk_text=chunk["text"],
                page_number=chunk.get("page_start"),
                section_title=chunk.get("section"),
                subsection_title=chunk.get("subsection"),
                pinecone_id=None,                   # not indexed in Pinecone
                parent_chunk_id=None,
                prev_chunk_id=None,
                next_chunk_id=None,
            )
            self.db.add(db_chunk)

        # Flush parents first so children can FK-reference them
        self.db.flush()

        # ── Insert level-1 child chunks + build Pinecone vectors ─────────────
        pinecone_vectors: List[Dict] = []

        for chunk, embedding in zip(child_chunks, child_embeddings):
            vector_id = f"{document.document_id}_{chunk['chunk_index']}"

            db_chunk = DocumentChunk(
                chunk_id=chunk["chunk_id"],
                document_id=document.document_id,
                project_id=self.project_id,
                chunk_index=chunk["chunk_index"],
                chunk_level=1,
                chunk_text=chunk["text"],
                page_number=chunk.get("page_start"),
                section_title=chunk.get("section"),
                subsection_title=chunk.get("subsection"),
                pinecone_id=vector_id,
                parent_chunk_id=chunk.get("parent_chunk_id"),
                prev_chunk_id=chunk.get("prev_chunk_id"),
                next_chunk_id=chunk.get("next_chunk_id"),
            )
            self.db.add(db_chunk)

            pinecone_vectors.append({
                "id": vector_id,
                "values": embedding,
                "metadata": {
                    "document_id": str(document.document_id),
                    "project_id":  str(self.project_id),
                    # Use per-chunk source_type when available (set by DrawingPDFParser
                    # to distinguish vision-extracted drawing pages from text pages within
                    # the same mixed PDF). Falls back to document-level file_type.
                    "file_type":   chunk.get("source_type", document.file_type),
                    "section":     chunk.get("section") or "",
                    "subsection":  chunk.get("subsection") or "",
                    "page_start":  chunk.get("page_start") or 0,
                    "is_table":    chunk.get("is_table", False),
                    "chunk_level": 1,
                    "text_preview": chunk["text"][:200],
                },
            })

        t_flush = time.perf_counter()
        # Batch-upsert to Pinecone (max 100 vectors per call)
        PINECONE_BATCH = 100
        for start in range(0, len(pinecone_vectors), PINECONE_BATCH):
            self.pinecone.upsert(vectors=pinecone_vectors[start: start + PINECONE_BATCH])
        t_pine = time.perf_counter()

        self.db.commit()
        logger.info(
            f"[TIMING][STORE] {len(parent_chunks)} parents + {len(child_chunks)} children — "
            f"DB flush: {t_flush - t0:.2f}s | "
            f"Pinecone upsert: {t_pine - t_flush:.2f}s | "
            f"DB commit: {time.perf_counter() - t_pine:.2f}s | "
            f"total: {time.perf_counter() - t0:.2f}s"
        )

    def _parse_excel_boq(self, file_path: str) -> List[Dict]:
        """Delegate to ExcelBOQParser."""
        parser = ExcelBOQParser()
        return parser.parse(file_path)

    def _extract_all_parameters(self) -> None:
        """
        After all documents are processed, run an LLM pass over stored
        chunks to extract structured project parameters (materials,
        dimensions, performance values, standards, etc.) and persist them
        to the ExtractedParameter table.

        Strategy
        --------
        • Fetch spec chunks (PDF / DOCX) in batches of PARAM_BATCH_SIZE.
        • Send each batch to the LLM with a structured extraction prompt.
        • Parse the JSON response and upsert ExtractedParameter rows.
        • Skip BOQ chunks – those are already structured data.
        """
        chunks: List[DocumentChunk] = (
            self.db.query(DocumentChunk)
            .join(Document)
            .filter(
                DocumentChunk.project_id == self.project_id,
                Document.file_type.in_(["pdf_spec", "docx_spec"]),
            )
            .order_by(DocumentChunk.chunk_index)
            .all()
        )

        logger.info(f"Found {len(chunks)} chunks")

        # for batch_start in range(0, len(chunks), PARAM_BATCH_SIZE):
        #     batch = chunks[batch_start: batch_start + PARAM_BATCH_SIZE]
        #     self._extract_parameters_from_batch(batch)
        #     # time.sleep(10)

        self.db.commit()

    def _extract_parameters_from_batch(self, chunks: List) -> None:
        """
        Send one batch of chunks to the LLM and persist extracted parameters.

        Expected LLM response (strict JSON array):
        [
            {
                "parameter_name":  "Glazing U-value",
                "value":           "1.4",
                "unit":            "W/m²K",
                "category":        "thermal_performance",
                "source_section":  "Section 3.2 – Glazing",
                "confidence":      0.92
            },
            ...
        ]
        """
        context_blocks = []
        for c in chunks:
            header = f"[Section: {c.section_title or 'N/A'} | Page: {c.page_number or 'N/A'}]"
            context_blocks.append(f"{header}\n{c.chunk_text}")
        context = "\n\n---\n\n".join(context_blocks)

    #     prompt = f"""You are a construction specification analyst.
    #
    # Extract ALL quantitative and qualitative parameters from the specification
    # excerpts below.  Return ONLY a valid JSON array – no prose, no markdown fences.
    #
    # Each element must have exactly these keys:
    #   parameter_name  – concise name (e.g. "Wind load resistance")
    #   value           – extracted value as a string
    #   unit            – unit of measure, or "" if dimensionless
    #   category        – one of: structural | thermal_performance | acoustic |
    #                     fire_rating | material_spec | finish | compliance | other
    #   source_section  – section heading where found, or ""
    #   confidence      – float 0-1 reflecting extraction certainty
    #
    # Specification excerpts:
    # {context}
    # """
        system_instr = "You are a construction specification analyst."

        prompt = f"""Extract ALL quantitative and qualitative parameters from the specification
        excerpts below. Return ONLY a valid JSON array.

        Each element must have exactly these keys:
          parameter_name  – concise name (e.g. "Wind load resistance")
          value           – extracted value as a string
          unit            – unit of measure, or "" if dimensionless
          category        – one of: structural | thermal_performance | acoustic |
                            fire_rating | material_spec | finish | compliance | other
          source_section  – section heading where found, or ""
          confidence      – float 0-1 reflecting extraction certainty

        Specification excerpts:
        {context}
        """

        response = self.gemini_llm_client.models.generate_content(
            model="gemini-3-flash-preview",
            config=types.GenerateContentConfig(
                system_instruction=system_instr,
                response_mime_type="application/json",
                temperature=0.1,
            ),
            contents=prompt
        )
        raw_response = response.text

        # ── parse & persist ───────────────────────────────────────────────────
        try:
            parameters: List[Dict] = json.loads(raw_response)
        except json.JSONDecodeError:
            # Attempt to salvage by stripping accidental markdown fences
            cleaned = raw_response.strip().removeprefix("```json").removesuffix("```").strip()
            try:
                parameters = json.loads(cleaned)
            except json.JSONDecodeError:
                # Log and skip – don't crash the whole pipeline
                print(f"[WARN] Could not parse LLM parameter response for batch; skipping.")
                return

        for param in parameters:
            if not isinstance(param, dict):
                continue

            record = ExtractedParameter(
                project_id=self.project_id,
                parameter_name=param.get("parameter_name", ""),
                value=param.get("value", ""),
                unit=param.get("unit", ""),
                category=param.get("category", "other"),
                source_section=param.get("source_section", ""),
                confidence=float(param.get("confidence", 0.0)),
            )
            self.db.add(record)

    def process_all_documents(self):
        """Process all project documents sequentially then extract parameters."""

        documents = self.db.query(Document).filter(
            Document.project_id == self.project_id,
            Document.processed == False
        ).all()

        for doc in documents:
            doc.processing_status = "processing"
            self.db.commit()
            try:
                if doc.file_type in ['pdf_spec', 'docx_spec']:
                    self._process_specification_document(doc)
                elif doc.file_type == 'excel_boq':
                    self._process_boq_document(doc)
                doc.processing_status = "completed"
                self.db.commit()
            except Exception as e:
                logger.error(f"Failed to process {doc.original_filename}: {e}")
                doc.processing_status = "failed"
                doc.processing_error = str(e)[:1000]
                self.db.commit()
                # Continue with other documents — don't abort the whole project

        self._extract_all_parameters()

    def _choose_pdf_parser(self, file_path: str):
        """
        Sample the first 5 pages to decide between text-extraction and vision parsing.

        Drawing PDFs (CAD sheets rendered to PDF) have virtually no selectable
        text — PyMuPDF returns < MIN_TEXT_CHARS characters per page.
        Text-heavy specification documents have hundreds of chars per page.
        """
        import fitz as _fitz
        MIN_TEXT_CHARS = 100
        sample_doc = _fitz.open(file_path)
        sample_n   = min(5, len(sample_doc))
        total_chars = sum(
            len(sample_doc[i].get_text("text").strip()) for i in range(sample_n)
        )
        sample_doc.close()
        avg = total_chars / sample_n if sample_n else 0

        if avg < MIN_TEXT_CHARS:
            logger.info(
                f"[AUTO-DETECT] {file_path}: avg {avg:.0f} chars/page "
                f"→ DRAWING PDF — using Gemini Vision parser"
            )
            from parsing.drawing_pdf_parser import DrawingPDFParser
            return DrawingPDFParser()
        else:
            logger.info(
                f"[AUTO-DETECT] {file_path}: avg {avg:.0f} chars/page "
                f"→ TEXT PDF — using standard parser"
            )
            from parsing.pdf_parser import PDFParser
            return PDFParser()

    def _process_specification_document(self, document):
        """
        Process a PDF/DOCX specification using the streaming hierarchical pipeline.

        Memory model
        ────────────
        The pipeline processes SECTION_BATCH sections at a time so that only
        one small batch of parents + children + embeddings is ever in memory
        simultaneously.  This allows arbitrarily large documents (400+ pages)
        to be processed without hitting memory limits.

        Pipeline per batch
        ──────────────────
        1. _group_into_sections()         — parse blocks into section objects
        2. _build_chunks_for_sections()   — assign UUIDs, wire prev/next links
        3. _generate_embeddings()         — embed child texts (64 per API call)
        4. _store_chunks()                — INSERT to PostgreSQL + Pinecone, commit
        5. del parents/children/embeddings — release batch memory
        """
        doc_name = document.original_filename
        t_doc_start = time.perf_counter()

        # ── Step 1: Parse ─────────────────────────────────────────────────────
        t0 = time.perf_counter()
        if document.file_type in ('pdf_spec', 'pdf_drawing'):
            # pdf_drawing → always hybrid vision parser (skip char-count sampling).
            # pdf_spec    → auto-detect via char count; vision used if drawing-heavy.
            if document.file_type == 'pdf_drawing':
                from parsing.drawing_pdf_parser import DrawingPDFParser
                parser = DrawingPDFParser()
                logger.info(f"[PARSE] {doc_name}: pdf_drawing → forced DrawingPDFParser")
            else:
                parser = self._choose_pdf_parser(document.file_path)
            parsed_content, page_count = parser.parse_with_page_count(document.file_path)
            document.page_count = page_count
        elif document.file_type in ('dxf_drawing', 'dwg_drawing'):
            from parsing.dxf_parser import DXFParser
            parsed_content = DXFParser().parse(document.file_path)
        else:  # docx_spec
            parsed_content = self._parse_docx(document.file_path)
        logger.info(f"[TIMING][PARSE] {doc_name}: {time.perf_counter() - t0:.2f}s")

        # ── Step 2: Group into sections (lightweight — no UUIDs) ──────────────
        t0 = time.perf_counter()
        sections = self._group_into_sections(parsed_content)
        del parsed_content   # free raw block memory — no longer needed
        logger.info(f"[TIMING][SECTION_GROUP] {doc_name}: {time.perf_counter() - t0:.2f}s")

        total_sections = len(sections)
        total_batches  = max(1, (total_sections + SECTION_BATCH - 1) // SECTION_BATCH)
        logger.info(
            f"[CHUNK] {doc_name}: {total_sections} sections "
            f"→ {total_batches} streaming batch(es) of up to {SECTION_BATCH}"
        )

        # ── Steps 3–6: Stream — build UUIDs → embed → store → free ───────────
        chunk_idx      = 0
        total_children = 0

        for batch_num, batch_start in enumerate(range(0, total_sections, SECTION_BATCH), 1):
            batch = sections[batch_start : batch_start + SECTION_BATCH]
            t_batch = time.perf_counter()

            t0 = time.perf_counter()
            parents, children, chunk_idx = self._build_chunks_for_sections(
                batch, document, chunk_idx_start=chunk_idx
            )
            logger.info(
                f"[TIMING][BUILD_CHUNKS] {doc_name} batch {batch_num}/{total_batches}: "
                f"{time.perf_counter() - t0:.2f}s ({len(children)} children)"
            )
            if not children:
                continue

            child_embeddings = self._generate_embeddings([c["text"] for c in children])

            t0 = time.perf_counter()
            self._store_chunks(parents, children, child_embeddings, document)
            total_children += len(children)

            logger.info(
                f"[TIMING][BATCH] {doc_name} batch {batch_num}/{total_batches}: "
                f"total {time.perf_counter() - t_batch:.2f}s | "
                f"running total: {total_children} child chunks"
            )

            # Explicitly release this batch to keep peak memory small
            del parents, children, child_embeddings

        del sections

        # ── Mark as processed ─────────────────────────────────────────────────
        document.processed  = True
        document.num_chunks = total_children
        self.db.commit()
        logger.info(
            f"[TIMING][DOC_TOTAL] {doc_name}: {time.perf_counter() - t_doc_start:.2f}s — "
            f"{total_children} searchable child chunks"
        )

    def _process_boq_document(self, document):
        """Process Excel BOQ — store items in PostgreSQL and embed into Pinecone."""
        doc_name = document.original_filename
        t_doc_start = time.perf_counter()

        t0 = time.perf_counter()
        try:
            boq_items = self._parse_excel_boq(document.file_path)
        except Exception as e:
            logger.warning(f"Skipping BOQ parsing for {doc_name}: {e}")
            boq_items = []
        logger.info(f"[TIMING][BOQ_PARSE] {doc_name}: {time.perf_counter() - t0:.2f}s ({len(boq_items)} items)")

        valid_items = []
        for item in boq_items:
            try:
                boq_record = BOQItem(
                    project_id=self.project_id,
                    document_id=document.document_id,
                    **item
                )
                self.db.add(boq_record)
                valid_items.append(item)
            except Exception as e:
                logger.warning(f"Skipping BOQ item: {e}")
                continue

        # Build a rich text representation for each item and embed into Pinecone
        if valid_items:
            t0 = time.perf_counter()
            texts = []
            for item in valid_items:
                parts = []
                if item.get("item_number"):
                    parts.append(f"Item: {item['item_number']}")
                if item.get("description"):
                    parts.append(f"Description: {item['description']}")
                if item.get("quantity") is not None:
                    parts.append(f"Quantity: {item['quantity']} {item.get('unit') or ''}")
                if item.get("rate") is not None:
                    parts.append(f"Rate: {item['rate']}")
                if item.get("amount") is not None:
                    parts.append(f"Amount: {item['amount']}")
                if item.get("category"):
                    parts.append(f"Category: {item['category']}")
                if item.get("sub_category"):
                    parts.append(f"Sub-category: {item['sub_category']}")
                texts.append(" | ".join(parts))

            logger.info(f"[TIMING][BOQ_TEXT_BUILD] {doc_name}: {time.perf_counter() - t0:.2f}s")
            embeddings = self._generate_embeddings(texts)

            pinecone_vectors = []
            for chunk_index, (item, text, embedding) in enumerate(zip(valid_items, texts, embeddings)):
                vector_id = f"{document.document_id}_boq_{chunk_index}"

                db_chunk = DocumentChunk(
                    document_id=document.document_id,
                    project_id=self.project_id,
                    chunk_index=chunk_index,
                    chunk_text=text,
                    page_number=None,
                    section_title=item.get("category"),
                    subsection_title=item.get("sub_category"),
                    pinecone_id=vector_id,
                )
                self.db.add(db_chunk)

                pinecone_vectors.append({
                    "id": vector_id,
                    "values": embedding,
                    "metadata": {
                        "document_id": str(document.document_id),
                        "project_id": str(self.project_id),
                        "file_type": document.file_type,
                        "section": item.get("category") or "",
                        "subsection": item.get("sub_category") or "",
                        "page_start": 0,
                        "is_table": False,
                        "text_preview": text[:200],
                    },
                })

            t0 = time.perf_counter()
            PINECONE_BATCH = 100
            for start in range(0, len(pinecone_vectors), PINECONE_BATCH):
                self.pinecone.upsert(vectors=pinecone_vectors[start: start + PINECONE_BATCH])
            logger.info(f"[TIMING][BOQ_PINECONE] {doc_name}: {time.perf_counter() - t0:.2f}s ({len(pinecone_vectors)} vectors)")

        document.processed = True
        self.db.commit()
        logger.info(f"[TIMING][BOQ_TOTAL] {doc_name}: {time.perf_counter() - t_doc_start:.2f}s")