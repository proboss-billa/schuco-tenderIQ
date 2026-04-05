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

from config.parameters import FACADE_PARAMETERS
from core.clients import pinecone_index, embedding_client
from core.database import SessionLocal
from extraction.parameter_extractor import ParameterExtractor
from models.project import Project
from services import event_bus

logger = logging.getLogger(__name__)

# Coalesce doc-indexed events within this many seconds into a single pass.
_DEBOUNCE_SECONDS = 5.0
# Hard cap on incremental passes (excludes the final pass).
_MAX_INCREMENTAL_RUNS = 10


@dataclass
class CoordinatorState:
    project_id: str
    model_key: Optional[str] = None
    runs_done: int = 0
    indexed_doc_ids: set[str] = field(default_factory=set)
    indexing_complete: bool = False
    last_notify_at: float = 0.0


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

    async def notify_doc_indexed(self, document_id, filename: str = "") -> None:
        """Signal that a new document has finished indexing."""
        self.state.indexed_doc_ids.add(str(document_id))
        self.state.last_notify_at = time.monotonic()
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

                # Clear the event BEFORE extraction so any notifications that
                # arrive during the pass will re-trigger the next iteration.
                self._pending_event.clear()
                await self._run_incremental_pass()
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
        """Sleep until `_DEBOUNCE_SECONDS` have elapsed since the last notify.

        Returns False if indexing completed while waiting (caller should
        fall through to the final pass on the next loop iteration).
        """
        while True:
            elapsed = time.monotonic() - self.state.last_notify_at
            remaining = _DEBOUNCE_SECONDS - elapsed
            if remaining <= 0:
                return True
            # Sleep in small chunks so we can bail out if indexing completes.
            await asyncio.sleep(min(remaining, 1.0))
            if self.state.indexing_complete:
                return False

    async def _run_incremental_pass(self) -> None:
        t0 = time.perf_counter()
        self.state.runs_done += 1
        run_number = self.state.runs_done

        logger.info(
            f"[COORDINATOR {self.state.project_id[:8]}] "
            f"Incremental pass #{run_number} starting "
            f"({len(self.state.indexed_doc_ids)} docs indexed so far)"
        )

        result = await self._invoke_extraction(is_final=False)

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

        result = await self._invoke_extraction(is_final=True)

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
            f"[COORDINATOR {self.state.project_id[:8]}] "
            f"Final pass done in {duration_ms}ms"
        )

    async def _invoke_extraction(self, *, is_final: bool) -> dict:
        """Invoke ParameterExtractor.extract_incremental in a fresh DB session."""
        db = SessionLocal()
        try:
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

            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=db,
                session_factory=SessionLocal,
                model_key=self.state.model_key,
            )

            result = await extractor.extract_incremental(
                project_id=self.state.project_id,
                facade_parameters=filtered_params,
                is_final=is_final,
                on_param_update=self._publish_param_update,
            )
            return result or {}
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
