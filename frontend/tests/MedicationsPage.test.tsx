import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import MedicationsPage from "../src/pages/MedicationsPage";
import { response } from "./fixtures";

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
});
