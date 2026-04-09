import json
import logging
import time
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.utils import get_current_user
from core.database import get_db, SessionLocal
from models.document import Document
from models.document_chunk import DocumentChunk
from models.extracted_parameter import ExtractedParameter
from models.project import Project
from models.user import User
from services.extraction import _run_doc_reprocess

logger = logging.getLogger("tenderiq.documents")
router = APIRouter(prefix="", tags=["documents"])


class DeleteDocumentsRequest(BaseModel):
    document_ids: List[uuid.UUID]


class ArchiveDocumentsRequest(BaseModel):
    document_ids: List[uuid.UUID]


async def _run_restore_reembed(project_id: uuid.UUID, doc_ids: List[uuid.UUID]):
    """Re-embed existing chunks for restored documents (skip parsing), then re-extract."""
    from core.clients import pinecone_index, embedding_client
    from services.extraction import _run_extraction
    from services.pipeline import _wait_for_pinecone_doc

    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.project_id == project_id).first()
        if project:
            project.processing_status = "processing"
            project.pipeline_step = "Re-embedding restored documents..."
            db.commit()

        for did in doc_ids:
            doc = db.query(Document).filter(Document.document_id == did).first()
            if not doc:
                continue

            doc.processing_status = "indexed"
            db.commit()

            # Get existing level-1 chunks (the ones that go in Pinecone)
            chunks = (
                db.query(DocumentChunk)
                .filter(
                    DocumentChunk.document_id == did,
                    DocumentChunk.chunk_level == 1,
                )
                .order_by(DocumentChunk.chunk_index)
                .all()
            )

            if not chunks:
                doc.processing_status = "completed"
                db.commit()
                continue

            if project:
                project.pipeline_step = f"Re-embedding: {doc.original_filename} ({len(chunks)} chunks)"
                db.commit()

            # Embed chunk texts
            texts = [c.chunk_text for c in chunks]
            EMBED_BATCH = 96
            all_embeddings = []
            for i in range(0, len(texts), EMBED_BATCH):
                batch = texts[i:i + EMBED_BATCH]
                for attempt in range(3):
                    try:
                        resp = embedding_client.embeddings.create(
                            model="text-embedding-3-small", input=batch
                        )
                        all_embeddings.extend([d.embedding for d in resp.data])
                        break
                    except Exception as e:
                        if attempt == 2:
                            raise
                        logger.warning(f"[RESTORE] Embed retry {attempt+1}: {e}")
                        time.sleep(2 ** attempt)

            # Build Pinecone vectors and update pinecone_id on chunks
            vectors = []
            for chunk, emb in zip(chunks, all_embeddings):
                vector_id = f"{did}_{chunk.chunk_index}"
                chunk.pinecone_id = vector_id
                vectors.append({
                    "id": vector_id,
                    "values": emb,
                    "metadata": {
                        "document_id": str(did),
                        "project_id": str(project_id),
                        "file_type": doc.file_type or "pdf_spec",
                        "section": chunk.section_title or "",
                        "subsection": chunk.subsection_title or "",
                        "page_start": chunk.page_number or 0,
                        "is_table": False,
                        "chunk_level": 1,
                        "text_preview": (chunk.chunk_text or "")[:200],
                    },
                })

            # Upsert to Pinecone
            BATCH = 100
            for i in range(0, len(vectors), BATCH):
                batch = vectors[i:i + BATCH]
                for attempt in range(3):
                    try:
                        pinecone_index.upsert(vectors=batch)
                        break
                    except Exception as e:
                        if attempt == 2:
                            logger.error(f"[RESTORE] Pinecone upsert failed: {e}")
                        else:
                            time.sleep(2 ** attempt)

            doc.processing_status = "completed"
            db.commit()

            # Wait for Pinecone indexing
            try:
                await _wait_for_pinecone_doc(str(project_id), str(did), timeout=60)
            except Exception:
                pass

            logger.info(f"[RESTORE] Re-embedded {len(vectors)} chunks for doc {did}")

        # Re-extract parameters with all active docs
        await _run_extraction(project_id)

    except Exception as e:
        logger.error(f"[RESTORE] Failed for project {project_id}: {e}")
        import traceback
        traceback.print_exc()
        try:
            project = db.query(Project).filter(Project.project_id == project_id).first()
            if project:
                project.processing_status = "completed"
                project.pipeline_step = None
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/projects/{project_id}/documents/{document_id}/reprocess", status_code=202)
async def reprocess_document(
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-process a single document: re-parse, re-embed, re-index, then re-extract parameters."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    doc = db.query(Document).filter(
        Document.document_id == document_id,
        Document.project_id == project_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    background_tasks.add_task(_run_doc_reprocess, project_id, document_id)
    return {
        "project_id": str(project_id),
        "document_id": str(document_id),
        "status": "reprocessing",
        "message": f"Re-processing '{doc.original_filename}' started.",
    }


@router.post("/projects/{project_id}/documents/archive")
def archive_documents(
    project_id: uuid.UUID,
    body: ArchiveDocumentsRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-delete: mark documents as archived, remove Pinecone vectors,
    delete extracted parameters sourced from these docs, and re-extract."""
    from datetime import datetime as _dt

    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if project.processing_status == "processing":
        raise HTTPException(status_code=409, detail="Cannot archive while processing")

    docs = (
        db.query(Document)
        .filter(
            Document.document_id.in_(body.document_ids),
            Document.project_id == project_id,
            Document.is_archived == False,
        )
        .all()
    )
    if not docs:
        raise HTTPException(status_code=404, detail="No matching documents found")

    doc_ids = [d.document_id for d in docs]
    doc_id_strs = [str(d) for d in doc_ids]
    now = _dt.utcnow()

    # Remove vectors from Pinecone so they don't appear in search
    pinecone_ids = [
        pid[0] for pid in
        db.query(DocumentChunk.pinecone_id)
        .filter(DocumentChunk.document_id.in_(doc_ids), DocumentChunk.pinecone_id.isnot(None))
        .all()
        if pid[0]
    ]
    if pinecone_ids:
        try:
            from core.clients import pinecone_index
            for i in range(0, len(pinecone_ids), 100):
                pinecone_index.delete(ids=pinecone_ids[i:i+100])
        except Exception as e:
            logger.warning(f"[ARCHIVE-DOCS] Pinecone cleanup failed: {e}")

    # ── 3-tier smart param handling ──
    # Instead of deleting all params + full re-extract, check if each param
    # has alternate sources in non-archived docs and re-point when possible.
    archived_id_strs = {str(d) for d in doc_ids}
    affected_params = (
        db.query(ExtractedParameter)
        .filter(
            ExtractedParameter.project_id == project_id
        )
        .all()
    )

    exclusive_param_names = []
    repointed_count = 0

    for param in affected_params:
        # Parse all_sources JSON to find alternate docs
        try:
            sources = json.loads(param.all_sources) if param.all_sources else []
        except (ValueError, TypeError):
            sources = []

        # Remove archived doc(s) from sources list
        remaining = [s for s in sources if s.get("document_id") not in archived_id_strs]

        if remaining:
            # Tier 1: Re-point to next available source (no deletion needed)
            new_primary = remaining[0]
            new_doc_id = new_primary.get("document_id")
            try:
                param.source_document_id = uuid.UUID(new_doc_id) if new_doc_id else None
            except (ValueError, AttributeError):
                param.source_document_id = None
            pages = new_primary.get("pages") or []
            param.source_page_number = pages[0] if pages else None
            param.source_pages = json.dumps(pages)
            param.source_section = (new_primary.get("sections") or [None])[0]
            param.source_subsection = None
            param.source_chunk_id = None
            param.all_sources = json.dumps(remaining)
            repointed_count += 1
        else:
            # Tier 2: Exclusive to archived doc — delete
            exclusive_param_names.append(param.parameter_name)
            db.delete(param)

    for doc in docs:
        doc.is_archived = True
        doc.archived_at = now

    # Delete associated document chunks
    db.query(DocumentChunk).filter(
        DocumentChunk.document_id.in_(doc_ids)
    ).delete(synchronize_session="fetch")

    db.commit()

    logger.info(
        f"[ARCHIVE-DOCS] {repointed_count} params re-pointed to other docs, "
        f"{len(exclusive_param_names)} exclusive params deleted"
    )

    # Tier 3: Re-extract only params exclusive to the archived doc
    active_count = db.query(Document).filter(
        Document.project_id == project_id,
        Document.is_archived == False,
        Document.processed == True,
    ).count()
    if active_count > 0 and exclusive_param_names:
        from services.extraction import _run_targeted_extraction
        background_tasks.add_task(_run_targeted_extraction, project_id, exclusive_param_names)

    logger.info(
        f"[ARCHIVE-DOCS] Archived {len(docs)} doc(s): "
        f"{repointed_count} re-pointed, {len(exclusive_param_names)} exclusive→re-extract, "
        f"{active_count} active docs remaining"
    )
    return {"archived_count": len(docs), "remaining_active": active_count}


@router.post("/projects/{project_id}/documents/restore")
def restore_documents(
    project_id: uuid.UUID,
    body: ArchiveDocumentsRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Restore archived documents: un-archive, re-embed existing chunks, re-extract."""
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    docs = (
        db.query(Document)
        .filter(
            Document.document_id.in_(body.document_ids),
            Document.project_id == project_id,
            Document.is_archived == True,
        )
        .all()
    )
    if not docs:
        raise HTTPException(status_code=404, detail="No matching archived documents found")

    doc_ids = [d.document_id for d in docs]

    for doc in docs:
        doc.is_archived = False
        doc.archived_at = None
    db.commit()

    # Re-embed existing chunks + re-extract in background
    background_tasks.add_task(_run_restore_reembed, project_id, doc_ids)

    logger.info(f"[RESTORE-DOCS] Restored {len(docs)} document(s) in project {project_id}")
    return {"restored_count": len(docs)}


@router.delete("/projects/{project_id}/documents")
def delete_documents(
    project_id: uuid.UUID,
    body: DeleteDocumentsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete selected documents from a project.

    Cleans up Pinecone vectors, extracted parameter references,
    document chunks (CASCADE), and physical files.
    """
    project = db.query(Project).filter(Project.project_id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id is not None and project.user_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if project.processing_status == "processing":
        raise HTTPException(status_code=409, detail="Cannot delete while processing")

    # Only allow permanent delete of archived documents
    docs = (
        db.query(Document)
        .filter(
            Document.document_id.in_(body.document_ids),
            Document.project_id == project_id,
            Document.is_archived == True,
        )
        .all()
    )
    if not docs:
        raise HTTPException(status_code=404, detail="No matching documents found")

    doc_ids = [d.document_id for d in docs]
    doc_id_strs = [str(d) for d in doc_ids]

    # ── Step 1: Collect and delete Pinecone vectors ─────────────────────────
    pinecone_ids = (
        db.query(DocumentChunk.pinecone_id)
        .filter(
            DocumentChunk.document_id.in_(doc_ids),
            DocumentChunk.pinecone_id.isnot(None),
        )
        .all()
    )
    pinecone_ids = [pid[0] for pid in pinecone_ids if pid[0]]
    vectors_removed = 0

    if pinecone_ids:
        try:
            from core.clients import pinecone_index
            BATCH = 100
            for i in range(0, len(pinecone_ids), BATCH):
                batch = pinecone_ids[i : i + BATCH]
                try:
                    pinecone_index.delete(ids=batch)
                    vectors_removed += len(batch)
                except Exception as e:
                    logger.warning(f"[DELETE-DOCS] Pinecone batch delete failed: {e}")
            logger.info(f"[DELETE-DOCS] Removed {vectors_removed} vectors from Pinecone")
        except Exception as e:
            logger.warning(f"[DELETE-DOCS] Pinecone cleanup failed (non-fatal): {e}")

    # ── Step 2: Delete via raw SQL in FK-safe order ─────────────────────────
    # Bypass ORM cascade to avoid SET NULL on NOT NULL columns
    from sqlalchemy import text

    # Expunge docs from session first to prevent ORM interference
    for doc in docs:
        db.expunge(doc)

    for did in doc_ids:
        did_str = str(did)
        # Delete extracted_parameters referencing chunks from this document
        db.execute(text(
            "DELETE FROM extracted_parameters WHERE source_chunk_id IN "
            "(SELECT chunk_id FROM document_chunks WHERE document_id = :did)"
        ), {"did": did_str})
        # Nullify source_chunk_id on remaining params that reference these chunks
        db.execute(text(
            "UPDATE extracted_parameters SET source_chunk_id = NULL WHERE source_chunk_id IN "
            "(SELECT chunk_id FROM document_chunks WHERE document_id = :did)"
        ), {"did": did_str})
        # Delete params by source_document_id
        db.execute(text(
            "DELETE FROM extracted_parameters WHERE source_document_id = :did"
        ), {"did": did_str})
        # Delete BOQ items referencing this document
        db.execute(text(
            "DELETE FROM boq_items WHERE document_id = :did"
        ), {"did": did_str})
        # Delete chunks
        db.execute(text(
            "DELETE FROM document_chunks WHERE document_id = :did"
        ), {"did": did_str})
        # Delete document
        db.execute(text(
            "DELETE FROM documents WHERE document_id = :did"
        ), {"did": did_str})
    db.commit()

    # ── Step 4: Delete physical files ──────────────────────────────────────
    for doc in docs:
        try:
            p = Path(doc.file_path)
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.warning(f"[DELETE-DOCS] File cleanup failed for {doc.file_path}: {e}")

    logger.info(
        f"[DELETE-DOCS] Deleted {len(docs)} document(s) from project {project_id}"
    )
    return {
        "deleted_count": len(docs),
        "vectors_removed": vectors_removed,
    }
