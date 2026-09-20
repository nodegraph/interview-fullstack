"""Conservative ingredient matching without scanning the catalog per mention.

Each worker builds an alias/trigram index once per catalog snapshot. ORM writes
invalidate the local cache after commit; the bulk importer does so explicitly.
A 60-second TTL also bounds staleness from other workers or external writers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from threading import RLock
from time import monotonic
from types import MappingProxyType
from weakref import WeakKeyDictionary

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import Medication
from app.schemas import MedicationOut


SHORTHAND = {
    "hctz": "hydrochlorothiazide",
    "apap": "acetaminophen",
    "asa": "aspirin",
    "mtx": "methotrexate",
}
CACHE_TTL_SECONDS = 60
MAX_FUZZY_CANDIDATES = 64
MIN_SIMILARITY = 0.84
MIN_CONCEPT_MARGIN = 0.08


def normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _trigrams(value: str) -> frozenset[str]:
    padded = f"  {value}  "
    return frozenset(padded[i : i + 3] for i in range(len(padded) - 2))


def _edit_distance(left: str, right: str) -> int:
    """Levenshtein distance, only evaluated for the small retrieved shortlist."""
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class _Alias:
    normalized: str
    spelling: str
    rxcui: str
    kind: str


def _unknown() -> dict:
    return {"matched": False, "match_type": "unknown", "correction": None, "medication": None}


class MedicationIndex:
    """An immutable catalog snapshot; construction is linear in catalog size."""

    def __init__(self, rows: Iterable[Medication | MedicationOut]):
        medications = {}
        aliases = []
        exact = defaultdict(list)
        postings = defaultdict(set)
        seen = set()
        for row in rows:
            brands = row.brand_names or []
            if isinstance(brands, str):
                brands = [part.strip() for part in brands.split(",") if part.strip()]
            medications[row.rxcui] = MedicationOut(
                rxcui=row.rxcui,
                name=row.name,
                tty=row.tty,
                synonym=row.synonym,
                brand_names=brands,
                drug_class=row.drug_class,
            )
            names = [(row.name, "exact"), (row.synonym, "synonym")]
            names.extend((brand, "brand") for brand in brands)
            for spelling, kind in names:
                if not spelling:
                    continue
                normalized = normalize_name(spelling)
                key = (normalized, row.rxcui)
                if not normalized or key in seen:
                    continue
                seen.add(key)
                alias_id = len(aliases)
                aliases.append(_Alias(normalized, spelling.strip(), row.rxcui, kind))
                exact[normalized].append(alias_id)
                for gram in _trigrams(normalized):
                    postings[gram].add(alias_id)
        self._medications = MappingProxyType(medications)
        self._aliases = tuple(aliases)
        self._exact = MappingProxyType({name: tuple(ids) for name, ids in exact.items()})
        self._postings = MappingProxyType({gram: frozenset(ids) for gram, ids in postings.items()})

    def _result(self, alias: _Alias, kind: str | None = None, correction: str | None = None) -> dict:
        return {
            "matched": True,
            "match_type": kind or alias.kind,
            "correction": correction,
            # Never share mutable Pydantic models with a caller.
            "medication": self._medications[alias.rxcui].model_copy(deep=True),
        }

    def resolve(self, name: str, suggested_name: str | None = None) -> dict:
        """Resolve raw written text. Model suggestions cannot override evidence.

        ``suggested_name`` is accepted for the extraction contract but deliberately
        unused in this first pass: an unverified expansion must not manufacture a
        catalog match. Known shorthand has an explicit, reviewable mapping.
        """
        query = normalize_name(name)
        if not query:
            return _unknown()
        direct = self._exact.get(query, ())
        if direct:
            if len({self._aliases[i].rxcui for i in direct}) != 1:
                return _unknown()
            alias = self._aliases[direct[0]]
            return self._result(alias, "shorthand" if query in SHORTHAND else None)

        if query in SHORTHAND:
            expanded = self._exact.get(SHORTHAND[query], ())
            if len({self._aliases[i].rxcui for i in expanded}) == 1:
                return self._result(self._aliases[expanded[0]], "shorthand")
            return _unknown()

        # Very short strings have too many plausible neighbors. Exact matches
        # still work, but unknown abbreviations should stay visibly unresolved.
        if len(query) < 5 or len(query) > 150:
            return _unknown()

        overlap = Counter()
        for gram in _trigrams(query):
            overlap.update(self._postings.get(gram, ()))
        # Most-common retrieval keeps expensive similarity work bounded, even
        # with the full ~15k ingredient catalog and all associated brand aliases.
        ranked = {}
        max_edits = 1 if len(query) < 8 else 2
        for alias_id, _ in overlap.most_common(MAX_FUZZY_CANDIDATES):
            alias = self._aliases[alias_id]
            if abs(len(query) - len(alias.normalized)) > max_edits:
                continue
            score = SequenceMatcher(None, query, alias.normalized, autojunk=False).ratio()
            previous = ranked.get(alias.rxcui)
            if previous is None or score > previous[0]:
                ranked[alias.rxcui] = (score, alias)
        candidates = sorted(ranked.values(), key=lambda item: item[0], reverse=True)
        if not candidates or candidates[0][0] < MIN_SIMILARITY:
            return _unknown()
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < MIN_CONCEPT_MARGIN:
            return _unknown()
        alias = candidates[0][1]
        if _edit_distance(query, alias.normalized) > max_edits:
            return _unknown()
        return self._result(alias, "misspelling", alias.spelling)


_cache = WeakKeyDictionary()
_cache_lock = RLock()
_CHANGED_KEY = "medication_matching_catalog_changed"


def _engine(db: Session):
    bind = db.get_bind()
    return getattr(bind, "engine", bind)


def invalidate_index(db: Session) -> None:
    """Call after committing bulk catalog writes (which bypass ORM events)."""
    with _cache_lock:
        _cache.pop(_engine(db), None)


def get_index(db: Session) -> MedicationIndex:
    engine = _engine(db)
    with _cache_lock:
        entry = _cache.get(engine)
        if entry is None or monotonic() >= entry[0]:
            snapshot = MedicationIndex(db.query(Medication).all())
            entry = (monotonic() + CACHE_TTL_SECONDS, snapshot)
            _cache[engine] = entry
        return entry[1]


def resolve_name(db: Session, name: str, suggested_name: str | None = None) -> dict:
    return get_index(db).resolve(name, suggested_name)


@event.listens_for(Session, "after_flush")
def _track_catalog_changes(db: Session, _flush_context) -> None:
    if any(isinstance(row, Medication) for group in (db.new, db.dirty, db.deleted) for row in group):
        db.info[_CHANGED_KEY] = True


@event.listens_for(Session, "after_commit")
def _invalidate_committed_catalog(db: Session) -> None:
    if db.info.pop(_CHANGED_KEY, False):
        invalidate_index(db)


@event.listens_for(Session, "after_rollback")
def _discard_rolled_back_catalog(db: Session) -> None:
    db.info.pop(_CHANGED_KEY, None)
