"""Bulk import of the RxNorm catalog from RxNav.

``app.rxnorm.scrape_concept`` resolves one medication at a time: it costs three
or more requests per concept, which is fine for adding a handful of names and
hopeless for the ~15k ingredients RxNorm actually contains.

This module inverts the expensive lookups so the whole catalog can be built in
about a minute and a half:

* **Ingredients** — ``/allconcepts?tty=IN`` returns every ingredient concept in a
  single response.
* **Drug classes** — instead of asking "what class is drug X" once per drug,
  ask "who belongs to class Y" once per class. There are ~750 Established
  Pharmacologic Classes and ~1300 ATC classes, versus ~15k drugs.
* **Brand names** — instead of asking "what brands contain drug X" (and then
  verifying each candidate is single-ingredient), fetch all ~5k brand concepts
  once and resolve each one's ingredients, which yields the brand-to-ingredient
  map and the single-ingredient filter in the same pass.

The import runs in a background thread with progress reported through
``get_job``; it is far too slow to hold an HTTP request open. Status is
also written to ``catalog_import_jobs`` so the UI can poll it.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import CatalogImportJob, Medication

# Singleton row in ``catalog_import_jobs``.
_JOB_ROW_ID = 1

# RxNav asks for <= 20 requests/second.
_WORKERS = 8
_WRITE_BATCH = 1000

# Ingredient scopes. "prescribable" is RxNorm's Current Prescribable Content —
# a smaller, actively-marketed subset.
SCOPES = {
    "all": "/allconcepts.json",
    "prescribable": "/Prescribe/allconcepts.json",
}


@dataclass
class ImportJob:
    state: str = "idle"  # "idle" | "running" | "done" | "error"
    phase: str = ""
    processed: int = 0
    total: int = 0
    concepts: int = 0
    created: int = 0
    updated: int = 0
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "phase": self.phase,
            "processed": self.processed,
            "total": self.total,
            "concepts": self.concepts,
            "created": self.created,
            "updated": self.updated,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


_job = ImportJob()
_job_lock = threading.Lock()


def _snapshot() -> ImportJob:
    return ImportJob(**_job.__dict__)


def _persist(job: ImportJob) -> None:
    """Write job status to the singleton DB row. Own session, own commit."""
    db = SessionLocal()
    try:
        row = db.get(CatalogImportJob, _JOB_ROW_ID)
        if row is None:
            row = CatalogImportJob(id=_JOB_ROW_ID)
            db.add(row)
        row.state = job.state
        row.phase = job.phase
        row.processed = job.processed
        row.total = job.total
        row.concepts = job.concepts
        row.created = job.created
        row.updated = job.updated
        row.error = job.error
        row.started_at = job.started_at
        row.finished_at = job.finished_at
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _load_persisted() -> ImportJob | None:
    db = SessionLocal()
    try:
        row = db.get(CatalogImportJob, _JOB_ROW_ID)
        if row is None:
            return None
        return ImportJob(
            state=row.state,
            phase=row.phase,
            processed=row.processed,
            total=row.total,
            concepts=row.concepts,
            created=row.created,
            updated=row.updated,
            error=row.error,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )
    except Exception:
        return None
    finally:
        db.close()


def get_job() -> ImportJob:
    with _job_lock:
        memory = _snapshot()
    if memory.state != "idle":
        return memory
    return _load_persisted() or memory


def _update(**fields) -> None:
    with _job_lock:
        for key, value in fields.items():
            setattr(_job, key, value)
        snapshot = _snapshot()
    _persist(snapshot)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# RxNav fetching
# ---------------------------------------------------------------------------


def _concepts_from(payload: dict) -> list[dict]:
    return payload.get("minConceptGroup", {}).get("minConcept", []) or []


def _related_concepts(payload: dict) -> list[dict]:
    groups = payload.get("relatedGroup", {}).get("conceptGroup", []) or []
    return [c for group in groups for c in (group.get("conceptProperties") or [])]


def _fetch_ingredients(client, base: str, scope: str) -> list[dict]:
    path = SCOPES.get(scope, SCOPES["all"])
    resp = client.get(f"{base}{path}", params={"tty": "IN"})
    resp.raise_for_status()
    return _concepts_from(resp.json())


def _class_members(client, base: str, class_id: str, source: str) -> list[tuple[str, str]]:
    """(rxcui, class name) for every ingredient in one class."""
    try:
        resp = client.get(
            f"{base}/rxclass/classMembers.json",
            params={"classId": class_id, "relaSource": source, "ttys": "IN"},
        )
        resp.raise_for_status()
        members = resp.json().get("drugMemberGroup", {}).get("drugMember", []) or []
        return [(m["minConcept"]["rxcui"], m["minConcept"]["name"]) for m in members]
    except Exception:
        return []


def _fetch_class_map(client, base: str) -> dict[str, str]:
    """Map rxcui -> pharmacologic class.

    A drug belongs to several classes at once, so two preferences pick one:

    1. **Source** — an FDA Established Pharmacologic Class beats an ATC class.
    2. **Specificity** — within a source, the class with the fewest members wins.

    The second rule matters more than it looks. RxNav types two catch-all
    buckets as EPC — "Chemical Structure" and "Enzyme Interaction" — that each
    contain thousands of drugs. Without a specificity preference they overwrite
    the useful classes and metformin ends up filed as "Chemical Structure"
    instead of "Biguanide".
    """
    # (source_rank, class_size, class_name); lower rank and size win.
    best: dict[str, tuple[int, int, str]] = {}

    sources = (("DAILYMED", "EPC"), ("ATC", "ATC1-4"))
    for rank, (source, class_type) in enumerate(sources):
        try:
            resp = client.get(
                f"{base}/rxclass/allClasses.json", params={"classTypes": class_type}
            )
            resp.raise_for_status()
            classes = (
                resp.json()
                .get("rxclassMinConceptList", {})
                .get("rxclassMinConcept", [])
                or []
            )
        except Exception:
            continue

        _update(total=len(classes), processed=0)
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            results = pool.map(
                lambda cls: (
                    cls["className"],
                    _class_members(client, base, cls["classId"], source),
                ),
                classes,
            )
            for index, (class_name, members) in enumerate(results, start=1):
                size = len(members)
                for rxcui, _name in members:
                    current = best.get(rxcui)
                    if current is None or (rank, size) < (current[0], current[1]):
                        best[rxcui] = (rank, size, class_name)
                if index % 50 == 0:
                    _update(processed=index)

    return {rxcui: entry[2] for rxcui, entry in best.items()}


def _fetch_brand_map(client, base: str) -> dict[str, list[str]]:
    """Map ingredient rxcui -> single-ingredient brand names.

    Every brand concept is resolved to its ingredient list once; brands with
    exactly one ingredient are attributed to it, so combination products
    (Janumet, Kombiglyze) never leak into a single ingredient's brand list.
    """
    try:
        resp = client.get(f"{base}/allconcepts.json", params={"tty": "BN"})
        resp.raise_for_status()
        brands = _concepts_from(resp.json())
    except Exception:
        return {}

    _update(total=len(brands), processed=0)

    def ingredients_of(brand: dict) -> tuple[str, list[str]]:
        try:
            resp = client.get(
                f"{base}/rxcui/{brand['rxcui']}/related.json", params={"tty": "IN"}
            )
            resp.raise_for_status()
            return brand["name"], [c["rxcui"] for c in _related_concepts(resp.json())]
        except Exception:
            return brand["name"], []

    brand_map: dict[str, list[str]] = defaultdict(list)
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        for index, (name, ingredient_ids) in enumerate(
            pool.map(ingredients_of, brands), start=1
        ):
            if len(ingredient_ids) == 1:
                brand_map[ingredient_ids[0]].append(name)
            if index % 100 == 0:
                _update(processed=index)

    return dict(brand_map)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _rows_for(ingredients: list[dict], brand_map: dict, class_map: dict) -> list[dict]:
    return [
        {
            "rxcui": concept["rxcui"],
            "name": concept["name"][:255],
            "tty": concept.get("tty") or "IN",
            "brand_names": ",".join(brand_map.get(concept["rxcui"], [])) or None,
            "drug_class": (class_map.get(concept["rxcui"]) or None),
        }
        for concept in ingredients
    ]


def _write(db: Session, rows: list[dict]) -> tuple[int, int]:
    """Upsert catalog rows. Returns (created, updated).

    Each batch is committed before the next one starts, so the catalog page
    can show rows as they land.
    """
    existing = {rxcui for (rxcui,) in db.query(Medication.rxcui).all()}
    created = sum(1 for row in rows if row["rxcui"] not in existing)

    for start in range(0, len(rows), _WRITE_BATCH):
        batch = rows[start : start + _WRITE_BATCH]
        statement = pg_insert(Medication).values(batch)
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[Medication.rxcui],
                set_={
                    "name": statement.excluded.name,
                    "tty": statement.excluded.tty,
                    "brand_names": statement.excluded.brand_names,
                    "drug_class": statement.excluded.drug_class,
                },
            )
        )
        db.commit()
        # PostgreSQL upserts bypass the ORM change events used by the matcher.
        from app.medication_matching import invalidate_index

        invalidate_index(db)
        _update(processed=min(start + len(batch), len(rows)))
    return created, len(rows) - created


def run_import(
    db: Session,
    scope: str = "all",
    include_classes: bool = True,
    include_brands: bool = True,
    limit: int | None = None,
) -> ImportJob:
    """Build the catalog from RxNav. Blocking — callers run it on a thread."""
    import httpx

    base = get_settings().rxnav_base_url.rstrip("/")
    _update(
        state="running",
        phase="ingredients",
        processed=0,
        total=0,
        concepts=0,
        created=0,
        updated=0,
        error=None,
        started_at=_now(),
        finished_at=None,
    )

    try:
        with httpx.Client(timeout=60.0) as client:
            ingredients = _fetch_ingredients(client, base, scope)
            if limit is not None:
                ingredients = ingredients[:limit]
            _update(concepts=len(ingredients), total=len(ingredients))

            # Names first, so the catalog starts filling while class and brand
            # maps are still being fetched.
            _update(phase="writing", processed=0, total=len(ingredients))
            created, updated = _write(db, _rows_for(ingredients, {}, {}))

            class_map: dict[str, str] = {}
            if include_classes:
                _update(phase="classes", processed=0)
                class_map = _fetch_class_map(client, base)

            brand_map: dict[str, list[str]] = {}
            if include_brands:
                _update(phase="brands", processed=0)
                brand_map = _fetch_brand_map(client, base)

        if class_map or brand_map:
            _update(phase="enriching", processed=0, total=len(ingredients))
            _write(db, _rows_for(ingredients, brand_map, class_map))

        _update(
            state="done",
            phase="done",
            processed=len(ingredients),
            created=created,
            updated=updated,
            finished_at=_now(),
        )
    except Exception as exc:
        _update(state="error", error=str(exc), finished_at=_now())

    return get_job()


def start_import(session_factory, **kwargs) -> tuple[bool, ImportJob]:
    """Kick off an import on a background thread.

    Returns ``(started, job)``. ``started`` is False when one is already running.
    """
    current = get_job()
    if current.state == "running":
        return False, current

    with _job_lock:
        if _job.state == "running":
            return False, _snapshot()
        _job.state = "running"
        _job.phase = "starting"
        _job.error = None
        _job.started_at = _now()
        _job.finished_at = None
        snapshot = _snapshot()
    _persist(snapshot)

    def worker() -> None:
        db = session_factory()
        try:
            run_import(db, **kwargs)
        finally:
            db.close()

    threading.Thread(target=worker, daemon=True).start()
    return True, get_job()
