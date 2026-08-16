import json
import random
from collections import Counter
from itertools import islice

import numpy as np
from django.conf import settings
from django.db import connection
from django.utils import timezone
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import normalize

from apps.ballot.models import (
    Ballot,
    Option,
    Reason,
    ReasonCluster,
    ReasonEmbedding,
)
from apps.utils.embedding import embed_texts
from apps.utils.llm import chat_json
from apps.utils.pii import redact_text

# ──────────────────────────────────────────────────────────────
# System prompts
# ──────────────────────────────────────────────────────────────

CLUSTER_LABEL_SYSTEM = """
You are a neutral civic analyst.
You are given representative voter comments from one thematic cluster.
Each comment is prefixed with the option the voter chose.

Return ONLY valid JSON. Do not return markdown.
Use this exact schema:
{
  "label": "short theme name, max 8 words",
  "summary": "2-4 sentence description of this theme",
  "sentiment": "positive|negative|mixed|neutral"
}

Rules:
Do not include names, emails, phone numbers, IDs, or personal data.
If you see any remaining personal data, replace it with [REDACTED].
""".strip()

OPTION_SUMMARY_SYSTEM = """
You are a neutral civic analyst.
You are given the discovered themes for voters who chose a specific ballot option.

Return ONLY valid JSON. Do not return markdown.
Use this exact schema:
{
  "summary": "3-5 sentence synthesis of why voters chose this option"
}

Rules:
Use only the provided themes.
Do not invent new facts.
Do not include personal data.
""".strip()

EXECUTIVE_SUMMARY_SYSTEM = """
You are a neutral civic analyst writing the final executive summary of a ballot.
You are given per-option summaries and overall statistics.

Return ONLY valid JSON. Do not return markdown.
Use this exact schema:
{
  "summary": "5-8 sentence executive summary covering all options",
  "themes": [
    {
      "name": "short cross-option theme name",
      "mentions": 120,
      "sentiment": "positive|negative|mixed|neutral",
      "examples": ["short anonymous quote"]
    }
  ]
}

Rules:
Highlight agreements and disagreements between options.
Preserve notable minority views.
Do not invent new facts.
Do not include personal data.
Maximum 10 themes.
""".strip()


# ──────────────────────────────────────────────────────────────
# Utility helpers
# ──────────────────────────────────────────────────────────────

def batched(iterable, n):
    it = iter(iterable)
    while True:
        batch = list(islice(it, n))
        if not batch:
            return
        yield batch


def safe_chat_json(messages: list[dict], temperature: float = 0.2) -> dict:
    try:
        data = chat_json(messages, temperature=temperature)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    result = redact_text(text)
    text = result["text"]
    text = " ".join(text.split())
    return text[:400]


def vector_to_pgvector(vector) -> str:
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


# ──────────────────────────────────────────────────────────────
# PII redaction backfill
# ──────────────────────────────────────────────────────────────

def ensure_ballot_reasons_redacted(ballot_id: int, batch_size: int = 1000):
    queryset = (
        Reason.objects.filter(ballot_id=ballot_id, redacted_text="")
        .exclude(text="")
        .only("id", "text", "redacted_text", "pii_entities")
        .order_by("id")
    )

    updates = []
    now = timezone.now()

    for reason in queryset.iterator(chunk_size=batch_size):
        result = redact_text(reason.text)
        reason.redacted_text = result["text"]
        reason.pii_entities = result["entities"]
        reason.pii_redacted_at = now
        updates.append(reason)

        if len(updates) >= batch_size:
            Reason.objects.bulk_update(
                updates,
                ["redacted_text", "pii_entities", "pii_redacted_at"],
                batch_size=batch_size,
            )
            updates = []

    if updates:
        Reason.objects.bulk_update(
            updates,
            ["redacted_text", "pii_entities", "pii_redacted_at"],
            batch_size=batch_size,
        )


# ──────────────────────────────────────────────────────────────
# Embedding stage
# ──────────────────────────────────────────────────────────────

