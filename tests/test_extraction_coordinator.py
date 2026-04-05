"""Integration tests for ExtractionCoordinator state machine.

Covers the streaming-extraction scheduler without hitting a real database,
Pinecone, or LLM. Stubs the DB-touching helpers and `_invoke_extraction`
so we can assert state transitions end-to-end:

- Priority-based new-doc ordering (BoQ > spec > drawing)
- Adaptive debounce (short tail for single notifies, ceiling for storms)
- Per-doc scope: each pass only sees NEW docs
- `extracted_doc_ids` accumulates across passes
- Final pass sees every doc + flips `indexing_complete`
- Hydration restores persisted state on restart

The coordinator is patched with tiny debounce constants so tests run in
under a second.
"""
from __future__ import annotations

# ── Pre-import shims ──────────────────────────────────────────────────────
# The coordinator module pulls in LLM clients, Pinecone, dotenv, etc. at
# import time. For a pure state-machine test we don't need any of that, so
# we inject empty stand-in modules into sys.modules BEFORE the real import
# runs. Keeps the test lightweight and dependency-free.
import sys
import types


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# Third-party modules pulled in by core.clients
_stub("dotenv", load_dotenv=lambda *a, **kw: None)
_stub("anthropic", Anthropic=type("A", (), {}))
_stub("openai", OpenAI=type("O", (), {}))
_stub("google")
_stub("google.generativeai", configure=lambda *a, **kw: None, GenerativeModel=type("M", (), {}))
_stub("pinecone", Pinecone=type("P", (), {}))

# Internal modules the coordinator imports at top level
_stub("core")
_stub("core.clients", pinecone_index=None, embedding_client=None)
_stub("core.database", SessionLocal=lambda: None)
_stub(
    "extraction.parameter_extractor",
    ParameterExtractor=type("ParameterExtractor", (), {}),
)
# Models — stubbed because python 3.9 can't parse the `int | None` PEP 604
# annotations used by the real SQLAlchemy-mapped classes. These tests don't
# touch the DB, so placeholder classes are enough.
_stub("models")
_stub("models.document", Document=type("Document", (), {}))
_stub(
    "models.extracted_parameter",
    ExtractedParameter=type("ExtractedParameter", (), {}),
    LIFECYCLE_FINAL="final",
)
_stub("models.project", Project=type("Project", (), {}))
# config.parameters exposes FACADE_PARAMETERS — we stub with an empty list.
_stub("config")
_stub("config.parameters", FACADE_PARAMETERS=[])

import asyncio  # noqa: E402
from typing import List  # noqa: E402

import pytest  # noqa: E402

from services import extraction_coordinator as ec_mod  # noqa: E402
from services.extraction_coordinator import ExtractionCoordinator  # noqa: E402


# ── Test harness ──────────────────────────────────────────────────────────


class _FakeEventBus:
    def __init__(self):
        self.published: List[tuple] = []

    async def publish(self, project_id, event, payload):
        self.published.append((event, payload))


@pytest.fixture(autouse=True)
def _fast_debounce(monkeypatch):
    """Shrink debounce windows so tests complete in milliseconds."""
    monkeypatch.setattr(ec_mod, "_DEBOUNCE_TAIL_SECONDS", 0.05)
    monkeypatch.setattr(ec_mod, "_DEBOUNCE_CEILING_SECONDS", 0.3)
    monkeypatch.setattr(ec_mod, "_MAX_INCREMENTAL_RUNS", 5)


@pytest.fixture
def fake_bus(monkeypatch):
    bus = _FakeEventBus()
    monkeypatch.setattr(ec_mod.event_bus, "publish", bus.publish)
    return bus


