"""Preserve nearby, explicitly written dosage details in medication highlights.

This only describes source text. An ingredient match does not establish a
particular prescribed product, dose, route, or active medication regimen.
"""

import re

from app.schemas import MedicationMention


_SPACE = r"[ \t]*"
_NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
_UNIT = r"(?:mcg|[µμ]g|mg|g|mL|L|IU|units?|mEq|mmol|%)"
_STRENGTH = re.compile(
    rf"(?P<strength>{_NUMBER}(?:{_SPACE}[-–]{_SPACE}{_NUMBER})?"
    rf"{_SPACE}{_UNIT}"
    rf"(?:{_SPACE}/{_SPACE}(?:{_NUMBER}{_SPACE})?{_UNIT})?)(?!\w|{_SPACE}/)",
    re.IGNORECASE,
)
_FORM = re.compile(
    rf"(?:{_NUMBER}[ \t]+)?"
    r"(?P<dose_form>(?:(?:oral|chewable|extended-release|delayed-release|"
    r"topical|nasal|ophthalmic|otic)[ \t]+)?"
    r"(?:tablets?|capsules?|tabs?|caps?|inhalers?|solutions?|suspensions?|"
    r"creams?|ointments?|gels?|patch(?:es)?|drops?|sprays?|"
    r"injections?|suppositor(?:y|ies)))(?!\w)",
    re.IGNORECASE,
)
_HORIZONTAL_SPACE = re.compile(_SPACE)


def enrich_mentions(
    note: str, mentions: list[MedicationMention]
) -> list[MedicationMention]:
    """Expand source spans over one adjacent strength and/or dosage form.

    Matching must run first using the name alone. All offsets remain Python
    Unicode character offsets, and every populated detail is copied literally
    from the note. Stop at line breaks, the next mention, or unrelated text;
    unitless numbers and administration frequencies are not dosage strengths.
    """
    # Extraction already deduplicates spans, but keeping this helper idempotent
    # also makes it safe to reuse when a response is enriched more than once.
    unique = {(mention.start, mention.end): mention for mention in mentions}
    ordered = sorted(unique.values(), key=lambda mention: mention.start)
    enriched = []
    for index, mention in enumerate(ordered):
        name_text = mention.name_text or mention.text
        name_end = mention.start + len(name_text)
        if note[mention.start:name_end] != name_text:
            enriched.append(mention)
            continue

        limit = ordered[index + 1].start if index + 1 < len(ordered) else len(note)
        # A short local suffix keeps incidental later numbers out of scope.
        suffix = note[name_end:min(limit, name_end + 100)]
        suffix = re.split(r"[\r\n]", suffix, maxsplit=1)[0]
        position = _HORIZONTAL_SPACE.match(suffix).end()
        parenthesized = suffix[position:position + 1] == "("
        if parenthesized:
            position = _HORIZONTAL_SPACE.match(suffix, position + 1).end()

        details: dict[str, str] = {}
        suffix_end = 0
        for _ in range(2):
            found = None
            for field, pattern in (("strength", _STRENGTH), ("dose_form", _FORM)):
                if field not in details:
                    found = pattern.match(suffix, position)
                    if found:
                        details[field] = found.group(field)
                        break
            if found is None:
                break
            suffix_end = found.end()
            position = _HORIZONTAL_SPACE.match(suffix, suffix_end).end()

        if parenthesized and details and suffix[position:position + 1] == ")":
            suffix_end = position + 1
        end = name_end + suffix_end if details else name_end
        enriched.append(
            mention.model_copy(
                update={
                    "name_text": name_text,
                    "text": note[mention.start:end],
                    "end": end,
                    "strength": details.get("strength"),
                    "dose_form": details.get("dose_form"),
                }
            )
        )
    return enriched
