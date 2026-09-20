import json
from types import SimpleNamespace

import httpx
import pytest

from app import medication_extraction as extraction


def mention(text, occurrence=1, suggested_name=None):
    return extraction.ExtractedMention(
        text=text, occurrence=occurrence, suggested_name=suggested_name
    )


def configure_provider(monkeypatch, **overrides):
    settings = {
        "llm_provider": "openai",
        "llm_model": "test-model",
        "llm_api_key": "test-key",
    }
    settings.update(overrides)
    monkeypatch.setattr(
        extraction, "get_settings", lambda: SimpleNamespace(**settings)
    )


def mock_completion(monkeypatch, content, status_code=200):
    requests = []

    def post(_client, url, **kwargs):
        requests.append({"url": url, **kwargs})
        return httpx.Response(
            status_code,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": content}}
                ]
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", post)
    return requests


def test_anchor_repeated_mentions_uses_requested_occurrence_and_unicode_offsets():
    note = "💊 Café: metformin, then metformin again."
    anchored = extraction.anchor_mentions(
        note, [mention("metformin", 2), mention("metformin", 1)]
    )

    assert len(anchored) == 2
    assert [item.start for item in anchored] == [
        note.index("metformin"),
        note.rindex("metformin"),
    ]
    for item in anchored:
        assert note[item.start : item.end] == item.text == "metformin"


def test_anchor_deduplicates_spans_and_prefers_longest_overlap():
    note = "Continue vitamin D3 daily."
    anchored = extraction.anchor_mentions(
        note,
        [mention("vitamin"), mention("vitamin D3"), mention("vitamin D3")],
    )

    assert len(anchored) == 1
    assert anchored[0].text == "vitamin D3"
    assert note[anchored[0].start : anchored[0].end] == "vitamin D3"


@pytest.mark.parametrize(
    "item",
    [
        mention("invented medication"),
        mention("Metformin"),  # Source text must preserve its original casing.
        mention("formin"),  # A substring of a drug name is not a whole mention.
    ],
)
def test_anchor_rejects_invented_or_invalid_source_occurrences(item):
    with pytest.raises(extraction.ExtractionError):
        extraction.anchor_mentions("Taking metformin daily.", [item])


def test_anchor_recovers_global_numbering_only_for_unique_source_text():
    note = "Advil and ibuprofen."
    result = extraction.anchor_mentions(note, [mention("Advil", 1), mention("ibuprofen", 2)])
    assert [(m.text, m.start) for m in result] == [("Advil", 0), ("ibuprofen", 10)]


def test_anchor_rejects_invalid_number_when_source_location_is_ambiguous():
    with pytest.raises(extraction.ExtractionError):
        extraction.anchor_mentions("metformin then metformin", [mention("metformin", 3)])


def test_anchor_keeps_context_selected_occurrence_instead_of_highlighting_a_score():
    note = "ASA physical status II. Takes ASA 81 mg."
    result = extraction.anchor_mentions(note, [mention("ASA", 2)])
    assert len(result) == 1
    assert result[0].start == note.rindex("ASA")


@pytest.mark.parametrize("score", ["ASA class II", "ASA physical status II", "ASA physical status class 2", "ASA score: 2"])
def test_anchor_filters_explicit_asa_scores_but_keeps_medication_use(score):
    note = f"{score}. Takes ASA 81 mg."
    result = extraction.anchor_mentions(note, [mention("ASA", 1), mention("ASA", 2)])
    assert len(result) == 1
    assert result[0].start == note.rindex("ASA")


def test_extract_requests_structured_output_and_parses_response(monkeypatch):
    configure_provider(monkeypatch)
    requests = mock_completion(
        monkeypatch,
        json.dumps(
            {
                "mentions": [
                    {
                        "text": "metformn",
                        "occurrence": 1,
                        "suggested_name": "metformin",
                    }
                ]
            }
        ),
    )

    result = extraction.extract_mentions("Continue metformn 500 mg.")

    assert result == [mention("metformn", suggested_name="metformin")]
    assert len(requests) == 1
    payload = requests[0]["json"]
    assert payload["model"] == "test-model"
    assert payload["response_format"]["type"] == "json_schema"
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert "mentions" in schema["schema"]["properties"]
    assert any(
        "Continue metformn 500 mg." in message["content"]
        for message in payload["messages"]
    )


def test_extract_accepts_successful_response_with_no_medication_mentions(monkeypatch):
    configure_provider(monkeypatch)
    mock_completion(monkeypatch, '{"mentions": []}')

    assert extraction.extract_mentions("Feeling well. No medication changes.") == []


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        None,  # A refusal or response without usable content must be an error.
        '{"mentions": "metformin"}',
        '{"mentions": [{"text": "metformin", "occurrence": 0, "suggested_name": null}]}',
        '{"mentions": [{"text": "metformin", "occurrence": 1}]}',
    ],
)
def test_extract_rejects_invalid_provider_output(monkeypatch, content):
    configure_provider(monkeypatch)
    mock_completion(monkeypatch, content)

    with pytest.raises(extraction.ExtractionError) as error:
        extraction.extract_mentions("Taking metformin.")

    assert error.value.status_code == 502


def test_extract_reports_missing_configuration(monkeypatch):
    configure_provider(monkeypatch, llm_api_key=None)

    with pytest.raises(extraction.ExtractionError) as error:
        extraction.extract_mentions("Taking metformin.")

    assert error.value.status_code == 503


@pytest.mark.parametrize("status_code", [401, 429, 500])
def test_extract_reports_provider_http_errors(monkeypatch, status_code):
    configure_provider(monkeypatch)
    mock_completion(monkeypatch, "provider error", status_code=status_code)

    with pytest.raises(extraction.ExtractionError) as error:
        extraction.extract_mentions("Taking metformin.")

    assert error.value.status_code == 502


def test_extract_reports_provider_network_errors(monkeypatch):
    configure_provider(monkeypatch)

    def post(_client, _url, **_kwargs):
        raise httpx.ConnectError("Provider unavailable")

    monkeypatch.setattr(httpx.Client, "post", post)
    with pytest.raises(extraction.ExtractionError) as error:
        extraction.extract_mentions("Taking metformin.")

    assert error.value.status_code == 502
