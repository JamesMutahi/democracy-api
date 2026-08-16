import re
from collections import Counter
from typing import Iterable, List

import numpy as np
from celery import chain, shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone
from sklearn.cluster import MiniBatchKMeans
from sklearn.preprocessing import normalize

from apps.survey.models import (
    ChoiceAnswer,
    Question,
    Response,
    Survey,
    SurveySummary,
    SurveyTextCluster,
    TextAnswer,
    TextAnswerEmbedding,
)
from apps.utils.embedding import embed_texts, clean_text_for_embedding
from apps.utils.pii import redact_text
from apps.utils.llm import chat_json

logger = get_task_logger(__name__)


# ======================
# Utility helpers
# ======================

def chunked(iterable: Iterable, size: int):
    batch = []

    for item in iterable:
        batch.append(item)

        if len(batch) >= size:
            yield batch
            batch = []

    if batch:
        yield batch


def clean_display_text(value: str) -> str:
    value = clean_text_for_embedding(value)
    return value[:1000]


def redacted_text_answers(queryset):
    """
    Streams redacted text answers.
    """

    for answer in queryset.only("id", "text", "redacted_text").iterator(chunk_size=2000):
        if answer.redacted_text:
            text = answer.redacted_text
        else:
            text = redact_text(answer.text)["text"]

        text = re.sub(r"\s+", " ", text or "")
        text = text.strip()
        text = text[:1000]

        if text:
            yield text


def truncate_prompt(value: str, max_chars: int) -> str:
    value = value or ""

    if len(value) <= max_chars:
        return value

    return value[:max_chars]


def vector_to_pgvector(vector: list[float]) -> str:
    """
    Convert a Python list to pgvector text format.
    Example: [0.1,0.2,0.3]
    """

    return "[" + ",".join(str(float(x)) for x in vector) + "]"


def safe_chat_json(messages: list[dict], temperature: float = 0.2) -> dict:
    """
    Wrapper around your existing chat_json().

    Returns {} if the local LLM fails or returns invalid JSON.
    """

    try:
        data = chat_json(messages, temperature=temperature)

        if isinstance(data, dict):
            return data

        return {}
    except Exception:
        logger.exception("Local LLM JSON call failed")
        return {}


def format_llm_summary(data: dict) -> str:
    """
    Convert common JSON response shapes into a plain summary string.
    """

    if not isinstance(data, dict):
        return ""

    summary = data.get("summary")

    if summary:
        return str(summary).strip()

    bullets = data.get("bullets") or data.get("themes") or []

    if isinstance(bullets, list) and bullets:
        return "\n".join(f"- {bullet}" for bullet in bullets)

    executive_summary = data.get("executive_summary")

    if executive_summary:
        return str(executive_summary).strip()

    text = data.get("text")

    if text:
        return str(text).strip()

    return ""


def normalize_redacted_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "")
    value = value.strip()
    return value[:2000]


def prepare_redacted_text_answers(answers):
    """
    Ensures each TextAnswer has redacted_text.

    Returns:
        list of (answer, cleaned_redacted_text)
    """

    items = []
    answers_to_update = []
    now = timezone.now()

    for answer in answers:
        if answer.redacted_text:
            redacted = answer.redacted_text
        else:
            result = redact_text(answer.text)

            answer.redacted_text = result["text"]
            answer.pii_entities = result["entities"]
            answer.pii_redacted_at = now

            answers_to_update.append(answer)

            redacted = answer.redacted_text

        cleaned = normalize_redacted_text(redacted)

        if cleaned:
            items.append((answer, cleaned))

    if answers_to_update:
        TextAnswer.objects.bulk_update(
            answers_to_update,
            ["redacted_text", "pii_entities", "pii_redacted_at"],
            batch_size=1000,
        )

    return items


# ======================
# Survey end detection
# ======================

