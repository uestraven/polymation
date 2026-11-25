"""Generation of slugs and stable project identifiers."""

from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import datetime

from polymation.errors import InvalidProjectIdError
from polymation.schemas.base import utc_now
from polymation.schemas.project import validate_project_id

#: Maximum slug length, matching the schema's slug grammar.
MAX_SLUG_LENGTH = 64

#: Maximum characters of slug retained *inside* a generated project identifier.
#: Shorter than a standalone slug, to leave room for the date prefix and random
#: suffix within the 64-character identifier limit.
ID_SLUG_LENGTH = 40

#: Length of the random suffix that disambiguates two projects created on the
#: same day with the same title.
SUFFIX_LENGTH = 6

_NON_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")


def slugify(title: str, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Return a filesystem- and URL-safe slug derived from ``title``.

    Raises:
        InvalidProjectIdError: if ``title`` contains no slug-able characters.
    """
    normalised = unicodedata.normalize("NFKD", title)
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    collapsed = _NON_SLUG_CHARACTERS.sub("-", ascii_only).strip("-")

    if len(collapsed) > max_length:
        collapsed = collapsed[:max_length].rstrip("-")

    if len(collapsed) < 3:
        # Pad short-but-real slugs ("ff7" is fine; "" and "x" are not usable as
        # a directory name under our identifier grammar).
        if not collapsed:
            raise InvalidProjectIdError(
                f"Title {title!r} contains no characters usable in a slug. Provide a "
                f"title with at least three ASCII letters or digits, or pass an "
                f"explicit --project-id."
            )
        collapsed = f"{collapsed}-video"[:max_length].rstrip("-")

    return collapsed


def generate_project_id(
    title: str,
    *,
    now: datetime | None = None,
    suffix: str | None = None,
) -> str:
    """Return a new, stable project identifier for ``title``.

    The identifier embeds the creation date and the title slug for human
    readability, plus a random suffix so two projects created on the same day
    from the same title do not collide. It is assigned once and persisted: a
    later title change never alters it.
    """
    timestamp = now or utc_now()
    slug = slugify(title, max_length=ID_SLUG_LENGTH)
    random_suffix = suffix if suffix is not None else secrets.token_hex(SUFFIX_LENGTH // 2)

    candidate = f"{timestamp.strftime('%Y%m%d')}-{slug}-{random_suffix}"
    return validate_project_id(candidate)
