import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import MedicationNotes from "../src/components/MedicationNotes";
import { analysis, deferred, mention, response } from "./fixtures";

describe("MedicationNotes", () => {
  it("preserves Unicode, whitespace, punctuation, and every repeated occurrence", async () => {
    const note = "🩺 Café: aspirin\nThen aspirin.";
    const first = mention(note, "aspirin");
    const start = Array.from(note.slice(0, note.lastIndexOf("aspirin"))).length;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ analysis: analysis(note, [first, { ...first, start, end: start + 7 }]) })));
    const { container } = render(<MedicationNotes visitId="visit-1" note={note} />);

    expect(await screen.findAllByRole("button", { name: /aspirin.*Show details/ })).toHaveLength(2);
    expect(container.querySelector("p")).toHaveTextContent("🩺 Café: aspirin Then aspirin.");
    expect(container.querySelector("p")?.textContent).toBe(note);
    expect(screen.getByText(/2 mentions highlighted/)).toBeInTheDocument();
  });

  it("ignores overlapping, mismatched, and out-of-range spans without changing the note", async () => {
    const note = "Take aspirin.";
    const valid = mention(note, "aspirin");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ analysis: analysis(note, [
      { ...valid, start: 0, end: 4, text: "fake" }, valid, { ...valid },
      { ...valid, start: 100, end: 107 },
    ]) })));
    const { container } = render(<MedicationNotes visitId="visit-1" note={note} />);
    expect(await screen.findAllByRole("button", { name: /Show details/ })).toHaveLength(1);
    expect(container.querySelector("p")?.textContent).toBe(note);
  });

  it("highlights dosage with a misspelling but limits the correction to the drug name", async () => {
    const note = "Take asprin 81 mg tablet daily.";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ analysis: analysis(note, [mention(note, "asprin 81 mg tablet", {
      name_text: "asprin", match_type: "misspelling", correction: "aspirin", strength: "81 mg", dose_form: "tablet",
    })]) })));
    const user = userEvent.setup();
    render(<MedicationNotes visitId="visit-1" note={note} />);
    await user.click(await screen.findByRole("button", { name: /asprin 81 mg tablet/ }));
    expect(screen.getByText(/Spelling correction:/)).toHaveTextContent("Spelling correction: asprin → aspirin");
    expect(screen.getByText("81 mg")).toBeInTheDocument();
    expect(screen.getByText("tablet")).toBeInTheDocument();
    expect(screen.getByText("1191")).toBeInTheDocument();
  });

  it("opens brand details with the keyboard and does not label a brand as misspelled", async () => {
    const note = "Bayer continued.";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ analysis: analysis(note, [mention(note, "Bayer", { match_type: "brand" })]) })));
    const user = userEvent.setup();
    render(<MedicationNotes visitId="visit-1" note={note} />);
    const highlight = await screen.findByRole("button", { name: /Bayer.*Show details/ });
    await user.tab();
    await user.keyboard("{Enter}");
    expect(highlight).toHaveFocus();
    expect(highlight).toHaveAttribute("aria-pressed", "true");
    expect(document.getElementById(highlight.getAttribute("aria-controls")!)).toHaveTextContent("aspirin");
    expect(screen.getByText("Nonsteroidal Anti-inflammatory Drug")).toBeInTheDocument();
    expect(screen.queryByText(/Spelling correction:/)).not.toBeInTheDocument();
  });

  it("retains strength and form for an unresolved mention without inventing an RxCUI", async () => {
    const note = "Unknownol 5 mg tablet.";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ analysis: analysis(note, [mention(note, "Unknownol 5 mg tablet", {
      matched: false, match_type: "unknown", medication: null, strength: "5 mg", dose_form: "tablet",
    })]) })));
    const user = userEvent.setup();
    render(<MedicationNotes visitId="visit-1" note={note} />);
    await user.click(await screen.findByRole("button", { name: /unresolved medication/ }));
    expect(screen.getByText(/1 unresolved/)).toBeInTheDocument();
    expect(screen.getByText(/no confident RxNorm match/)).toBeInTheDocument();
    expect(screen.getByText("5 mg")).toBeInTheDocument();
    expect(screen.getByText("tablet")).toBeInTheDocument();
    expect(screen.queryByText("RxCUI")).not.toBeInTheDocument();
  });

  it("distinguishes a successful empty result from a loading or failed analysis", async () => {
    const request = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(request.promise));
    render(<MedicationNotes visitId="visit-1" note="No medicines listed." />);
    expect(screen.getByRole("status")).toHaveTextContent("Analyzing medications");
    expect(screen.queryByText("No medication mentions found.")).not.toBeInTheDocument();
    await act(async () => { request.resolve(response({ analysis: analysis("No medicines listed.") })); });
    expect(screen.getByRole("status")).toHaveTextContent("No medication mentions found.");
  });

  it("shows provider errors and retries successfully", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ error: "Medication provider unavailable" }, 503))
      .mockResolvedValueOnce(response({ analysis: analysis("aspirin", [mention("aspirin", "aspirin")]) }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MedicationNotes visitId="visit-1" note="aspirin" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Medication provider unavailable");
    expect(screen.queryByText("No medication mentions found.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry analysis" }));
    expect(await screen.findByRole("button", { name: /aspirin.*Show details/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows network failures without reporting an empty successful result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Connection lost")));
    render(<MedicationNotes visitId="visit-1" note="aspirin" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
    expect(screen.queryByText("No medication mentions found.")).not.toBeInTheDocument();
  });

  it.each([
    { label: "unimplemented response", result: { ...analysis("aspirin"), implemented: false }, error: /unavailable/ },
    { label: "different note", result: analysis("Different note"), error: /note changed/ },
    { label: "different visit", result: analysis("aspirin", [], "visit-2"), error: /note changed/ },
  ])("rejects $label", async ({ result, error }) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ analysis: result })));
    render(<MedicationNotes visitId="visit-1" note="aspirin" />);
    expect(await screen.findByRole("alert")).toHaveTextContent(error);
    expect(screen.queryByText("No medication mentions found.")).not.toBeInTheDocument();
  });

  it.each(["note", "visit"])("aborts and suppresses a stale response when the %s changes", async (changed) => {
    const first = deferred<Response>();
    const oldNote = "aspirin";
    const newNote = changed === "note" ? "No medications" : oldNote;
    const nextVisit = changed === "visit" ? "visit-2" : "visit-1";
    const fetchMock = vi.fn().mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce(response({ analysis: analysis(newNote, [], nextVisit) }));
    vi.stubGlobal("fetch", fetchMock);
    const { rerender } = render(<MedicationNotes visitId="visit-1" note={oldNote} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const oldSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    rerender(<MedicationNotes visitId={nextVisit} note={newNote} />);
    expect(oldSignal.aborted).toBe(true);
    expect(await screen.findByText("No medication mentions found.")).toBeInTheDocument();
    await act(async () => { first.resolve(response({ analysis: analysis(oldNote, [mention(oldNote, "aspirin")]) })); });
    expect(screen.queryByRole("button", { name: /Show details/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("aborts an in-flight analysis when unmounted", async () => {
    const fetchMock = vi.fn().mockReturnValue(new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<MedicationNotes visitId="visit-1" note="aspirin" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });

  it("starts only one analysis request under React StrictMode", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ analysis: analysis("No medications") }));
    vi.stubGlobal("fetch", fetchMock);
    render(<StrictMode><MedicationNotes visitId="visit-1" note="No medications" /></StrictMode>);
    expect(await screen.findByText("No medication mentions found.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
