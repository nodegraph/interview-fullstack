import pytest

from app.medication_details import enrich_mentions
from app.schemas import MedicationMention


def mention(note, name, start=0, **kwargs):
    offset = note.index(name, start)
    return MedicationMention(text=name, start=offset, end=offset + len(name), **kwargs)


@pytest.mark.parametrize(
    "note,name,expected,strength,dose_form",
    [
        ("metformn 500 mg tablets daily", "metformn", "metformn 500 mg tablets", "500 mg", "tablets"),
        ("amoxicillin capsules 250mg TID", "amoxicillin", "amoxicillin capsules 250mg", "250mg", "capsules"),
        ("Ventolin inhaler as needed", "Ventolin", "Ventolin inhaler", None, "inhaler"),
        ("insulin 100 units/mL injection", "insulin", "insulin 100 units/mL injection", "100 units/mL", "injection"),
        ("amoxicillin 250 mg/5 mL oral suspension", "amoxicillin", "amoxicillin 250 mg/5 mL oral suspension", "250 mg/5 mL", "oral suspension"),
        ("levothyroxine 75 mcg daily", "levothyroxine", "levothyroxine 75 mcg", "75 mcg", None),
        ("💊 levothyroxine 75 µg tablets", "levothyroxine", "levothyroxine 75 µg tablets", "75 µg", "tablets"),
        ("Café: folic acid 1 g daily", "folic acid", "folic acid 1 g", "1 g", None),
        ("ASA (81 mg tablets) daily", "ASA", "ASA (81 mg tablets)", "81 mg", "tablets"),
        ("vitamin D 1,000 IU daily", "vitamin D", "vitamin D 1,000 IU", "1,000 IU", None),
        ("hydrocortisone 1% cream", "hydrocortisone", "hydrocortisone 1% cream", "1%", "cream"),
        ("prednisone 5–10 mg daily", "prednisone", "prednisone 5–10 mg", "5–10 mg", None),
        ("Tylenol 2 tablets daily", "Tylenol", "Tylenol 2 tablets", None, "tablets"),
    ],
)
def test_enriches_literal_adjacent_details(note, name, expected, strength, dose_form):
    original = mention(note, name)
    result = enrich_mentions(note, [original])[0]

    assert result.name_text == name
    assert result.text == note[result.start:result.end] == expected
    assert result.strength == strength
    assert result.dose_form == dose_form
    assert result.product_rxcui is None
    assert result.matched is False  # Unresolved names still have source-observed details.
    assert original.text == name  # Response enrichment does not mutate input.


@pytest.mark.parametrize(
    "suffix",
    [
        " 500 daily",  # A number alone is not a dose strength.
        " daily 500 mg",
        " twice daily",
        "\n500 mg tablets",
        "\r\n500 mg tablets",
        ". Glucose 250 mg/dL",
        " glucose 250 mg/dL",
        " 20 magnesium",  # Do not match mg/g prefixes inside another word.
        " 20 mg/dL",  # Not a supported medication concentration.
        " 20 mg / dL",  # Spacing must not turn a lab unit into a dose.
        " (dose unknown)",
    ],
)
def test_does_not_extend_into_unrelated_text(suffix):
    note = "metformin" + suffix
    result = enrich_mentions(note, [mention(note, "metformin")])[0]

    assert result.text == result.name_text == "metformin"
    assert result.strength is None
    assert result.dose_form is None


def test_bounds_each_span_to_next_medication_and_preserves_repeated_occurrences():
    note = "💊 ASA 81 mg metformin 500 mg tablets, ASA 325 mg."
    result = enrich_mentions(
        note,
        [mention(note, "ASA", 4), mention(note, "metformin"), mention(note, "ASA")],
    )

    assert [item.text for item in result] == ["ASA 81 mg", "metformin 500 mg tablets", "ASA 325 mg"]
    assert all(note[item.start:item.end] == item.text for item in result)
    assert all(left.end <= right.start for left, right in zip(result, result[1:]))


def test_never_uses_next_mentions_name_as_dosage_form():
    note = "ASA tablet"
    result = enrich_mentions(note, [mention(note, "ASA"), mention(note, "tablet")])

    assert [item.text for item in result] == ["ASA", "tablet"]
    assert result[0].dose_form is None


def test_deduplicates_spans_and_is_idempotent_without_changing_resolution():
    note = "Continue metformn 500 mg tablets daily."
    original = mention(note, "metformn", correction="metformin", match_type="misspelling")
    first = enrich_mentions(note, [original, original])
    second = enrich_mentions(note, first)

    assert second == first
    assert len(second) == 1
    assert second[0].correction == "metformin"
    assert second[0].match_type == "misspelling"


def test_stops_after_first_strength_and_form():
    note = "metformin 500 mg tablets 1000 mg capsules"
    result = enrich_mentions(note, [mention(note, "metformin")])[0]

    assert result.text == "metformin 500 mg tablets"
