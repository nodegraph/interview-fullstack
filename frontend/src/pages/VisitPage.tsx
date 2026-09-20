import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { formatDate } from "../dates";
import MedicationNotes from "../components/MedicationNotes";

interface VisitDetail {
  id: string;
  patient_id: string;
  clinician_id: string;
  visit_date: string;
  chief_complaint: string;
  notes: string;
  patient_name: string;
  patient_dob: string;
  patient_mrn: string;
  clinician_name: string;
  clinician_specialty: string;
  created_at: string;
  updated_at: string;
}

export default function VisitPage() {
  const { id, visitId } = useParams<{ id: string; visitId: string }>();
  const navigate = useNavigate();
  const [visit, setVisit] = useState<VisitDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [notes, setNotes] = useState("");
  const [chiefComplaint, setChiefComplaint] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    const clinicianId = localStorage.getItem("clinicianId");
    if (!clinicianId) {
      void navigate("/");
      return;
    }
    if (!visitId) {
      return;
    }

    const controller = new AbortController();
    let active = true;
    setLoading(true);
    setVisit(null);
    setEditMode(false);
    const loadVisit = async () => {
      try {
        const res = await fetch(`/api/visits/${visitId}`, { signal: controller.signal });
        const data = await res.json();
        if (active && res.ok && data.visit) {
          setVisit(data.visit);
          setNotes(data.visit.notes || "");
          setChiefComplaint(data.visit.chief_complaint || "");
        }
      } catch (e) {
        if (!controller.signal.aborted) console.error("Failed to load visit:", e);
      }
      if (active) setLoading(false);
    };

    void loadVisit();
    return () => {
      active = false;
      controller.abort();
    };
  }, [visitId, navigate]);

  const handleSave = async () => {
    if (!visitId) {
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetch(`/api/visits/${visitId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chief_complaint: chiefComplaint, notes }),
      });
      const data = await res.json();
      if (res.ok && data.visit) {
        setVisit((prev) => (prev ? { ...prev, ...data.visit } : data.visit));
        setEditMode(false);
      } else {
        setSaveError(data.error || `Save failed (${res.status})`);
      }
    } catch (e) {
      console.error("Failed to save:", e);
      setSaveError("Save failed: could not reach the server");
    }
    setSaving(false);
  };

  if (loading) {
    return (
      <main className="max-w-4xl mx-auto p-8">
        <p className="text-gray-500">Loading...</p>
      </main>
    );
  }

  if (!visit || !id) {
    return (
      <main className="max-w-4xl mx-auto p-8">
        <p className="text-red-600">Visit not found</p>
        <Link to={`/patients/${id ?? ""}`} className="text-blue-600 hover:underline mt-4 inline-block">
          ← Back to Patient
        </Link>
      </main>
    );
  }

  return (
    <main className="max-w-4xl mx-auto p-8">
      <Link to={`/patients/${id}`} className="text-blue-600 hover:underline text-sm">
        ← Back to {visit.patient_name}
      </Link>

      <div className="mt-4 mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Visit Details</h1>
        <p className="text-gray-600 mt-1">
          {formatDate(visit.visit_date, {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric",
          })}
        </p>
      </div>

      <div className="grid md:grid-cols-2 gap-6 mb-8">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-1">Patient</h2>
          <p className="text-lg font-semibold text-gray-900">{visit.patient_name}</p>
          <p className="text-gray-600 text-sm">MRN: {visit.patient_mrn}</p>
        </div>

        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-1">Clinician</h2>
          <p className="text-lg font-semibold text-gray-900">{visit.clinician_name}</p>
          <p className="text-gray-600 text-sm">{visit.clinician_specialty}</p>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Chief Complaint</h2>
          {!editMode && (
            <button
              onClick={() => setEditMode(true)}
              className="text-blue-600 hover:text-blue-700 text-sm"
            >
              Edit
            </button>
          )}
        </div>

        {editMode ? (
          <input
            type="text"
            value={chiefComplaint}
            onChange={(e) => setChiefComplaint(e.target.value)}
            className="w-full border rounded-lg px-4 py-2"
            placeholder="Enter chief complaint..."
          />
        ) : (
          <p className="text-gray-700">
            {visit.chief_complaint || (
              <span className="text-gray-400 italic">No chief complaint recorded</span>
            )}
          </p>
        )}
      </div>

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Visit Notes</h2>
          {!editMode && (
            <button
              onClick={() => setEditMode(true)}
              className="text-blue-600 hover:text-blue-700 text-sm"
            >
              Edit
            </button>
          )}
        </div>

        {editMode ? (
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={8}
            className="w-full border rounded-lg px-4 py-3 font-mono text-sm"
            placeholder="Enter visit notes..."
          />
        ) : visit.notes ? (
          <MedicationNotes visitId={visit.id} note={visit.notes} />
        ) : (
          <p className="italic text-gray-400">No notes recorded</p>
        )}
      </div>

      {editMode && saveError && (
        <p className="mb-3 text-sm text-red-600">{saveError}</p>
      )}

      {editMode && (
        <div className="flex gap-3">
          <button
            onClick={() => void handleSave()}
            disabled={saving}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
          <button
            onClick={() => {
              setEditMode(false);
              setSaveError(null);
              setNotes(visit.notes || "");
              setChiefComplaint(visit.chief_complaint || "");
            }}
            className="bg-gray-200 text-gray-700 px-6 py-2 rounded-lg hover:bg-gray-300"
          >
            Cancel
          </button>
        </div>
      )}

      <div className="mt-8 pt-6 border-t text-sm text-gray-400">
        <p>Created: {new Date(visit.created_at).toLocaleString()}</p>
        <p>Last updated: {new Date(visit.updated_at).toLocaleString()}</p>
      </div>
    </main>
  );
}
