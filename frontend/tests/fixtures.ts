import type { MedicationMention, NoteAnalysis } from "../src/types";

export function mention(note: string, text: string, overrides: Partial<MedicationMention> = {}): MedicationMention {
  const start = Array.from(note.slice(0, note.indexOf(text))).length;
  return {
    text,
    start,
    end: start + Array.from(text).length,
    matched: true,
    match_type: "exact",
    correction: null,
    confidence: null,
    medication: {
      rxcui: "1191", name: "aspirin", tty: "IN", synonym: null,
      brand_names: ["Bayer"], drug_class: "Nonsteroidal Anti-inflammatory Drug", source: "rxnorm",
    },
    ...overrides,
  };
}

export function analysis(note: string, mentions: MedicationMention[] = [], visitId = "visit-1"): NoteAnalysis {
  return { note, mentions, visit_id: visitId, implemented: true };
}

export function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}