@shared_task(queue="pii")
def redact_survey_text_answers(survey_id: int):
    """
    Backfill PII redaction for all text answers in a survey.
    """

    queryset = (
        TextAnswer.objects.filter(
            question__page__survey_id=survey_id,
            redacted_text="",
        )
        .exclude(text="")
        .exclude(text__regex=r"^\s*$")
        .only("id", "text", "redacted_text", "pii_entities")
        .order_by("id")
    )

    for batch in chunked(queryset.iterator(chunk_size=1000), 1000):
        prepare_redacted_text_answers(batch)

    return survey_id


@shared_task
def check_ended_surveys():
    """
    Periodic task.

    Finds surveys whose end_time has passed and starts summarization.
    """

    now = timezone.now()

    survey_ids = (
        Survey.objects.filter(end_time__lte=now)
        .filter(
            Q(summary__isnull=True)
            | Q(summary__status=SurveySummary.Status.PENDING)
        )
        .values_list("id", flat=True)
        .distinct()
    )

    for survey_id in survey_ids.iterator(chunk_size=500):
        start_survey_summary_pipeline.delay(survey_id)

    return {"scheduled": len(survey_ids)}


@shared_task
def start_survey_summary_pipeline(survey_id: int):
    try:
        survey = Survey.objects.get(pk=survey_id)
    except Survey.DoesNotExist:
        logger.warning("Survey %s does not exist", survey_id)
        return

    summary, _ = SurveySummary.objects.get_or_create(survey=survey)

    if summary.status == SurveySummary.Status.COMPLETED:
        return

    if summary.status == SurveySummary.Status.RUNNING:
        return

    SurveySummary.objects.filter(pk=summary.pk).update(
        status=SurveySummary.Status.RUNNING,
        error="",
    )

    chain(
        redact_survey_text_answers.s(survey_id),
        ensure_survey_text_embeddings.s(survey_id),
        cluster_survey_text_answers.s(),
        summarize_survey_clusters.s(),
        finalize_survey_summary.s(),
    ).apply_async()

    return survey_id


# ======================
# Embedding tasks
# ======================

@shared_task(queue="embeddings")
def ensure_survey_text_embeddings(survey_id: int):
    """
    Backfill missing embeddings for all TEXT answers in a survey.

    Uses persisted redacted text.
    """

    missing_answers = (
        TextAnswer.objects.filter(
            question__page__survey_id=survey_id,
            question__type=Question.Type.TEXT,
            embedding__isnull=True,
        )
        .exclude(text="")
        .exclude(text__regex=r"^\s*$")
        .only("id", "question_id", "text", "redacted_text", "pii_entities")
        .order_by("id")
    )

    embedding_batch_size = getattr(settings, "EMBEDDING_BATCH_SIZE", 64)

    for batch in chunked(missing_answers.iterator(chunk_size=1000), embedding_batch_size):
        items = prepare_redacted_text_answers(batch)

        if not items:
            continue

        texts = [text for _, text in items]

        try:
            vectors = embed_texts(texts)
        except Exception:
            logger.exception("Embedding failed for survey %s", survey_id)
            raise

        embeddings_to_create = []

        for (answer, _text), vector in zip(items, vectors):
            if not vector:
                continue

            embeddings_to_create.append(
                TextAnswerEmbedding(
                    text_answer_id=answer.id,
                    survey_id=survey_id,
                    question_id=answer.question_id,
                    embedding=vector,
                )
            )

        TextAnswerEmbedding.objects.bulk_create(
            embeddings_to_create,
            batch_size=len(embeddings_to_create),
            ignore_conflicts=True,
        )

    return survey_id