def ensure_ballot_reason_embeddings(ballot_id: int):
    """
    Create embeddings for all redacted reasons that do not yet have one.
    """

    embedding_batch_size = getattr(settings, "EMBEDDING_BATCH_SIZE", 64)

    missing = (
        Reason.objects.filter(
            ballot_id=ballot_id,
            embedding__isnull=True,
        )
        .exclude(redacted_text="")
        .select_related("option")
        .only("id", "option_id", "redacted_text")
        .order_by("id")
    )

    for batch in batched(missing.iterator(chunk_size=1000), embedding_batch_size):
        items = []
        for reason in batch:
            text = reason.redacted_text or normalize_text(reason.text)
            if text:
                items.append((reason, text))

        if not items:
            continue

        texts = [text for _, text in items]

        try:
            vectors = embed_texts(texts)
        except Exception:
            raise

        embeddings_to_create = []
        for (reason, _text), vector in zip(items, vectors):
            if not vector:
                continue
            embeddings_to_create.append(
                ReasonEmbedding(
                    reason_id=reason.id,
                    ballot_id=ballot_id,
                    option_id=reason.option_id,
                    embedding=vector,
                )
            )

        ReasonEmbedding.objects.bulk_create(
            embeddings_to_create,
            batch_size=len(embeddings_to_create),
            ignore_conflicts=True,
        )


# ──────────────────────────────────────────────────────────────
# Clustering stage
# ──────────────────────────────────────────────────────────────

def choose_cluster_count(count: int) -> int:
    if count < 200:
        target = 5
    elif count < 1_000:
        target = 10
    elif count < 5_000:
        target = 20
    elif count < 20_000:
        target = 35
    else:
        target = 50

    return max(2, min(target, count))


def cluster_ballot_reasons(ballot_id: int):
    """
    Cluster embeddings per option within a ballot.
    """

    options = Option.objects.filter(ballot_id=ballot_id).order_by("number", "id")

    min_cluster_size = getattr(settings, "BALLOT_CLUSTER_MIN_REASONS", 50)

    for option in options:
        count = ReasonEmbedding.objects.filter(
            ballot_id=ballot_id,
            option=option,
        ).count()

        if count < min_cluster_size:
            continue

        cluster_option_embeddings(
            ballot_id=ballot_id,
            option=option,
            embedding_count=count,
        )


def cluster_option_embeddings(ballot_id: int, option: Option, embedding_count: int):
    n_clusters = choose_cluster_count(embedding_count)

    kmeans = MiniBatchKMeans(
        n_clusters=n_clusters,
        batch_size=4096,
        n_init=3,
        random_state=42,
    )

    embeddings_qs = (
        ReasonEmbedding.objects.filter(ballot_id=ballot_id, option=option)
        .order_by("id")
    )

    # Train in batches
    for batch in embeddings_qs.values_list("embedding", flat=True).iterator(chunk_size=4096):
        if not batch:
            continue
        X = np.asarray(batch, dtype=np.float32)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        if X.shape[0] == 0:
            continue
        X = normalize(X, norm="l2")
        kmeans.partial_fit(X)

    # Remove previous clusters for this option
    ReasonCluster.objects.filter(ballot_id=ballot_id, option=option).delete()

    # Create cluster rows
    centers = normalize(kmeans.cluster_centers_, norm="l2")
    clusters_by_label = {}

    for label, centroid in enumerate(centers):
        cluster = ReasonCluster.objects.create(
            ballot_id=ballot_id,
            option=option,
            external_cluster_id=int(label),
            centroid=centroid.tolist(),
            size=0,
        )
        clusters_by_label[int(label)] = cluster

    # Assign embeddings to clusters
    sizes = Counter()
    update_buffer = []

    for embeddings in embeddings_qs.only("id", "embedding").iterator(chunk_size=2000):
        if not embeddings:
            continue

        X = np.asarray([item.embedding for item in embeddings], dtype=np.float32)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        X = normalize(X, norm="l2")

        labels = kmeans.predict(X)

        for item, label in zip(embeddings, labels):
            label = int(label)
            item.cluster_id = clusters_by_label[label].id
            sizes[label] += 1
            update_buffer.append(item)

        if len(update_buffer) >= 1000:
            ReasonEmbedding.objects.bulk_update(
                update_buffer, ["cluster_id"], batch_size=1000
            )
            update_buffer = []

    if update_buffer:
        ReasonEmbedding.objects.bulk_update(
            update_buffer, ["cluster_id"], batch_size=1000
        )

    for label, cluster in clusters_by_label.items():
        cluster.size = sizes.get(label, 0)
        cluster.save(update_fields=["size", "updated_at"])


# Add cluster_id to ReasonEmbedding model:
# cluster = models.ForeignKey(
#     ReasonCluster, null=True, blank=True,
#     on_delete=models.SET_NULL, related_name="members",
# )


# ──────────────────────────────────────────────────────────────
# Cluster summarization stage
# ──────────────────────────────────────────────────────────────

