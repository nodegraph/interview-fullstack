"""Importer resilience checks use mocked HTTP and the isolated test database."""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

from app import rxnorm_bulk as bulk
from app.database import SessionLocal
from app.models import CatalogImportJob, Medication


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


def client_for(handler):
    clock = FakeClock()
    limiter = bulk._RateLimiter(rate=10, clock=clock.monotonic, sleep=clock.sleep)
    transport = httpx.Client(transport=httpx.MockTransport(handler))
    return bulk._RxNavClient(transport, limiter=limiter), clock


@pytest.fixture
def no_import(client):
    with SessionLocal() as db:
        db.query(CatalogImportJob).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(CatalogImportJob).delete()
        db.commit()


def test_retry_429_respects_retry_after_and_shared_pacing():
    requests = []
    responses = [httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={})]
    client, clock = client_for(lambda request: requests.append(clock.now) or responses.pop(0))
    assert client.get("https://rxnav.test/example").status_code == 200
    assert requests == [100.0, 103.0]
    client.limiter.wait()
    assert clock.now == pytest.approx(103.1)


def test_retries_are_bounded_and_nonretryable_errors_fail_immediately():
    for status, expected in [(503, 3), (400, 1)]:
        requests = []
        client, _ = client_for(lambda request: requests.append(request) or httpx.Response(status))
        with pytest.raises(httpx.HTTPStatusError):
            client.get("https://rxnav.test/example")
        assert len(requests) == expected


def test_transport_failure_retried_and_long_retry_after_not_ignored():
    attempts = []
    def fail(request):
        attempts.append(request)
        raise httpx.ConnectError("unavailable", request=request)
    client, _ = client_for(fail)
    with pytest.raises(httpx.ConnectError):
        client.get("https://rxnav.test/example")
    assert len(attempts) == 3
    requests = []
    client, _ = client_for(lambda request: requests.append(request) or httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(httpx.HTTPStatusError):
        client.get("https://rxnav.test/example")
    assert len(requests) == 1


def test_retry_after_supports_http_dates(monkeypatch):
    monkeypatch.setattr(bulk.time, "time", lambda: 1000)
    date = format_datetime(datetime.fromtimestamp(1010, timezone.utc), usegmt=True)
    assert bulk._retry_delay(httpx.Response(429, headers={"Retry-After": date}), 0) == 10


def test_rate_limit_applies_across_concurrent_workers():
    clock = FakeClock()
    limiter = bulk._RateLimiter(rate=10, clock=clock.monotonic, sleep=clock.sleep)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: limiter.wait(), range(20)))
    assert clock.now == pytest.approx(101.9)
    assert len(clock.sleeps) == 19


@pytest.mark.parametrize("payload", [{}, {"minConceptGroup": {}}, {"minConceptGroup": {"minConcept": []}},
    {"minConceptGroup": {"minConcept": {}}}, {"minConceptGroup": {"minConcept": [{"name": "bad"}]}}])
def test_empty_or_malformed_ingredient_payload_rejected(payload):
    with pytest.raises(ValueError):
        bulk._concepts_from(payload)


def test_brand_failure_visible_and_combination_brands_excluded():
    payloads = {
        "/allconcepts.json": {"minConceptGroup": {"minConcept": [
            {"rxcui": "1", "name": "Single"}, {"rxcui": "2", "name": "Combo"}, {"rxcui": "3", "name": "Broken"}]}},
        "/rxcui/1/related.json": {"relatedGroup": {"conceptGroup": [{"conceptProperties": [{"rxcui": "11"}]}]}},
        "/rxcui/2/related.json": {"relatedGroup": {"conceptGroup": [{"conceptProperties": [{"rxcui": "11"}, {"rxcui": "22"}]}]}},
        "/rxcui/3/related.json": {"unexpected": {}},
    }
    client, _ = client_for(lambda request: httpx.Response(200, json=payloads[request.url.path]))
    run = bulk._Run("test", SessionLocal, bulk.ImportJob())
    client.run = run
    result = bulk._fetch_brand_map(client, "https://rxnav.test")
    assert result == {"11": ["Single"]}
    assert run.job.warning_count == 1
    assert "Broken" in run.job.warnings[0]


