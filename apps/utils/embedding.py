import re

import requests
from django.conf import settings

try:
    from apps.utils.pii import redact_text
except ImportError:
    # Minimal fallback if apps/utils/pii.py is not available yet.
    EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    PHONE_RE = re.compile(
        r"(?<!\d)"
        r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?"
        r"(?:\(\d{1,4}\)[\s.\-]?)?"
        r"\d{2,4}(?:[\s.\-]?\d{2,4}){2,4}"
        r"(?!\d)"
    )


    def redact_text(text: str) -> dict:
        if not text:
            return {"text": "", "entities": []}

        text = str(text)
        text = EMAIL_RE.sub("[EMAIL]", text)
        text = PHONE_RE.sub("[PHONE_NUMBER]", text)

        return {
            "text": text,
            "entities": [],
        }


def normalize_text_for_embedding(value: str) -> str:
    """
    Normalize already-redacted text before embedding.
    """

    if not value:
        return ""

    value = re.sub(r"\s+", " ", value)
    value = value.strip()

    # Safety truncation for embedding model token limits.
    return value[:2000]


def clean_text_for_embedding(value: str) -> str:
    """
    Redact PII and normalize text before embedding.

    Use this in survey tasks and ballot tasks before calling embed_texts().
    """

    if not value:
        return ""

    result = redact_text(value)

    if isinstance(result, dict):
        text = result.get("text", "")
    else:
        text = str(result)

    return normalize_text_for_embedding(text)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed multiple texts using a local embedding model.

    Supports:
    - Ollama /api/embed
    - OpenAI-compatible /embeddings endpoint
    """

    if not texts:
        return []

    backend = getattr(settings, "EMBEDDING_BACKEND", "ollama")
    model = getattr(settings, "EMBEDDING_MODEL", "bge-m3")

    if backend == "openai":
        base_url = getattr(
            settings,
            "EMBEDDING_BASE_URL",
            getattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:8000/v1"),
        )

        api_key = getattr(settings, "EMBEDDING_API_KEY", "local")

        response = requests.post(
            f"{base_url.rstrip('/')}/embeddings",
            json={
                "model": model,
                "input": texts,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=180,
        )
        response.raise_for_status()

        data = response.json().get("data", [])
        data = sorted(data, key=lambda item: item.get("index", 0))

        return [item.get("embedding", []) for item in data]

    # Default: Ollama
    base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")

    response = requests.post(
        f"{base_url}/api/embed",
        json={
            "model": model,
            "input": texts,
        },
        timeout=180,
    )

    # Fallback for older Ollama versions.
    if response.status_code == 404:
        return [_embed_ollama_legacy(text, model, base_url) for text in texts]

    response.raise_for_status()

    payload = response.json()

    if "embeddings" in payload:
        return payload["embeddings"]

    if "embedding" in payload:
        return [payload["embedding"]]

    raise RuntimeError("Unexpected response from Ollama embedding endpoint.")


def _embed_ollama_legacy(text: str, model: str, base_url: str) -> list[float]:
    response = requests.post(
        f"{base_url}/api/embeddings",
        json={
            "model": model,
            "prompt": text,
        },
        timeout=180,
    )
    response.raise_for_status()

    return response.json().get("embedding", [])
