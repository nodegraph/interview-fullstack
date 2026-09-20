from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ClinicianOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    specialty: str


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    dob: date
    mrn: str
    assigned_clinician_id: str | None = None


class PatientDetail(PatientOut):
    clinician_name: str | None = None
    clinician_specialty: str | None = None


class VisitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    patient_id: str | None = None
    clinician_id: str | None = None
    visit_date: date
    chief_complaint: str | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VisitListItem(VisitOut):
    clinician_name: str | None = None


class VisitDetail(VisitOut):
    patient_name: str | None = None
    patient_dob: date | None = None
    patient_mrn: str | None = None
    clinician_name: str | None = None
    clinician_specialty: str | None = None


class MedicationOut(BaseModel):
    """A normalized RxNorm concept from the reference catalog."""

    rxcui: str
    name: str
    tty: str
    synonym: str | None = None
    brand_names: list[str] = []
    drug_class: str | None = None
    source: str = "catalog"


class MedicationScrapeRequest(BaseModel):
    """Ask the catalog to (re)build itself from the live RxNav API.

    With no ``names``, every concept already in the catalog is refreshed in
    place. Pass ``names`` to add new concepts (or refresh a subset).
    """

    names: list[str] | None = None


class MedicationScrapeResult(BaseModel):
    """Per-name outcome of a scrape, so the UI can show what failed and why."""

    name: str
    status: str  # "created" | "updated" | "not_found"
    rxcui: str | None = None


class MedicationScrapeSummary(BaseModel):
    created: int = 0
    updated: int = 0
    not_found: int = 0
    results: list[MedicationScrapeResult] = []


class MedicationImportRequest(BaseModel):
    """Build the whole catalog from RxNav (see ``app.rxnorm_bulk``)."""

    # "all" (~15k ingredient concepts) or "prescribable" (RxNorm's Current
    # Prescribable Content, ~6k).
    scope: str = "all"
    include_classes: bool = True
    include_brands: bool = True
    # Cap the number of concepts imported. Mostly for tests and quick trials.
    limit: int | None = Field(default=None, ge=1)


class MedicationImportJob(BaseModel):
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
    warning_count: int = 0
    warnings: list[str] = []


class MedicationMention(BaseModel):
    """A medication reference detected inside a visit note.

    ``start``/``end`` are character offsets into the analyzed ``note`` text and
    are what the frontend uses to place highlights.

    ``medication`` is the ingredient-level match (the catalog). Strength, dose
    form, and a more specific product RxCUI (SCD/SBD) are optional — fill them
    in if you take the form/dosage stretch in EXERCISE.md.
    """

    text: str  # the exact highlighted substring, including adjacent strength/form
    name_text: str | None = None  # original drug name, before strength/form expansion
    start: int
    end: int
    matched: bool = False
    # How the written text maps to the RxNorm concept:
    # "exact" | "brand" | "synonym" | "misspelling" | "shorthand" | "unknown"
    match_type: str = "unknown"
    # Proper spelling to surface in the tooltip when the note misspelled the drug.
    correction: str | None = None
    confidence: float | None = None
    medication: MedicationOut | None = None
    # Optional product-level match when the note includes a strength / form.
    strength: str | None = None  # e.g. "500 MG", "81 mg"
    dose_form: str | None = None  # e.g. "Oral Tablet", "inhaler"
    product_rxcui: str | None = None
    product_name: str | None = None


class NoteAnalysis(BaseModel):
    """Result of analyzing a visit note for medication mentions."""

    visit_id: str
    note: str
    mentions: list[MedicationMention] = []
    # Set to False by the scaffold's empty stub. Flip it to True once real
    # detection + matching is implemented (see EXERCISE.md).
    implemented: bool = False
