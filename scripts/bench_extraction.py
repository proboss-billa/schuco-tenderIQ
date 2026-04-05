#!/usr/bin/env python3
"""
⚠️  PARKED EXPERIMENT — references the parked ExtractionCoordinator work.
    Not wired into active CI. Kept in-tree for later resumption. Do not
    delete.  ⚠️

bench_extraction.py — Parameter extraction benchmark harness.

Runs `ParameterExtractor.extract_all_parameters_async` against an existing
project (one already processed through the pipeline, with chunks already
in PostgreSQL + Pinecone) and reports wall-clock latency, LLM call count,
per-shard breakdown, and found-ratio.

Purpose: turn the "it feels faster" hand-wave into an actual number.

Usage
─────
    python scripts/bench_extraction.py <project_id> [--model gemini-3-flash]
                                                    [--runs 3]
                                                    [--narrow DOC_ID1,DOC_ID2]

Examples
────────
    # Full-corpus benchmark, 3 runs, median reported
    python scripts/bench_extraction.py 7b6f...  --runs 3

    # Benchmark a narrow per-doc pass (simulates a streaming incremental)
    python scripts/bench_extraction.py 7b6f... --narrow abc-123,def-456

Output
──────
    Per-run line + summary block with min / median / max wall-clock,
    median LLM call count, median found count. All data parsed from the
    structured `[METRICS][EXTRACT_ALL]` log line emitted by the extractor,
    so any future metric added there automatically shows up here.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import statistics
import sys
import time
from typing import List, Optional


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


class _MetricCapture(logging.Handler):
    """Captures [METRICS][EXTRACT_ALL] log lines and parses key=value pairs."""

    def __init__(self):
        super().__init__()
        self.last: dict = {}

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "[METRICS][EXTRACT_ALL]" not in msg:
            return
        pairs = re.findall(r"(\w+)=([^\s]+)", msg)
        self.last = {k: v for k, v in pairs}


async def _run_once(
    project_id: str,
    model_key: str,
    narrow_doc_ids: Optional[set],
) -> dict:
    """Run one extraction pass and return captured metrics."""
    from config.parameters import FACADE_PARAMETERS
    from core.clients import pinecone_index, embedding_client
    from core.database import SessionLocal
    from extraction.parameter_extractor import ParameterExtractor
    from models.project import Project

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.project_id == project_id).first()
        if not project:
            raise SystemExit(f"Project {project_id} not found")
        project_type = project.project_type or "commercial"
        params = [
            p for p in FACADE_PARAMETERS
            if p.get("project_type", "both") in ("both", project_type)
        ]

        extractor = ParameterExtractor(
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            db_session=db,
            session_factory=SessionLocal,
            model_key=model_key,
        )

        capture = _MetricCapture()
        logging.getLogger("extraction.parameter_extractor").addHandler(capture)

        t0 = time.perf_counter()
        results = await extractor.extract_all_parameters_async(
            project_id=project_id,
            facade_parameters=params,
            num_docs=1,
            skip_vector_fallback=False,
            document_ids_filter=narrow_doc_ids,
            skip_persist_not_found=bool(narrow_doc_ids),
        )
        wall_clock = time.perf_counter() - t0

        metric = dict(capture.last)
        metric.setdefault("wall_clock_seconds", f"{wall_clock:.2f}")
        metric.setdefault("found", str(sum(1 for r in results if r.get("found"))))
        metric.setdefault("total_params", str(len(results)))
        return metric
    finally:
        db.close()


def _summarize(runs: List[dict]) -> None:
    wall = [float(r.get("duration_ms", "0")) / 1000 for r in runs]
    calls = [int(r.get("llm_calls", "0")) for r in runs]
    found = [int(r.get("found", "0")) for r in runs]
    shards = [int(r.get("shards", "0")) for r in runs]

    def _stats(label: str, xs: List[float]) -> str:
        if not xs:
            return f"{label}: n/a"
        return (
            f"{label}: min={min(xs):.2f} median={statistics.median(xs):.2f} "
            f"max={max(xs):.2f}"
        )

    print()
    print("=" * 72)
    print(f"BENCHMARK SUMMARY ({len(runs)} runs)")
    print("=" * 72)
    print(_stats("wall_clock_s", wall))
    print(_stats("llm_calls   ", [float(c) for c in calls]))
    print(_stats("found_params", [float(f) for f in found]))
    if shards:
        print(f"shards       : {shards[0]} (constant)")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", help="Project UUID to benchmark")
    parser.add_argument(
        "--model",
        default="gemini-3-flash",
        help="Model key (see config/models.py). Default: gemini-3-flash",
    )
    parser.add_argument(
        "--runs", type=int, default=3, help="Number of benchmark runs. Default: 3"
    )
    parser.add_argument(
        "--narrow",
        default=None,
        help="Comma-separated document IDs to simulate a narrow incremental pass",
    )
    args = parser.parse_args()

    _setup_logging()

    narrow = None
    if args.narrow:
        narrow = {d.strip() for d in args.narrow.split(",") if d.strip()}
        print(f"[BENCH] Narrow mode: {len(narrow)} docs")

    all_runs: List[dict] = []
    for i in range(args.runs):
        print(f"\n[BENCH] ━━━ Run {i + 1}/{args.runs} ━━━")
        try:
            metric = asyncio.run(_run_once(args.project_id, args.model, narrow))
        except Exception as e:
            print(f"[BENCH] Run {i + 1} failed: {e}", file=sys.stderr)
            continue
        print(
            f"[BENCH] duration_ms={metric.get('duration_ms', '?')} "
            f"found={metric.get('found', '?')}/{metric.get('total_params', '?')} "
            f"llm_calls={metric.get('llm_calls', '?')} "
            f"shards={metric.get('shards', '?')} "
            f"budget_aborted={metric.get('budget_aborted', '?')}"
        )
        all_runs.append(metric)

    if all_runs:
        _summarize(all_runs)
        return 0
    print("[BENCH] No successful runs", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
