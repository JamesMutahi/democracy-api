import re
from typing import Optional, Tuple
from urllib.parse import ParseResult, parse_qs, urlparse

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Model
from urlextract import URLExtract

from apps.ballot.models import Ballot
from apps.broadcast.models import Broadcast
from apps.constitution.models import Section
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey


_url_extractor = URLExtract()

# Some URLExtract versions can explicitly extract localhost-style URLs.
# This is useful for development environments.
if hasattr(_url_extractor, "set_extract_localhost"):
    _url_extractor.set_extract_localhost(True)


_PATH_KEYWORD_MAP = {
    "post": Post,
    "posts": Post,
    "meeting": Broadcast,
    "meetings": Broadcast,
    "live-stream": Broadcast,
    "live-streams": Broadcast,
    "live_stream": Broadcast,
    "live_streams": Broadcast,
    "livestream": Broadcast,
    "livestreams": Broadcast,
    "broadcast": Broadcast,
    "broadcasts": Broadcast,
    "ballot": Ballot,
    "ballots": Ballot,
    "survey": Survey,
    "surveys": Survey,
    "petition": Petition,
    "petitions": Petition,
}

_LEADING_ID_RE = re.compile(r"^(?P<id>\d+)(?:$|[.\-_])", re.ASCII)
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", re.ASCII)
_PUNCTUATION_TO_STRIP = ".,;:!?\"'"


def extract_linked_object(text: str) -> Optional[Model]:
    """
    Extract the first supported internal object link from free text.

    Returns None when no supported object can be found instead of raising
    ObjectDoesNotExist.
    """
    if text is None:
        return None

    if isinstance(text, bytes):
        text = text.decode("utf-8", "ignore")
    elif not isinstance(text, str):
        text = str(text)

    if not text:
        return None

    try:
        candidates = _url_extractor.find_urls(text) or []
    except Exception:
        # URL extraction should never take down the surrounding feature.
        return None

    seen_urls = set()

    for candidate in candidates:
        url = _normalize_url(candidate)
        if not url or url in seen_urls:
            continue

        seen_urls.add(url)

        try:
            parsed = urlparse(url)
        except ValueError:
            continue

        if parsed.scheme not in {"http", "https"}:
            continue

        if not _host_is_allowed(_hostname(parsed)):
            continue

        linked_object = _object_from_parsed(parsed)
        if linked_object is not None:
            return linked_object

    return None


def _normalize_url(candidate: str) -> str:
    candidate = candidate.strip().strip(_PUNCTUATION_TO_STRIP)
    if not candidate:
        return ""

    # Handle protocol-relative URLs.
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif not _SCHEME_RE.match(candidate):
        candidate = f"https://{candidate}"

    return candidate


def _hostname(parsed: ParseResult) -> str:
    hostname = (parsed.hostname or "").lower().rstrip(".")

    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname


def _normalize_allowed_host(raw_host) -> Tuple[str, bool]:
    """
    Normalize a Django ALLOWED_HOSTS entry.

    Returns:
        (hostname, allow_subdomains)
    """
    if raw_host is None:
        return "", False

    host = str(raw_host).strip().lower()
    if not host:
        return "", False

    if host == "*":
        return "*", False

    if host.startswith("*."):
        host = host[2:]
        allow_subdomains = True
    else:
        allow_subdomains = host.startswith(".")
        host = host.lstrip(".")

    # If someone accidentally includes a scheme, reduce it to host/netloc.
    if "://" in host:
        host = urlparse(host).netloc

    # If userinfo is accidentally present, ignore it.
    if "@" in host:
        host = host.rsplit("@", 1)[1]

    parsed_host = urlparse(f"//{host}")
    hostname = (parsed_host.hostname or host or "").lower().rstrip(".")

    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname, allow_subdomains


def _host_is_allowed(hostname: str) -> bool:
    if not hostname:
        return False

    allowed_hosts = getattr(settings, "ALLOWED_HOSTS", None) or []

    for raw_host in allowed_hosts:
        pattern, allow_subdomains = _normalize_allowed_host(raw_host)

        if pattern == "*":
            return True

        if not pattern:
            continue

        if hostname == pattern:
            return True

        if allow_subdomains and hostname.endswith(f".{pattern}"):
            return True

    return False


def _is_ascii_integer(value: str) -> bool:
    return value.isascii() and value.isdigit()


def _safe_get(model, object_id):
    """
    Fetch an object by integer primary key without raising DoesNotExist,
    ValueError, OverflowError, or database conversion errors.
    """
    if object_id is None:
        return None

    object_id = str(object_id).strip()

    if not _is_ascii_integer(object_id):
        return None

    # Avoid needlessly huge integers reaching the database layer.
    if len(object_id) > 20:
        return None

    try:
        object_id = int(object_id)
    except (TypeError, ValueError, OverflowError):
        return None

    try:
        return model.objects.filter(pk=object_id).first()
    except (TypeError, ValueError, OverflowError, DatabaseError):
        return None


def _first_object_from_segments(model, segments):
    """
    Prefer explicit numeric path segments:

        /post/123
        /posts/123/
        /ballot/456/edit

    Also allow a leading numeric slug component:

        /post/123-some-slug
    """
    for segment in segments:
        if _is_ascii_integer(segment):
            obj = _safe_get(model, segment)
            if obj is not None:
                return obj

    for segment in segments:
        match = _LEADING_ID_RE.match(segment)
        if match:
            obj = _safe_get(model, match.group("id"))
            if obj is not None:
                return obj

    return None


def _object_from_parsed(parsed: ParseResult) -> Optional[Model]:
    path = (parsed.path or "").lower()
    segments = [segment for segment in path.split("/") if segment]
    query_ids = parse_qs(parsed.query).get("id", [])

    # Constitution links may carry the object id in the query string:
    #
    #   https://example.com/constitution?id=123
    #
    # Also support path-style constitution links:
    #
    #   https://example.com/constitution/123
    if "constitution" in segments:
        for query_id in query_ids:
            obj = _safe_get(Section, query_id)
            if obj is not None:
                return obj

        constitution_index = segments.index("constitution")
        obj = _first_object_from_segments(
            Section,
            segments[constitution_index + 1:],
        )
        if obj is not None:
            return obj

    # Other object types are primarily identified by a path segment:
    #
    #   /post/123
    #   /meetings/45
    #   /ballots/67
    for index, segment in enumerate(segments):
        model = _PATH_KEYWORD_MAP.get(segment)
        if model is None:
            continue

        obj = _first_object_from_segments(model, segments[index + 1:])
        if obj is not None:
            return obj

        # Fallback for query-string ids when the path already identifies
        # the object type:
        #
        #   /post?id=123
        for query_id in query_ids:
            obj = _safe_get(model, query_id)
            if obj is not None:
                return obj

    return None