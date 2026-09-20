"""Extract medication mentions and resolve them against the RxNorm catalog."""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.medication_extraction import ExtractionError, anchor_mentions, extract_mentions
from app.medication_details import enrich_mentions
from app.medication_fallback import RxNavFallback
from app.medication_matching import resolve_name
from app.models import Visit
from app.schemas import MedicationMention, NoteAnalysis

router = APIRouter()
logger = logging.getLogger(__name__)


def analyze_note(db: Session, note: str) -> list[MedicationMention]:
    if not note.strip():
        return []
    mentions = anchor_mentions(note, extract_mentions(note))
    # Repeated source occurrences remain distinct; resolve each spelling once.
    resolved: dict[tuple[str, str | None], dict] = {}
    results = []
    use_fallback = get_settings().rxnav_fallback_enabled
    with RxNavFallback() as fallback:
        for mention in mentions:
            key = (mention.text, mention.suggested_name)
            if key not in resolved:
                match = resolve_name(db, mention.text, mention.suggested_name)
                if not match["matched"] and use_fallback:
                    match = fallback.resolve(mention.text) or match
                resolved[key] = match
            results.append(MedicationMention(
                text=mention.text, name_text=mention.text,
                start=mention.start, end=mention.end,
                **resolved[key],
            ))
    return enrich_mentions(note, results)


@router.post("/visits/{visit_id}/analyze")
def analyze_visit(visit_id: str, db: Session = Depends(get_db)):
    visit = db.get(Visit, visit_id)
    if visit is None:
        return JSONResponse({"error": "Visit not found"}, status_code=404)

    note = visit.notes or ""
    try:
        payload = NoteAnalysis(
            visit_id=visit_id,
            note=note,
            mentions=analyze_note(db, note),
            implemented=True,
        )
        return {"analysis": payload.model_dump(mode="json")}
    except ExtractionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status_code)
    except Exception:
        logger.exception("Medication analysis failed for visit %s", visit_id)
        return JSONResponse({"error": "Medication analysis failed. Please retry."}, status_code=500)
