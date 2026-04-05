import asyncio
import logging as _logging
import time as _time
import traceback
import uuid
from datetime import datetime

from config.parameters import FACADE_PARAMETERS
from core.clients import pinecone_index, embedding_client
from core.database import SessionLocal
from core.logging import _timing_project_id, _project_timings, _pipeline_log
from extraction.parameter_extractor import ParameterExtractor
from models.document import Document
from models.project import Project
from processing.document_processor import DocumentProcessor

# ── Global pipeline concurrency cap ──────────────────────────────────────────
# Limits simultaneous _run_pipeline executions to prevent DB pool exhaustion
# and Gemini rate-limit saturation when many projects are uploaded at once.
# Projects beyond the limit queue in the asyncio event loop rather than crashing.
# 3 concurrent pipelines x ~7 DB sessions each = 21 connections (within pool of 30).
_PIPELINE_SEMAPHORE = asyncio.Semaphore(3)


async def _wait_for_pinecone_doc(project_id: str, document_id: str, timeout: int = 90) -> bool:
    """Poll Pinecone until a specific document's vectors are queryable.

    Uses a per-document filter so we only proceed once THIS doc's chunks are
    visible -- not just any old vector from a previous run.

    Returns True if ready, False if timed out.
    """
    dummy_vec = [0.0] * 1536
    elapsed = 0
    poll_interval = 3
    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        try:
            probe = pinecone_index.query(
                vector=dummy_vec,
                top_k=1,
                filter={"project_id": project_id, "document_id": document_id},
                include_metadata=False,
            )
            if probe.get("matches"):
                _pipeline_log.info(
                    f"[PIPELINE] Pinecone ready for doc {document_id} after {elapsed}s"
                )
                return True
        except Exception as exc:
            _pipeline_log.warning(f"[PIPELINE] Pinecone probe error: {exc}")
    _pipeline_log.warning(
        f"[PIPELINE] Pinecone timed out for doc {document_id} after {timeout}s -- proceeding anyway"
    )
    return False


async def _run_pipeline(project_id: uuid.UUID, model_key: str = None, ocr_engine: str = "auto"):
    """Queuing wrapper -- acquires global pipeline slot, then delegates to inner."""
    _timing_project_id.set(str(project_id))
    _pipeline_log.info(f"[PIPELINE] Queued project {project_id} -- waiting for pipeline slot...")
    async with _PIPELINE_SEMAPHORE:
        await _run_pipeline_inner(project_id, model_key=model_key, ocr_engine=ocr_engine)