@shared_task(queue="embeddings")
def embed_response_text_answers(response_id: int):
    """
    Optional incremental embedding task.
    """

    try:
        response = Response.objects.get(pk=response_id)
    except Response.DoesNotExist:
        return

    answers = (
        TextAnswer.objects.filter(
            response=response,
            question__type=Question.Type.TEXT,
            embedding__isnull=True,
        )
        .exclude(text="")
        .exclude(text__regex=r"^\s*$")
        .only("id", "question_id", "response", "text", "redacted_text", "pii_entities")
    )

    items = prepare_redacted_text_answers(answers)

    if not items:
        return

    texts = [text for _, text in items]
    vectors = embed_texts(texts)

    embeddings_to_create = []

    for (answer, _text), vector in zip(items, vectors):
        if not vector:
            continue

        embeddings_to_create.append(
            TextAnswerEmbedding(
                text_answer_id=answer.id,
                survey_id=response.survey_id,
                question_id=answer.question_id,
                embedding=vector,
            )
        )

    TextAnswerEmbedding.objects.bulk_create(
        embeddings_to_create,
        batch_size=len(embeddings_to_create),
        ignore_conflicts=True,
    )


# ======================
# Clustering stage
# ======================

@shared_task(queue="survey_summary")
def cluster_survey_text_answers(survey_id: int):
    """
    Clusters text-answer embeddings per TEXT question.
    """

    questions = Question.objects.filter(
        page__survey_id=survey_id,
        type=Question.Type.TEXT,
    ).order_by("page__number", "number")

    min_text_answers = getattr(settings, "CLUSTER_MIN_TEXT_ANSWERS", 200)

    for question in questions:
        embedding_count = TextAnswerEmbedding.objects.filter(
            survey_id=survey_id,
            question=question,
        ).count()

        if embedding_count == 0:
            continue

        if embedding_count < min_text_answers:
            logger.info(
                "Skipping clustering for question %s because it has only %s embeddings",
                question.id,
                embedding_count,
            )
            continue

        cluster_question_embeddings(
            survey_id=survey_id,
            question=question,
            embedding_count=embedding_count,
        )

    return survey_id


def choose_number_of_clusters(embedding_count: int) -> int:
    """
    Heuristic cluster count.

    Tune this depending on your data.
    """

    if embedding_count < 500:
        target = 10
    elif embedding_count < 2_000:
        target = 20
    elif embedding_count < 10_000:
        target = 40
    elif embedding_count < 50_000:
        target = 70
    elif embedding_count < 200_000:
        target = 100
    else:
        target = 150

    return max(2, min(target, embedding_count))


def cluster_question_embeddings(
        survey_id: int,
        question: Question,
        embedding_count: int,
):
    """
    Uses MiniBatchKMeans so that vectors can be processed in batches.

    Embeddings are normalized so Euclidean KMeans approximates cosine
    similarity behavior.
    """

    n_clusters = choose_number_of_clusters(embedding_count)

    kmeans = MiniBatchKMeans(
        n_clusters=n_clusters,
        batch_size=4096,
        n_init=3,
        random_state=42,
    )

    embeddings_qs = (
        TextAnswerEmbedding.objects.filter(
            survey_id=survey_id,
            question=question,
        )
        .order_by("id")
    )

    # Train in batches.
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

    # Remove previous clusters for this question.
    SurveyTextCluster.objects.filter(
        survey_id=survey_id,
        question=question,
    ).delete()

    # Create new cluster rows.
    centers = normalize(kmeans.cluster_centers_, norm="l2")

    clusters_by_label = {}

    for label, centroid in enumerate(centers):
        cluster = SurveyTextCluster.objects.create(
            survey_id=survey_id,
            question=question,
            external_cluster_id=int(label),
            centroid=centroid.tolist(),
            size=0,
        )
        clusters_by_label[int(label)] = cluster

    # Assign embeddings to clusters in batches.
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
            TextAnswerEmbedding.objects.bulk_update(
                update_buffer,
                ["cluster_id"],
                batch_size=1000,
            )
            update_buffer = []

    if update_buffer:
        TextAnswerEmbedding.objects.bulk_update(
            update_buffer,
            ["cluster_id"],
            batch_size=1000,
        )

    # Save cluster sizes.
    for label, cluster in clusters_by_label.items():
        cluster.size = sizes.get(label, 0)
        cluster.save(update_fields=["size", "updated_at"])

    logger.info(
        "Clustered question %s into %s clusters from %s embeddings",
        question.id,
        n_clusters,
        embedding_count,
    )


