import re

from django.conf import settings


# ======================
# Regex patterns
# ======================

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

_URL_RE = re.compile(
    r"\b(?:https?://|www\.)[^\s<>\"']+",
    re.IGNORECASE,
)

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)

_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?"
    r"(?:\(\d{1,4}\)[\s.\-]?)?"
    r"\d{2,4}(?:[\s.\-]?\d{2,4}){2,4}"
    r"(?!\d)"
)

_CREDIT_CARD_RE = re.compile(
    r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)"
)

_ID_CONTEXT_RE = re.compile(
    r"\b(?:national\s+id|id\s+number|identity\s+card|identity\s+no|id\s+no|id)"
    r"[:#\-]?\s*"
    r"([A-Za-z0-9\-]{5,20})\b",
    re.IGNORECASE,
)

_ACCOUNT_CONTEXT_RE = re.compile(
    r"\b(?:bank\s+account|account\s+number|account\s+no|acc\s+no|acct\s+no|"
    r"m-?pesa\s+number|mobile\s+money\s+number)"
    r"[:#\-]?\s*"
    r"([A-Za-z0-9\-]{5,25})\b",
    re.IGNORECASE,
)

_DOB_RE = re.compile(
    r"\b(?:date\s+of\s+birth|birth\s+date|dob|born(?:\s+on)?)"
    r"[:#\-]?\s*"
    r"("
    r"\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}"
    r"|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2}"
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)


_ANALYZER = None


# ======================
# Settings helper
# ======================

def _setting(name: str, default=None):
    """
    Allows using either:

        PII_MODE

    or the older survey-specific:

        SURVEY_PII_MODE
    """

    if hasattr(settings, f"PII_{name}"):
        return getattr(settings, f"PII_{name}")

    if hasattr(settings, f"SURVEY_PII_{name}"):
        return getattr(settings, f"SURVEY_PII_{name}")

    return default


# ======================
# Public API
# ======================

def redact_text(text: str) -> dict:
    """
    Redact PII from text.

    Returns JSON-serializable dict:

    {
        "text": "redacted text",
        "entities": [
            {
                "type": "EMAIL_ADDRESS",
                "start": 10,
                "end": 25,
                "replacement": "[EMAIL]"
            }
        ]
    }

    Important:
    This intentionally does NOT store the original matched PII text.
    """

    if text is None:
        return {
            "text": "",
            "entities": [],
        }

    text = str(text)

    if not _setting("ENABLED", True):
        return {
            "text": text,
            "entities": [],
        }

    max_chars = int(_setting("MAX_INPUT_CHARS", 10000))

    if len(text) > max_chars:
        text = text[:max_chars]

    mode = str(_setting("MODE", "regex")).lower()

    if mode not in {"regex", "presidio", "hybrid", "llm"}:
        mode = "regex"

    if mode == "llm":
        return _llm_redact_text(text)

    entities = []

    if mode in {"regex", "hybrid", "presidio"}:
        entities.extend(_regex_entities(text))

    if mode in {"presidio", "hybrid"}:
        entities.extend(_presidio_entities(text))

    entities = _merge_entities(entities)
    redacted = _apply_entities(text, entities)

    return {
        "text": redacted,
        "entities": entities,
    }


# ======================
# Regex detectors
# ======================

def _entity(entity_type: str, start: int, end: int, replacement: str) -> dict:
    return {
        "type": entity_type,
        "start": start,
        "end": end,
        "replacement": replacement,
    }


