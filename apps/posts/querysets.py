from django.db.models import (
    BooleanField,
    Case,
    Count,
    Q,
    Value,
    When, Subquery, TextField, ExpressionWrapper, F, FloatField, IntegerField, OuterRef,
)
from django.db.models.functions import Coalesce, NullIf

from apps.posts.models import Post


def top_community_note_body_subquery():
    """
    Returns the body of the top community note for a post.

    Community notes are Post objects where:
    community_note_of = parent post
    """
    top_note = (
        Post.objects.filter(
            community_note_of=OuterRef("pk"),
        )
        # Recommended visibility filters.
        # Remove any of these if your product rules differ.
        .filter(
            is_deleted=False,
            is_active=True,
            status="published",
        )
        .annotate(
            note_upvotes_count=Count("upvotes", distinct=True),
            note_downvotes_count=Count("downvotes", distinct=True),
        )
        .annotate(
            total_votes=ExpressionWrapper(
                F("note_upvotes_count") + F("note_downvotes_count"),
                output_field=IntegerField(),
            )
        )
        .annotate(
            helpful_score=ExpressionWrapper(
                F("note_upvotes_count") * 1.0 / NullIf(F("total_votes"), 0),
                output_field=FloatField(),
            )
        )
        .filter(
            total_votes__gt=0,
            helpful_score__gt=0.7,
        )
        .order_by(
            "-helpful_score",
            "-note_upvotes_count",
            "-note_downvotes_count",
            "-published_at",
        )
        .values("body")[:1]
    )

    return Coalesce(
        Subquery(top_note, output_field=TextField()),
        Value("", output_field=TextField()),
    )


def annotate_post_metrics(queryset, user, include_top_community_note=True):
    """
    Annotate post queryset with counts and user-specific flags.
    """
    qs = queryset.select_related(
        "author",
        "ballot",
        "survey",
        "petition",
        "broadcast",
        "section",
        "reply_to",
        "repost_of",
        "community_note_of",
    ).prefetch_related(
        "tagged_users",
        "hashtags",
        "assets",
    ).annotate(
        # Public counts
        likes_count=Count(
            "likes",
            distinct=True,
        ),
        bookmarks_count=Count(
            "bookmarks",
            distinct=True,
        ),
        replies_count=Count(
            "replies",
            filter=Q(
                replies__is_active=True,
                replies__status="published",
            ),
            distinct=True,
        ),
        reposts_count=Count(
            "reposts",
            filter=Q(
                reposts__is_active=True,
            ),
            distinct=True,
        ),
        upvotes_count=Count(
            "upvotes",
            filter=Q(
                community_note_of__isnull=False,
            ),
            distinct=True,
        ),
        downvotes_count=Count(
            "downvotes",
            filter=Q(
                community_note_of__isnull=False,
            ),
            distinct=True,
        ),

        # Current-user-specific counts
        liked_count=Count(
            "likes",
            filter=Q(
                likes=user,
            ),
            distinct=True,
        ),
        bookmarked_count=Count(
            "bookmarks",
            filter=Q(
                bookmarks=user,
            ),
            distinct=True,
        ),
        reposted_count=Count(
            "reposts",
            filter=Q(
                reposts__author=user,
                reposts__is_active=True,
                reposts__repost_type=Post.RepostType.REPOST,
            ),
            distinct=True,
        ),
        quoted_count=Count(
            "reposts",
            filter=Q(
                reposts__author=user,
                reposts__is_active=True,
                reposts__repost_type=Post.RepostType.QUOTE,
            ),
            distinct=True,
        ),
        upvoted_count=Count(
            "upvotes",
            filter=Q(
                upvotes=user,
            ),
            distinct=True,
        ),
        downvoted_count=Count(
            "downvotes",
            filter=Q(
                downvotes=user,
            ),
            distinct=True,
        ),
    ).annotate(
        # Boolean flags derived from the current-user-specific counts
        is_liked=Case(
            When(liked_count__gt=0, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_bookmarked=Case(
            When(bookmarked_count__gt=0, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_reposted=Case(
            When(reposted_count__gt=0, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_quoted=Case(
            When(quoted_count__gt=0, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_upvoted=Case(
            When(upvoted_count__gt=0, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
        is_downvoted=Case(
            When(downvoted_count__gt=0, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )

    if include_top_community_note:
        qs = qs.annotate(
            top_community_note_body=top_community_note_body_subquery(),
        )

    return qs