def summarize_ballot_clusters(ballot_id: int):
    """
    Use Qwen to label and summarize each discovered cluster.
    """

    max_clusters = getattr(settings, "BALLOT_MAX_CLUSTERS_TO_SUMMARIZE", 15)

    options = Option.objects.filter(ballot_id=ballot_id).order_by("number", "id")

    for option in options:
        clusters = (
            ReasonCluster.objects.filter(ballot_id=ballot_id, option=option)
            .order_by("-size")[:max_clusters]
        )

        for cluster in clusters:
            examples = get_representative_texts(cluster, limit=10)
            cluster.representative_texts = examples

            if examples:
                label, summary, sentiment = generate_cluster_label(
                    option=option,
                    cluster=cluster,
                    examples=examples,
                )
            else:
                label = f"Theme {cluster.external_cluster_id}"
                summary = ""
                sentiment = "neutral"

            cluster.label = label
            cluster.summary = summary
            cluster.sentiment = sentiment
            cluster.save(
                update_fields=["label", "summary", "sentiment", "representative_texts", "updated_at"]
            )


def get_representative_texts(cluster: ReasonCluster, limit: int = 10) -> list[str]:
    if not cluster.centroid:
        return []

    centroid = vector_to_pgvector(cluster.centroid)

    sql = """
        SELECT COALESCE(NULLIF(r."redacted_text", ''), r."text")
        FROM "ReasonEmbedding" e
        JOIN "Reason" r ON r."id" = e."reason_id"
        WHERE e."cluster_id" = %s
        ORDER BY e."embedding" <=> %s::vector
        LIMIT %s
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, [cluster.id, centroid, limit])
        rows = cursor.fetchall()

    examples = []
    for (text,) in rows:
        if text:
            cleaned = " ".join(text.split())[:300]
            examples.append(cleaned)

    return examples


def generate_cluster_label(option: Option, cluster: ReasonCluster, examples: list[str]):
    option_text = option.text if option else "Unknown"
    example_block = "\n".join(
        f"- [Voted for: {option_text}] {ex}" for ex in examples
    )

    prompt = f"""
Ballot option: {option_text}
Cluster size: {cluster.size}

Representative voter comments:
{example_block}
""".strip()

    messages = [
        {"role": "system", "content": CLUSTER_LABEL_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    data = safe_chat_json(messages, temperature=0.2)

    label = str(data.get("label", "")).strip()[:250]
    summary = str(data.get("summary", "")).strip()
    sentiment = str(data.get("sentiment", "neutral")).lower().strip()[:20]

    allowed = {"positive", "negative", "mixed", "neutral"}
    if sentiment not in allowed:
        sentiment = "neutral"

    if not label:
        label = f"Theme {cluster.external_cluster_id}"

    return label, summary, sentiment


# ──────────────────────────────────────────────────────────────
# Final summary builder
# ──────────────────────────────────────────────────────────────

def summarize_ballot_reasons(ballot_id: int) -> dict:
    ballot = Ballot.objects.get(pk=ballot_id)

    total = Reason.objects.filter(ballot_id=ballot.pk).count()

    if total == 0:
        return {
            "summary": "No voter reasons were submitted for this ballot.",
            "themes": [],
            "option_themes": [],
            "reasons_total": 0,
            "reasons_processed": 0,
            "method": "none",
            "model": settings.LOCAL_QWEN_MODEL,
        }

    # Step 1: PII redaction
    ensure_ballot_reasons_redacted(ballot_id)

    # Step 2: Determine method
    cluster_threshold = getattr(settings, "BALLOT_CLUSTER_THRESHOLD", 500)

    if total >= cluster_threshold:
        # Large ballot: embedding + clustering
        method = "clusters"

        ensure_ballot_reason_embeddings(ballot_id)
        cluster_ballot_reasons(ballot_id)
        summarize_ballot_clusters(ballot_id)

        option_themes, processed = build_option_themes_from_clusters(ballot_id)
        executive = build_executive_summary(ballot, total, option_themes)

        return {
            "summary": executive,
            "themes": [],
            "option_themes": option_themes,
            "reasons_total": total,
            "reasons_processed": processed,
            "method": method,
            "model": settings.LOCAL_QWEN_MODEL,
        }

    else:
        # Small ballot: existing map-reduce
        method = "map_reduce"
        result = map_reduce_summarize(ballot_id, total)
        result["method"] = method
        return result


def build_option_themes_from_clusters(ballot_id: int):
    """
    Build structured option_themes from saved clusters.
    """

    options = Option.objects.filter(ballot_id=ballot_id).order_by("number", "id")
    option_themes = []
    processed_total = 0

    for option in options:
        clusters = list(
            ReasonCluster.objects.filter(ballot_id=ballot_id, option=option)
            .exclude(label="")
            .order_by("-size")[:15]
        )

        if not clusters:
            continue

        themes = []
        for cluster in clusters:
            themes.append({
                "name": cluster.label,
                "mentions": cluster.size,
                "sentiment": cluster.sentiment,
                "examples": cluster.representative_texts[:3],
            })

        # Generate per-option narrative summary
        option_summary = generate_option_summary(option, themes)

        option_themes.append({
            "option": option.text,
            "option_id": option.id,
            "summary": option_summary,
            "themes": themes,
        })

        processed_total += sum(c.size for c in clusters)

    return option_themes, processed_total


def generate_option_summary(option: Option, themes: list[dict]) -> str:
    theme_lines = "\n".join(
        f"- {t['name']} ({t['mentions']} mentions, {t['sentiment']})"
        for t in themes
    )

    prompt = f"""
