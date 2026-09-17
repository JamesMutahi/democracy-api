from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, OuterRef, Q, Max, Subquery

from apps.chat.models import ChatParticipant

User = get_user_model()


def annotate_chat_metrics(queryset, user):
    """
    Annotate chat queryset with counts, user-specific flags, and
    message-request status — all via ChatParticipant.
    """
    pending_request_exists = Exists(
        ChatParticipant.objects.filter(
            chat=OuterRef("pk"),
            user=user,
            status=ChatParticipant.Status.PENDING,
        )
    )

    return (
        queryset.filter(users=user)
        .select_related()
        .prefetch_related("users", "messages")
        .annotate(
            user_count=Count("users", distinct=True),
            latest_message_id=Max("messages__id"),

            # Unread count, but ignore messages that arrived while the
            # current user's participant row is still PENDING (i.e. they
            # haven't accepted the request yet).
            unread_messages_count=Count(
                "messages",
                filter=(
                        Q(messages__is_read=False)
                        & Q(messages__is_deleted=False)
                        & ~Q(messages__author=user)
                        & ~pending_request_exists
                ),
                distinct=True,
            ),

            # Is the current user looking at a chat where their own
            # participant row is still PENDING?
            is_message_request=pending_request_exists,

            # The actual status string of the current user's participation
            # in this chat (e.g., 'pending', 'accepted', 'declined')
            request_status=Subquery(
                ChatParticipant.objects.filter(
                    chat=OuterRef("pk"),
                    user=user,
                ).values("status")[:1]
            ),
        )
        .order_by("-latest_message_id")
    )


def search_chats_by_user(queryset, user, search_term: str):
    if not search_term:
        return queryset
    search_term = search_term.strip().lower()
    user_query = Q(username__icontains=search_term)
    if hasattr(User, "name"):
        user_query |= Q(name__icontains=search_term)
    other_user_match = (
        User.objects.filter(chats=OuterRef("pk"))
        .exclude(id=user.id)
        .filter(user_query)
    )
    return queryset.annotate(
        has_matching_user=Exists(other_user_match)
    ).filter(has_matching_user=True)
