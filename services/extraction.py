import asyncio
import traceback as _tb
import uuid

from config.parameters import FACADE_PARAMETERS
from core.clients import pinecone_index, embedding_client
from core.database import SessionLocal
from core.logging import _timing_project_id, _project_timings, _pipeline_log
from extraction.parameter_extractor import ParameterExtractor, _get_llm_semaphore
from models.document import Document
from models.document_chunk import DocumentChunk
from models.project import Project
from processing.document_processor import DocumentProcessor
from services.pipeline import _wait_for_pinecone_doc


async def _run_extraction(pid: uuid.UUID, model_key: str = None):
    _timing_project_id.set(str(pid))
    _project_timings[str(pid)] = []   # reset on re-extract
    _pipeline_log.info(f"[RE-EXTRACT] Starting for project {pid} (model={model_key or 'default'})")
    _db = SessionLocal()
    try:
        # Mark as processing so the frontend polling detects it
        _proj = _db.query(Project).filter(Project.project_id == pid).first()
        if _proj:
            _proj.processing_status = "processing"
            _proj.pipeline_step = "Re-extracting parameters from all documents..."
            _proj.error_message = None  # clear previous errors
            _db.commit()

        # Count already-processed docs so top_k / max_sources scale correctly
        doc_count = _db.query(Document).filter(
            Document.project_id == pid,
            Document.processed == True,
        ).count()

        # Filter parameters by project type
        p_type = _proj.project_type if _proj else "commercial"
        filtered_params = [
            p for p in FACADE_PARAMETERS
            if p.get("project_type", "both") in ("both", p_type)
        ]
        _pipeline_log.info(f"[RE-EXTRACT] {len(filtered_params)} params for {p_type} project")

        extractor = ParameterExtractor(
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            db_session=_db,
            session_factory=SessionLocal,
            model_key=model_key,
        )
        extractions = await extractor.extract_all_parameters_async(
            str(pid),
            facade_parameters=filtered_params,
            max_concurrent=10,
            num_docs=max(1, doc_count),
        )
        found_count = len([e for e in extractions if e.get("found")])
        _pipeline_log.info(f"[RE-EXTRACT] Done -- {found_count}/{len(extractions)} parameters found")

        # Mark complete so polling stops
        _proj = _db.query(Project).filter(Project.project_id == pid).first()
        if _proj:
            _proj.processing_status = "completed"
            _proj.pipeline_step = None
            _db.commit()
    except Exception as e:
        _tb.print_exc()
        _pipeline_log.error(f"[RE-EXTRACT] FAILED for project {pid}: {e}")
        try:
            _proj = _db.query(Project).filter(Project.project_id == pid).first()
            if _proj:
                _proj.processing_status = "failed"
                _proj.pipeline_step = None
                _db.commit()
        except Exception:
            pass
    finally:
        _db.close()


async def _run_single_extraction(pid: uuid.UUID, p_config: dict):
    _timing_project_id.set(str(pid))
    _db = SessionLocal()
    try:
        spec_count = _db.query(Document).filter(
            Document.project_id == pid,
            Document.file_type.in_(['pdf_spec', 'docx_spec', 'pdf_drawing']),
            Document.processed == True,
        ).count()

        extractor = ParameterExtractor(
            pinecone_index=pinecone_index,
            embedding_client=embedding_client,
            db_session=_db,
            session_factory=SessionLocal,
        )
        loop = asyncio.get_running_loop()
        import time as _t
        t0 = _t.perf_counter()
        # Use focused single-param search
        focused_query = f"{p_config['display_name']} {' '.join(p_config['search_keywords'][:6])}"
        param_types = p_config.get('source_types') or None
        top_k = min(60, max(10, 4 * max(1, spec_count)))
        chunk_dicts = await extractor._search_pinecone_async(
            loop, focused_query, str(pid), top_k=top_k, file_types=param_types
        )
        if chunk_dicts:
            context = extractor._build_context(chunk_dicts, max_sources=min(20, len(chunk_dicts)))
            async with _get_llm_semaphore():
                result_text = await asyncio.wait_for(
                    loop.run_in_executor(None, extractor._call_llm, p_config, context),
                    timeout=90.0,
                )
            result = extractor._parse_llm_response(result_text, p_config, chunk_dicts)
        else:
            result = {'found': False, 'explanation': 'No relevant content found in indexed documents.'}

        extractor._store_extraction(str(pid), p_config, result)
        _pipeline_log.info(
            f"[SINGLE-EXTRACT] '{p_config['name']}' -> "
            f"{'found' if result.get('found') else 'not found'} "
            f"({_t.perf_counter()-t0:.2f}s)"
        )
    except Exception as e:
        _pipeline_log.error(f"[SINGLE-EXTRACT] Failed '{p_config['name']}': {e}")
    finally:
        _db.close()


