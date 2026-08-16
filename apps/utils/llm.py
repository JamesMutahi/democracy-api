import json
import re

import requests
from django.conf import settings

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def extract_json(text: str):
    """
    Parse JSON from an LLM response.
    Handles cases where the model wraps JSON in markdown.
    """
    text = (text or "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}


def chat_json(messages, temperature: float = 0.1, timeout: int = 900):
    """
    Call a local Qwen model and return parsed JSON.

    Supports:
      - Ollama
      - OpenAI-compatible server such as vLLM
    """
    backend = getattr(settings, "LOCAL_LLM_BACKEND", "ollama")
    model = getattr(settings, "LOCAL_QWEN_MODEL", "qwen2.5:7b-instruct")

    if backend == "openai":
        if OpenAI is None:
            raise RuntimeError("The openai package is required for LOCAL_LLM_BACKEND=openai")

        client = OpenAI(
            base_url=getattr(settings, "LOCAL_LLM_BASE_URL", "http://localhost:8000/v1"),
            api_key="local",
        )

        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )

        content = completion.choices[0].message.content
        return extract_json(content)

    # Default: Ollama
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
        },
    }

    response = requests.post(
        f"{getattr(settings, 'OLLAMA_BASE_URL', 'http://localhost:11434')}/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    content = response.json()["message"]["content"]
    return extract_json(content)
