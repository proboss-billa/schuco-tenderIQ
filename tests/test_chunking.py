"""Tests for chunking/semantic_chunker.py."""

import pytest
from chunking.semantic_chunker import SemanticChunker


def _make_blocks(texts, page=1, section="Test Section", subsection=None):
    """Helper to build block dicts for the chunker."""
    return [
        {
            "text": t,
            "page": page,
            "section": section,
            "subsection": subsection,
            "is_heading": False,
        }
        for t in texts
    ]


class TestSemanticChunker:
    def test_single_block_produces_one_chunk(self):
        chunker = SemanticChunker(chunk_size=100, overlap=10)
        blocks = _make_blocks(["This is a simple paragraph."])
        chunks = chunker.chunk(blocks)
        assert len(chunks) == 1
        assert "simple paragraph" in chunks[0]["text"]
        assert chunks[0]["section"] == "Test Section"

    def test_heading_blocks_are_skipped(self):
        chunker = SemanticChunker(chunk_size=1000, overlap=10)
        blocks = [
            {"text": "Section Title", "page": 1, "section": "S1", "subsection": None, "is_heading": True},
            {"text": "Body text content here.", "page": 1, "section": "S1", "subsection": None, "is_heading": False},
        ]
        chunks = chunker.chunk(blocks)
        assert len(chunks) == 1
        assert "Section Title" not in chunks[0]["text"]
        assert "Body text" in chunks[0]["text"]

    def test_large_content_splits_into_multiple_chunks(self):
        chunker = SemanticChunker(chunk_size=20, overlap=5)
        # Each block has ~10 words
        blocks = _make_blocks([
            "one two three four five six seven eight nine ten",
            "eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty",
            "twenty-one twenty-two twenty-three twenty-four twenty-five twenty-six twenty-seven twenty-eight twenty-nine thirty",
        ])
        chunks = chunker.chunk(blocks)
        assert len(chunks) >= 2, f"Expected multiple chunks, got {len(chunks)}"

    def test_page_numbers_tracked(self):
        chunker = SemanticChunker(chunk_size=1000, overlap=10)
        blocks = [
            {"text": "Page 1 content", "page": 1, "section": "S", "subsection": None, "is_heading": False},
            {"text": "Page 2 content", "page": 2, "section": "S", "subsection": None, "is_heading": False},
        ]
        chunks = chunker.chunk(blocks)
        assert len(chunks) == 1
        assert 1 in chunks[0]["page_numbers"]
        assert 2 in chunks[0]["page_numbers"]
        assert chunks[0]["start_page"] == 1
        assert chunks[0]["end_page"] == 2

    def test_section_metadata_preserved(self):
        chunker = SemanticChunker(chunk_size=1000, overlap=10)
        blocks = _make_blocks(
            ["Some content about glass specifications."],
            section="Glass Requirements",
            subsection="3.2 IGU Makeup",
        )
        chunks = chunker.chunk(blocks)
        assert chunks[0]["section"] == "Glass Requirements"
        assert chunks[0]["subsection"] == "3.2 IGU Makeup"

    def test_empty_blocks_produce_no_chunks(self):
        chunker = SemanticChunker()
        assert chunker.chunk([]) == []
