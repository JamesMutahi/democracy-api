import json
import random
import re
from itertools import islice

from django.conf import settings

from apps.ballot.models import Ballot, Reason
from apps.utils.llm import chat_json

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]?\d{3}[\s.-]?\d{3,4}(?!\d)"
)

MAP_SYSTEM = """
You are analyzing anonymous voter comments for a public ballot.
Your job is to identify the main reasons voters gave.

Return ONLY valid JSON, no markdown, with this shape:
{
  "summary": "2-4 sentence synthesis of this batch",
  "themes": [
    {
      "name": "short theme name",
      "mentions": 12,
      "sentiment": "positive|negative|mixed|neutral",
      "examples": ["short anonymous quote"]
    }
  ]
}

Rules:
- Use only the provided comments.
- Do not include names, emails, phone numbers, or personal data.
- Keep examples short and anonymous.
- Maximum 8 themes.
- If unsure, use neutral sentiment.
""".strip()

REDUCE_SYSTEM = """
You are merging partial analyses of voter comments into one final analysis.

Return ONLY valid JSON, no markdown, with this shape:
{
  "summary": "final 3-6 sentence summary",
  "themes": [
    {
      "name": "short theme name",
      "mentions": 120,
      "sentiment": "positive|negative|mixed|neutral",
      "examples": ["short anonymous quote"]
    }
  ]
}

Rules:
- Combine similar themes.
- Preserve important disagreements and notable minority views.
- Do not invent new facts.
- Keep examples short and anonymous.
- Maximum 10 themes.
""".strip()


def batched(iterable, n):
    it = iter(iterable)
    while True:
        batch = list(islice(it, n))
        if not batch:
            return
        yield batch


def normalize_text(text: str) -> str:
    """
    Basic cleaning and PII redaction.
    """
    if not text:
        return ""

    text = EMAIL_RE.sub("[email]", text)
    text = PHONE_RE.sub("[phone]", text)
    text = " ".join(text.split())
    return text[:400]


def iter_clean_reasons(ballot_id: int):
    """
    Stream reasons from the database without loading all rows into memory.
    """
    queryset = (
        Reason.objects.filter(ballot_id=ballot_id)
        .order_by("id")
        .values_list("text", flat=True)
    )

    for text in queryset.iterator(chunk_size=10000):
        cleaned = normalize_text(text)
        if cleaned:
            yield cleaned


def reservoir_sample(ballot_id: int, k: int):
    """
    Uniform random sample of size k using reservoir sampling.

    This scans the matching rows but avoids loading everything into memory
    and avoids expensive ORDER BY RANDOM() queries.
    """
    reservoir = []
    n = 0

    for text in iter_clean_reasons(ballot_id):
        if n < k:
            reservoir.append(text)
        else:
            j = random.randint(0, n)
            if j < k:
                reservoir[j] = text
        n += 1

    return reservoir


def normalize_llm_summary(data):
    if not isinstance(data, dict):
        data = {}

    themes = []
    for item in data.get("themes", [])[:15]:
        if not isinstance(item, dict):
            continue

        try:
            mentions = int(item.get("mentions", 0))
        except Exception:
            mentions = 0

        examples = []
        for example in item.get("examples", [])[:2]:
            example_text = str(example or "").strip()[:200]
            if example_text:
                examples.append(example_text)

        themes.append(
            {
                "name": str(item.get("name", "Unnamed"))[:120],
                "mentions": mentions,
                "sentiment": str(item.get("sentiment", "neutral"))[:20],
                "examples": examples,
            }
        )

    return {
        "summary": str(data.get("summary", "")).strip()[:5000],
        "themes": themes,
    }


def summarize_reason_chunk(texts):
    """
    Map step: summarize one small chunk of reasons.
    """
    lines = "\n".join(f"{idx}. {text}" for idx, text in enumerate(texts, 1))

    user_message = f"""
Voter comments:
{lines}

Return ONLY valid JSON.
""".strip()

    data = chat_json(
        [
            {"role": "system", "content": MAP_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
    )

    return normalize_llm_summary(data)


def compact_partial(partial):
    """
    Reduce token usage before merging partial summaries.
    """
    return {
        "summary": partial.get("summary", ""),
        "themes": [
            {
                "name": theme.get("name"),
                "mentions": theme.get("mentions", 0),
                "sentiment": theme.get("sentiment"),
            }
            for theme in partial.get("themes", [])[:10]
        ],
    }


def merge_partial_summaries(partials):
    """
    Reduce step: merge multiple partial summaries.
    """
    user_message = f"""
Partial analyses:
{json.dumps(partials, ensure_ascii=False)}

Merge these into one final analysis.
Return ONLY valid JSON.
""".strip()

    data = chat_json(
        [
            {"role": "system", "content": REDUCE_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
    )

    return normalize_llm_summary(data)


def hierarchical_reduce(partials):
    """
    Repeatedly merge partial summaries until one remains.
    """
    while len(partials) > 1:
        merged = []

        for batch in batched(partials, settings.BALLOT_SUMMARY_REDUCE_BATCH_SIZE):
            if len(batch) == 1:
                merged.append(batch[0])
            else:
                compacted = [compact_partial(p) for p in batch]
                merged.append(merge_partial_summaries(compacted))

        partials = merged

    return partials[0]


def summarize_ballot_reasons(ballot_id: int):
    ballot = Ballot.objects.get(pk=ballot_id)

    total = Reason.objects.filter(ballot_id=ballot.pk).count()

    if total == 0:
        return {
            "summary": "No voter reasons were submitted for this ballot.",
            "themes": [],
            "reasons_total": 0,
            "reasons_processed": 0,
            "method": "none",
            "model": settings.LOCAL_QWEN_MODEL,
        }

    # For small ballots, process everything.
    # For large ballots, sample.
    if total <= settings.BALLOT_SUMMARY_MAX_FULL_COMMENTS:
        texts = list(iter_clean_reasons(ballot.pk))
        method = "all"
    else:
        texts = reservoir_sample(ballot.pk, settings.BALLOT_SUMMARY_SAMPLE_SIZE)
        method = "sample"

    if not texts:
        return {
            "summary": "Voter reasons were submitted, but no usable text remained after cleaning.",
            "themes": [],
            "reasons_total": total,
            "reasons_processed": 0,
            "method": method,
            "model": settings.LOCAL_QWEN_MODEL,
        }

    random.shuffle(texts)

    partials = []
    for batch in batched(texts, settings.BALLOT_SUMMARY_LLM_CHUNK_SIZE):
        partials.append(summarize_reason_chunk(batch))

    final = hierarchical_reduce(partials)

    final.update(
        {
            "reasons_total": total,
            "reasons_processed": len(texts),
            "method": method,
            "model": settings.LOCAL_QWEN_MODEL,
        }
    )

    return final
