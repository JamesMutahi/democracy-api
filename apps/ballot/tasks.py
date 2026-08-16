from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from .models import Ballot, BallotSummary, Reason
from .services.summarizer import summarize_ballot_reasons
from ..utils.pii import redact_text


@shared_task(queue="pii")
def redact_reason(reason_id: int):
    try:
        reason = Reason.objects.get(pk=reason_id)
    except Reason.DoesNotExist:
        return

    if reason.redacted_text:
        return

    result = redact_text(reason.text)

    reason.redacted_text = result["text"]
    reason.pii_entities = result["entities"]
    reason.pii_redacted_at = timezone.now()

    reason.save(
        update_fields=[
            "redacted_text",
            "pii_entities",
            "pii_redacted_at",
            "updated_at",
        ]
    )


@shared_task
def check_ended_ballots():
    now = timezone.now()
    retry_cutoff = now - timedelta(minutes=15)

    ballot_ids = (
        Ballot.objects.filter(end_time__lte=now)
        .filter(
            Q(summary__isnull=True)
            | (
                    Q(summary__status=BallotSummary.Status.FAILED)
                    & Q(summary__attempts__lt=5)
                    & Q(summary__updated_at__lte=retry_cutoff)
            )
        )
        .values_list("id", flat=True)[:200]
    )

    for ballot_id in ballot_ids:
        summarize_ballot.delay(ballot_id)

    return len(ballot_ids)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def summarize_ballot(self, ballot_id: int):
    lock_key = f"ballot_summary_lock:{ballot_id}"

    if not cache.add(lock_key, "1", timeout=60 * 120):
        return "locked"

    try:
        summary, _ = BallotSummary.objects.get_or_create(ballot_id=ballot_id)

        if summary.status == BallotSummary.Status.COMPLETED:
            return "already_completed"

        summary.attempts += 1
        summary.status = BallotSummary.Status.PROCESSING
        summary.started_at = timezone.now()
        summary.error = ""
        summary.save(
            update_fields=["attempts", "status", "started_at", "error", "updated_at"]
        )

        try:
            result = summarize_ballot_reasons(ballot_id)

            summary.status = BallotSummary.Status.COMPLETED
            summary.summary = result.get("summary", "")
            summary.themes = result.get("themes", [])
            summary.option_themes = result.get("option_themes", [])
            summary.model_name = result.get("model", "")
            summary.method = result.get("method", "")
            summary.reasons_total = result.get("reasons_total", 0)
            summary.reasons_processed = result.get("reasons_processed", 0)
            summary.finished_at = timezone.now()
            summary.error = ""
            summary.save()

            return "completed"

        except Exception as exc:
            summary.status = BallotSummary.Status.FAILED
            summary.error = str(exc)[:10000]
            summary.finished_at = timezone.now()
            summary.save()
            raise self.retry(exc=exc)

    finally:
        cache.delete(lock_key)
