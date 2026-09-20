"""Answer-key acceptance checks; detection is mocked, anchoring/matching are real.

These tests do not measure the LLM's recall. A live-provider smoke test remains
necessary to verify extraction of these same 7 and 12 source occurrences.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.medication_extraction import ExtractedMention
from app.models import Medication
from app.routers import analysis
from app.seed import MEDICATIONS


TRANSCRIPTS = Path(__file__).resolve().parents[2] / "test-transcripts"
EXTRA_CONCEPTS = {
    "6851": "methotrexate",
    "11253": "vitamin D",
    "4511": "folic acid",
}


@pytest.mark.parametrize(
    "filename,expected",
    [
        (
            "transcript-01.txt",
            [
                ("lisinopril", "29046", "exact", None),
                ("metformin", "6809", "exact", None),
                ("Lipitor", "83367", "brand", None),
                ("Norvasc", "17767", "brand", None),
                ("HCTZ", "5487", "shorthand", None),
                ("ASA", "1191", "shorthand", None),
                ("hydrochlorothiazde", "5487", "misspelling", "hydrochlorothiazide"),
            ],
        ),
        (
            "transcript-02.txt",
            [
                ("Coumadin", "11289", "brand", None),
                ("metopralol", "6918", "misspelling", "metoprolol"),
                ("omeprazole", "7646", "exact", None),
                ("gabapentine", "25480", "misspelling", "gabapentin"),
                ("sertralin", "36437", "misspelling", "sertraline"),
                ("Ventolin", "435", "brand", None),
                ("MTX", "6851", "shorthand", None),
                ("vitamin D", "11253", "exact", None),
                ("folic acid", "4511", "exact", None),
                ("Tylenol", "161", "brand", None),
                ("warfarin", "11289", "exact", None),
                ("levothyroxin", "10582", "misspelling", "levothyroxine"),
            ],
        ),
    ],
    ids=["seven-mentions", "twelve-mentions-with-supplements"],
)
def test_transcript_answer_keys_with_seed_and_expanded_catalog(
    monkeypatch, filename, expected
):
    note = (TRANSCRIPTS / filename).read_text()
    extracted = [
        ExtractedMention(text=text, occurrence=1, suggested_name=correction)
        for text, _rxcui, _kind, correction in expected
    ]
    monkeypatch.setattr(analysis, "extract_mentions", lambda _note: extracted)

    engine = create_engine("sqlite://")
    Medication.__table__.create(engine)
    try:
        with Session(engine) as db:
            db.add_all(
                Medication(
                    rxcui=rxcui,
                    name=name,
                    tty=tty,
                    brand_names=brands,
                    drug_class=drug_class,
                )
                for rxcui, name, tty, brands, drug_class in MEDICATIONS
            )
            db.commit()

            seeded = analysis.analyze_note(db, note)
            assert len(seeded) == len(expected)
            for result, (_text, rxcui, _kind, _correction) in zip(seeded, expected):
                assert result.matched is (rxcui not in EXTRA_CONCEPTS)
                if rxcui in EXTRA_CONCEPTS:
                    assert result.medication is None
                    assert result.correction is None

            # These three ingredient IDs come from the supplied answer key.
            # Adding them represents the concepts obtained by catalog import.
            db.add_all(
                Medication(rxcui=rxcui, name=name, tty="IN")
                for rxcui, name in EXTRA_CONCEPTS.items()
            )
            db.commit()
            results = analysis.analyze_note(db, note)

            assert len(results) == len(expected)
            for result, (text, rxcui, kind, correction) in zip(results, expected):
                assert result.name_text == text
                assert note[result.start : result.end] == result.text
                assert result.text.startswith(text)
                assert result.matched is True
                assert result.medication.rxcui == rxcui
                assert result.match_type == kind
                assert result.correction == correction
            by_name = {result.name_text: result for result in results}
            if filename == "transcript-01.txt":
                assert by_name["lisinopril"].text == "lisinopril 20 mg"
                assert by_name["metformin"].strength == "1000 mg"
                assert by_name["ASA"].text == "ASA 81 mg"
            else:
                assert by_name["Ventolin"].text == "Ventolin inhaler"
                assert by_name["Ventolin"].dose_form == "inhaler"
    finally:
        engine.dispose()