# ======================
# Cluster summarization stage
# ======================

@shared_task(queue="survey_summary")
def summarize_survey_clusters(survey_id: int):
    """
    Uses local Qwen to label and summarize each discovered cluster.
    """

    questions = Question.objects.filter(
        page__survey_id=survey_id,
        type=Question.Type.TEXT,
    ).order_by("page__number", "number")

    max_clusters = getattr(settings, "SURVEY_MAX_CLUSTERS_TO_SUMMARIZE", 30)

    for question in questions:
        clusters = (
            SurveyTextCluster.objects.filter(
                survey_id=survey_id,
                question=question,
            )
            .order_by("-size")[:max_clusters]
        )

        for cluster in clusters:
            examples = get_representative_texts(cluster=cluster, limit=12)

            cluster.representative_texts = examples

            if examples:
                label, summary = generate_cluster_label_and_summary(
                    question=question,
                    cluster=cluster,
                    examples=examples,
                )
            else:
                label = f"Theme {cluster.external_cluster_id}"
                summary = ""

            cluster.label = label
            cluster.summary = summary

            cluster.save(
                update_fields=[
                    "label",
                    "summary",
                    "representative_texts",
                    "updated_at",
                ]
            )

    return survey_id


def get_representative_texts(cluster: SurveyTextCluster, limit: int = 12) -> List[str]:
    """
    Fetches redacted text answers nearest to the cluster centroid.
    """

    if not cluster.centroid:
        return []

    centroid = vector_to_pgvector(cluster.centroid)

    sql = """
        SELECT ta."redacted_text", ta."text"
        FROM "TextAnswerEmbedding" e
        JOIN "TextAnswer" ta ON ta."id" = e."text_answer_id"
        WHERE e."cluster_id" = %s
        ORDER BY e."embedding" <=> %s::vector
        LIMIT %s
    """

    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [
                cluster.id,
                centroid,
                limit,
            ],
        )

        rows = cursor.fetchall()

    examples = []

    for redacted_text, raw_text in rows:
        if redacted_text:
            text = redacted_text
        else:
            text = redact_text(raw_text)["text"]

        text = re.sub(r"\s+", " ", text or "")
        text = text.strip()
        text = text[:800]

        if text:
            examples.append(text)

    return examples


def generate_cluster_label_and_summary(
        question: Question,
        cluster: SurveyTextCluster,
        examples: List[str],
):
    """
    Uses your existing chat_json() function.
    """

    example_block = "\n".join(f"- {example}" for example in examples)

    prompt = f"""
Survey question:
{question.text}

Cluster size:
{cluster.size}

Representative respondent answers:
{example_block}
""".strip()

    max_prompt_chars = getattr(settings, "SURVEY_SUMMARY_MAX_PROMPT_CHARS", 12000)
    prompt = truncate_prompt(prompt, max_prompt_chars)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a neutral survey analyst. "
                "Return ONLY valid JSON. "
                "Do not return markdown. "
                "Use this exact schema: "
                '{"label": "string", "summary": "string"}'
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    data = safe_chat_json(messages, temperature=0.2)

    label = str(data.get("label", "")).strip()[:250]
    summary = str(data.get("summary", "")).strip()

    if not label:
        label = f"Theme {cluster.external_cluster_id}"

    return label, summary


# ======================
# Final summary stage
# ======================

