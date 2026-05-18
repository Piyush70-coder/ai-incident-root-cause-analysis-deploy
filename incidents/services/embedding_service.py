from incidents.models import IncidentEmbedding

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _model


class _LazySentenceTransformer:
    """Delegates to the real model on first use (backward-compatible `model` import)."""

    def __getattr__(self, name):
        return getattr(_get_model(), name)


model = _LazySentenceTransformer()


def get_embedding(text: str):
    """
    Text → vector (numbers)
    DB friendly list return karta hai
    """
    return _get_model().encode(text).tolist()


def save_incident_embedding(incident, text: str):
    """
    Incident + text → embedding generate → DB me save
    """
    vector = get_embedding(text)

    IncidentEmbedding.objects.update_or_create(
        incident=incident,
        defaults={"vector": vector}
    )

    return vector
