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
CHUNK_SIZE = 512  # target tokens per chunk
CHUNK_OVERLAP = 64  # overlap tokens between consecutive chunks
EMBED_BATCH_SIZE = 64  # max texts per embedding API call
PARAM_BATCH_SIZE = 1800  # chunks sent per LLM parameter-extraction call

class DocumentProcessor:
    """Main processing orchestrator"""

    def __init__(self, project_id: uuid.UUID, db_session, pinecone_index, embedding_client, llm_client):
        self.project_id = project_id
        self.db = db_session
        self.pinecone = pinecone_index
        self.embedder = embedding_client
        self.llm = llm_client
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

    def _chunk_with_metadata(
            self,
            parsed_content: List[Dict],
            document,
    ) -> List[Dict]:
        """
        Merge consecutive non-heading text blocks into fixed-size chunks
        (by word count as a token proxy) with CHUNK_OVERLAP words of
        overlap.  Table blocks are always emitted as standalone chunks so
        their tabular structure is never split mid-row.

        Returned chunk schema:
            {
                "chunk_index":  int,
                "text":         str,
                "document_id":  uuid,
                "file_type":    str,
                "page_start":   int | None,
                "page_end":     int | None,
                "section":      str | None,
                "subsection":   str | None,
                "is_table":     bool,
            }
        """
        chunks: List[Dict] = []
        chunk_index = 0

        # Rolling word buffer for text blocks
        word_buffer: List[str] = []
        meta_buffer: Dict = {
            "page_start": None,
            "page_end": None,
            "section": None,
            "subsection": None,
        }

        def flush_buffer(final: bool = False) -> None:
            """Emit a chunk from the current word buffer."""
            nonlocal chunk_index

            if not word_buffer:
                return

            # Determine how many words to keep as overlap for the next chunk
            overlap_words = word_buffer[-CHUNK_OVERLAP:] if not final else []

            chunks.append({
                "chunk_index": chunk_index,
                "text": " ".join(word_buffer),
                "document_id": document.document_id,
                "file_type": document.file_type,
                **meta_buffer,
                "is_table": False,
            })
            chunk_index += 1

            # Reset buffer, keeping overlap
            word_buffer.clear()
            word_buffer.extend(overlap_words)

        for block in parsed_content:
            # ── skip pure headings (they're captured in section/subsection) ──
            if block.get("is_heading"):
                continue

            # ── tables → always standalone chunks ────────────────────────────
            if block["type"] == "table":
                flush_buffer()  # emit any buffered text first
                chunks.append({
                    "chunk_index": chunk_index,
                    "text": block["text"],
                    "document_id": document.document_id,
                    "file_type": document.file_type,
                    "page_start": block.get("page"),
                    "page_end": block.get("page"),
                    "section": block.get("section"),
                    "subsection": block.get("subsection"),
                    "is_table": True,
                })
                chunk_index += 1
                continue

            # ── text blocks → accumulate into rolling word buffer ─────────────
            words = block["text"].split()

            # Update page / section metadata from the *first* block in buffer
            if not word_buffer:
                meta_buffer["page_start"] = block.get("page")
                meta_buffer["section"] = block.get("section")
                meta_buffer["subsection"] = block.get("subsection")

            meta_buffer["page_end"] = block.get("page")
            word_buffer.extend(words)

            # Flush when the buffer is large enough
            while len(word_buffer) >= CHUNK_SIZE:
                # Emit exactly CHUNK_SIZE words
                emit_words = word_buffer[:CHUNK_SIZE]
                chunks.append({
                    "chunk_index": chunk_index,
                    "text": " ".join(emit_words),
                    "document_id": document.document_id,
                    "file_type": document.file_type,
                    **meta_buffer,
                    "is_table": False,
                })
                chunk_index += 1
                # Slide window forward (keep overlap)
                word_buffer[:] = word_buffer[CHUNK_SIZE - CHUNK_OVERLAP:]

        flush_buffer(final=True)
        return chunks

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
            chunks: List[Dict],
            embeddings: List[List[float]],
            document,
    ) -> None:
        """
        Persist chunks to:
          • PostgreSQL  – one DocumentChunk row per chunk (full text + metadata)
          • Pinecone    – one vector per chunk (embedding + lightweight metadata)

        Pinecone vector id format:  "{document_id}_{chunk_index}"
        """
        pinecone_vectors: List[Dict] = []

        for chunk, embedding in zip(chunks, embeddings):
            vector_id = f"{document.document_id}_{chunk['chunk_index']}"

            # ── PostgreSQL ────────────────────────────────────────────────────
            db_chunk = DocumentChunk(
                document_id=document.document_id,
                project_id=self.project_id,
                chunk_index=chunk["chunk_index"],
                chunk_text=chunk["text"],
                page_number=chunk.get("page"),
                section_title=chunk.get("section"),
                subsection_title=chunk.get("subsection"),
                pinecone_id=vector_id,
            )
            self.db.add(db_chunk)

            # ── Pinecone vector (batched below) ───────────────────────────────
            pinecone_vectors.append({
                "id": vector_id,
                "values": embedding,
                "metadata": {
                    "document_id": str(document.document_id),
                    "project_id": str(self.project_id),
                    "file_type": document.file_type,
                    "section": chunk.get("section") or "",
                    "subsection": chunk.get("subsection") or "",
                    "page_start": chunk.get("page_start") or 0,
                    "is_table": chunk.get("is_table", False),
                    # Store a short preview so Pinecone metadata is searchable
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

        # Call Gemini with JSON response mime type
        response = self.gemini_llm_client.models.generate_content(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                system_instruction=system_instr,
                response_mime_type="application/json",
                temperature=0.1,  # Lower temperature for better extraction accuracy
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
        """Main entry point - synchronous processing"""

        # Get all documents for this project
        documents = self.db.query(Document).filter(
            Document.project_id == self.project_id,
            Document.processed == False
        ).all()

        for doc in documents:
            if doc.file_type in ['pdf_spec', 'docx_spec']:
                self._process_specification_document(doc)
            elif doc.file_type == 'excel_boq':
                self._process_boq_document(doc)

        # After all docs processed, extract parameters
        self._extract_all_parameters()

    def _process_specification_document(self, document):
        """Process PDF/DOCX specification"""

        # Step 1: Parse document
        if document.file_type == 'pdf_spec':
            parsed_content = self._parse_pdf(document.file_path)
        else:  # docx_spec
            parsed_content = self._parse_docx(document.file_path)

        # Step 2: Chunk with metadata preservation
        chunks = self._chunk_with_metadata(parsed_content, document)

        # Step 3: Generate embeddings
        embeddings = self._generate_embeddings([c['text'] for c in chunks])

        # Step 4: Store in Pinecone + PostgreSQL
        self._store_chunks(chunks, embeddings, document)

        # Mark as processed
        document.processed = True
        document.num_chunks = len(chunks)
        self.db.commit()

    def _process_boq_document(self, document):
        """Process Excel BOQ"""

        # Parse Excel
        boq_items = self._parse_excel_boq(document.file_path)

        # Store in PostgreSQL
        for item in boq_items:
            boq_record = BOQItem(
                project_id=self.project_id,
                document_id=document.document_id,
                **item
            )
            self.db.add(boq_record)

        document.processed = True
        self.db.commit()