# processing/document_processor.py
from __future__ import annotations

import logging
import time
from typing import List, Dict, Any
import uuid
from pathlib import Path

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

    def _build_hierarchical_chunks(
            self,
            parsed_content: List[Dict],
            document,
    ) -> tuple[List[Dict], List[Dict]]:
        """
        Build a two-level hierarchy of chunks from parsed document blocks.

        ── Level 0  (parent / section) ─────────────────────────────────────────
        One chunk per contiguous (section, subsection) group.  Contains the
        FULL concatenated text of that section.  Stored in PostgreSQL only —
        NOT embedded or indexed in Pinecone.  Acts as the rich-context window
        passed to the LLM after a child-level vector search hits.

        ── Level 1  (child / paragraph) ────────────────────────────────────────
        ~CHILD_SIZE-word slices within each section, with NO overlap (the
        parent already provides surrounding context).  These are embedded and
        indexed in Pinecone for precise retrieval.

        Each child stores:
          parent_chunk_id → the level-0 chunk for its section
          prev_chunk_id   → previous sibling in the same section
          next_chunk_id   → next sibling in the same section

        Very long sections (> PARENT_MAX_WORDS) are split into multiple parent
        chunks so the LLM context window is never overwhelmed.

        Returns
        -------
        (parent_chunks, child_chunks)  –  plain dicts (no SQLAlchemy objects).
        Each dict contains a pre-assigned ``chunk_id`` UUID so that children
        can reference parent IDs before any DB write.
        """
        # ── Phase 1: group blocks into contiguous sections ───────────────────
        sections: List[Dict] = []
        current: Dict | None = None

        for block in parsed_content:
            if block.get("is_heading"):
                continue

            sec_key = (block.get("section"), block.get("subsection"))
            if current is None or current["sec_key"] != sec_key:
                current = {
                    "sec_key":   sec_key,
                    "section":   block.get("section"),
                    "subsection": block.get("subsection"),
                    "page_start": block.get("page"),
                    "page_end":   block.get("page"),
                    "all_words":  [],
                    "is_table":   block["type"] == "table",
                }
                sections.append(current)

            current["all_words"].extend(block["text"].split())
            if block.get("page") is not None:
                current["page_end"] = block.get("page")
            if block["type"] != "table":
                current["is_table"] = False   # mixed → not a pure table section

        # ── Phase 2: produce parent + child chunks ───────────────────────────
        parents:  List[Dict] = []
        children: List[Dict] = []
        chunk_idx = 0   # shared sequential counter preserves document order

        for sec in sections:
            all_words = sec["all_words"]
            if not all_words:
                continue

            # Split very long sections into ≤PARENT_MAX_WORDS parent windows
            # so no single LLM context is gigantic.
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

                # Slice parent into CHILD_SIZE children
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
                        "page_start":      sec["page_start"],
                        "page_end":        sec["page_end"],
                        "section":         sec["section"],
                        "subsection":      sec["subsection"],
                        "is_table":        False,
                        "parent_chunk_id": parent_id,
                        "prev_chunk_id":   None,   # wired below
                        "next_chunk_id":   None,
                    })
                    chunk_idx += 1

                # Wire doubly-linked list within the parent window
                for j, child in enumerate(section_children):
                    if j > 0:
                        child["prev_chunk_id"] = section_children[j - 1]["chunk_id"]
                        section_children[j - 1]["next_chunk_id"] = child["chunk_id"]

                children.extend(section_children)

        logger.info(
            f"[CHUNK] doc={document.original_filename}: "
            f"{len(parents)} section-parents, {len(children)} child chunks"
        )
        return parents, children

    def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Call the embedding client in batches to avoid hitting API size limits.
        Assumes self.embedder exposes:
            embedder.embed(texts: List[str]) -> List[List[float]]
        """
        all_embeddings: List[List[float]] = []

        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start: start + EMBED_BATCH_SIZE]
            batch_embeddings = self.embedder.embed(batch)
            all_embeddings.extend(batch_embeddings)

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
                    "file_type":   document.file_type,
                    "section":     chunk.get("section") or "",
                    "subsection":  chunk.get("subsection") or "",
                    "page_start":  chunk.get("page_start") or 0,
                    "is_table":    chunk.get("is_table", False),
                    "chunk_level": 1,
                    "text_preview": chunk["text"][:200],
                },
            })

        # Batch-upsert to Pinecone (max 100 vectors per call)
        PINECONE_BATCH = 100
        for start in range(0, len(pinecone_vectors), PINECONE_BATCH):
            self.pinecone.upsert(vectors=pinecone_vectors[start: start + PINECONE_BATCH])

        self.db.commit()

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
            try:
                if doc.file_type in ['pdf_spec', 'docx_spec']:
                    self._process_specification_document(doc)
                elif doc.file_type == 'excel_boq':
                    self._process_boq_document(doc)
            except Exception as e:
                logger.error(f"Failed to process {doc.original_filename}: {e}")

        self._extract_all_parameters()

    def _process_specification_document(self, document):
        """Process PDF/DOCX specification using hierarchical chunking."""

        # Step 1: Parse document into raw blocks
        if document.file_type == 'pdf_spec':
            parsed_content = self._parse_pdf(document.file_path)
        else:  # docx_spec
            parsed_content = self._parse_docx(document.file_path)

        # Step 2: Build section-parent + paragraph-child chunk hierarchy
        parents, children = self._build_hierarchical_chunks(parsed_content, document)

        # Step 3: Embed only child chunks (parents are context, not queries)
        child_embeddings = self._generate_embeddings([c['text'] for c in children])

        # Step 4: Store parents (DB only) + children (DB + Pinecone)
        self._store_chunks(parents, children, child_embeddings, document)

        # Mark as processed — report searchable child count
        document.processed = True
        document.num_chunks = len(children)
        self.db.commit()

    def _process_boq_document(self, document):
        """Process Excel BOQ — store items in PostgreSQL and embed into Pinecone."""
        try:
            boq_items = self._parse_excel_boq(document.file_path)
        except Exception as e:
            logger.warning(f"Skipping BOQ parsing for {document.original_filename}: {e}")
            boq_items = []

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

            PINECONE_BATCH = 100
            for start in range(0, len(pinecone_vectors), PINECONE_BATCH):
                self.pinecone.upsert(vectors=pinecone_vectors[start: start + PINECONE_BATCH])

        document.processed = True
        self.db.commit()