async def _run_pipeline_inner(project_id: uuid.UUID, model_key: str = None, ocr_engine: str = "auto"):
    """Background task: per-document parse -> embed -> extract parameters.

    Architecture
    ------------
    Phase 1: Parse + embed ALL spec documents sequentially (shared DB session).
             After each parse, fire a concurrent asyncio.Task to watch for that
             document's vectors in Pinecone. All watch-tasks run in parallel so
             total Phase-1 wall time = parse_time_sum + max(single_pinecone_wait).
    Phase 2: Extraction runs ONCE after all docs are indexed -- one set of
             18 batch LLM calls regardless of how many documents were uploaded.
    """
    _timing_project_id.set(str(project_id))
    _project_timings[str(project_id)] = []   # reset on each new run
    t_pipeline_start = _time.perf_counter()
    _pipeline_log.info(f"[PIPELINE] Starting for project {project_id}")
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.project_id == project_id).first()
        if not project:
            _pipeline_log.error(f"[PIPELINE] Project {project_id} not found")
            return

        all_docs = db.query(Document).filter(
            Document.project_id == project_id,
            Document.processed == False,
        ).all()

        spec_docs = [d for d in all_docs if d.file_type in ['pdf_spec', 'docx_spec', 'dxf_drawing', 'dwg_drawing', 'pdf_drawing']]
        boq_docs  = [d for d in all_docs if d.file_type == 'excel_boq']
        # Process non-drawing specs first (fast), then heavy drawing PDFs last
        spec_docs.sort(key=lambda d: (1 if d.file_type == 'pdf_drawing' else 0, d.original_filename))
        total_spec = len(spec_docs)
        _pipeline_log.info(
            f"[PIPELINE] {len(spec_docs)} spec docs, {len(boq_docs)} BOQ docs"
        )

        processor = DocumentProcessor(
            project_id=project_id,
            db_session=db,
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            ocr_engine=ocr_engine,
        )

        # ── Process BOQ documents (no LLM extraction needed) ─────────────────
        boq_pinecone_tasks = []
        for doc in boq_docs:
            project.pipeline_step = f"Processing BOQ: {doc.original_filename}"
            doc.processing_status = "processing"
            db.commit()
            t_boq = _time.perf_counter()
            try:
                processor._process_boq_document(doc)
                doc.processing_status = "completed"
                _pipeline_log.info(
                    f"[TIMING][PIPELINE] BOQ {doc.original_filename}: "
                    f"{_time.perf_counter() - t_boq:.2f}s"
                )
                # Fire Pinecone wait so BOQ vectors are ready for extraction
                boq_pinecone_tasks.append(
                    asyncio.create_task(
                        _wait_for_pinecone_doc(str(project_id), str(doc.document_id), timeout=120)
                    )
                )
            except Exception as e:
                _pipeline_log.error(f"[PIPELINE] BOQ failed {doc.original_filename}: {e}")
                doc.processing_status = "failed"
                doc.processing_error = str(e)[:4000]
            db.commit()

        # ── Phase 1: Parse all spec docs in PARALLEL ─────────────────────────
        DOC_CONCURRENCY = 6
        _doc_semaphore = asyncio.Semaphore(DOC_CONCURRENCY)
        _progress_lock = asyncio.Lock()
        _completed_count = [0]

        indexed_spec_count = 0
        indexed_docs    = []
        pinecone_wait_tasks = []

        async def _process_one_spec(doc, idx):
            """Process a single spec document, return (doc, success, wait_task|None)."""
            async with _doc_semaphore:
                doc_id   = doc.document_id
                doc_name = doc.original_filename

                async with _progress_lock:
                    project.pipeline_step = (
                        f"Processing documents ({idx}/{total_spec})..."
                    )
                    db.commit()

                t_doc = _time.perf_counter()
                doc_session = SessionLocal()
                try:
                    doc_local = doc_session.query(Document).filter(
                        Document.document_id == doc_id
                    ).first()
                    doc_local.processing_status = "processing"
                    doc_session.commit()

                    doc_processor = DocumentProcessor(
                        project_id=project_id,
                        db_session=doc_session,
                        pinecone_index=pinecone_index,
                        embedding_client=embedding_client,
                        ocr_engine=ocr_engine,
                    )

                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, doc_processor._process_specification_document, doc_local
                    )

                    doc_local.processing_status = "indexed"
                    doc_session.commit()

                    _completed_count[0] += 1
                    _pipeline_log.info(
                        f"[TIMING][PIPELINE] Parse+embed {doc_name} "
                        f"({_completed_count[0]}/{total_spec}): "
                        f"{_time.perf_counter() - t_doc:.2f}s"
                    )

                    wait_task = asyncio.create_task(
                        _wait_for_pinecone_doc(str(project_id), str(doc_id), timeout=180)
                    )
                    return (doc, True, wait_task)

                except Exception as e:
                    _pipeline_log.error(f"[PIPELINE] Parse failed {doc_name}: {e}")
                    try:
                        doc_session.rollback()
                        doc_local = doc_session.query(Document).filter(
                            Document.document_id == doc_id
                        ).first()
                        if doc_local:
                            doc_local.processing_status = "failed"
                            doc_local.processing_error = str(e)[:4000]
                            doc_session.commit()
                    except Exception:
                        pass
                    return (doc, False, None)
                finally:
                    doc_session.close()

        # Launch all spec docs concurrently (semaphore limits to DOC_CONCURRENCY)
        spec_tasks = [
            _process_one_spec(doc, i)
            for i, doc in enumerate(spec_docs, 1)
        ]
        spec_results = await asyncio.gather(*spec_tasks, return_exceptions=True)

        for result in spec_results:
            if isinstance(result, Exception):
                _pipeline_log.error(f"[PIPELINE] Unexpected task error: {result}")
                continue
            doc, success, wait_task = result
            if success:
                indexed_spec_count += 1
                indexed_docs.append(doc)
                if wait_task:
                    pinecone_wait_tasks.append(wait_task)

        # ── Retry failed spec documents once ──────────────────────────────────
        failed_docs = []
        for result in spec_results:
            if isinstance(result, Exception):
                continue
            doc, success, wait_task = result
            if not success:
                failed_docs.append(doc)

        if failed_docs:
            _pipeline_log.info(
                f"[PIPELINE] Retrying {len(failed_docs)} failed document(s) after 5s delay..."
            )
            await asyncio.sleep(5)

            retry_tasks = [
                _process_one_spec(doc, idx)
                for idx, doc in enumerate(failed_docs, total_spec + 1)
            ]
            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)

            for result in retry_results:
                if isinstance(result, Exception):
                    _pipeline_log.error(f"[PIPELINE] Retry task error: {result}")
                    continue
                doc, success, wait_task = result
                if success:
                    indexed_spec_count += 1
                    indexed_docs.append(doc)
                    if wait_task:
                        pinecone_wait_tasks.append(wait_task)
                    _pipeline_log.info(
                        f"[PIPELINE] Retry succeeded for {doc.original_filename}"
                    )
                else:
                    _pipeline_log.warning(
                        f"[PIPELINE] Retry also failed for {doc.original_filename} — skipping"
                    )

        # ── Synchronization barrier: all Pinecone tasks must complete ─────────
        all_pinecone_tasks = pinecone_wait_tasks + boq_pinecone_tasks
        if all_pinecone_tasks:
            project.pipeline_step = (
                f"Indexing {len(all_pinecone_tasks)} document(s) into vector database..."
            )
            db.commit()
            t_wait_all = _time.perf_counter()
            pinecone_results = await asyncio.gather(*all_pinecone_tasks)
            _pipeline_log.info(
                f"[TIMING][PIPELINE] All Pinecone waits completed: "
                f"{_time.perf_counter() - t_wait_all:.2f}s"
            )
            # Surface Pinecone timeout as a visible warning
            timed_out_count = sum(1 for r in pinecone_results if r is False)
            if timed_out_count > 0:
                _pipeline_log.warning(
                    f"[PIPELINE] {timed_out_count}/{len(all_pinecone_tasks)} "
                    f"doc(s) not indexed in Pinecone — extraction may be degraded"
                )
                project.error_message = (
                    f"{timed_out_count} document(s) not fully indexed in vector database. "
                    f"Results may be incomplete — try re-extracting if parameters are missing."
                )
                db.commit()

        # ── Phase 2: Extract ALL parameters ONCE after all docs are indexed ───
        total_indexed = indexed_spec_count + len(boq_docs)
        if total_indexed > 0:
            # Filter parameters by project type (commercial/residential)
            p_type = project.project_type or "commercial"
            filtered_params = [
                p for p in FACADE_PARAMETERS
                if p.get("project_type", "both") in ("both", p_type)
            ]
            project.pipeline_step = (
                f"Extracting {len(filtered_params)} parameters (full-context + vector search)..."
            )
            db.commit()
            db.expire_all()

            extractor = ParameterExtractor(
                pinecone_index=pinecone_index,
                embedding_client=embedding_client,
                db_session=db,
                session_factory=SessionLocal,
                model_key=model_key,
            )
            t_extract = _time.perf_counter()
            extractions = await extractor.extract_all_parameters_async(
                str(project_id),
                facade_parameters=filtered_params,
                max_concurrent=10,
                num_docs=total_indexed,
            )
            found_count = len([e for e in extractions if e.get("found")])
            _pipeline_log.info(
                f"[TIMING][PIPELINE] Extraction (single pass, {total_indexed} docs): "
                f"{_time.perf_counter() - t_extract:.2f}s -- "
                f"{found_count}/{len(extractions)} params found"
            )
        else:
            _pipeline_log.warning("[PIPELINE] No documents processed -- skipping extraction")

        # Mark all successfully indexed docs as completed
        for doc in indexed_docs:
            doc.processing_status = "completed"
        db.commit()

        # ── All documents done ────────────────────────────────────────────────
        project.processing_status = "completed"
        project.pipeline_step = None
        project.processing_completed_at = datetime.now()
        db.commit()
        _pipeline_log.info(
            f"[TIMING][PIPELINE] TOTAL for project {project_id}: "
            f"{_time.perf_counter() - t_pipeline_start:.2f}s"
        )
        _pipeline_log.info(f"[PIPELINE] Completed for project {project_id}")

    except Exception as e:
        traceback.print_exc()
        _pipeline_log.error(f"[PIPELINE] FAILED for project {project_id}: {e}")
        try:
            project = db.query(Project).filter(Project.project_id == project_id).first()
            if project:
                project.processing_status = "failed"
                project.pipeline_step = None
                project.error_message = str(e)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