Ballot option: {option.text}

Discovered themes from voters who chose this option:
{theme_lines}

Write a concise synthesis of why voters chose this option.
""".strip()

    messages = [
        {"role": "system", "content": OPTION_SUMMARY_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    data = safe_chat_json(messages, temperature=0.2)
    return str(data.get("summary", "")).strip()[:2000]


def build_executive_summary(ballot: Ballot, total: int, option_themes: list[dict]) -> str:
    """
    Build the final cross-option executive summary.
    """

    lines = [
        f"Ballot: {ballot.title}",
        f"Total reasons: {total}",
    ]

    if ballot.description:
        lines.append(f"Description: {ballot.description}")

    for ot in option_themes:
        theme_names = ", ".join(t["name"] for t in ot["themes"][:5])
        lines.append(
            f"\nOption: {ot['option']}\n"
            f"Summary: {ot['summary'][:500]}\n"
            f"Top themes: {theme_names}"
        )

    prompt = "\n".join(lines)

    messages = [
        {"role": "system", "content": EXECUTIVE_SUMMARY_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    data = safe_chat_json(messages, temperature=0.2)
    return str(data.get("summary", "")).strip()


# ──────────────────────────────────────────────────────────────
# Legacy map-reduce (for small ballots)
# ──────────────────────────────────────────────────────────────

MAP_SYSTEM = """
You are analyzing anonymous voter comments for a public ballot.
Each comment is prefixed with the option the voter chose.

Return ONLY valid JSON, no markdown, with this shape:
{
  "summary": "2-4 sentence synthesis of this batch",
  "option_themes": [
    {
      "option": "The option text",
      "themes": [
        {
          "name": "short theme name",
          "mentions": 12,
          "sentiment": "positive|negative|mixed|neutral",
          "examples": ["short anonymous quote"]
        }
      ]
    }
  ]
}

Rules:
Use only the provided comments.
Do not include names, emails, phone numbers, IDs, or personal data.
If you see any remaining personal data, replace it with [REDACTED].
Keep examples short and anonymous.
Maximum 8 themes per option.
If unsure, use neutral sentiment.
""".strip()

REDUCE_SYSTEM = """
You are merging partial analyses of voter comments into one final analysis.

Return ONLY valid JSON, no markdown, with this shape:
{
  "summary": "final 3-6 sentence summary",
  "option_themes": [
    {
      "option": "The option text",
      "themes": [
        {
          "name": "short theme name",
          "mentions": 120,
          "sentiment": "positive|negative|mixed|neutral",
          "examples": ["short anonymous quote"]
        }
      ]
    }
  ]
}

