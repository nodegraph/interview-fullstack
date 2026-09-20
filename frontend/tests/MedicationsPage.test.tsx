import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import MedicationsPage from "../src/pages/MedicationsPage";
import { deferred, mention, response } from "./fixtures";

describe("catalog import status", () => {
  it("surfaces incomplete enrichment even when the ingredient import succeeds", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url === "/api/medications/import"
      ? response({ job: {
        state: "done", phase: "done", created: 120, updated: 4,
        warning_count: 2, warnings: ["Unable to fetch brand names for ingredient 1191."],
      } })
      : response({ medications: [], total: 124 })));
    render(<MemoryRouter><MedicationsPage /></MemoryRouter>);
    expect(await screen.findByRole("status")).toHaveTextContent("Import finished with warnings: 120 created, 4 updated.");
    expect(screen.getByText(/2 enrichment requests failed/)).toBeInTheDocument();
    expect(screen.getByText("Unable to fetch brand names for ingredient 1191.")).toBeInTheDocument();
  });

  it("does not replace search results with an older catalog response", async () => {
    const first = deferred<Response>();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/medications/import") return response({ job: null });
      if (url.includes("search=missing")) return response({ medications: [], total: 0 });
      return first.promise;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<MemoryRouter><MedicationsPage /></MemoryRouter>);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.startsWith("/api/medications?"))).toBe(true));
    await user.type(screen.getByRole("searchbox"), "missing");
    expect(await screen.findByText("No concepts match “missing”.")).toBeInTheDocument();
    await act(async () => { first.resolve(response({ medications: [mention("aspirin", "aspirin").medication], total: 1 })); });
    expect(screen.getByText("No concepts match “missing”.")).toBeInTheDocument();
    expect(screen.queryByText("aspirin")).not.toBeInTheDocument();
  });
});