def test_warning_list_capped_without_losing_failure_count():
    run = bulk._Run("test", SessionLocal, bulk.ImportJob())
    for index in range(30):
        run.warn(str(index))
    assert run.job.warning_count == 30
    assert len(run.job.warnings) == bulk._MAX_WARNINGS


def test_names_only_import_preserves_enrichment(clean_catalog, no_import, monkeypatch):
    monkeypatch.setattr(bulk, "_fetch_ingredients", lambda *args: [{"rxcui": "6809", "name": "metformin", "tty": "IN"}])
    with SessionLocal() as db:
        before = db.get(Medication, "6809")
        expected = before.brand_names, before.drug_class, before.synonym
        job = bulk.run_import(db, include_classes=False, include_brands=False)
        db.expire_all()
        after = db.get(Medication, "6809")
        assert (after.brand_names, after.drug_class, after.synonym) == expected
    assert job.state == "done"
    assert job.warning_count == 0


def test_partial_enrichment_keeps_existing_aliases_and_class(clean_catalog, no_import, monkeypatch):
    monkeypatch.setattr(bulk, "_fetch_ingredients", lambda *args: [{"rxcui": "6809", "name": "metformin", "tty": "IN"}])
    def brands(client, base):
        client.warn("One brand request failed")
        return {"6809": ["NewBrand"]}
    monkeypatch.setattr(bulk, "_fetch_brand_map", brands)
    monkeypatch.setattr(bulk, "_fetch_class_map", lambda *args: {"6809": "Potentially less specific"})
    with SessionLocal() as db:
        before = db.get(Medication, "6809")
        old_brands, old_class = before.brand_names.split(","), before.drug_class
        job = bulk.run_import(db)
        db.expire_all()
        after = db.get(Medication, "6809")
        assert set(old_brands + ["NewBrand"]) == set(after.brand_names.split(","))
        assert after.drug_class == old_class
    assert job.state == "done" and job.warning_count == 1
    assert bulk.get_job().warnings == ["One brand request failed"]


def test_empty_upstream_fails_without_catalog_changes(clean_catalog, no_import, monkeypatch):
    monkeypatch.setattr(bulk, "_fetch_ingredients", lambda *args: [])
    with SessionLocal() as db:
        before = db.query(Medication).count()
        job = bulk.run_import(db)
        assert db.query(Medication).count() == before
    assert job.state == "error"
    assert "no ingredient concepts" in job.error


def test_stale_job_reported_and_reclaimed_atomically(no_import):
    first = bulk._claim(SessionLocal)
    assert first is not None
    assert bulk._claim(SessionLocal) is None
    with SessionLocal() as db:
        row = db.get(CatalogImportJob, 1)
        row.lease_expires_at = time.time() - 1
        db.commit()
    assert bulk.get_job().state == "error"
    assert "interrupted" in bulk.get_job().error
    replacement = bulk._claim(SessionLocal)
    assert replacement and replacement.owner != first.owner
    with pytest.raises(bulk.LeaseLost):
        bulk._persist(first)
    assert bulk.get_job().state == "running"


def test_concurrent_claims_only_one_worker_wins(no_import):
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: bulk._claim(SessionLocal), range(4)))
    assert sum(run is not None for run in claimed) == 1


def test_lost_owner_cannot_write_catalog(no_import):
    first = bulk._claim(SessionLocal)
    with SessionLocal() as db:
        row = db.get(CatalogImportJob, 1)
        row.lease_expires_at = 0
        db.commit()
    assert bulk._claim(SessionLocal) is not None
    bulk._context.run = first
    try:
        with SessionLocal() as db:
            with pytest.raises(bulk.LeaseLost):
                bulk._write(db, [{"rxcui": "999000", "name": "stale worker", "tty": "IN", "brand_names": None, "drug_class": None}])
            assert db.get(Medication, "999000") is None
    finally:
        del bulk._context.run


def test_invalid_import_limit_rejected(client):
    assert client.post("/api/medications/import", json={"limit": 0}).status_code == 422
    assert client.post("/api/medications/import", json={"limit": -1}).status_code == 422
