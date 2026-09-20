import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.medication_matching import MedicationIndex, get_index, invalidate_index, resolve_name
from app.models import Base, Medication
from app.schemas import MedicationOut
from app.seed import MEDICATIONS


@pytest.fixture
def index():
    return MedicationIndex(
        MedicationOut(rxcui=rxcui, name=name, tty=tty, brand_names=brands.split(","), drug_class=drug_class)
        for rxcui, name, tty, brands, drug_class in MEDICATIONS
    )


@pytest.mark.parametrize("written,rxcui,kind,correction", [
    ("  LISINOPRIL  ", "29046", "exact", None),
    ("Glucophage", "6809", "brand", None),
    ("Toprol   XL", "6918", "brand", None),
    ("HCTZ", "5487", "shorthand", None),
    ("APAP", "161", "shorthand", None),
    ("ASA", "1191", "shorthand", None),
    ("metformn", "6809", "misspelling", "metformin"),
    ("hydrochlorothiazde", "5487", "misspelling", "hydrochlorothiazide"),
    ("metopralol", "6918", "misspelling", "metoprolol"),
    ("gabapentine", "25480", "misspelling", "gabapentin"),
    ("sertralin", "36437", "misspelling", "sertraline"),
    ("levothyroxin", "10582", "misspelling", "levothyroxine"),
    ("Glucophag", "6809", "misspelling", "Glucophage"),
])
def test_seed_matches(index, written, rxcui, kind, correction):
    result = index.resolve(written)
    assert result["matched"] is True
    assert result["medication"].rxcui == rxcui
    assert result["match_type"] == kind
    assert result["correction"] == correction


@pytest.mark.parametrize("written", ["", "mg", "MMSE", "MTX", "vitamin D", "folic acid", "diabetes", "take daily", "unknown treatment"])
def test_unresolved_stays_unresolved(index, written):
    result = index.resolve(written, suggested_name="metformin")
    assert result["matched"] is False
    assert result["medication"] is None
    assert result["correction"] is None


def test_shorthand_and_synonym_added_in_full_catalog():
    index = MedicationIndex([
        MedicationOut(rxcui="6851", name="methotrexate", tty="IN", synonym="amethopterin"),
    ])
    assert index.resolve("MTX")["match_type"] == "shorthand"
    assert index.resolve("amethopterin")["match_type"] == "synonym"


def test_ambiguous_alias_is_unresolved():
    index = MedicationIndex([
        MedicationOut(rxcui="1", name="first ingredient", tty="IN", brand_names=["Shared"]),
        MedicationOut(rxcui="2", name="second ingredient", tty="IN", brand_names=["Shared"]),
    ])
    assert index.resolve("Shared")["matched"] is False
    assert index.resolve("Sharedd")["matched"] is False


def test_close_distinct_concepts_are_unresolved():
    index = MedicationIndex([
        MedicationOut(rxcui="1", name="hydroxyzine", tty="IN"),
        MedicationOut(rxcui="2", name="hydralazine", tty="IN"),
    ])
    assert index.resolve("hydroazine")["matched"] is False


def test_aliases_of_same_concept_do_not_create_false_ambiguity():
    index = MedicationIndex([
        MedicationOut(rxcui="1", name="metformin", tty="IN", synonym="metformine"),
    ])
    assert index.resolve("metformn")["medication"].rxcui == "1"


def test_returned_model_cannot_mutate_cached_data(index):
    result = index.resolve("metformin")
    result["medication"].brand_names.append("invented")
    assert "invented" not in index.resolve("metformin")["medication"].brand_names


def test_cache_reused_and_invalidated_on_committed_catalog_change():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    reads = []

    @event.listens_for(engine, "before_cursor_execute")
    def record_reads(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            reads.append(statement)

    with Session(engine) as db:
        db.add(Medication(rxcui="1", name="metformin", tty="IN"))
        db.commit()
        first = get_index(db)
        for _ in range(20):
            assert resolve_name(db, "metformn")["matched"] is True
        assert len(reads) == 1
        db.add(Medication(rxcui="2", name="aspirin", tty="IN"))
        db.commit()
        assert get_index(db) is not first
        assert resolve_name(db, "ASA")["medication"].rxcui == "2"
        assert len(reads) == 2


def test_rollback_keeps_committed_snapshot_and_bulk_hook_refreshes():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Medication(rxcui="1", name="metformin", tty="IN"))
        db.commit()
        first = get_index(db)
        db.add(Medication(rxcui="2", name="aspirin", tty="IN"))
        db.flush()
        db.rollback()
        assert get_index(db) is first
        assert resolve_name(db, "ASA")["matched"] is False
        db.query(Medication).delete()
        db.commit()
        invalidate_index(db)
        assert resolve_name(db, "metformin")["matched"] is False
