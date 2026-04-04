import logging
import uuid

import anthropic
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from config.models import get_model_config, DEFAULT_MODEL
from core.clients import pinecone_index, embedding_client, anthropic_client, gemini_client, openai_client
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
- Use the conversation history to understand follow-up questions in context
- The user may have typos or misspellings in their question — interpret what they meant and answer accordingly
- Do NOT point out the user's spelling mistakes — just answer the intended question"""

SPELL_CORRECT_PROMPT = """Fix any spelling or grammar mistakes in this user query about tender/construction documents. Also expand abbreviations into full technical terms where relevant (e.g. "u-val" -> "U-value thermal transmittance", "wp" -> "waterproofing").

Rules:
- Return ONLY the corrected query text, nothing else
- If the query is already correct, return it unchanged
- Keep the meaning identical — only fix spelling and expand abbreviations
- Keep it concise — do not add extra words beyond corrections
- This is about construction/facade/tender documents — use domain knowledge for corrections

Query: {query}"""


def _correct_query(raw_query: str) -> str:
    """Use a fast LLM to correct spelling and expand abbreviations before embedding.
    Falls back to original query on any failure — never blocks the pipeline."""
    try:
        prompt = SPELL_CORRECT_PROMPT.format(query=raw_query)
        # Try Anthropic Haiku first (fastest)
        if anthropic_client is not None:
            resp = anthropic_client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=256,
                temperature=0.0,
                timeout=5.0,
                messages=[{"role": "user", "content": prompt}],
            )
            corrected = resp.content[0].text.strip()
        elif openai_client is not None:
            resp = openai_client.chat.completions.create(
                model="gpt-5.4-nano-2026-03-17",
                max_tokens=256,
                temperature=0.0,
                timeout=5.0,
                messages=[{"role": "user", "content": prompt}],
            )
            corrected = resp.choices[0].message.content.strip()
        elif gemini_client is not None:
            from google import genai
            resp = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=256,
                ),
            )
            corrected = resp.text.strip()
        else:
            return raw_query

        # Sanity check: if correction is wildly different length, likely hallucinated
        if corrected and len(corrected) < len(raw_query) * 5:
            if corrected.lower() != raw_query.lower():
                logger.info(f"[QUERY] Spell-corrected: '{raw_query}' -> '{corrected}'")
            return corrected
        return raw_query
    except Exception as e:
        logger.warning(f"[QUERY] Spell-correction failed (using original): {e}")
        return raw_query


def process_query(project_id: uuid.UUID, query: str, db: Session, model_key: str = None) -> dict:
    """Execute an ad-hoc query against project documents and return answer + sources."""

    # ── Spell-correct / expand query before embedding ───────────────────────
    corrected_query = _correct_query(query)

    # ── Embed query (with error handling) ────────────────────────────────────
    if embedding_client is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service not initialized. Check server logs.",
        )

    # Embed BOTH original and corrected query for better recall
    try:
        queries_to_embed = [corrected_query]
        if corrected_query.lower() != query.lower():
            queries_to_embed.append(query)
        embeddings = embedding_client.embed(queries_to_embed)
        query_embedding = embeddings[0]  # Use corrected query as primary
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
        # Primary search with corrected query
        results = pinecone_index.query(
            vector=query_embedding,
            top_k=5,
            filter={"project_id": str(project_id)},
            include_metadata=True,
        )
        all_matches = {m["id"]: m for m in results["matches"]}

        # If query was corrected, also search with original to catch edge cases
        if len(embeddings) > 1:
            results2 = pinecone_index.query(
                vector=embeddings[1],
                top_k=3,
                filter={"project_id": str(project_id)},
                include_metadata=True,
            )
            for m in results2["matches"]:
                if m["id"] not in all_matches:
                    all_matches[m["id"]] = m

    except Exception as e:
        logger.error(f"[QUERY] Pinecone search failed: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Vector search failed: {e}",
        )

    chunk_ids = list(all_matches.keys())
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
        for i, chunk in enumerate(context_chunks[:7])
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

    # Show both original and corrected query to the answering LLM
    question_text = corrected_query
    if corrected_query.lower() != query.lower():
        question_text = f"{corrected_query}\n(Original user query with possible typos: {query})"

    user_content = f"{chat_history}Question: {question_text}\n\nDocument Context:\n{context}"

    if provider == "google":
        if gemini_client is None:
            raise HTTPException(status_code=503, detail="Google AI not initialized.")
        try:
            from google import genai
            resp = gemini_client.models.generate_content(
                model=model_id,
                contents=user_content,
                config=genai.types.GenerateContentConfig(
                    temperature=0.3,
                    max_output_tokens=2048,
                    system_instruction=QUERY_SYSTEM_PROMPT,
                ),
            )
            answer = resp.text
        except Exception as e:
            logger.error(f"[QUERY] Gemini error: {e}")
            raise HTTPException(status_code=502, detail=f"AI service error: {e}")
    elif provider == "openai":
        if openai_client is None:
            raise HTTPException(status_code=503, detail="OpenAI not initialized. Check OPENAI_API_KEY.")
        try:
            response = openai_client.chat.completions.create(
                model=model_id,
                max_tokens=2048,
                temperature=0.3,
                timeout=60.0,
                messages=[
                    {"role": "system", "content": QUERY_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            answer = response.choices[0].message.content
        except Exception as e:
            logger.error(f"[QUERY] OpenAI error: {e}")
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
        for chunk in context_chunks[:7]
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
