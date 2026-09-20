"""Opt-in live checks: only bundled exercise fixtures are transmitted to OpenAI.

RUN_LIVE_ANALYSIS=1 .venv/bin/pytest tests/test_live_analysis.py -v -s
Normal test runs never call external services. No application DB is modified.
"""

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Medication
from app.routers.analysis import analyze_note
from app.seed import MEDICATIONS

pytestmark = pytest.mark.skipif(
    os.environ.get('RUN_LIVE_ANALYSIS') != '1',
    reason='Live provider checks require explicit RUN_LIVE_ANALYSIS=1 and LLM_API_KEY',
)

CASES = [
    ('transcript-01.txt', [
        ('lisinopril', '29046'), ('metformin', '6809'), ('Lipitor', '83367'),
        ('Norvasc', '17767'), ('HCTZ', '5487'), ('ASA', '1191'),
        ('hydrochlorothiazde', '5487'),
    ]),
    ('transcript-02.txt', [
        ('Coumadin', '11289'), ('metopralol', '6918'), ('omeprazole', '7646'),
        ('gabapentine', '25480'), ('sertralin', '36437'), ('Ventolin', '435'),
        ('MTX', '6851'), ('vitamin D', '11253'), ('folic acid', '4511'),
        ('Tylenol', '161'), ('warfarin', '11289'), ('levothyroxin', '10582'),
    ]),
]
CORRECTIONS = {
    'hydrochlorothiazde': 'hydrochlorothiazide', 'metopralol': 'metoprolol',
    'gabapentine': 'gabapentin', 'sertralin': 'sertraline',
    'levothyroxin': 'levothyroxine',
}


@pytest.fixture(scope='module')
def live_catalog():
    settings = get_settings()
    assert settings.llm_api_key, 'Set LLM_API_KEY in backend/.env'
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.rxnav_base_url.rstrip('/') + '/allconcepts.json', params={'tty': 'IN'})
        response.raise_for_status()
        concepts = response.json()['minConceptGroup']['minConcept']
    assert len(concepts) > 1000
    seed = {rxcui: (brands, drug_class) for rxcui, _name, _tty, brands, drug_class in MEDICATIONS}
    engine = create_engine('sqlite://')
    Medication.__table__.create(engine)
    with Session(engine) as db:
        db.add_all(Medication(
            rxcui=c['rxcui'], name=c['name'], tty='IN',
            brand_names=seed.get(c['rxcui'], (None, None))[0],
            drug_class=seed.get(c['rxcui'], (None, None))[1],
        ) for c in concepts)
        db.commit()
    print(f'Live model: {settings.llm_model}; ingredient concepts: {len(concepts)}; seed brand aliases only')
    yield engine
    engine.dispose()


@pytest.mark.parametrize('filename,expected', CASES)
def test_live_sample_analysis(live_catalog, filename, expected):
    note = (Path(__file__).resolve().parents[2] / 'test-transcripts' / filename).read_text()
    with Session(live_catalog) as db:
        mentions = analyze_note(db, note)
    assert [(m.name_text, m.medication.rxcui if m.medication else None) for m in mentions] == expected
    for mention in mentions:
        assert note[mention.start:mention.end] == mention.text
        assert mention.correction == CORRECTIONS.get(mention.name_text)
    by_name = {m.name_text: m for m in mentions}
    if filename == 'transcript-01.txt':
        assert by_name['ASA'].strength == '81 mg'
        assert by_name['lisinopril'].text == 'lisinopril 20 mg'
        assert by_name['metformin'].text == 'metformin 1000 mg'
    else:
        assert by_name['Ventolin'].text == 'Ventolin inhaler'
        assert by_name['Ventolin'].dose_form == 'inhaler'
    print(f'{filename}: {len(mentions)}/{len(expected)} mentions, RxCUIs, corrections, and dosage spans passed')
