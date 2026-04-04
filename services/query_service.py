import logging
import uuid

import anthropic
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from config.models import get_model_config, DEFAULT_MODEL
from core.clients import pinecone_index, embedding_client, anthropic_client, gemini_client
from models.document_chunk import DocumentChunk
from models.query_log import QueryLog

logger = logging.getLogger("tenderiq.query")

QUERY_SYSTEM_PROMPT = """You are TenderIQ, an expert AI tender analyst. You answer questions about tender documents, BOQs, technical specs, and project requirements.

Answer like a knowledgeable colleague in a chat -- direct, clear, and conversational.

Format every answer like this:
1. **Bold title** that states what was asked
2. A direct 1-2 line answer with the core value/fact
3. **Key Details:** as bullet points with specific values, units, standards, classifications
4. Do NOT add recommendations, suggestions, or opinions -- only facts from the documents

Rules:
- Use **bold** for important values and labels
- Use bullet points (- ) for lists
- Include specific numbers, units, measurements, standards from the documents
- Do NOT mention source documents or page numbers in your answer -- they are shown separately
- If info is not found, say clearly what is missing
- Keep it concise -- no filler, no repetition
- For cost/BOQ questions, show itemized breakdowns with quantities and rates when available
- Use the conversation history to understand follow-up questions in context"""


def process_query(project_id: uuid.UUID, query: str, db: Session, model_key: str = None) -> dict:
    """Execute an ad-hoc query against project documents and return answer + sources."""

    # ── Embed query (with error handling) ────────────────────────────────────
    if embedding_client is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service not initialized. Check server logs.",
        )
    try:
        query_embedding = embedding_client.embed([query])[0]
    except Exception as e:
        logger.error(f"[QUERY] Embedding failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Embedding service unavailable: {e}",
        )

    # ── Vector search (with error handling) ──────────────────────────────────
    if pinecone_index is None:
        raise HTTPException(
            status_code=503,
            detail="Vector database not initialized. Check server logs.",
        )
    try:
        results = pinecone_index.query(
            vector=query_embedding,
            top_k=5,
            filter={"project_id": str(project_id)},
            include_metadata=True,
        )
    except Exception as e:
        logger.error(f"[QUERY] Pinecone search failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Vector search failed: {e}",
        )

    chunk_ids = [match["id"] for match in results["matches"]]
    child_chunks = db.query(DocumentChunk).options(
        joinedload(DocumentChunk.document)
    ).filter(
        DocumentChunk.pinecone_id.in_(chunk_ids)
    ).all()

    # Hierarchical context expansion: prefer full section (parent) over fragment (child)
    parent_ids = [c.parent_chunk_id for c in child_chunks if c.parent_chunk_id]
    parent_map = {}
    if parent_ids:
        parent_rows = db.query(DocumentChunk).options(
            joinedload(DocumentChunk.document)
        ).filter(DocumentChunk.chunk_id.in_(parent_ids)).all()
        parent_map = {p.chunk_id: p for p in parent_rows}

    seen_parents: set = set()
    context_chunks = []
    for child in child_chunks:
        if child.parent_chunk_id and child.parent_chunk_id in parent_map:
            if child.parent_chunk_id not in seen_parents:
                seen_parents.add(child.parent_chunk_id)
                context_chunks.append(parent_map[child.parent_chunk_id])
        else:
            context_chunks.append(child)

    context = "\n\n".join([
        f"[Source {i+1}: {chunk.document.original_filename}, Page {chunk.page_number or 'N/A'}, "
        f"Section: {chunk.section_title or 'N/A'}]\n{chunk.chunk_text}"
        for i, chunk in enumerate(context_chunks[:5])
    ])

    # Load recent chat history for conversational context
    recent_logs = (
        db.query(QueryLog)
        .filter(QueryLog.project_id == project_id)
        .order_by(QueryLog.created_at.desc())
        .limit(6)
        .all()
    )
    recent_logs.reverse()
    chat_history = ""
    if recent_logs:
        chat_history = "\n\nPrevious conversation:\n" + "\n".join(
            f"User: {log.query_text}\nAssistant: {log.response_text}"
            for log in recent_logs if log.response_text
        ) + "\n\n"

    # ── LLM call (with timeout and error handling) ───────────────────────────
    model_cfg = get_model_config(model_key or DEFAULT_MODEL)
    provider = model_cfg["provider"]
    model_id = model_cfg["model_id"]
    user_content = f"{chat_history}Question: {query}\n\nDocument Context:\n{context}"

    if provider == "google":
        if gemini_client is None:
            raise HTTPException(status_code=503, detail="Google AI not initialized.")
        try:
            from google import genai
            full_prompt = f"{QUERY_SYSTEM_PROMPT}\n\n{user_content}"
            resp = gemini_client.models.generate_content(
                model=model_id,
                contents=full_prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=2048,
                ),
            )
            answer = resp.text
        except Exception as e:
            logger.error(f"[QUERY] Gemini error: {e}")
            raise HTTPException(status_code=502, detail=f"AI service error: {e}")
    else:
        if anthropic_client is None:
            raise HTTPException(status_code=503, detail="AI service not initialized.")
        try:
            response = anthropic_client.messages.create(
                model=model_id,
                max_tokens=2048,
                temperature=0.3,
                timeout=60.0,
                system=QUERY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            answer = response.content[0].text
        except anthropic.APITimeoutError:
            logger.warning(f"[QUERY] LLM timed out for project {project_id}")
            raise HTTPException(status_code=504, detail="AI response timed out. Try a shorter question.")
        except anthropic.RateLimitError:
            logger.warning(f"[QUERY] LLM rate limited for project {project_id}")
            raise HTTPException(status_code=429, detail="AI service rate limited. Wait a moment and retry.")
        except anthropic.APIError as e:
            logger.error(f"[QUERY] LLM API error: {e}")
            raise HTTPException(status_code=502, detail=f"AI service error: {e}")

    sources = [
        {
            "document": chunk.document.original_filename,
            "page": chunk.page_number,
            "section": chunk.section_title,
            "subsection": chunk.subsection_title,
        }
        for chunk in context_chunks[:5]
    ]

    query_log = QueryLog(
        project_id=project_id,
        query_text=query,
        query_type="adhoc",
        response_text=answer,
        sources_json=sources,
        num_sources_used=len(context_chunks),
    )
    db.add(query_log)
    db.commit()

    return {
        "query": query,
        "answer": answer,
        "sources": sources,
    }
