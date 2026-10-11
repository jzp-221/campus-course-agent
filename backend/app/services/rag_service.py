"""Shared FastAPI ingestion / MCP retrieval, with honest failure semantics."""

import math
import re

from app.common.exceptions import BusinessException
from app.config import settings
from app.database import SessionLocal
from app.models.document import Document
from app.services import embedding_service, rag_vector_service

# Calibrated on the current embedding model and four course documents:
# 0.40 > worst answer-block distance 0.3883, and 0.40 < nearest unrelated distance 0.4162.
# Recalibrate whenever the embedding model or document collection changes.
MAX_COSINE_DISTANCE = 0.40


def _embedding_text(chunk: str, filename: str) -> str:
    """Embed chapter content; keep provenance in the stored/returned snippet.

    A filename is a citation label, not course content. Repeating it (and an
    already present chapter title) dilutes short sections. Continuation windows
    still receive their chapter title so their semantic context is preserved.
    """
    prefix, newline, body = chunk.partition("\n")
    marker = f"{filename} · "
    if not newline or not prefix.startswith(marker):
        return chunk
    title = prefix[len(marker):]
    if title == "正文" or re.match(r"#{2,3}\s", body):
        return body
    return f"## {title}\n{body}"


def index_document(document_id: int, filename: str, chunks: list[str]) -> None:
    if not filename or not chunks:
        raise BusinessException("检索失败：文档没有可入库的片段或来源", code=400)
    embeddings = embedding_service.embed_texts([_embedding_text(chunk, filename) for chunk in chunks])
    rag_vector_service.vector_operation(
        "index", document_id=document_id, filename=filename, chunks=chunks,
        embeddings=embeddings, embedding_identity=rag_vector_service.embedding_identity(),
    )


def delete_document(document_id: int) -> None:
    # Removing stale vectors must work even if the embedding key is missing.
    rag_vector_service.vector_operation("delete", document_id=document_id)


def vector_document_count(document_id: int) -> int:
    return rag_vector_service.vector_operation("count", document_id=document_id)["count"]


def vector_count() -> int:
    return rag_vector_service.vector_operation("count")["count"]


def _indexed_documents() -> dict[int, str]:
    try:
        with SessionLocal() as db:
            return dict(db.query(Document.id, Document.filename).filter(Document.status == "indexed").all())
    except Exception as error:
        raise BusinessException("检索失败：无法读取知识库文档状态", code=502) from error


def search_course_docs(query: str) -> dict:
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise BusinessException("检索失败：query 必须是 1 到 2000 字的文本", code=400)
    # Key/provider failures take precedence over normal no-hits, even on an
    # empty collection. Never return an empty result from an exception handler.
    embeddings = embedding_service.embed_texts([query.strip()])
    documents = _indexed_documents()
    result = rag_vector_service.vector_operation(
        "query", embeddings=embeddings, document_ids=list(documents),
        candidate_count=settings.RAG_CANDIDATE_COUNT,
        embedding_identity=rag_vector_service.embedding_identity(),
    )
    # Reindex/delete may have withdrawn an ID after the first snapshot but
    # before/during vector I/O. Do not expose a processing/failed document.
    current_documents = _indexed_documents()
    items = []
    try:
        for hit in result["items"]:
            distance = hit["distance"]
            if (type(distance) not in (int, float) or not math.isfinite(distance)
                    or not -1e-6 <= distance <= 2.000001):
                raise ValueError("invalid distance")
            document_id = hit["document_id"]
            if type(document_id) is not int or documents.get(document_id) != hit["source"]:
                raise ValueError("invalid source")
            if current_documents.get(document_id) != hit["source"]:
                continue
            if not isinstance(hit["text"], str) or not hit["text"].strip():
                raise ValueError("empty snippet")
            if type(hit["chunk_index"]) is not int or hit["chunk_index"] < 0:
                raise ValueError("invalid chunk")
            if distance <= MAX_COSINE_DISTANCE:
                # Cosine's theoretical minimum is zero; native float rounding
                # can produce tiny negative values for identical vectors.
                items.append({**hit, "distance": max(0.0, distance)})
    except (ValueError, KeyError, TypeError) as error:
        raise BusinessException("检索失败：向量库返回了无效片段或来源", code=502) from error
    return {"items": items[:3], "count": len(items[:3])}