async def _run_doc_reprocess(pid: uuid.UUID, did: uuid.UUID):
    _timing_project_id.set(str(pid))
    _pipeline_log.info(f"[REPROCESS-DOC] Starting for document {did}")
    _db = SessionLocal()
    try:
        _proj = _db.query(Project).filter(Project.project_id == pid).first()
        _doc = _db.query(Document).filter(Document.document_id == did).first()
        if not _doc:
            return

        _doc.processing_status = "processing"
        _doc.processing_error = None
        if _proj:
            _proj.processing_status = "processing"
            _proj.pipeline_step = f"Re-processing: {_doc.original_filename}"
        _db.commit()

        # Delete old chunks for this document from DB and Pinecone
        old_chunks = _db.query(DocumentChunk).filter(
            DocumentChunk.document_id == did
        ).all()
        old_pinecone_ids = [c.pinecone_id for c in old_chunks if c.pinecone_id]
        if old_pinecone_ids:
            BATCH = 100
            for i in range(0, len(old_pinecone_ids), BATCH):
                try:
                    pinecone_index.delete(ids=old_pinecone_ids[i:i+BATCH])
                except Exception:
                    pass
        for c in old_chunks:
            _db.delete(c)
        _db.commit()

        # Re-process based on file type
        if _doc.file_type == 'excel_boq':
            processor = DocumentProcessor(
                project_id=pid, db_session=_db,
                pinecone_index=pinecone_index, embedding_client=embedding_client,
            )
            processor._process_boq_document(_doc)
        else:
            doc_processor = DocumentProcessor(
                project_id=pid, db_session=_db,
                pinecone_index=pinecone_index, embedding_client=embedding_client,
            )
            doc_processor._process_specification_document(_doc)

        _doc.processing_status = "completed"
        _doc.processed = True
        _db.commit()

        # Wait for Pinecone indexing
        await _wait_for_pinecone_doc(str(pid), str(did), timeout=120)

        # Re-extract parameters
        p_type = _proj.project_type if _proj else "commercial"
        filtered_params = [
            p for p in FACADE_PARAMETERS
            if p.get("project_type", "both") in ("both", p_type)
        ]
        if _proj:
            _proj.pipeline_step = f"Re-extracting {len(filtered_params)} parameters..."
            _db.commit()

        doc_count = _db.query(Document).filter(
            Document.project_id == pid, Document.processed == True,
        ).count()
        extractor = ParameterExtractor(
            pinecone_index=pinecone_index, embedding_client=embedding_client,
            db_session=_db, session_factory=SessionLocal,
        )
        await extractor.extract_all_parameters_async(
            str(pid), facade_parameters=filtered_params,
            max_concurrent=10, num_docs=max(1, doc_count),
        )

        if _proj:
            _proj.processing_status = "completed"
            _proj.pipeline_step = None
            _db.commit()
        _pipeline_log.info(f"[REPROCESS-DOC] Completed for document {did}")

    except Exception as e:
        _tb.print_exc()
        _pipeline_log.error(f"[REPROCESS-DOC] FAILED for document {did}: {e}")
        try:
            _doc = _db.query(Document).filter(Document.document_id == did).first()
            if _doc:
                _doc.processing_status = "failed"
                _doc.processing_error = str(e)[:4000]
            _proj = _db.query(Project).filter(Project.project_id == pid).first()
            if _proj:
                _proj.processing_status = "completed"
                _proj.pipeline_step = None
            _db.commit()
        except Exception:
            pass
    finally:
        _db.close()