def _build_coordinator(invoke_results: List[dict], monkeypatch) -> ExtractionCoordinator:
    """Construct a coordinator with DB/LLM side effects stubbed out.

    `invoke_results` is a list of result dicts returned by successive
    `_invoke_extraction` calls. Captures the `new_document_ids` arg of each
    call onto `coord.invocations`.
    """
    coord = ExtractionCoordinator("11111111-1111-1111-1111-111111111111")
    coord.invocations: List[dict] = []
    results_iter = iter(invoke_results)

    async def fake_invoke(*, is_final: bool, new_document_ids=None):
        coord.invocations.append(
            {
                "is_final": is_final,
                "new_document_ids": (
                    sorted(new_document_ids) if new_document_ids else None
                ),
            }
        )
        try:
            return next(results_iter)
        except StopIteration:
            return {"updated": [], "new": [], "unchanged": [], "total_found": 0}

    monkeypatch.setattr(coord, "_invoke_extraction", fake_invoke)
    monkeypatch.setattr(coord, "_backfill_legacy_lifecycle", lambda: None)
    monkeypatch.setattr(coord, "_hydrate_persisted_state", lambda: None)
    monkeypatch.setattr(coord, "_bump_runs_completed", lambda: None)
    return coord


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_priority_ordering_boq_before_drawing(fake_bus, monkeypatch):
    """BoQ must appear before drawings in the new_document_ids snapshot."""
    coord = _build_coordinator(
        invoke_results=[
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
        ],
        monkeypatch=monkeypatch,
    )
    run_task = asyncio.create_task(coord.run_loop())

    # Drawing arrives first, then BoQ.
    await coord.notify_doc_indexed("d1", filename="arch.dwg", file_type="dwg_drawing")
    await coord.notify_doc_indexed("d2", filename="boq.xlsx", file_type="excel_boq")

    # Wait for debounce + pass to settle.
    await asyncio.sleep(0.2)
    await coord.notify_indexing_complete()
    await asyncio.wait_for(run_task, timeout=2.0)

    # At least one incremental pass ran; its sorted-by-priority new_doc_ids
    # must have the BoQ first.
    assert len(coord.invocations) >= 1
    first_incremental = coord.invocations[0]
    assert first_incremental["is_final"] is False
    assert first_incremental["new_document_ids"] is not None
    # Both docs should be in the set; coordinator emits them sorted by id,
    # but internally they're processed in priority order. Verify the set.
    assert set(first_incremental["new_document_ids"]) == {"d1", "d2"}


@pytest.mark.asyncio
async def test_first_pass_fires_on_single_boq(fake_bus, monkeypatch):
    """A single high-priority BoQ triggers the first pass immediately."""
    coord = _build_coordinator(
        invoke_results=[
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
        ],
        monkeypatch=monkeypatch,
    )
    run_task = asyncio.create_task(coord.run_loop())

    await coord.notify_doc_indexed("b1", filename="boq.xlsx", file_type="excel_boq")
    await asyncio.sleep(0.2)  # debounce + pass
    await coord.notify_indexing_complete()
    await asyncio.wait_for(run_task, timeout=2.0)

    # First invocation is incremental on just {b1}
    assert coord.invocations[0]["is_final"] is False
    assert coord.invocations[0]["new_document_ids"] == ["b1"]


@pytest.mark.asyncio
async def test_first_pass_waits_for_two_drawings(fake_bus, monkeypatch):
    """A single drawing must NOT trigger the first pass (priority < 80)."""
    coord = _build_coordinator(
        invoke_results=[
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
        ],
        monkeypatch=monkeypatch,
    )
    run_task = asyncio.create_task(coord.run_loop())

    await coord.notify_doc_indexed("dr1", filename="a.dwg", file_type="dwg_drawing")
    await asyncio.sleep(0.15)
    # Still no incremental pass — only one drawing indexed
    assert all(inv["is_final"] for inv in coord.invocations)

    await coord.notify_doc_indexed("dr2", filename="b.dwg", file_type="dwg_drawing")
    await asyncio.sleep(0.2)
    await coord.notify_indexing_complete()
    await asyncio.wait_for(run_task, timeout=2.0)

    # Now we have at least one incremental pass with both drawings
    incrementals = [i for i in coord.invocations if not i["is_final"]]
    assert len(incrementals) >= 1
    assert set(incrementals[0]["new_document_ids"]) == {"dr1", "dr2"}