Rules:
Combine similar themes within each option.
Preserve important disagreements between options.
Do not invent new facts.
Do not include personal data.
Maximum 10 themes per option.
""".strip()


def iter_clean_reasons(ballot_id: int):
    queryset = (
        Reason.objects.filter(ballot_id=ballot_id)
        .select_related("option")
        .order_by("id")
    )

    for reason in queryset.iterator(chunk_size=10000):
        if reason.redacted_text:
            text = reason.redacted_text
        else:
            text = normalize_text(reason.text)

        text = " ".join(text.split())

        if not text:
            continue

        if reason.option:
            yield f"[Voted for: {reason.option.text}] {text}"
        else:
            yield text


def reservoir_sample(ballot_id: int, k: int):
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
    """
    Handles both option_themes and flat themes.
    """

    if not isinstance(data, dict):
        data = {}

    allowed_sentiments = {"positive", "negative", "mixed", "neutral"}

    # ── Process option_themes ──
    option_themes = []
    for option_group in data.get("option_themes", [])[:10]:
        if not isinstance(option_group, dict):
            continue

        option_name = redact_text(str(option_group.get("option", "Unknown")))["text"]
        option_name = " ".join(option_name.split())[:200]

        themes = []
        for item in option_group.get("themes", [])[:10]:
            if not isinstance(item, dict):
                continue
            try:
                mentions = int(item.get("mentions", 0))
            except Exception:
                mentions = 0

            examples = []
            for example in item.get("examples", [])[:2]:
                ex_text = redact_text(str(example or ""))["text"]
                ex_text = " ".join(ex_text.split())[:200]
                if ex_text:
                    examples.append(ex_text)

            name = redact_text(str(item.get("name", "Unnamed")))["text"]
            name = " ".join(name.split())[:120]

            sentiment = str(item.get("sentiment", "neutral")).lower().strip()[:20]
            if sentiment not in allowed_sentiments:
                sentiment = "neutral"

            themes.append({
                "name": name or "Unnamed",
                "mentions": mentions,
                "sentiment": sentiment,
                "examples": examples,
            })

        option_themes.append({
            "option": option_name,
            "themes": themes,
        })

    # ── Process flat themes (fallback) ──
    flat_themes = []
    for item in data.get("themes", [])[:15]:
        if not isinstance(item, dict):
            continue
        try:
            mentions = int(item.get("mentions", 0))
        except Exception:
            mentions = 0

        examples = []
        for example in item.get("examples", [])[:2]:
            ex_text = redact_text(str(example or ""))["text"]
            ex_text = " ".join(ex_text.split())[:200]
            if ex_text:
                examples.append(ex_text)

        name = redact_text(str(item.get("name", "Unnamed")))["text"]
        name = " ".join(name.split())[:120]

        sentiment = str(item.get("sentiment", "neutral")).lower().strip()[:20]
        if sentiment not in allowed_sentiments:
            sentiment = "neutral"

        flat_themes.append({
            "name": name or "Unnamed",
            "mentions": mentions,
            "sentiment": sentiment,
            "examples": examples,
        })

    summary = redact_text(str(data.get("summary", "")))["text"]
    summary = " ".join(summary.split())[:5000]

    result = {
        "summary": summary,
        "themes": flat_themes,
    }

    if option_themes:
        result["option_themes"] = option_themes

    return result


def summarize_reason_chunk(texts):
    lines = "\n".join(f"{idx}. {text}" for idx, text in enumerate(texts, 1))

    user_message = f"Voter comments:\n{lines}\n\nReturn ONLY valid JSON.".strip()

    data = chat_json(
        [
            {"role": "system", "content": MAP_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
    )

    return normalize_llm_summary(data)


def compact_partial(partial):
    option_themes = []
    for ot in partial.get("option_themes", [])[:5]:
        option_themes.append({
            "option": ot.get("option"),
            "themes": [
                {
                    "name": t.get("name"),
                    "mentions": t.get("mentions", 0),
                    "sentiment": t.get("sentiment"),
                }
                for t in ot.get("themes", [])[:8]
            ],
        })

    return {
        "summary": partial.get("summary", ""),
        "option_themes": option_themes,
        "themes": [
            {
                "name": t.get("name"),
                "mentions": t.get("mentions", 0),
                "sentiment": t.get("sentiment"),
            }
            for t in partial.get("themes", [])[:10]
        ],
    }


def merge_partial_summaries(partials):
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


def map_reduce_summarize(ballot_id: int, total: int) -> dict:
    """
    Legacy map-reduce for small ballots.
    """

    max_full = getattr(settings, "BALLOT_SUMMARY_MAX_FULL_COMMENTS", 5000)
    sample_size = getattr(settings, "BALLOT_SUMMARY_SAMPLE_SIZE", 3000)
    chunk_size = getattr(settings, "BALLOT_SUMMARY_LLM_CHUNK_SIZE", 50)

    if total <= max_full:
        texts = list(iter_clean_reasons(ballot_id))
        method = "all"
    else:
        texts = reservoir_sample(ballot_id, sample_size)
        method = "sample"

    if not texts:
        return {
            "summary": "Voter reasons were submitted, but no usable text remained after cleaning.",
            "themes": [],
            "option_themes": [],
            "reasons_total": total,
            "reasons_processed": 0,
            "method": method,
            "model": settings.LOCAL_QWEN_MODEL,
        }

    random.shuffle(texts)

    partials = []
    for batch in batched(texts, chunk_size):
        partials.append(summarize_reason_chunk(batch))

    final = hierarchical_reduce(partials)

    final.update({
        "reasons_total": total,
        "reasons_processed": len(texts),
        "method": method,
        "model": settings.LOCAL_QWEN_MODEL,
    })

    return final
