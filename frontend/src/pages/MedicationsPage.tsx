import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ImportJob, Medication } from "../types";

const PAGE_SIZE = 50;

const PHASE_LABELS: Record<string, string> = {
  starting: "Starting",
  ingredients: "Fetching ingredients",
  classes: "Mapping drug classes",
  brands: "Mapping brand names",
  writing: "Writing to database",
  enriching: "Writing classes and brands",
  done: "Done",
};

export default function MedicationsPage() {
  const [medications, setMedications] = useState<Medication[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [newNames, setNewNames] = useState("");
  const [scraping, setScraping] = useState(false);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async (term: string, from: number) => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(from),
      });
      if (term.trim()) {
        params.set("search", term.trim());
      }
      const res = await fetch(`/api/medications?${params}`);
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setMedications(data.medications || []);
        setTotal(data.total ?? 0);
        setError(null);
      }
    } catch (e) {
      console.error("Failed to load medications:", e);
      setError("Could not load the catalog.");
    }
    setLoading(false);
  }, []);

  // Debounce search so typing does not fire a query per keystroke.
  useEffect(() => {
    const handle = setTimeout(() => {
      setOffset(0);
      void load(search, 0);
    }, 250);
    return () => clearTimeout(handle);
  }, [search, load]);

  const pollJob = useCallback(() => {
    if (pollRef.current !== null) {
      return;
    }
    pollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch("/api/medications/import");
        const data = await res.json();
        const current: ImportJob | null = data.job ?? null;
        setJob(current);
        if (current && current.state !== "running") {
          window.clearInterval(pollRef.current!);
          pollRef.current = null;
          void load(search, 0);
          setOffset(0);
        }
      } catch (e) {
        console.error("Failed to poll import:", e);
      }
    }, 1500);
  }, [load, search]);

  // Pick up an import already running when the page opens.
  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch("/api/medications/import");
        const data = await res.json();
        if (data.job) {
          setJob(data.job);
          if (data.job.state === "running") {
            pollJob();
          }
        }
      } catch {
        /* status is best-effort */
      }
    };
    void check();
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [pollJob]);

  const startImport = async (scope: "all" | "prescribable") => {
    setError(null);
    try {
      const res = await fetch("/api/medications/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      }
      if (data.job) {
        setJob(data.job);
      }
      pollJob();
    } catch (e) {
      console.error("Import failed to start:", e);
      setError("Could not start the import.");
    }
  };

  const scrapeNames = async () => {
    const names = newNames
      .split(",")
      .map((n) => n.trim())
      .filter(Boolean);
    if (names.length === 0) {
      return;
    }
    setScraping(true);
    setError(null);
    try {
      const res = await fetch("/api/medications/scrape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
      });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
      } else {
        setNewNames("");
        void load(search, 0);
        setOffset(0);
      }
    } catch (e) {
      console.error("Scrape failed:", e);
      setError("Scrape failed. RxNav may be unreachable.");
    }
    setScraping(false);
  };

  const goTo = (next: number) => {
    setOffset(next);
    void load(search, next);
  };

  const running = job?.state === "running";
  const pct =
    running && job && job.total > 0
      ? Math.round((job.processed / job.total) * 100)
      : 0;

  return (
    <main className="max-w-6xl mx-auto p-8">
      <Link to="/dashboard" className="text-blue-600 hover:underline text-sm">
        ← Back to My Patients
      </Link>

      <div className="mt-4 mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">RxNorm Catalog</h1>
          <p className="text-gray-600 mt-1">
            {total.toLocaleString()} normalized concepts · the set of targets
            that visit-note medications are matched against
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => void startImport("prescribable")}
            disabled={running}
            className="border border-gray-300 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-100 disabled:opacity-50"
          >
            Import prescribable
          </button>
          <button
            onClick={() => void startImport("all")}
            disabled={running}
            className="bg-blue-600 text-white px-5 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            Import full RxNorm
          </button>
        </div>
      </div>

      {job && running && (
        <div className="mb-6 rounded-lg bg-blue-50 px-4 py-3">
          <div className="flex justify-between text-sm text-blue-900">
            <span>{PHASE_LABELS[job.phase] ?? job.phase}</span>
            <span>
              {job.total > 0
                ? `${job.processed.toLocaleString()} / ${job.total.toLocaleString()}`
                : "…"}
            </span>
          </div>
          <div className="mt-2 h-2 w-full rounded bg-blue-100">
            <div
              className="h-2 rounded bg-blue-600 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="mt-2 text-xs text-blue-700">
            Runs in the background — you can leave this page. A full import is
            about 15,000 concepts and takes a couple of minutes.
          </p>
        </div>
      )}

      {job && job.state === "done" && (
        <div className={`mb-6 rounded px-4 py-3 text-sm ${job.warning_count ? "bg-amber-50 text-amber-900" : "bg-emerald-50 text-emerald-800"}`} role="status">
          <p>
            Import finished{job.warning_count ? " with warnings" : ""}: {job.created.toLocaleString()} created,{" "}
            {job.updated.toLocaleString()} updated.
          </p>
          {!!job.warning_count && (
            <>
              <p className="mt-2">{job.warning_count} enrichment request{job.warning_count === 1 ? "" : "s"} failed. Existing details were preserved; retry the import to fill missing details.</p>
              {!!job.warnings?.length && (
                <ul className="mt-2 list-disc pl-5">
                  {job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
                </ul>
              )}
            </>
          )}
        </div>
      )}

      {job && job.state === "error" && (
        <p className="mb-6 rounded bg-red-50 px-4 py-3 text-sm text-red-700">
          Import failed: {job.error}
        </p>
      )}

      {error && (
        <p className="mb-6 rounded bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-sm font-medium text-gray-500 mb-2">
          Add specific concepts
        </h2>
        <div className="flex flex-wrap gap-3">
          <input
            type="text"
            value={newNames}
            onChange={(e) => setNewNames(e.target.value)}
            placeholder="e.g. montelukast, duloxetine, rosuvastatin"
            className="flex-1 min-w-64 border rounded-lg px-4 py-2"
          />
          <button
            onClick={() => void scrapeNames()}
            disabled={scraping || newNames.trim() === ""}
            className="bg-gray-800 text-white px-5 py-2 rounded-lg hover:bg-gray-900 disabled:opacity-50"
          >
            {scraping ? "Scraping…" : "Scrape"}
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-400">
          Comma-separated ingredient names, resolved live against RxNav. For the
          whole catalog use the import buttons above.
        </p>
      </div>

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Search by name or brand…"
        className="w-full border rounded-lg px-4 py-2 mb-4"
      />

      <div className="bg-white rounded-lg shadow overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
              <th className="px-4 py-3 font-medium">RxCUI</th>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">TTY</th>
              <th className="px-4 py-3 font-medium">Drug class</th>
              <th className="px-4 py-3 font-medium">Brand names</th>
            </tr>
          </thead>
          <tbody>
            {medications.length === 0 && !loading && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-gray-500">
                  {search
                    ? `No concepts match “${search}”.`
                    : "Catalog is empty. Import from RxNorm above."}
                </td>
              </tr>
            )}
            {medications.map((med) => (
              <tr key={med.rxcui} className="border-b last:border-0 align-top">
                <td className="px-4 py-3 font-mono text-xs text-gray-500">
                  {med.rxcui}
                </td>
                <td className="px-4 py-3 font-medium text-gray-900">
                  {med.name}
                </td>
                <td className="px-4 py-3 text-gray-500">{med.tty}</td>
                <td className="px-4 py-3 text-gray-700">
                  {med.drug_class || <span className="text-gray-300">—</span>}
                </td>
                <td className="px-4 py-3 text-gray-600">
                  {med.brand_names.length > 0 ? (
                    med.brand_names.join(", ")
                  ) : (
                    <span className="text-gray-300">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {total > PAGE_SIZE && (
        <div className="mt-4 flex items-center justify-between text-sm">
          <button
            onClick={() => goTo(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0 || loading}
            className="border border-gray-300 px-4 py-2 rounded-lg hover:bg-gray-100 disabled:opacity-40"
          >
            ← Previous
          </button>
          <span className="text-gray-500">
            {(offset + 1).toLocaleString()}–
            {Math.min(offset + PAGE_SIZE, total).toLocaleString()} of{" "}
            {total.toLocaleString()}
          </span>
          <button
            onClick={() => goTo(offset + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total || loading}
            className="border border-gray-300 px-4 py-2 rounded-lg hover:bg-gray-100 disabled:opacity-40"
          >
            Next →
          </button>
        </div>
      )}
    </main>
  );
}
