def test_health_ok(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "message": "Database connected",
    }


def test_seed_then_list_clinicians(clean_catalog, client):
    # clean_catalog resets the medications table: seed no longer wipes it, so
    # the count below would otherwise depend on what earlier tests imported.
    response = client.post("/api/seed")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["counts"] == {
        "clinicians": 3,
        "patients": 8,
        "visits": 12,
        "medications": 20,
    }

    listed = client.get("/api/clinicians")
    assert listed.status_code == 200
    names = {row["name"] for row in listed.json()["clinicians"]}
    assert names == {
        "Dr. Sarah Chen",
        "Dr. Michael Roberts",
        "Dr. Emily Watson",
    }


def test_patients_filtered_by_clinician(client):
    client.post("/api/seed")
    clinicians = client.get("/api/clinicians").json()["clinicians"]
    chen = next(row for row in clinicians if row["name"] == "Dr. Sarah Chen")

    response = client.get("/api/patients", params={"clinicianId": chen["id"]})
    assert response.status_code == 200
    names = {row["name"] for row in response.json()["patients"]}
    assert "John Smith" in names
    assert "Patricia Brown" not in names


def test_unknown_patient_404(client):
    response = client.get("/api/patients/not-a-real-id")
    assert response.status_code == 404
    assert response.json() == {"error": "Patient not found"}


def test_update_visit(client):
    client.post("/api/seed")
    patients = client.get("/api/patients").json()["patients"]
    smith = next(row for row in patients if row["name"] == "John Smith")
    visits = client.get(f"/api/patients/{smith['id']}/visits").json()["visits"]
    visit_id = visits[0]["id"]

    updated = client.put(
        f"/api/visits/{visit_id}",
        json={"chief_complaint": "Updated complaint", "notes": "Updated notes"},
    )
    assert updated.status_code == 200
    assert updated.json()["visit"]["chief_complaint"] == "Updated complaint"
    assert updated.json()["visit"]["notes"] == "Updated notes"

    fetched = client.get(f"/api/visits/{visit_id}")
    assert fetched.status_code == 200
    visit = fetched.json()["visit"]
    assert visit["chief_complaint"] == "Updated complaint"
    assert visit["notes"] == "Updated notes"
    assert visit["patient_name"] == "John Smith"


def test_list_medications_is_paginated_and_searchable(clean_catalog, client):
    response = client.get("/api/medications")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 20
    by_name = {row["name"]: row for row in body["medications"]}
    assert by_name["metformin"]["rxcui"] == "6809"
    assert "Glucophage" in by_name["metformin"]["brand_names"]

    # Paging.
    page = client.get("/api/medications", params={"limit": 5, "offset": 5}).json()
    assert page["total"] == 20
    assert len(page["medications"]) == 5
    assert page["offset"] == 5

    # Search matches the normalized name...
    hit = client.get("/api/medications", params={"search": "metfor"}).json()
    assert [row["name"] for row in hit["medications"]] == ["metformin"]

    # ...and brand names.
    brand = client.get("/api/medications", params={"search": "glucophage"}).json()
    assert [row["name"] for row in brand["medications"]] == ["metformin"]

    assert client.get("/api/medications", params={"search": "zzzz"}).json()["total"] == 0


def test_rxnorm_lookup_catalog(clean_catalog, client):
    # Exact normalized name.
    by_name = client.get("/api/rxnorm/lookup", params={"name": "metformin"})
    assert by_name.status_code == 200
    assert by_name.json()["medication"]["rxcui"] == "6809"

    # Brand name resolves to the same concept, case-insensitively.
    by_brand = client.get("/api/rxnorm/lookup", params={"name": "glucophage"})
    assert by_brand.status_code == 200
    assert by_brand.json()["medication"]["name"] == "metformin"


def analysis_visit(client, note):
    patients = client.get("/api/patients").json()["patients"]
    smith = next(row for row in patients if row["name"] == "John Smith")
    visits = client.get(f"/api/patients/{smith['id']}/visits").json()["visits"]
    annual = next(v for v in visits if v["visit_date"] == "2026-01-15")
    response = client.put(f"/api/visits/{annual['id']}", json={"notes": note})
    assert response.status_code == 200
    return annual["id"]


