import numpy as np
from django.conf import settings
from django.db.models import Count, Max

from incidents.models import IncidentEmbedding
from incidents.services.embedding_service import get_embedding, model

_DB_INDEX_CACHE = {
    "count": None,
    "latest_created_at": None,
    "index": None,
    "incidents": [],
}


def _load_faiss():
    try:
        import faiss

        return faiss
    except Exception:
        return None


def normalize_vectors(vectors):
    faiss = _load_faiss()
    if faiss is None:
        return vectors
    faiss.normalize_L2(vectors)
    return vectors


def _build_cached_incident_index():
    faiss = _load_faiss()
    if faiss is None:
        return None, []

    stats = IncidentEmbedding.objects.aggregate(
        count=Count("id"),
        latest_created_at=Max("created_at"),
    )
    if not stats["count"]:
        _DB_INDEX_CACHE.update(
            {
                "count": 0,
                "latest_created_at": None,
                "index": None,
                "incidents": [],
            }
        )
        return None, []

    if (
        _DB_INDEX_CACHE["index"] is not None
        and _DB_INDEX_CACHE["count"] == stats["count"]
        and _DB_INDEX_CACHE["latest_created_at"] == stats["latest_created_at"]
    ):
        return _DB_INDEX_CACHE["index"], _DB_INDEX_CACHE["incidents"]

    embeddings = list(IncidentEmbedding.objects.select_related("incident"))
    db_vectors_np = np.array([item.vector for item in embeddings], dtype="float32")
    normalize_vectors(db_vectors_np)

    dimension = db_vectors_np.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(db_vectors_np)

    _DB_INDEX_CACHE.update(
        {
            "count": stats["count"],
            "latest_created_at": stats["latest_created_at"],
            "index": index,
            "incidents": [item.incident for item in embeddings],
        }
    )
    return index, _DB_INDEX_CACHE["incidents"]


def find_similar_incidents_db(target_text, top_k=3):
    if not getattr(settings, "ENABLE_SEMANTIC_RETRIEVAL", True):
        return []

    try:
        target_vector = np.array([get_embedding(target_text)], dtype="float32")
    except Exception:
        return []

    normalize_vectors(target_vector)
    index, incidents = _build_cached_incident_index()
    if index is None or not incidents:
        return []

    distances, indices = index.search(target_vector, top_k)
    results = []
    for i in range(top_k):
        idx = indices[0][i]
        score = distances[0][i]
        if idx != -1 and idx < len(incidents):
            results.append((incidents[idx], float(score)))

    return results


def filter_relevant_logs(query_text, log_lines, top_k=10):
    if not getattr(settings, "ENABLE_SEMANTIC_RETRIEVAL", True):
        return log_lines[:top_k]

    if not log_lines:
        return []
    if len(log_lines) <= top_k:
        return log_lines

    faiss = _load_faiss()
    if faiss is None:
        return log_lines[:top_k]

    try:
        line_vectors = model.encode(log_lines, show_progress_bar=False)
        line_vectors = np.array(line_vectors, dtype="float32")
        normalize_vectors(line_vectors)
    except Exception:
        return log_lines[:top_k]

    try:
        query_vector = np.array([get_embedding(query_text)], dtype="float32")
    except Exception:
        return log_lines[:top_k]

    normalize_vectors(query_vector)
    dimension = line_vectors.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(line_vectors)
    _, indices = index.search(query_vector, top_k)

    relevant_lines = []
    for idx in sorted(indices[0]):
        if idx != -1 and idx < len(log_lines):
            relevant_lines.append(log_lines[idx])

    return relevant_lines
