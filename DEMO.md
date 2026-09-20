Three-minute demo script for the medication detection feature.

Before recording, open the latest local app at http://localhost:5173 or a deployment
containing these changes. Select a clinician, patient, and demo visit. Have
[transcript 01](test-transcripts/transcript-01.txt) and
[transcript 02](test-transcripts/transcript-02.txt) ready to copy. Save the original
note if you want to restore it afterward. Confirm analysis works once before
recording; the backend needs its configured LLM key.

1. **0:00–0:15 — Introduce the problem.**

   Show the visit page.

   Say: “Clinical notes mention medications by generic name, brand, abbreviation,
   and sometimes with spelling mistakes. This feature finds those mentions in
   the original note and connects them to structured RxNorm information.”

2. **0:15–1:05 — Show detection, matching, and dosage.**

   Click **Edit** beside Visit Notes, paste transcript 01, and click **Save Changes**.
   Wait for **7 mentions highlighted**. Click `lisinopril 20 mg`, `Lipitor`, `HCTZ`,
   and `hydrochlorothiazde` in turn.

   Say: “Saving the note automatically starts analysis. Here we have seven
   mentions. The highlight includes the written dose, and selecting it shows
   the normalized name, RxCUI, and available catalog details. Lipitor resolves
   to atorvastatin, while HCTZ resolves to hydrochlorothiazide. This misspelling
   also resolves correctly, with an explicit spelling correction. The original
   note stays exactly as written.”

   Check on screen: lisinopril has RxCUI `29046` and strength `20 mg`;
   `hydrochlorothiazde → hydrochlorothiazide` appears as the correction.

3. **1:05–1:50 — Show a messier note.**

   Replace the note with transcript 02 and save. Wait for **12 mentions highlighted**.
   Select `metopralol`, `Ventolin inhaler`, and `MTX`. Point out the separate
   `Coumadin` and `warfarin` highlights.

   Say: “This second note combines misspellings, brands, supplements, and
   shorthand. Metopralol is corrected to metoprolol. Ventolin resolves to
   albuterol, and the written inhaler form stays in the highlight. MTX resolves
   to methotrexate. Coumadin and warfarin are highlighted separately even though
   they refer to the same ingredient. We detect mentions, including held or
   historical medications, rather than presenting this as an active prescription
   list.”

4. **1:50–2:10 — Show the editing experience.**

   Click **Edit**, make a small change, then **Cancel**. Tab to a medication
   highlight to reveal its details using the keyboard.

   Say: “Canceling an edit restores the saved note and its existing analysis.
   The highlights also work from the keyboard. If analysis fails, the note
   remains readable and the user gets an explicit retry action.”

5. **2:10–2:40 — Show the catalog and explain the design.**

   Return to the dashboard, open **RxNorm Catalog**, and search for `metformin`.
   Point out the import controls. If you started an import before recording,
   show its progress or completion status; full enrichment can outlast this video.

   Say: “The model extracts names from the note. The backend validates their
   locations and matches them against an indexed catalog, so it doesn't send
   the whole catalog to the model. Missing names can use a bounded, verified
   RxNav lookup. The importer preserves existing details, retries transient
   failures, reports incomplete enrichment, and allows interrupted jobs to
   restart.”

6. **2:40–3:00 — Close with validation and limits.**

   Return to the highlighted note, or show TESTING.md.

   Say: “We verified both sample notes against the live model, passed 141 backend
   tests and 22 frontend tests, and checked migration preservation and live RxNav
   integration. The next improvements would be broader clinical-context
   evaluation and exact product matching. Today, strength and form describe
   what's written; they don't establish a specific prescription product.”

Allow a few extra seconds for live analysis. If a sample returns a different count
or an unresolved result, describe what is actually on screen and investigate it
instead of reading the expected result as though it passed.