@shared_task(queue="survey_summary")
def finalize_survey_summary(survey_id: int):
    """
    Builds the final SurveySummary from:
    - choice answer aggregation
    - number answer aggregation
    - clustered text themes
    """

    try:
        survey = Survey.objects.get(pk=survey_id)
    except Survey.DoesNotExist:
        logger.warning("Survey %s does not exist", survey_id)
        return

    summary, _ = SurveySummary.objects.get_or_create(survey=survey)

    try:
        total_responses = survey.responses.count()

        choice_stats = build_choice_stats(survey)
        number_stats = build_number_stats(survey)

        text_themes, processed_text_answers, sampled = build_text_themes(survey)

        executive_summary = build_executive_summary(
            survey=survey,
            total_responses=total_responses,
            choice_stats=choice_stats,
            number_stats=number_stats,
            text_themes=text_themes,
        )

        SurveySummary.objects.filter(pk=summary.pk).update(
            status=SurveySummary.Status.COMPLETED,
            summary=executive_summary,
            choice_stats=choice_stats,
            number_stats=number_stats,
            text_themes=text_themes,
            total_responses=total_responses,
            processed_text_answers=processed_text_answers,
            sampled=sampled,
            model_name=getattr(settings, "LOCAL_QWEN_MODEL", ""),
            prompt_version="embed-cluster-v1",
            completed_at=timezone.now(),
            error="",
        )

        logger.info("Completed survey summary for survey %s", survey_id)

    except Exception as exc:
        logger.exception("Failed to finalize summary for survey %s", survey_id)

        SurveySummary.objects.filter(pk=summary.pk).update(
            status=SurveySummary.Status.FAILED,
            error=str(exc)[:10000],
        )

        raise


# ======================
# Structured answer stats
# ======================

def build_choice_stats(survey: Survey):
    stats = []

    questions = (
        Question.objects.filter(
            page__survey=survey,
            type__in=[
                Question.Type.SINGLE_CHOICE,
                Question.Type.MULTIPLE_CHOICE,
            ],
        )
        .order_by("page__number", "number")
    )

    for question in questions:
        rows = list(
            ChoiceAnswer.objects.filter(question=question)
            .values("choice_id", "choice__text")
            .annotate(total=Count("id"))
            .order_by("-total")
        )

        total_answers = sum(row["total"] for row in rows)

        choices = []

        for row in rows:
            percent = 0

            if total_answers:
                percent = round((row["total"] / total_answers) * 100, 2)

            choices.append(
                {
                    "choice_id": row["choice_id"],
                    "text": row["choice__text"],
                    "count": row["total"],
                    "percent": percent,
                }
            )

        stats.append(
            {
                "question_id": question.id,
                "question": question.text,
                "type": question.type,
                "total_answers": total_answers,
                "choices": choices[:50],
            }
        )

    return stats


def build_number_stats(survey: Survey):
    """
    Basic numeric aggregation for NUMBER questions.

    For very large datasets, consider using SQL casting/aggregation instead.
    """

    stats = []

    questions = Question.objects.filter(
        page__survey=survey,
        type=Question.Type.NUMBER,
    ).order_by("page__number", "number")

    for question in questions:
        count = 0
        total = 0.0
        min_value = None
        max_value = None

        values = TextAnswer.objects.filter(question=question).values_list(
            "text",
            flat=True,
        )

        for raw in values.iterator(chunk_size=2000):
            cleaned = re.sub(r"[^\d.\-]", "", raw or "")

            if not cleaned:
                continue

            try:
                value = float(cleaned)
            except ValueError:
                continue

            count += 1
            total += value

            if min_value is None or value < min_value:
                min_value = value

            if max_value is None or value > max_value:
                max_value = value

        if count:
            stats.append(
                {
                    "question_id": question.id,
                    "question": question.text,
                    "count": count,
                    "average": round(total / count, 2),
                    "min": min_value,
                    "max": max_value,
                }
            )

    return stats


# ======================
# Text theme builder
# ======================

