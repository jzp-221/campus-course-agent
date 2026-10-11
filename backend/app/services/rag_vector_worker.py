"""One isolated Chroma operation. Parent holds the directory lock until exit.

No API keys are passed here. Avoid process-local PersistentClient caches across
FastAPI/MCP: every operation opens the latest durable index in a fresh process.
"""

import json
import sys

import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError

COLLECTION_NAME = "course_docs_v1"


def execute(payload):
    client = chromadb.PersistentClient(
        path=payload["path"], settings=Settings(anonymized_telemetry=False)
    )
    operation = payload["operation"]
    try:
        collection = client.get_collection(COLLECTION_NAME, embedding_function=None)
    except NotFoundError:
        if operation != "index":
            return {"items": []} if operation == "query" else {"count": 0}
        collection = client.create_collection(
            COLLECTION_NAME,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"embedding_identity": payload["embedding_identity"]},
        )
    if operation in ("index", "query"):
        if (collection.metadata or {}).get("embedding_identity") != payload["embedding_identity"]:
            raise ValueError("embedding configuration does not match persisted collection")
    document_id = payload.get("document_id")
    if operation == "delete":
        collection.delete(where={"document_id": document_id})
        return {"count": 0}
    if operation == "count":
        if document_id is None:
            return {"count": collection.count()}
        return {"count": len(collection.get(where={"document_id": document_id}, include=[]) ["ids"])}
    if operation == "index":
        collection.delete(where={"document_id": document_id})
        chunks, embeddings = payload["chunks"], payload["embeddings"]
        # Respect the SDK's maximum upsert batch size for large documents.
        batch_size = min(client.get_max_batch_size(), 1000)
        for start in range(0, len(chunks), batch_size):
            indexes = range(start, min(start + batch_size, len(chunks)))
            collection.upsert(
                ids=[f"document:{document_id}:chunk:{index}" for index in indexes],
                documents=chunks[start:start + batch_size],
                embeddings=embeddings[start:start + batch_size],
                metadatas=[{"document_id": document_id, "source": payload["filename"], "chunk_index": index} for index in indexes],
            )
        return {"count": len(chunks)}
    if operation == "query":
        if not collection.count() or not payload["document_ids"]:
            return {"items": []}
        candidate_count = payload.get("candidate_count", 8)
        if type(candidate_count) is not int or not 6 <= candidate_count <= 8:
            raise ValueError("invalid candidate count")
        result = collection.query(
            query_embeddings=payload["embeddings"],
            # Only committed indexed documents are searchable, including during
            # another request's upload/reindex/rollback.
            where={"document_id": {"$in": payload["document_ids"]}},
            n_results=min(candidate_count, collection.count()),
            include=["documents", "metadatas", "distances"],
        )
        return {"items": [
            {"text": text, "distance": distance, **(metadata or {})}
            for text, metadata, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True)
        ]}
    raise ValueError("unknown operation")


def main():
    try:
        result = {"ok": True, "result": execute(json.load(sys.stdin))}
    except Exception:
        # Private text and exception details never escape this worker.
        result = {"ok": False}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
