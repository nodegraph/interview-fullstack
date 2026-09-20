import { Fragment, useEffect, useId, useMemo, useState } from "react";
import type { MedicationMention, NoteAnalysis } from "../types";

interface MedicationNotesProps {
  visitId: string;
  note: string;
}

/** Backend offsets count Unicode code points, rather than JavaScript UTF-16 units. */
function getNoteParts(note: string, mentions: MedicationMention[]) {
  const characters = Array.from(note);
  const parts: { text: string; mention?: MedicationMention }[] = [];
  let cursor = 0;

  for (const mention of [...mentions].sort((a, b) => a.start - b.start)) {
    if (
      !Number.isInteger(mention.start) ||
      !Number.isInteger(mention.end) ||
      mention.start < cursor ||
      mention.end <= mention.start ||
      mention.end > characters.length ||
      characters.slice(mention.start, mention.end).join("") !== mention.text
    ) {
      continue;
    }
    if (mention.start > cursor) {
      parts.push({ text: characters.slice(cursor, mention.start).join("") });
    }
    parts.push({ text: mention.text, mention });
    cursor = mention.end;
  }

  parts.push({ text: characters.slice(cursor).join("") });
  return parts;
}

function MentionDetails({ mention }: { mention: MedicationMention }) {
  const medication = mention.medication;
  return (
    <>
      <p className="font-semibold text-gray-900">
        {medication?.name ?? mention.text}
      </p>
      {mention.correction && mention.match_type === "misspelling" && (
        <p className="mt-2 text-sm text-amber-900">
          Spelling correction: <span className="font-medium">{mention.text}</span>
          {" → "}
          <span className="font-medium">{mention.correction}</span>
        </p>
      )}
      {!mention.matched || !medication ? (
        <p className="mt-2 text-sm text-gray-600">
          A medication mention was detected, but no confident RxNorm match was found.
        </p>
      ) : (
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-gray-500">Written as</dt>
          <dd className="break-words text-gray-800">{mention.text}</dd>
          <dt className="text-gray-500">RxCUI</dt>
          <dd className="text-gray-800">{medication.rxcui}</dd>
          <dt className="text-gray-500">Term type</dt>
          <dd className="text-gray-800">{medication.tty}</dd>
          <dt className="text-gray-500">Brand names</dt>
          <dd className="break-words text-gray-800">
            {medication.brand_names.length ? medication.brand_names.join(", ") : "Not available"}
          </dd>
          <dt className="text-gray-500">Drug class</dt>
          <dd className="break-words text-gray-800">{medication.drug_class || "Not available"}</dd>
          <dt className="text-gray-500">Match</dt>
          <dd className="capitalize text-gray-800">{mention.match_type}</dd>
          <dt className="text-gray-500">Source</dt>
          <dd className="text-gray-800">{medication.source}</dd>
          {mention.strength && (
            <>
              <dt className="text-gray-500">Strength in note</dt>
              <dd className="text-gray-800">{mention.strength}</dd>
            </>
          )}
          {mention.dose_form && (
            <>
              <dt className="text-gray-500">Form in note</dt>
              <dd className="text-gray-800">{mention.dose_form}</dd>
            </>
          )}
        </dl>
      )}
    </>
  );
}

export default function MedicationNotes({ visitId, note }: MedicationNotesProps) {
  const [analysis, setAnalysis] = useState<NoteAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<MedicationMention | null>(null);
  const detailsId = useId();

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setAnalysis(null);
    setSelected(null);
    setError(null);
    setAnalyzing(true);

    const analyze = async () => {
      try {
        const response = await fetch(`/api/visits/${visitId}/analyze`, {
          method: "POST",
          signal: controller.signal,
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `Analysis failed (${response.status})`);
        }
        const result: NoteAnalysis | undefined = data.analysis;
        if (!result?.implemented || !Array.isArray(result.mentions)) {
          throw new Error("Medication analysis is unavailable. Please try again.");
        }
        if (result.note !== note || result.visit_id !== visitId) {
          throw new Error("The note changed during analysis. Refresh this visit to analyze the latest version.");
        }
        if (active) setAnalysis(result);
      } catch (cause) {
        if (active && !controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "Could not analyze medications. Please try again.");
        }
      } finally {
        if (active) setAnalyzing(false);
      }
    };

    void analyze();
    return () => {
      active = false;
      controller.abort();
    };
  }, [visitId, note, retry]);

  const parts = useMemo(
    () => getNoteParts(note, analysis?.note === note ? analysis.mentions : []),
    [note, analysis],
  );
  const mentions = parts.flatMap((part) => part.mention ? [part.mention] : []);
  const unresolved = mentions.filter((mention) => !mention.matched).length;

  return (
    <>
      <p className="whitespace-pre-wrap leading-relaxed text-gray-700">
        {parts.map((part, index) => {
          const mention = part.mention;
          if (!mention) return <Fragment key={index}>{part.text}</Fragment>;
          const isSelected = selected === mention;
          return (
            <button
              key={`${mention.start}-${mention.end}`}
              type="button"
              onClick={() => setSelected(mention)}
              onFocus={() => setSelected(mention)}
              aria-controls={detailsId}
              aria-pressed={isSelected}
              aria-label={`${mention.text}: ${mention.matched ? mention.medication?.name ?? "matched medication" : "unresolved medication"}. Show details.`}
              className={`rounded px-0.5 text-left font-medium [white-space:inherit] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${
                mention.matched ? "bg-blue-100 text-blue-900 hover:bg-blue-200" : "bg-amber-100 text-amber-900 underline decoration-dotted hover:bg-amber-200"
              } ${isSelected ? "ring-2 ring-blue-500" : ""}`}
            >
              {part.text}
            </button>
          );
        })}
      </p>

      <div className="mt-5 border-t pt-4">
        <h3 className="text-sm font-semibold text-gray-900">Medication mentions</h3>
        {analyzing && (
          <p role="status" className="mt-2 flex items-center gap-2 text-sm text-gray-500">
            <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-blue-600 motion-reduce:animate-none" />
            Analyzing medications…
          </p>
        )}
        {error && (
          <div role="alert" className="mt-2 rounded-md bg-red-50 p-3 text-sm text-red-800">
            <p>{error}</p>
            <button
              type="button"
              onClick={() => setRetry((value) => value + 1)}
              className="mt-2 font-medium underline hover:no-underline"
            >
              Retry analysis
            </button>
          </div>
        )}
        {!analyzing && !error && analysis && (
          mentions.length === 0 ? (
            <p role="status" className="mt-2 text-sm text-gray-500">No medication mentions found.</p>
          ) : (
            <>
              <p className="mt-2 text-sm text-gray-500">
                {mentions.length} mention{mentions.length === 1 ? "" : "s"} highlighted
                {unresolved ? ` · ${unresolved} unresolved` : ""}.
                {" "}Select a highlight to view details.
              </p>
              <div id={detailsId} className="mt-3 rounded-lg border border-blue-100 bg-blue-50/40 p-4" aria-live="polite">
                {selected ? (
                  <MentionDetails mention={selected} />
                ) : (
                  <p className="text-sm text-gray-500">Medication details appear here.</p>
                )}
              </div>
            </>
          )
        )}
      </div>
    </>
  );
}