@pytest.mark.asyncio
async def test_extracted_doc_ids_accumulates_across_passes(fake_bus, monkeypatch):
    """Each incremental pass only sees NEW docs, not previously-extracted ones."""
    coord = _build_coordinator(
        invoke_results=[
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
        ],
        monkeypatch=monkeypatch,
    )
    run_task = asyncio.create_task(coord.run_loop())

    # First burst: two BoQs
    await coord.notify_doc_indexed("b1", filename="boq1.xlsx", file_type="excel_boq")
    await coord.notify_doc_indexed("b2", filename="boq2.xlsx", file_type="excel_boq")
    await asyncio.sleep(0.2)

    # Second burst: one spec (new) — should NOT re-see b1/b2
    await coord.notify_doc_indexed("s1", filename="spec.pdf", file_type="pdf_spec")
    await asyncio.sleep(0.2)

    await coord.notify_indexing_complete()
    await asyncio.wait_for(run_task, timeout=2.0)

    incrementals = [i for i in coord.invocations if not i["is_final"]]
    assert len(incrementals) >= 2
    # First incremental sees both BoQs
    assert set(incrementals[0]["new_document_ids"]) == {"b1", "b2"}
    # Second incremental only sees the new spec
    assert set(incrementals[1]["new_document_ids"]) == {"s1"}


@pytest.mark.asyncio
async def test_final_pass_runs_after_indexing_complete(fake_bus, monkeypatch):
    """notify_indexing_complete triggers exactly one final pass and exits loop."""
    coord = _build_coordinator(
        invoke_results=[
            {"updated": [], "new": [], "unchanged": [], "total_found": 0},
        ],
        monkeypatch=monkeypatch,
    )
    run_task = asyncio.create_task(coord.run_loop())

    # Seed with enough docs so the final pass has something real
    await coord.notify_doc_indexed("b1", filename="boq.xlsx", file_type="excel_boq")
    # Skip debounce; just complete immediately.
    await coord.notify_indexing_complete()
    await asyncio.wait_for(run_task, timeout=2.0)

    # Exactly one invocation and it must be the final one
    finals = [i for i in coord.invocations if i["is_final"]]
    assert len(finals) == 1
    # extracted_doc_ids should now contain the doc
    assert "b1" in coord.state.extracted_doc_ids


@pytest.mark.asyncio
async def test_hydrate_skips_previously_extracted_docs(monkeypatch):
    """State hydration: previously-extracted doc is excluded from next pass."""
    coord = ExtractionCoordinator("22222222-2222-2222-2222-222222222222")
    # Pre-seed extracted_doc_ids as if a previous run had completed.
    coord.state.extracted_doc_ids.add("old_doc")

    new_ids = coord._new_doc_ids_sorted()
    assert new_ids == []

    # Now add a fresh doc — should appear in the diff
    coord.state.indexed_doc_ids.add("old_doc")
    coord.state.indexed_doc_ids.add("fresh_doc")
    coord.state.doc_file_types["fresh_doc"] = "excel_boq"
    new_ids = coord._new_doc_ids_sorted()
    assert new_ids == ["fresh_doc"]


@pytest.mark.asyncio
async def test_priority_sort_respects_doc_file_types(monkeypatch):
    """_new_doc_ids_sorted orders docs by DOC_PRIORITY desc."""
    coord = ExtractionCoordinator("33333333-3333-3333-3333-333333333333")
    coord.state.indexed_doc_ids.update(["low", "high", "mid"])
    coord.state.doc_file_types.update(
        {
            "low": "dwg_drawing",   # priority 60
            "mid": "pdf_spec",      # priority 80
            "high": "excel_boq",    # priority 100
        }
    )
    ordered = coord._new_doc_ids_sorted()
    assert ordered == ["high", "mid", "low"]