def build_text_themes(survey: Survey):
    """
    Builds qualitative summaries per TEXT question.

    If clusters exist, it uses cluster summaries.
    If not, it falls back to sampled direct summarization.
    """

    results = []
    processed_total = 0
    sampled_any = False

    questions = Question.objects.filter(
        page__survey=survey,
        type=Question.Type.TEXT,
    ).order_by("page__number", "number")

    max_clusters = getattr(settings, "SURVEY_MAX_CLUSTERS_TO_SUMMARIZE", 30)

    for question in questions:
        clusters = list(
            SurveyTextCluster.objects.filter(
                survey=survey,
                question=question,
            )
            .exclude(summary="")
            .order_by("-size")[:max_clusters]
        )

        if clusters:
            cluster_json = []
            cluster_lines = []

            for cluster in clusters:
                label = cluster.label or f"Theme {cluster.external_cluster_id}"

                cluster_lines.append(
                    f"Theme: {label}\n"
                    f"Size: {cluster.size}\n"
                    f"Summary: {cluster.summary}"
                )

                cluster_json.append(
                    {
                        "cluster_id": cluster.external_cluster_id,
                        "label": label,
                        "size": cluster.size,
                        "summary": cluster.summary,
                        "representative_texts": cluster.representative_texts[:5],
                    }
                )

            if len(cluster_lines) == 1:
                question_summary = clusters[0].summary
            else:
                question_summary = reduce_cluster_summaries(
                    question=question,
                    cluster_lines=cluster_lines,
                )

            processed = TextAnswerEmbedding.objects.filter(
                survey=survey,
                question=question,
            ).count()

            results.append(
                {
                    "question_id": question.id,
                    "question": question.text,
                    "summary": question_summary,
                    "processed_answers": processed,
                    "sampled": False,
                    "clusters": cluster_json,
                }
            )

            processed_total += processed

        else:
            summary, processed, sampled = summarize_small_text_question(
                survey=survey,
                question=question,
            )

            if summary:
                results.append(
                    {
                        "question_id": question.id,
                        "question": question.text,
                        "summary": summary,
                        "processed_answers": processed,
                        "sampled": sampled,
                        "clusters": [],
                    }
                )

                processed_total += processed
                sampled_any = sampled_any or sampled

    return results, processed_total, sampled_any


# ======================
# Fallback direct summarization
# ======================