def test_analyze_resolves_catalog_and_preserves_occurrences(clean_catalog, client, monkeypatch):
    """Only detection is mocked: the endpoint resolves real seeded catalog rows."""
    from app.medication_extraction import ExtractedMention
    from app.routers import analysis as analysis_router

    note = "💊 Continue metformn. Previously Glucophage. Hold HCTZ. Restart metformn."
    detected = [
        ExtractedMention(text="metformn", occurrence=1, suggested_name="metformin"),
        ExtractedMention(text="Glucophage", occurrence=1, suggested_name="metformin"),
        ExtractedMention(text="HCTZ", occurrence=1, suggested_name="hydrochlorothiazide"),
        ExtractedMention(text="metformn", occurrence=2, suggested_name="metformin"),
    ]
    monkeypatch.setattr(analysis_router, "extract_mentions", lambda _note: detected)
    visit_id = analysis_visit(client, note)

    response = client.post(f"/api/visits/{visit_id}/analyze")
    assert response.status_code == 200
    analysis = response.json()["analysis"]

    assert analysis["implemented"] is True
    assert analysis["note"] == note
    mentions = analysis["mentions"]
    assert len(mentions) == 4
    assert [item["match_type"] for item in mentions] == [
        "misspelling", "brand", "shorthand", "misspelling"
    ]
    assert [item["correction"] for item in mentions] == [
        "metformin", None, None, "metformin"
    ]
    assert [item["medication"]["rxcui"] for item in mentions] == [
        "6809", "6809", "5487", "6809"
    ]
    for item in mentions:
        assert item["matched"] is True
        assert note[item["start"] : item["end"]] == item["text"]
    assert mentions[0]["start"] != mentions[3]["start"]


def test_analyze_empty_note_does_not_call_provider(clean_catalog, client, monkeypatch):
    from app.routers import analysis as analysis_router

    def unexpected_extraction(_note):
        raise AssertionError("Empty notes should not call the provider")

    monkeypatch.setattr(analysis_router, "extract_mentions", unexpected_extraction)
    visit_id = analysis_visit(client, "")
    response = client.post(f"/api/visits/{visit_id}/analyze")

    assert response.status_code == 200
    assert response.json()["analysis"]["mentions"] == []
    assert response.json()["analysis"]["implemented"] is True


def test_analyze_provider_unavailable_returns_error(clean_catalog, client, monkeypatch):
    from app.medication_extraction import ExtractionError
    from app.routers import analysis as analysis_router

    def unavailable(_note):
        raise ExtractionError("Configure LLM_API_KEY to analyze notes.", status_code=503)

    monkeypatch.setattr(analysis_router, "extract_mentions", unavailable)
    visit_id = analysis_visit(client, "Continue metformin.")
    response = client.post(f"/api/visits/{visit_id}/analyze")

    assert response.status_code == 503
    assert "LLM_API_KEY" in response.json()["error"]
    assert "analysis" not in response.json()


def test_analyze_unknown_visit_404(client):
    response = client.post("/api/visits/not-a-real-id/analyze")
    assert response.status_code == 404
    assert response.json() == {"error": "Visit not found"}


