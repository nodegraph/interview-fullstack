import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VisitPage from "../src/pages/VisitPage";
import { analysis, mention, response } from "./fixtures";

const visit = {
  id: "visit-1", patient_id: "patient-1", clinician_id: "clinician-1",
  visit_date: "2026-09-19", chief_complaint: "Follow-up", notes: "aspirin",
  patient_name: "Sample Patient", patient_dob: "1970-01-01", patient_mrn: "MRN-1",
  clinician_name: "Sample Clinician", clinician_specialty: "Family Medicine",
  created_at: "2026-09-19T10:00:00Z", updated_at: "2026-09-19T10:00:00Z",
};

function renderVisit() {
  return render(<MemoryRouter initialEntries={["/patients/patient-1/visits/visit-1"]}>
    <Routes><Route path="/patients/:id/visits/:visitId" element={<VisitPage />} /></Routes>
  </MemoryRouter>);
}

describe("visit editing and medication analysis", () => {
  beforeEach(() => localStorage.setItem("clinicianId", "clinician-1"));

  it("preserves the existing analysis when edits are cancelled", async () => {
    const fetchMock = vi.fn(async (url: string) => url.endsWith("/analyze")
      ? response({ analysis: analysis(visit.notes, [mention(visit.notes, "aspirin")]) })
      : response({ visit }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderVisit();
    expect(await screen.findByRole("button", { name: /aspirin.*Show details/ })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await user.clear(screen.getByRole("textbox", { name: "Visit notes" }));
    await user.type(screen.getByRole("textbox", { name: "Visit notes" }), "Changed draft");
    expect(screen.queryByRole("button", { name: /aspirin.*Show details/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: /aspirin.*Show details/ })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/analyze"))).toHaveLength(1);
  });

  it("analyzes the saved note after a successful edit", async () => {
    let savedNotes = visit.notes;
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/analyze")) {
        return response({ analysis: analysis(savedNotes, savedNotes === "aspirin" ? [mention(savedNotes, "aspirin")] : []) });
      }
      if (options?.method === "PUT") savedNotes = JSON.parse(options.body as string).notes;
      return response({ visit: { ...visit, notes: savedNotes } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderVisit();
    expect(await screen.findByRole("button", { name: /aspirin.*Show details/ })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await user.clear(screen.getByRole("textbox", { name: "Visit notes" }));
    await user.type(screen.getByRole("textbox", { name: "Visit notes" }), "No medications listed.");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText("No medication mentions found.")).toBeInTheDocument();
    expect(screen.getByText("No medications listed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /aspirin.*Show details/ })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/visits/visit-1", expect.objectContaining({
      method: "PUT", body: JSON.stringify({ chief_complaint: "Follow-up", notes: "No medications listed." }),
    }));
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/analyze"))).toHaveLength(2);
  });

  it("keeps an unsuccessful save editable and preserves the old analysis", async () => {
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/analyze")) return response({ analysis: analysis(visit.notes, [mention(visit.notes, "aspirin")]) });
      if (options?.method === "PUT") return response({ error: "Could not save this visit" }, 503);
      return response({ visit });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderVisit();
    await screen.findByRole("button", { name: /aspirin.*Show details/ });
    await user.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await user.clear(screen.getByRole("textbox", { name: "Visit notes" }));
    await user.type(screen.getByRole("textbox", { name: "Visit notes" }), "Edited draft");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    expect(await screen.findByText("Could not save this visit")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Visit notes" })).toHaveValue("Edited draft");
    await waitFor(() => expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: /aspirin.*Show details/ })).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/analyze"))).toHaveLength(1);
  });
});