def summarize_small_text_question(survey: Survey, question: Question):
    """
    Used when a question has too few text answers for clustering.
    """

    queryset = (
        TextAnswer.objects.filter(
            question=question,
            question__page__survey=survey,
        )
        .exclude(text="")
        .exclude(text__regex=r"^\s*$")
        .order_by("id")
    )

    total = queryset.count()

    if total == 0:
        return "", 0, False

    limit = getattr(settings, "SURVEY_SMALL_TEXT_LIMIT", 2000)

    sampled = False

    if total > limit:
        step = max(1, total // limit)
        queryset = queryset.filter(id__mod=step)
        sampled = True

    summaries = []
    processed = 0

    batch_size = getattr(settings, "SURVEY_TEXT_BATCH_SIZE", 100)
    reduce_group_size = 8

    for batch in chunked(redacted_text_answers(queryset), batch_size):
        summary = summarize_answer_batch_json(
            question=question,
            answers=batch,
        )

        if summary:
            summaries.append(summary)
            processed += len(batch)

        while len(summaries) >= reduce_group_size:
            head = summaries[:reduce_group_size]
            tail = summaries[reduce_group_size:]

            summaries = [
                            reduce_text_summaries_json(
                                question=question,
                                summaries=head,
                            )
                        ] + tail

    while len(summaries) > 1:
        groups = [
            summaries[i: i + reduce_group_size]
            for i in range(0, len(summaries), reduce_group_size)
        ]

        summaries = [
            reduce_text_summaries_json(
                question=question,
                summaries=group,
            )
            for group in groups
        ]

    final_summary = summaries[0] if summaries else ""

    return final_summary, processed, sampled


def summarize_answer_batch_json(question: Question, answers: List[str]) -> str:
    answer_block = "\n".join(f"- {answer}" for answer in answers)

    prompt = f"""
Survey question:
{question.text}

Respondent answers:
{answer_block}
""".strip()

    max_prompt_chars = getattr(settings, "SURVEY_SUMMARY_MAX_PROMPT_CHARS", 12000)
    prompt = truncate_prompt(prompt, max_prompt_chars)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a neutral survey analyst. "
                "Return ONLY valid JSON. "
                "Do not return markdown. "
                "Use this exact schema: "
                '{"summary": "string", "bullets": ["string"]}'
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    data = safe_chat_json(messages, temperature=0.2)

    return format_llm_summary(data)


def reduce_text_summaries_json(question: Question, summaries: List[str]) -> str:
    summary_block = "\n\n".join(
        f"Summary {index + 1}:\n{summary}"
        for index, summary in enumerate(summaries)
    )

    prompt = f"""
Survey question:
{question.text}

Existing summaries:
{summary_block}
""".strip()

    max_prompt_chars = getattr(settings, "SURVEY_SUMMARY_MAX_PROMPT_CHARS", 12000)
    prompt = truncate_prompt(prompt, max_prompt_chars)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a neutral survey analyst. "
                "Return ONLY valid JSON. "
                "Do not return markdown. "
                "Use this exact schema: "
                '{"summary": "string"}'
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    data = safe_chat_json(messages, temperature=0.2)

    return format_llm_summary(data)


def reduce_cluster_summaries(question: Question, cluster_lines: List[str]) -> str:
    prompt = f"""
Survey question:
{question.text}

Discovered themes:
{chr(10).join(cluster_lines)}
""".strip()

    max_prompt_chars = getattr(settings, "SURVEY_SUMMARY_MAX_PROMPT_CHARS", 12000)
    prompt = truncate_prompt(prompt, max_prompt_chars)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a neutral survey analyst. "
                "Return ONLY valid JSON. "
                "Do not return markdown. "
                "Use this exact schema: "
                '{"summary": "string"}'
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    data = safe_chat_json(messages, temperature=0.2)

    return format_llm_summary(data)


# ======================
# Executive summary
# ======================

def build_executive_summary(
        survey: Survey,
        total_responses: int,
        choice_stats: list[dict],
        number_stats: list[dict],
        text_themes: list[dict],
) -> str:
    lines = [
        f"Survey title: {survey.title}",
        f"Total responses: {total_responses}",
    ]

    if survey.description:
        lines.append(f"Survey description: {survey.description}")

    for item in choice_stats[:20]:
        top_choices = "; ".join(
            f"{choice['text']} {choice['percent']}%"
            for choice in item["choices"][:5]
        )

        lines.append(
            f"Choice question: {item['question']}\n"
            f"Top answers: {top_choices}"
        )

    for item in number_stats[:20]:
        lines.append(
            f"Number question: {item['question']}\n"
            f"Average: {item['average']}, Min: {item['min']}, Max: {item['max']}"
        )

    for item in text_themes[:20]:
        lines.append(
            f"Open-ended question: {item['question']}\n"
            f"Qualitative summary: {item['summary'][:1500]}"
        )

    prompt = "\n\n".join(lines)

    max_prompt_chars = getattr(settings, "SURVEY_SUMMARY_MAX_PROMPT_CHARS", 12000)
    prompt = truncate_prompt(prompt, max_prompt_chars * 2)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a neutral survey analyst. "
                "Return ONLY valid JSON. "
                "Do not return markdown. "
                "Use this exact schema: "
                '{"executive_summary": "string"}'
            ),
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    data = safe_chat_json(messages, temperature=0.2)

    executive_summary = str(data.get("executive_summary", "")).strip()

    if executive_summary:
        return executive_summary

    # Fallback if local LLM fails.
    return f"{survey.title} received {total_responses} responses."
