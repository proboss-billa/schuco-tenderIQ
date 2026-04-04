"""Tests for parameter extractor parsing and utility methods."""

import json
import pytest

# We test static/instance methods by importing the class and calling them
# without a real Pinecone/DB connection.
from extraction.parameter_extractor import ParameterExtractor


# ---------------------------------------------------------------------------
# Helpers — build a minimal extractor (no real connections)
# ---------------------------------------------------------------------------

def _make_extractor():
    """Create a ParameterExtractor with None dependencies (only static methods used)."""
    # Pass None for all deps — we only call pure/static methods in these tests.
    return ParameterExtractor(
        pinecone_index=None,
        embedding_client=None,
        db_session=None,
        session_factory=None,
    )


# ---------------------------------------------------------------------------
# _parse_batch_response
# ---------------------------------------------------------------------------

class TestParseBatchResponse:
    def test_valid_json(self, sample_facade_parameters, sample_chunk_dicts):
        ext = _make_extractor()
        response_json = json.dumps({
            "wind_load": {
                "found": True,
                "value": "2.2 kN/m2",
                "value_numeric": 2.2,
                "unit": "kN/m2",
                "confidence": 0.95,
                "source_numbers": [1],
                "explanation": "Explicitly stated in structural requirements",
            },
            "glass_type": {
                "found": True,
                "value": "8+16+8 IGU Low-E",
                "value_numeric": None,
                "unit": "mm",
                "confidence": 0.90,
                "source_numbers": [2],
                "explanation": "Glass spec found in section",
            },
        })

        results = ext._parse_batch_response(
            response_json, sample_facade_parameters, sample_chunk_dicts
        )

        assert len(results) == 2
        assert results[0]["parameter_name"] == "wind_load"
        assert results[0]["found"] is True
        assert results[0]["value"] == "2.2 kN/m2"
        assert results[0]["confidence"] == 0.95

        assert results[1]["parameter_name"] == "glass_type"
        assert results[1]["found"] is True

    def test_truncated_json_falls_back_to_not_found(
        self, sample_facade_parameters, sample_chunk_dicts
    ):
        """If JSON is completely unparseable, all params should be marked not-found."""
        ext = _make_extractor()
        truncated = '{"wind_load": {"found": true, "value": "2.2 kN'  # cut off

        results = ext._parse_batch_response(
            truncated, sample_facade_parameters, sample_chunk_dicts
        )

        assert len(results) == 2
        for r in results:
            assert r["found"] is False
            assert r["reason"] == "JSON parse failed"

    def test_missing_param_in_response(
        self, sample_facade_parameters, sample_chunk_dicts
    ):
        """If one param is missing from the JSON dict, it should be marked not-found."""
        ext = _make_extractor()
        response_json = json.dumps({
            "wind_load": {
                "found": True,
                "value": "2.2 kN/m2",
                "value_numeric": 2.2,
                "unit": "kN/m2",
                "confidence": 0.95,
                "source_numbers": [1],
                "explanation": "Found",
            },
            # glass_type intentionally omitted
        })

        results = ext._parse_batch_response(
            response_json, sample_facade_parameters, sample_chunk_dicts
        )

        assert len(results) == 2
        assert results[0]["found"] is True
        assert results[1]["found"] is False
        assert results[1]["reason"] == "Missing in response"


# ---------------------------------------------------------------------------
# _recover_truncated_json
# ---------------------------------------------------------------------------

class TestRecoverTruncatedJson:
    def test_recovers_complete_params(
        self, sample_facade_parameters, sample_chunk_dicts
    ):
        """Should recover params from a JSON response truncated mid-object."""
        ext = _make_extractor()
        # Valid JSON for wind_load, then glass_type gets cut off
        partial = (
            '{"wind_load": {"found": true, "value": "2.2 kN/m2", "value_numeric": 2.2, '
            '"unit": "kN/m2", "confidence": 0.95, "source_numbers": [1], '
            '"explanation": "Found in doc"}, '
            '"glass_type": {"found": true, "value": "8+16+8 IGU Low-E", "confiden'
        )

        results = ext._recover_truncated_json(
            partial, sample_facade_parameters, sample_chunk_dicts
        )

        # wind_load should be recovered, glass_type may or may not be
        wind = next(r for r in results if r["parameter_name"] == "wind_load")
        assert wind["found"] is True
        assert wind["value"] == "2.2 kN/m2"

    def test_totally_broken_json(self, sample_facade_parameters, sample_chunk_dicts):
        """If nothing can be recovered, all should be not-found."""
        ext = _make_extractor()
        results = ext._recover_truncated_json(
            "not json at all {{{", sample_facade_parameters, sample_chunk_dicts
        )
        assert all(r["found"] is False for r in results)


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        assert ParameterExtractor._estimate_tokens("") == 0

    def test_known_word_count(self):
        text = "one two three four five six seven eight nine ten"
        tokens = ParameterExtractor._estimate_tokens(text)
        # 10 words * 1.35 = 13.5 -> int = 13
        assert tokens == 13

    def test_single_word(self):
        assert ParameterExtractor._estimate_tokens("hello") == 1


# ---------------------------------------------------------------------------
# _build_context_windows
# ---------------------------------------------------------------------------

class TestBuildContextWindows:
    def test_single_window(self):
        ext = _make_extractor()
        chunks = [
            {"chunk_text": "word " * 100, "document_name": "d.pdf", "page_number": 1}
            for _ in range(3)
        ]
        # 3 chunks * 100 words * 1.35 = 405 tokens — fits in one window
        windows = ext._build_context_windows(chunks, max_tokens=1000)
        assert len(windows) == 1
        assert len(windows[0]) == 3

    def test_multiple_windows(self):
        ext = _make_extractor()
        # Each chunk ~135 tokens.  max_tokens=200 → should split.
        chunks = [
            {"chunk_text": "word " * 100, "document_name": "d.pdf", "page_number": i}
            for i in range(5)
        ]
        windows = ext._build_context_windows(chunks, max_tokens=200)
        assert len(windows) > 1

    def test_empty_input(self):
        ext = _make_extractor()
        assert ext._build_context_windows([]) == []