def _regex_entities(text: str) -> list[dict]:
    entities = []

    if _setting("REDACT_EMAILS", True):
        for match in _EMAIL_RE.finditer(text):
            entities.append(
                _entity(
                    entity_type="EMAIL_ADDRESS",
                    start=match.start(),
                    end=match.end(),
                    replacement="[EMAIL]",
                )
            )

    if _setting("REDACT_URLS", True):
        for match in _URL_RE.finditer(text):
            entities.append(
                _entity(
                    entity_type="URL",
                    start=match.start(),
                    end=match.end(),
                    replacement="[URL]",
                )
            )

    if _setting("REDACT_IP_ADDRESSES", True):
        for match in _IPV4_RE.finditer(text):
            entities.append(
                _entity(
                    entity_type="IP_ADDRESS",
                    start=match.start(),
                    end=match.end(),
                    replacement="[IP_ADDRESS]",
                )
            )

    if _setting("REDACT_PHONE_NUMBERS", True):
        entities.extend(_phone_entities(text))

    if _setting("REDACT_CREDIT_CARDS", True):
        entities.extend(_credit_card_entities(text))

    if _setting("REDACT_ID_NUMBERS", True):
        entities.extend(
            _context_entities(
                text=text,
                pattern=_ID_CONTEXT_RE,
                entity_type="ID_NUMBER",
                replacement="[ID_NUMBER]",
                min_digits=5,
                max_digits=20,
            )
        )

    if _setting("REDACT_ACCOUNT_NUMBERS", True):
        entities.extend(
            _context_entities(
                text=text,
                pattern=_ACCOUNT_CONTEXT_RE,
                entity_type="ACCOUNT_NUMBER",
                replacement="[ACCOUNT_NUMBER]",
                min_digits=5,
                max_digits=25,
            )
        )

    if _setting("REDACT_DATES_OF_BIRTH", True):
        entities.extend(_dob_entities(text))

    return entities


def _phone_entities(text: str) -> list[dict]:
    entities = []

    for match in _PHONE_RE.finditer(text):
        candidate = match.group(0)

        # Avoid matching emails or URLs accidentally.
        if "@" in candidate or "://" in candidate:
            continue

        digits = re.sub(r"\D", "", candidate)

        if not 9 <= len(digits) <= 15:
            continue

        if _is_low_information_number(digits):
            continue

        entities.append(
            _entity(
                entity_type="PHONE_NUMBER",
                start=match.start(),
                end=match.end(),
                replacement="[PHONE_NUMBER]",
            )
        )

    return entities


def _credit_card_entities(text: str) -> list[dict]:
    entities = []

    for match in _CREDIT_CARD_RE.finditer(text):
        candidate = match.group(0)
        digits = re.sub(r"\D", "", candidate)

        if not 13 <= len(digits) <= 19:
            continue

        if not _luhn_valid(digits):
            continue

        entities.append(
            _entity(
                entity_type="CREDIT_CARD",
                start=match.start(),
                end=match.end(),
                replacement="[CREDIT_CARD]",
            )
        )

    return entities


def _context_entities(
    text: str,
    pattern: re.Pattern,
    entity_type: str,
    replacement: str,
    min_digits: int,
    max_digits: int,
) -> list[dict]:
    entities = []

    for match in pattern.finditer(text):
        try:
            value = match.group(1)
        except IndexError:
            continue

        digits = re.sub(r"\D", "", value)

        if not min_digits <= len(digits) <= max_digits:
            continue

        entities.append(
            _entity(
                entity_type=entity_type,
                start=match.start(1),
                end=match.end(1),
                replacement=replacement,
            )
        )

    return entities


def _dob_entities(text: str) -> list[dict]:
    entities = []

    for match in _DOB_RE.finditer(text):
        entities.append(
            _entity(
                entity_type="DATE_OF_BIRTH",
                start=match.start(1),
                end=match.end(1),
                replacement="[DATE_OF_BIRTH]",
            )
        )

    return entities


# ======================
# Presidio detector
# ======================

def _presidio_entities(text: str) -> list[dict]:
    """
    Optional stronger detection using Microsoft Presidio.

    Install:
        pip install presidio-analyzer presidio-anonymizer spacy
        python -m spacy download en_core_web_lg
    """

    try:
        from presidio_analyzer import AnalyzerEngine
    except ImportError:
        return []

    analyzer = _get_presidio_analyzer()

    entities = _presidio_entity_list()
    threshold = float(_setting("PRESIDIO_SCORE_THRESHOLD", 0.5))

    try:
        results = analyzer.analyze(
            text=text,
            language="en",
            entities=entities,
        )
    except Exception:
        return []

    found = []

    for result in results:
        if result.score < threshold:
            continue

        found.append(
            _entity(
                entity_type=result.entity_type,
                start=result.start,
                end=result.end,
                replacement=f"[{result.entity_type}]",
            )
        )

    return found


