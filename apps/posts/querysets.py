from django.db.models import (
    BooleanField,
    Case,
    Count,
    Q,
    Value,
    When,
)

from apps.posts.models import Post


def annotate_post_metrics(queryset, user):
    """
    Annotate post queryset with counts and user-specific flags.
    """
    if not user or not user.is_authenticated:
        raise ValueError("annotate_post_metrics expects an authenticated user.")

    return queryset.select_related(
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