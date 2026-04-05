"""
extraction_coordinator.py
─────────────────────────
Per-project coordinator that runs parameter extraction *incrementally* as
documents finish indexing, instead of waiting for the entire corpus.

Lifecycle
---------
    pipeline._run_pipeline_inner
        │
        ├── coordinator = ExtractionCoordinator(project_id, …)
        ├── run_task = asyncio.create_task(coordinator.run_loop())
        │
        ├── (per document, after Pinecone sync)
        │       await coordinator.notify_doc_indexed(doc_id)
        │
        ├── (after last doc)
        │       await coordinator.notify_indexing_complete()
        │
        └── await run_task   # resolves once the final pass finishes

Design notes
------------
• Debounced: a burst of `notify_doc_indexed` calls coalesces into one
  extraction pass after `_DEBOUNCE_SECONDS` of quiet.
• Cost-capped: no more than `_MAX_INCREMENTAL_RUNS` incremental passes
  before the final one, regardless of how many docs land.
• Crash-safe: lifecycle state lives on the DB row, not in memory. A
  worker restart just means one extra incremental pass next time.
• Emits SSE events via `services.event_bus` after every pass and every
  parameter update, so the frontend can render live.
• The extraction work itself lives in
  `ParameterExtractor.extract_incremental` (Phase 3). This module is
  purely the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text

from config.parameters import FACADE_PARAMETERS
from core.clients import pinecone_index, embedding_client
from core.database import SessionLocal
from extraction.parameter_extractor import ParameterExtractor
from extraction.priority import priority_for
from models.document import Document
from models.extracted_parameter import ExtractedParameter, LIFECYCLE_FINAL
from models.project import Project
from services import event_bus

logger = logging.getLogger(__name__)

# ── Adaptive debounce ──────────────────────────────────────────────────────
# Instead of a fixed 12s window we wait just `_DEBOUNCE_TAIL_SECONDS` after
# the most recent doc-indexed event (tail) but never more than
# `_DEBOUNCE_CEILING_SECONDS` from the first unprocessed event (ceiling).
# Short tail → quick response when docs trickle in one-by-one; ceiling →
# we don't hold off indefinitely when docs keep arriving in a storm.
_DEBOUNCE_TAIL_SECONDS = 3.0
_DEBOUNCE_CEILING_SECONDS = 20.0

# Hard cap on incremental passes (excludes the final pass).
_MAX_INCREMENTAL_RUNS = 3
# Fire the very first pass as soon as ONE high-priority doc (BoQ/spec)
# lands, or after two drawings — see `_should_run_first_pass`.
_MIN_DOCS_BEFORE_FIRST_PASS = 1
# Use Gemini Flash (cheap + fast) for incremental passes regardless of the
# user's chosen model. The final pass runs on the user-selected model so
# the authoritative answer uses whatever accuracy they paid for.
_FAST_INCREMENTAL_MODEL = "gemini-3-flash"


@dataclass
class CoordinatorState:
    project_id: str
    model_key: Optional[str] = None
    runs_done: int = 0
    indexed_doc_ids: set[str] = field(default_factory=set)
    # Docs the extractor has already been shown (final pass always sees all).
    extracted_doc_ids: set[str] = field(default_factory=set)
    # Per-doc file_type cache so we can prioritise BoQ/spec over drawings
    # without round-tripping to the DB.
    doc_file_types: dict[str, str] = field(default_factory=dict)
    indexing_complete: bool = False
    last_notify_at: float = 0.0
    # Timestamp of first unprocessed event — anchors the debounce ceiling.
    first_pending_at: float = 0.0


class ExtractionCoordinator:
    """Scheduler that fires incremental parameter extractions as docs index."""

    def __init__(self, project_id, model_key: Optional[str] = None):
        self.state = CoordinatorState(
            project_id=str(project_id),
            model_key=model_key,
        )
        self._pending_event = asyncio.Event()
        self._complete_event = asyncio.Event()
        self._run_task: Optional[asyncio.Task] = None

    # ---------- public API called by the pipeline ----------

    async def notify_doc_indexed(
        self,
        document_id,
        filename: str = "",
        file_type: Optional[str] = None,
    ) -> None:
        """Signal that a new document has finished indexing."""
        doc_id_str = str(document_id)
        self.state.indexed_doc_ids.add(doc_id_str)
        if file_type:
            self.state.doc_file_types[doc_id_str] = file_type
        now = time.monotonic()
        if self.state.first_pending_at == 0.0 or not self._pending_event.is_set():
            # Anchor the ceiling on the first notify of the current burst.
            self.state.first_pending_at = now
        self.state.last_notify_at = now
        self._pending_event.set()

        await event_bus.publish(
            self.state.project_id,
            "doc_indexed",
            {
                "document_id": str(document_id),
                "filename": filename,
                "indexed_count": len(self.state.indexed_doc_ids),
            },
        )
        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] Doc indexed: {filename} "
            f"({len(self.state.indexed_doc_ids)} total)"
        )

    async def notify_indexing_complete(self) -> None:
        """Signal that no more documents will be indexed — triggers final pass."""
        self.state.indexing_complete = True
        self._pending_event.set()
        self._complete_event.set()
        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] Indexing complete — "
            f"final pass will run"
        )

    # ---------- main loop ----------

    async def run_loop(self) -> None:
        """Event-driven scheduler. Exits after final pass."""
        logger.info(f"[COORDINATOR {self.state.project_id[:8]}] Starting run loop")
        # Legacy-row backfill: projects that completed before streaming existed
        # have rows without lifecycle_status; mark them `final` on first touch
        # so the UI doesn't show them as tentative.
        await asyncio.get_event_loop().run_in_executor(
            None, self._backfill_legacy_lifecycle
        )
        try:
            while True:
                # Wait for either a doc-indexed event or completion.
                await self._pending_event.wait()

                # If the indexing barrier was hit, run the final pass and exit.
                if self.state.indexing_complete:
                    await self._run_final_pass()
                    break

                # Debounce: wait until the burst of doc-indexed events settles.
                if not await self._wait_for_quiet_period():
                    # Indexing finished during debounce — handle as final.
                    continue

                # Cost guard: skip incremental runs beyond the cap; they'll all
                # be absorbed by the final pass anyway.
                if self.state.runs_done >= _MAX_INCREMENTAL_RUNS:
                    logger.info(
                        f"[COORDINATOR {self.state.project_id[:8]}] "
                        f"Hit incremental run cap ({_MAX_INCREMENTAL_RUNS}) — "
                        f"waiting for final pass"
                    )
                    self._pending_event.clear()
                    continue

                # Don't fire the very first incremental pass until we have
                # enough documents to make extraction worthwhile. A single
                # indexed doc rarely contains cross-document evidence we'd
                # commit to anyway, and running the extractor on it just to
                # re-run a few seconds later wastes an entire LLM round trip.
                if self.state.runs_done == 0 and not self._should_run_first_pass():
                    logger.info(
                        f"[COORDINATOR {self.state.project_id[:8]}] "
                        f"Waiting for high-priority doc or ≥2 docs before first pass "
                        f"(have {len(self.state.indexed_doc_ids)})"
                    )
                    self._pending_event.clear()
                    continue

                # Nothing new to extract? Wait for more notifications.
                new_doc_ids = self._new_doc_ids_sorted()
                if not new_doc_ids:
                    self._pending_event.clear()
                    continue

                # Reset burst anchor + clear event BEFORE extraction so any
                # notifications that arrive during the pass will re-trigger
                # the next iteration with a fresh ceiling.
                self._pending_event.clear()
                self.state.first_pending_at = 0.0
                await self._run_incremental_pass(new_doc_ids)
        except Exception as e:
            logger.exception(
                f"[COORDINATOR {self.state.project_id[:8]}] Run loop crashed: {e}"
            )
            await event_bus.publish(
                self.state.project_id,
                "error",
                {"message": str(e)},
            )
        finally:
            await event_bus.publish(
                self.state.project_id,
                "done",
                {
                    "runs_done": self.state.runs_done,
                    "indexed_doc_count": len(self.state.indexed_doc_ids),
                },
            )
            logger.info(f"[COORDINATOR {self.state.project_id[:8]}] Run loop exited")

    # ---------- internal ----------

    async def _wait_for_quiet_period(self) -> bool:
        """Adaptive debounce: tail timer + absolute ceiling.

        Waits until either:
          • `_DEBOUNCE_TAIL_SECONDS` have passed since the last notify
            (quiet period reached), OR
          • `_DEBOUNCE_CEILING_SECONDS` have elapsed since the first
            unprocessed notify (prevents infinite hold during a storm).

        Returns False if indexing completed while waiting (caller should
        fall through to the final pass on the next loop iteration).
        """
        while True:
            now = time.monotonic()
            tail_remaining = _DEBOUNCE_TAIL_SECONDS - (now - self.state.last_notify_at)
            ceiling_remaining = _DEBOUNCE_CEILING_SECONDS - (
                now - (self.state.first_pending_at or now)
            )
            remaining = min(tail_remaining, ceiling_remaining)
            if remaining <= 0:
                return True
            await asyncio.sleep(min(remaining, 0.5))
            if self.state.indexing_complete:
                return False

    def _should_run_first_pass(self) -> bool:
        """First pass fires as soon as there's high-signal content (BoQ/spec)
        or when at least 2 docs have indexed.
        """
        if len(self.state.indexed_doc_ids) >= 2:
            return True
        # 1 doc: only fire if it's a high-authority type.
        for doc_id in self.state.indexed_doc_ids:
            ft = self.state.doc_file_types.get(doc_id, "")
            if priority_for(ft) >= 80:
                return True
        return False

    def _new_doc_ids_sorted(self) -> list[str]:
        """Return not-yet-extracted doc ids ordered by DOC_PRIORITY desc."""
        new_ids = list(self.state.indexed_doc_ids - self.state.extracted_doc_ids)
        new_ids.sort(
            key=lambda d: priority_for(self.state.doc_file_types.get(d, "")),
            reverse=True,
        )
        return new_ids

    async def _run_incremental_pass(self, new_doc_ids: list[str]) -> None:
        t0 = time.perf_counter()
        self.state.runs_done += 1
        run_number = self.state.runs_done

        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] "
            f"Incremental pass #{run_number} starting — "
            f"{len(new_doc_ids)} new docs "
            f"(priority order: {[self.state.doc_file_types.get(d, '?') for d in new_doc_ids[:5]]})"
        )

        result = await self._invoke_extraction(
            is_final=False,
            new_document_ids=set(new_doc_ids),
        )
        # Mark these docs as extracted so the next pass only sees newer arrivals.
        self.state.extracted_doc_ids.update(new_doc_ids)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        self._bump_runs_completed()

        await event_bus.publish(
            self.state.project_id,
            "pass_complete",
            {
                "pass_number": run_number,
                "is_final": False,
                "updated_count": len(result.get("updated", [])),
                "new_count": len(result.get("new", [])),
                "total_found": result.get("total_found", 0),
                "duration_ms": duration_ms,
            },
        )
        # Structured metrics line for log aggregators / dashboards.
        logger.info(
            "[METRICS][EXTRACTION_PASS] "
            f"project_id={self.state.project_id} "
            f"pass_number={run_number} "
            f"is_final=false "
            f"docs_indexed={len(self.state.indexed_doc_ids)} "
            f"params_updated={len(result.get('updated', []))} "
            f"params_new={len(result.get('new', []))} "
            f"params_unchanged={len(result.get('unchanged', []))} "
            f"total_found={result.get('total_found', 0)} "
            f"skipped_pass={result.get('skipped_pass', False)} "
            f"duration_ms={duration_ms}"
        )
        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] "
            f"Incremental pass #{run_number} done in {duration_ms}ms — "
            f"{len(result.get('updated', []))} updated, "
            f"{len(result.get('new', []))} new"
        )

    async def _run_final_pass(self) -> None:
        t0 = time.perf_counter()
        self.state.runs_done += 1
        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] "
            f"Final pass starting ({len(self.state.indexed_doc_ids)} docs)"
        )

        result = await self._invoke_extraction(is_final=True, new_document_ids=None)
        # After the final pass every doc is considered extracted.
        self.state.extracted_doc_ids.update(self.state.indexed_doc_ids)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        self._bump_runs_completed()

        await event_bus.publish(
            self.state.project_id,
            "pass_complete",
            {
                "pass_number": self.state.runs_done,
                "is_final": True,
                "updated_count": len(result.get("updated", [])),
                "new_count": len(result.get("new", [])),
                "total_found": result.get("total_found", 0),
                "duration_ms": duration_ms,
            },
        )
        logger.info(
            "[METRICS][EXTRACTION_PASS] "
            f"project_id={self.state.project_id} "
            f"pass_number={self.state.runs_done} "
            f"is_final=true "
            f"docs_indexed={len(self.state.indexed_doc_ids)} "
            f"params_updated={len(result.get('updated', []))} "
            f"params_new={len(result.get('new', []))} "
            f"params_unchanged={len(result.get('unchanged', []))} "
            f"total_found={result.get('total_found', 0)} "
            f"duration_ms={duration_ms}"
        )
        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] "
            f"Final pass done in {duration_ms}ms"
        )

    async def _invoke_extraction(
        self,
        *,
        is_final: bool,
        new_document_ids: Optional[set] = None,
    ) -> dict:
        """Invoke ParameterExtractor.extract_incremental in a fresh DB session.

        Wraps the extraction in a PostgreSQL advisory lock keyed on the
        project's UUID so a manual /re-extract can't race with the coordinator.
        """
        db = SessionLocal()
        lock_acquired = False
        # Derive a 64-bit integer lock key from the project UUID.
        # Python `hash()` is randomized per-process, so we use hex slicing.
        try:
            lock_key = int(self.state.project_id.replace("-", "")[:15], 16)
        except Exception:
            lock_key = abs(hash(self.state.project_id)) % (2**63 - 1)
        try:
            try:
                db.execute(text("SELECT pg_advisory_lock(:k)"), {"k": lock_key})
                lock_acquired = True
            except Exception as e:
                logger.warning(
                    f"[COORDINATOR {self.state.project_id[:8]}] "
                    f"Advisory lock failed ({e}) — proceeding without lock"
                )

            project = db.query(Project).filter(
                Project.project_id == self.state.project_id
            ).first()
            if not project:
                logger.warning(
                    f"[COORDINATOR {self.state.project_id[:8]}] Project vanished — aborting"
                )
                return {}

            p_type = project.project_type or "commercial"
            filtered_params = [
                p for p in FACADE_PARAMETERS
                if p.get("project_type", "both") in ("both", p_type)
            ]

            # Model tiering: use fast Flash for incremental passes,
            # user-selected model only for the authoritative final pass.
            model_for_pass = (
                self.state.model_key if is_final else _FAST_INCREMENTAL_MODEL
            )

            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=db,
                session_factory=SessionLocal,
                model_key=model_for_pass,
            )

            result = await extractor.extract_incremental(
                project_id=self.state.project_id,
                facade_parameters=filtered_params,
                is_final=is_final,
                on_param_update=self._publish_param_update,
                new_document_ids=new_document_ids,
            )
            return result or {}
        finally:
            if lock_acquired:
                try:
                    db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                    db.commit()
                except Exception:
                    pass
            db.close()

    def _backfill_legacy_lifecycle(self) -> None:
        """Mark pre-streaming rows as `final` so they don't appear tentative.

        Projects created before the streaming migration have rows where
        `lifecycle_status` defaults to 'tentative'. If the project is already
        `completed`, those rows should actually be `final`.
        """
        db = SessionLocal()
        try:
            project = db.query(Project).filter(
                Project.project_id == self.state.project_id
            ).first()
            if project and project.processing_status == "completed":
                updated = (
                    db.query(ExtractedParameter)
                    .filter(
                        ExtractedParameter.project_id == self.state.project_id,
                        ExtractedParameter.lifecycle_status != LIFECYCLE_FINAL,
                    )
                    .update(
                        {ExtractedParameter.lifecycle_status: LIFECYCLE_FINAL},
                        synchronize_session=False,
                    )
                )
                if updated:
                    db.commit()
                    logger.info(
                        f"[COORDINATOR {self.state.project_id[:8]}] "
                        f"Backfilled {updated} legacy rows to final"
                    )
        except Exception as e:
            logger.warning(f"[COORDINATOR] legacy backfill failed: {e}")
            db.rollback()
        finally:
            db.close()

    async def _publish_param_update(self, param_payload: dict) -> None:
        """Callback passed to the extractor — publishes a param_updated event."""
        await event_bus.publish(
            self.state.project_id,
            "param_updated",
            param_payload,
        )

    def _bump_runs_completed(self) -> None:
        """Persist run counter to DB (best-effort)."""
        db = SessionLocal()
        try:
            project = db.query(Project).filter(
                Project.project_id == self.state.project_id
            ).first()
            if project:
                project.extraction_runs_completed = self.state.runs_done
                db.commit()
        except Exception as e:
            logger.warning(f"[COORDINATOR] Failed to bump runs counter: {e}")
        finally:
            db.close()
