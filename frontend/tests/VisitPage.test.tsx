import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import VisitPage from "../src/pages/VisitPage";
import { analysis, deferred, mention, response } from "./fixtures";

const visit = {
  id: "visit-1", patient_id: "patient-1", clinician_id: "clinician-1",
  visit_date: "2026-09-19", chief_complaint: "Follow-up", notes: "aspirin",
  patient_name: "Sample Patient", patient_dob: "1970-01-01", patient_mrn: "MRN-1",
  clinician_name: "Sample Clinician", clinician_specialty: "Family Medicine",
  created_at: "2026-09-19T10:00:00Z", updated_at: "2026-09-19T10:00:00Z",
};

function renderVisit() {
  const router = createMemoryRouter([
    { path: "/patients/:id/visits/:visitId", element: <VisitPage /> },
  ], { initialEntries: ["/patients/patient-1/visits/visit-1"] });
  return { ...render(<RouterProvider router={router} />), router };
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

  it.each([200, 503])("ignores a previous visit's save response (%s) after navigation", async (status) => {
    const save = deferred<Response>();
    const secondVisit = { ...visit, id: "visit-2", notes: "Second visit notes." };
    const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
      if (options?.method === "PUT") return save.promise;
      const current = url.includes("visit-2") ? secondVisit : visit;
      if (url.endsWith("/analyze")) return response({ analysis: analysis(current.notes, [], current.id) });
      return response({ visit: current });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { router } = renderVisit();
    await screen.findByText("No medication mentions found.");
    await user.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    await user.clear(screen.getByRole("textbox", { name: "Visit notes" }));
    await user.type(screen.getByRole("textbox", { name: "Visit notes" }), "Saved first note");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await act(async () => { await router.navigate("/patients/patient-1/visits/visit-2"); });
    expect(await screen.findByText("Second visit notes.")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[1]);
    expect(screen.getByRole("button", { name: "Save Changes" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Visit notes" })).toBeEnabled();

    await act(async () => { save.resolve(response(status === 200
      ? { visit: { ...visit, notes: "Saved first note" } }
      : { error: "First visit save failed" }, status)); });
    expect(screen.getByRole("textbox", { name: "Visit notes" })).toHaveValue("Second visit notes.");
    expect(screen.queryByText("First visit save failed")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Second visit notes.")).toBeInTheDocument();
    expect(screen.queryByText("Saved first note")).not.toBeInTheDocument();
    const saveOptions = fetchMock.mock.calls.find(([, options]) => options?.method === "PUT")![1];
    expect(saveOptions?.signal?.aborted).toBe(true);
  });
});