def _get_presidio_analyzer():
    global _ANALYZER

    if _ANALYZER is None:
        from presidio_analyzer import AnalyzerEngine

        _ANALYZER = AnalyzerEngine()

    return _ANALYZER


def _presidio_entity_list() -> list[str]:
    configured = _setting("PRESIDIO_ENTITIES", None)

    if configured:
        return list(configured)

    entities = {
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "IBAN_CODE",
    }

    if _setting("REDACT_PERSON", True):
        entities.add("PERSON")

    if _setting("REDACT_LOCATION", False):
        entities.add("LOCATION")

    return list(entities)


# ======================
# LLM redaction
# ======================

def _llm_redact_text(text: str) -> dict:
    """
    Expensive LLM-assisted redaction.

    Uses your existing chat_json() function and returns JSON.

    This is best used for:
    - sampled text
    - high-risk ballots
    - secondary review

    Not recommended for every reason at very large scale.
    """

    try:
        from apps.utils.llm import chat_json
    except ImportError:
        return _regex_fallback(text)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a privacy redaction system. "
                "Redact all personally identifiable information from the user text. "
                "Replace each PII span with a bracketed label such as "
                "[PERSON], [PHONE_NUMBER], [EMAIL], [ID_NUMBER], [ACCOUNT_NUMBER]. "
                "Preserve the non-PII meaning of the text. "
                "Return ONLY valid JSON. "
                "Do not return markdown. "
                "Use this exact schema: "
                '{"redacted_text": "string", "entities": [{"type": "string"}]}'
            ),
        },
        {
            "role": "user",
            "content": text,
        },
    ]

    try:
        data = chat_json(messages, temperature=0.0)
    except Exception:
        return _regex_fallback(text)

    redacted = str(data.get("redacted_text", "")).strip()

    entities = []

    raw_entities = data.get("entities", [])

    if isinstance(raw_entities, list):
        for item in raw_entities:
            if isinstance(item, dict):
                entity_type = str(item.get("type", "UNKNOWN")).upper()
            else:
                entity_type = str(item).upper()

            entities.append(
                {
                    "type": entity_type,
                    "replacement": f"[{entity_type}]",
                }
            )

    # Fallback if local LLM fails.
    if not redacted:
        return _regex_fallback(text)

    return {
        "text": redacted,
        "entities": entities,
    }


# ======================
# Helpers
# ======================

def _regex_fallback(text: str) -> dict:
    entities = _merge_entities(_regex_entities(text))
    redacted = _apply_entities(text, entities)

    return {
        "text": redacted,
        "entities": entities,
    }


def _merge_entities(entities: list[dict]) -> list[dict]:
    """
    Merge overlapping entities.

    Longer matches win.
    """

    if not entities:
        return []

    sorted_entities = sorted(
        entities,
        key=lambda item: (
            item["start"],
            -(item["end"] - item["start"]),
        ),
    )

    merged = []

    for entity in sorted_entities:
        if not merged:
            merged.append(entity)
            continue

        previous = merged[-1]

        # No overlap.
        if entity["start"] >= previous["end"]:
            merged.append(entity)
            continue

        # Overlap: keep the longer entity.
        entity_length = entity["end"] - entity["start"]
        previous_length = previous["end"] - previous["start"]

        if entity_length > previous_length:
            merged[-1] = entity

    return merged


def _apply_entities(text: str, entities: list[dict]) -> str:
    """
    Apply replacements from end to start so offsets remain valid.
    """

    redacted = text

    for entity in sorted(entities, key=lambda item: item["start"], reverse=True):
        redacted = (
            redacted[: entity["start"]]
            + entity["replacement"]
            + redacted[entity["end"]:]
        )

    return redacted


def _luhn_valid(digits: str) -> bool:
    """
    Luhn checksum for credit-card-like numbers.
    """

    if not digits.isdigit():
        return False

    total = 0
    reversed_digits = digits[::-1]

    for index, char in enumerate(reversed_digits):
        value = int(char)

        if index % 2 == 1:
            value *= 2

            if value > 9:
                value -= 9

        total += value

    return total % 10 == 0


def _is_low_information_number(digits: str) -> bool:
    """
    Avoid redacting obvious non-PII numbers such as 0000000000.
    """

    if not digits:
        return True

    unique_digits = set(digits)

    return len(unique_digits) <= 2