def test_scrape_upserts_catalog_rows(clean_catalog, client, monkeypatch):
    """Scrape writes RxNav results into the catalog without touching the network."""
    from app import rxnorm
    from app.routers import medications as medications_router

    fake = {
        "metformin": rxnorm.MedicationInfo(
            rxcui="6809",
            name="metformin",
            tty="IN",
            brand_names=["Glucophage", "Riomet"],
            drug_class="Biguanide",
            source="rxnav",
        ),
        "montelukast": rxnorm.MedicationInfo(
            rxcui="88249",
            name="montelukast",
            tty="IN",
            brand_names=["Singulair"],
            drug_class="Leukotriene Receptor Antagonist",
            source="rxnav",
        ),
    }
    monkeypatch.setattr(
        medications_router.rxnorm, "scrape_concept", lambda name: fake.get(name)
    )

    response = client.post(
        "/api/medications/scrape",
        json={"names": ["metformin", "montelukast", "not-a-drug"]},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["scrape"]["created"] == 1  # montelukast is new
    assert body["scrape"]["updated"] == 1  # metformin was already seeded
    assert body["scrape"]["not_found"] == 1

    listed = client.get("/api/medications", params={"limit": 1000}).json()
    assert listed["total"] == 21
    by_name = {row["name"]: row for row in listed["medications"]}
    assert by_name["montelukast"]["rxcui"] == "88249"
    assert by_name["metformin"]["drug_class"] == "Biguanide"
    assert by_name["metformin"]["brand_names"] == ["Glucophage", "Riomet"]


def test_scrape_not_found_does_not_fail_the_request(clean_catalog, client, monkeypatch):
    from app.routers import medications as medications_router

    monkeypatch.setattr(
        medications_router.rxnorm, "scrape_concept", lambda name: None
    )

    response = client.post("/api/medications/scrape", json={"names": ["zzzz"]})
    assert response.status_code == 200
    assert response.json()["scrape"]["not_found"] == 1
    assert client.get("/api/medications").json()["total"] == 20


def test_seed_preserves_an_imported_catalog(clean_catalog, client):
    """Reloading demo patients must not wipe a catalog scraped from RxNav."""
    from app.database import SessionLocal
    from app.models import Medication

    db = SessionLocal()
    try:
        db.add(Medication(rxcui="999999", name="imported-only", tty="IN"))
        db.commit()
    finally:
        db.close()

    body = client.post("/api/seed").json()
    assert body["counts"]["medications"] == 21  # 20 demo + the imported row

    survivors = client.get(
        "/api/medications", params={"search": "imported-only"}
    ).json()
    assert survivors["total"] == 1


def test_bulk_import_builds_catalog_without_network(clean_catalog, client, monkeypatch):
    """The bulk importer's RxNav calls are stubbed; the write path is real."""
    from app import rxnorm_bulk
    from app.database import SessionLocal

    monkeypatch.setattr(
        rxnorm_bulk,
        "_fetch_ingredients",
        lambda client_, base, scope: [
            {"rxcui": "6809", "name": "metformin", "tty": "IN"},
            {"rxcui": "88249", "name": "montelukast", "tty": "IN"},
        ],
    )
    monkeypatch.setattr(
        rxnorm_bulk,
        "_fetch_class_map",
        lambda client_, base: {"6809": "Biguanide", "88249": "Leukotriene Antagonist"},
    )
    monkeypatch.setattr(
        rxnorm_bulk,
        "_fetch_brand_map",
        lambda client_, base: {"6809": ["Glucophage"], "88249": ["Singulair"]},
    )

    db = SessionLocal()
    try:
        job = rxnorm_bulk.run_import(db)
    finally:
        db.close()

    assert job.state == "done", job.error
    assert job.concepts == 2
    assert job.created == 1  # montelukast is new; metformin was already seeded
    assert job.updated == 1

    listed = client.get("/api/medications", params={"search": "montelukast"}).json()
    row = listed["medications"][0]
    assert row["drug_class"] == "Leukotriene Antagonist"
    assert row["brand_names"] == ["Singulair"]


def test_import_status_endpoint(client):
    body = client.get("/api/medications/import").json()
    assert "job" in body
    assert body["job"]["state"] in {"idle", "running", "done", "error"}


def test_import_keeps_names_if_enrichment_fails(clean_catalog, client, monkeypatch):
    """A failure after the name write leaves those rows committed (no rollback)."""
    from app import rxnorm_bulk
    from app.database import SessionLocal

    monkeypatch.setattr(
        rxnorm_bulk,
        "_fetch_ingredients",
        lambda client_, base, scope: [
            {"rxcui": "88249", "name": "montelukast", "tty": "IN"},
        ],
    )
    def _class_map_fails(client_, base):
        raise RuntimeError("rxnav down")

    monkeypatch.setattr(rxnorm_bulk, "_fetch_class_map", _class_map_fails)
    monkeypatch.setattr(rxnorm_bulk, "_fetch_brand_map", lambda client_, base: {})

    db = SessionLocal()
    try:
        job = rxnorm_bulk.run_import(db)
    finally:
        db.close()

    assert job.state == "error"
    listed = client.get("/api/medications", params={"search": "montelukast"}).json()
    assert listed["total"] == 1
    assert listed["medications"][0]["drug_class"] is None


def test_import_rejects_a_persisted_running_job(clean_catalog, client):
    """A job left `running` in the DB blocks a new import; there is no TTL."""
    from app import rxnorm_bulk
    from app.database import SessionLocal
    from app.models import CatalogImportJob

    db = SessionLocal()
    try:
        row = db.get(CatalogImportJob, rxnorm_bulk._JOB_ROW_ID)
        if row is None:
            row = CatalogImportJob(id=rxnorm_bulk._JOB_ROW_ID)
            db.add(row)
        row.state = "running"
        row.phase = "writing"
        row.started_at = "2026-01-01T00:00:00+00:00"
        row.finished_at = None
        row.error = None
        db.commit()
    finally:
        db.close()

    with rxnorm_bulk._job_lock:
        rxnorm_bulk._job = rxnorm_bulk.ImportJob()

    try:
        response = client.post(
            "/api/medications/import", json={"scope": "all", "limit": 1}
        )
        assert response.status_code == 409
        assert response.json()["job"]["state"] == "running"
    finally:
        db = SessionLocal()
        try:
            row = db.get(CatalogImportJob, rxnorm_bulk._JOB_ROW_ID)
            if row is not None:
                row.state = "idle"
                db.commit()
        finally:
            db.close()
        with rxnorm_bulk._job_lock:
            rxnorm_bulk._job = rxnorm_bulk.ImportJob()
