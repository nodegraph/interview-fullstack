"""RxNorm ingredient import with incremental writes and a renewable DB lease.

Enrichment is best effort: missing or failed fields never erase existing catalog
metadata. All import requests share a paced client; progress and capped warnings
are persisted so any API worker can report the same job.
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

import httpx
from sqlalchemy import func, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import CatalogImportJob, Medication

_JOB_ROW_ID = 1
_WORKERS = 8
_WRITE_BATCH = 1000
_LEASE_SECONDS = 120
_HEARTBEAT_SECONDS = 20
_MAX_WARNINGS = 20
SCOPES = {"all": "/allconcepts.json", "prescribable": "/Prescribe/allconcepts.json"}


@dataclass
class ImportJob:
    state: str = "idle"
    phase: str = ""
    processed: int = 0
    total: int = 0
    concepts: int = 0
    created: int = 0
    updated: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    warning_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_job = ImportJob()
_job_lock = threading.Lock()
_context = threading.local()


class LeaseLost(RuntimeError):
    pass


@dataclass
class _Run:
    owner: str
    factory: object
    job: ImportJob
    stopped: threading.Event = field(default_factory=threading.Event)
    lost: threading.Event = field(default_factory=threading.Event)

    def warn(self, message: str) -> None:
        with _job_lock:
            self.job.warning_count += 1
            if len(self.job.warnings) < _MAX_WARNINGS:
                self.job.warnings.append(message)


def _snapshot() -> ImportJob:
    return ImportJob(**_job.to_dict())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_values(job: ImportJob) -> dict:
    values = job.to_dict()
    values["warnings"] = json.dumps(values["warnings"])
    return values


def _from_row(row) -> ImportJob:
    values = {key: getattr(row, key) for key in ImportJob.__dataclass_fields__}
    values["warnings"] = json.loads(row.warnings or "[]")
    return ImportJob(**values)


def _load_persisted(factory=SessionLocal) -> ImportJob | None:
    with factory() as db:
        row = db.get(CatalogImportJob, _JOB_ROW_ID)
        if row is None:
            return None
        job = _from_row(row)
        if job.state == "running" and (row.lease_expires_at or 0) <= time.time():
            job.state = "error"
            job.error = "Import interrupted or worker unavailable. Start the import again to retry."
        return job


def get_job() -> ImportJob:
    # The database is authoritative across workers, including after a restart.
    return _load_persisted() or ImportJob()


def _claim(factory) -> _Run | None:
    """Atomically acquire the singleton lease, including an expired old job."""
    global _job
    owner = str(uuid4())
    job = ImportJob(state="running", phase="starting", started_at=_now())
    with factory() as db:
        db.execute(pg_insert(CatalogImportJob).values(id=_JOB_ROW_ID).on_conflict_do_nothing())
        claimed = db.execute(
            update(CatalogImportJob)
            .where(CatalogImportJob.id == _JOB_ROW_ID)
            .where(or_(CatalogImportJob.state != "running",
                       CatalogImportJob.lease_expires_at.is_(None),
                       CatalogImportJob.lease_expires_at <= time.time()))
            .values(**_row_values(job), owner_token=owner,
                    lease_expires_at=time.time() + _LEASE_SECONDS)
        ).rowcount
        db.commit()
    if not claimed:
        return None
    with _job_lock:
        _job = job
    return _Run(owner, factory, job)


def _persist(run: _Run) -> None:
    with _job_lock:
        values = _row_values(run.job)
    with run.factory() as db:
        changed = db.execute(
            update(CatalogImportJob)
            .where(CatalogImportJob.id == _JOB_ROW_ID,
                   CatalogImportJob.owner_token == run.owner,
                   CatalogImportJob.lease_expires_at > time.time())
            .values(**values, lease_expires_at=time.time() + _LEASE_SECONDS)
        ).rowcount
        db.commit()
    if not changed:
        run.lost.set()
        raise LeaseLost("Import lease expired or another worker owns the import.")


def _update(**fields) -> None:
    run = getattr(_context, "run", None)
    with _job_lock:
        job = run.job if run else _job
        for key, value in fields.items():
            setattr(job, key, value)
    if run:
        _persist(run)


def _heartbeat(run: _Run) -> None:
    while not run.stopped.wait(_HEARTBEAT_SECONDS):
        try:
            with run.factory() as db:
                changed = db.execute(
                    update(CatalogImportJob)
                    .where(CatalogImportJob.id == _JOB_ROW_ID,
                           CatalogImportJob.owner_token == run.owner,
                           CatalogImportJob.state == "running",
                           CatalogImportJob.lease_expires_at > time.time())
                    .values(lease_expires_at=time.time() + _LEASE_SECONDS)
                ).rowcount
                db.commit()
            if not changed:
                run.lost.set()
                return
        except Exception:
            # Do not continue writing if ownership cannot be verified.
            run.lost.set()
            return


class _RateLimiter:
    """Pace all importer threads below RxNav's 20 requests/sec/IP ceiling.

    https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html
    """
    def __init__(self, rate=15, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1 / rate
        self.clock, self.sleep = clock, sleep
        self.next_request = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            delay = self.next_request - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.next_request = self.clock() + self.interval

    def defer(self, seconds):
        with self.lock:
            self.next_request = max(self.next_request, self.clock() + seconds)


_rate_limiter = _RateLimiter()


def _retry_delay(response, attempt: int) -> float:
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(0, parsedate_to_datetime(value).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    return min(2 ** attempt, 8)


class _RxNavClient:
    def __init__(self, client, run=None, limiter=None):
        self.client = client
        self.run = run
        self.limiter = limiter or _rate_limiter

    def warn(self, message):
        if self.run:
            self.run.warn(message)

    def get(self, url, **kwargs):
        for attempt in range(3):
            if self.run and self.run.lost.is_set():
                raise LeaseLost("Import worker lost its lease.")
            self.limiter.wait()
            response = None
            try:
                response = self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError:
                if response.status_code != 429 and response.status_code < 500:
                    raise
                delay = _retry_delay(response, attempt)
                # A long Retry-After must not be ignored or hold workers forever.
                if attempt == 2 or delay > 60:
                    raise
            except httpx.TransportError:
                if attempt == 2:
                    raise
                delay = _retry_delay(None, attempt)
            self.limiter.defer(delay)
        raise RuntimeError("RxNav retries exhausted")


def _items(payload: dict, group: str, key: str, *, nonempty=False) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get(group), dict):
        raise ValueError(f"Malformed RxNav response: missing {group}")
    values = payload[group].get(key)
    if values is None:
        values = []
    if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
        raise ValueError(f"Malformed RxNav response: invalid {key}")
    if nonempty and not values:
        raise ValueError(f"RxNav returned an empty {key} list")
    return values


def _concepts_from(payload: dict) -> list[dict]:
    concepts = _items(payload, "minConceptGroup", "minConcept", nonempty=True)
    if any(not str(c.get("rxcui", "")).isdigit() or not isinstance(c.get("name"), str)
           or not c["name"].strip() for c in concepts):
        raise ValueError("Malformed RxNav concept identifiers or names")
    return concepts


def _related_concepts(payload: dict) -> list[dict]:
    groups = _items(payload, "relatedGroup", "conceptGroup")
    concepts = []
    for group in groups:
        members = group.get("conceptProperties")
        if members is None:
            members = []
        if not isinstance(members, list) or any(
            not isinstance(c, dict) or not str(c.get("rxcui", "")).isdigit() for c in members
        ):
            raise ValueError("Malformed RxNav related concepts")
        concepts.extend(members)
    return concepts


def _fetch_ingredients(client, base: str, scope: str) -> list[dict]:
    response = client.get(f"{base}{SCOPES[scope]}", params={"tty": "IN"})
    return _concepts_from(response.json())


def _class_members(client, base: str, class_id: str, source: str) -> list[tuple[str, str]]:
    try:
        response = client.get(f"{base}/rxclass/classMembers.json",
                              params={"classId": class_id, "relaSource": source, "ttys": "IN"})
        members = _items(response.json(), "drugMemberGroup", "drugMember")
        return [(m["minConcept"]["rxcui"], m["minConcept"]["name"]) for m in members]
    except LeaseLost:
        raise
    except Exception as exc:
        client.warn(f"Class {class_id} ({source}) unavailable: {type(exc).__name__}")
        return []


def _fetch_class_map(client, base: str) -> dict[str, str]:
    # Prefer EPC over ATC, then the smallest (most specific) member set.
    best: dict[str, tuple[int, int, str]] = {}
    for rank, (source, class_type) in enumerate((("DAILYMED", "EPC"), ("ATC", "ATC1-4"))):
        try:
            response = client.get(f"{base}/rxclass/allClasses.json", params={"classTypes": class_type})
            classes = _items(response.json(), "rxclassMinConceptList", "rxclassMinConcept", nonempty=True)
            if any(not c.get("classId") or not c.get("className") for c in classes):
                raise ValueError("Malformed RxNav classes")
        except LeaseLost:
            raise
        except Exception as exc:
            client.warn(f"{class_type} class list unavailable: {type(exc).__name__}")
            continue
        _update(total=len(classes), processed=0)
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            results = pool.map(lambda cls: (cls["className"], _class_members(client, base, cls["classId"], source)), classes)
            for index, (name, members) in enumerate(results, 1):
                for rxcui, _ in members:
                    current = best.get(rxcui)
                    if current is None or (rank, len(members)) < current[:2]:
                        best[rxcui] = (rank, len(members), name)
                if index % 50 == 0 or index == len(classes):
                    _update(processed=index)
    return {rxcui: entry[2] for rxcui, entry in best.items()}


def _fetch_brand_map(client, base: str) -> dict[str, list[str]]:
    try:
        response = client.get(f"{base}/allconcepts.json", params={"tty": "BN"})
        brands = _concepts_from(response.json())
    except LeaseLost:
        raise
    except Exception as exc:
        client.warn(f"Brand list unavailable: {type(exc).__name__}")
        return {}
    _update(total=len(brands), processed=0)

    def ingredients_of(brand):
        try:
            response = client.get(f"{base}/rxcui/{brand['rxcui']}/related.json", params={"tty": "IN"})
            return brand["name"], sorted({c["rxcui"] for c in _related_concepts(response.json())})
        except LeaseLost:
            raise
        except Exception as exc:
            client.warn(f"Brand {brand['name']} unavailable: {type(exc).__name__}")
            return brand["name"], []

    brand_map: dict[str, list[str]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for index, (name, ingredients) in enumerate(pool.map(ingredients_of, brands), 1):
            if len(ingredients) == 1:
                brand_map[ingredients[0]].append(name)
            if index % 100 == 0 or index == len(brands):
                _update(processed=index)
    return dict(brand_map)


def _rows_for(ingredients: list[dict], brand_map: dict, class_map: dict) -> list[dict]:
    return [{"rxcui": c["rxcui"], "name": c["name"][:255], "tty": c.get("tty") or "IN",
             "brand_names": ",".join(brand_map.get(c["rxcui"], [])) or None,
             "drug_class": class_map.get(c["rxcui"]) or None} for c in ingredients]


def _write(db: Session, rows: list[dict]) -> tuple[int, int]:
    existing = {rxcui for (rxcui,) in db.query(Medication.rxcui).all()}
    created = sum(row["rxcui"] not in existing for row in rows)
    run = getattr(_context, "run", None)
    for start in range(0, len(rows), _WRITE_BATCH):
        # Lock ownership in the SAME transaction as the catalog mutation. A
        # replacement worker cannot claim the lease mid-batch.
        if run:
            row = db.query(CatalogImportJob).filter_by(id=_JOB_ROW_ID).with_for_update().populate_existing().one()
            if run.lost.is_set() or row.owner_token != run.owner or (row.lease_expires_at or 0) <= time.time():
                db.rollback()
                raise LeaseLost("Import worker lost its lease before writing.")
        batch = rows[start:start + _WRITE_BATCH]
        statement = pg_insert(Medication).values(batch)
        db.execute(statement.on_conflict_do_update(index_elements=[Medication.rxcui], set_={
            "name": statement.excluded.name, "tty": statement.excluded.tty,
            # Names-only passes and unavailable enrichment cannot erase data.
            "brand_names": func.coalesce(statement.excluded.brand_names, Medication.brand_names),
            "drug_class": func.coalesce(statement.excluded.drug_class, Medication.drug_class),
        }))
        db.commit()
        from app.medication_matching import invalidate_index
        invalidate_index(db)
        _update(processed=min(start + len(batch), len(rows)))
    return created, len(rows) - created


def run_import(db: Session, scope="all", include_classes=True, include_brands=True, limit=None, *, _run=None) -> ImportJob:
    """Blocking import; also used by tests and the background worker."""
    run = _run or _claim(SessionLocal)
    if run is None:
        return get_job()
    _context.run = run
    heartbeat = threading.Thread(target=_heartbeat, args=(run,), daemon=True)
    heartbeat.start()
    try:
        if scope not in SCOPES or (limit is not None and limit < 1):
            raise ValueError("Invalid import scope or limit")
        base = get_settings().rxnav_base_url.rstrip("/")
        _update(phase="ingredients")
        with httpx.Client(timeout=30.0) as transport:
            client = _RxNavClient(transport, run)
            ingredients = _fetch_ingredients(client, base, scope)
            if not ingredients:
                raise ValueError("RxNav returned no ingredient concepts")
            # RxCUIs are unique keys. Handle repeated upstream concepts once.
            ingredients = list({c["rxcui"]: c for c in ingredients}.values())
            if limit is not None:
                ingredients = ingredients[:limit]
            _update(concepts=len(ingredients), total=len(ingredients), phase="writing")
            created, updated = _write(db, _rows_for(ingredients, {}, {}))
            _update(created=created, updated=updated)
            class_map, brand_map = {}, {}
            for enabled, phase, fetch in ((include_classes, "classes", _fetch_class_map), (include_brands, "brands", _fetch_brand_map)):
                if enabled:
                    _update(phase=phase, processed=0, total=0)
                    try:
                        warning_before = run.job.warning_count
                        result = fetch(client, base)
                        if not result and run.job.warning_count == warning_before:
                            run.warn(f"No {phase} enrichment was returned; existing values were preserved.")
                        if phase == "classes":
                            class_map = result
                        else:
                            brand_map = result
                    except LeaseLost:
                        raise
                    except Exception as exc:
                        run.warn(f"{phase.capitalize()} enrichment unavailable: {type(exc).__name__}")
            # Partial maps can omit an unknown subset of brand aliases or more
            # specific class choices. Preserve old enrichment by merging brand
            # names and retaining old class values whenever that phase failed.
            if run.job.warning_count:
                for medication in db.query(Medication).filter(Medication.rxcui.in_([c["rxcui"] for c in ingredients])):
                    if medication.brand_names and medication.rxcui in brand_map:
                        brand_map[medication.rxcui] = sorted(set(brand_map[medication.rxcui]) | set(medication.brand_names.split(",")))
                    if medication.drug_class and medication.rxcui in class_map:
                        class_map.pop(medication.rxcui)
                db.rollback()  # End the read transaction before ownership lock.
        if class_map or brand_map:
            _update(phase="enriching", processed=0, total=len(ingredients))
            _write(db, _rows_for(ingredients, brand_map, class_map))
        _update(state="done", phase="done", processed=len(ingredients), total=len(ingredients), finished_at=_now())
    except LeaseLost:
        db.rollback()
        # The replacement owner's status and catalog must remain untouched.
    except Exception as exc:
        db.rollback()
        try:
            _update(state="error", error=f"Import failed: {type(exc).__name__}: {exc}", finished_at=_now())
        except LeaseLost:
            pass
    finally:
        run.stopped.set()
        heartbeat.join(timeout=2)
        del _context.run
    return _load_persisted(run.factory) or run.job


def start_import(session_factory, **kwargs) -> tuple[bool, ImportJob]:
    run = _claim(session_factory)
    if run is None:
        return False, _load_persisted(session_factory) or ImportJob()

    def worker():
        with session_factory() as db:
            run_import(db, _run=run, **kwargs)

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        run.job.state = "error"
        run.job.error = "Import worker could not start. Try again."
        run.job.finished_at = _now()
        _persist(run)
        raise
    return True, ImportJob(**run.job.to_dict())
