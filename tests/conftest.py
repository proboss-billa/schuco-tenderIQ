"""Shared pytest fixtures."""

import pytest


@pytest.fixture
def sample_chunk_dicts():
    """Return a list of chunk dicts as produced by _chunk_to_dict."""
    return [
        {
            "chunk_text": "Wind load shall be 2.2 kN/m2 as per IS 875 Part 3.",
            "page_number": 5,
            "section_title": "Structural Requirements",
            "subsection_title": "Wind Loads",
            "document_name": "spec_doc.pdf",
            "document_id": "doc-111",
            "chunk_id": "chunk-aaa",
            "chunk_level": 0,
            "score": 0.95,
        },
        {
            "chunk_text": "Glass specification: 8mm+16mm+8mm IGU with Low-E coating.",
            "page_number": 12,
            "section_title": "Glass Specifications",
            "subsection_title": None,
            "document_name": "glass_spec.pdf",
            "document_id": "doc-222",
            "chunk_id": "chunk-bbb",
            "chunk_level": 0,
            "score": 0.88,
        },
        {
            "chunk_text": "Facade mullion visible width: 60mm.",
            "page_number": 8,
            "section_title": "Facade Details",
            "subsection_title": "Mullion Profile",
            "document_name": "spec_doc.pdf",
            "document_id": "doc-111",
            "chunk_id": "chunk-ccc",
            "chunk_level": 1,
            "score": 0.82,
        },
    ]


@pytest.fixture
def sample_facade_parameters():
    """Minimal facade parameter configs for testing."""
    return [
        {
            "name": "wind_load",
            "display_name": "Wind Load",
            "description": "Design wind load pressure",
            "expected_units": ["kN/m2", "Pa"],
            "search_keywords": ["wind load", "wind pressure"],
        },
        {
            "name": "glass_type",
            "display_name": "Glass Type",
            "description": "Glass specification and makeup",
            "expected_units": ["mm"],
            "search_keywords": ["glass", "IGU", "glazing"],
        },
    ]
