# Hardware Set Extractor

Extracts **hardware sets** (door-hardware groups) from Division 08 construction specbooks: for every set the set
number, description, where it sits (page + bounding box) and its components (qty, description, catalog number,
manufacturer, finish, notes). Built for the Fresco coding challenge (`prompt.txt`).

> **Status - read this first**
> * The **deterministic** half (PDF parsing, header detection, page selection, furniture filtering, continuation
>   context, dry run, API/UI shell, evaluator) is implemented and covered by **234 offline tests**.
> * The **semantic** half (Claude reads the prepared text and returns components) has now been **run for real on three
>   specbooks** - see [Results of the first real runs](#results-of-the-first-real-runs). Accuracy was scored against
>   hand-verified truth for **5 sets (27 rows) of one document only**; the other results are consistency checks, not
>   accuracy figures. Please read the caveats there before quoting any number.
> * No OCR, no catalog-code resolution (see [Known limitations](#known-limitations)).

---

## Architecture

```
                         DETERMINISTIC (offline, tested, no API)                          |  SEMANTIC (needs API key)
                                                                                          |
PDF -> layout extraction -> furniture filtering -> header detection -> candidate-page      |
       (PyMuPDF words,      (flag repeated         (set/group header   selection          |
        lines, bboxes,       headers/footers,       lines, NOT USED)   (hybrid evidence)   |
        stable line ids)     keep them traceable)                                          |
   -> chunk + continuation context -> [ Claude: structured extraction ] -> validation -> API / UI
      (4-page chunks, 1 overlap;       (JSON schema; cites source        (grounding,    (FastAPI,
       "this chunk starts inside        line ids, never coordinates)      mfr/finish     Streamlit
       set X" block; split-safe)                                          cross-check,   review UI)
                                                                          bbox from ids)
```

Everything left of the `|` runs without a network. `python -m hwsets ... --dry-run` runs the whole left side and
prints **exactly** the message that would be sent to the model, without sending it.

| Module (`src/hwsets/`) | Role |
|---|---|
| `ingest.py` | PyMuPDF words -> visual lines with stable ids (`<page>-<n>`), bboxes and ` \| ` column-gap markers |
| `furniture.py` | flags repeated running headers/footers/page numbers (kept, but hidden from the model) |
| `textnorm.py` | model-facing text normalisation (removes private-use icon glyphs; source text untouched) |
| `headers.py` | deterministic set-header detector, NOT USED detection, `ChunkContext` / `ContextResolver` |
| `pages.py` | hybrid candidate-page selection, chunking |
| `prompts.py` | system prompt, continuation block, the exact user message |
| `extractor.py` | Claude call (structured outputs via `messages.parse`), split-and-retry |
| `postprocess.py`, `merge.py`, `codes.py` | grounding checks, bbox from cited lines, mfr/finish cross-check, merging chunks |
| `pipeline.py` | orchestration -> `ExtractionResult` |
| `dryrun.py` | offline "what would be sent" report |
| `evaluate.py` | offline scoring against **manual** ground truth |
| `api.py`, `ui/app.py` | FastAPI backend, Streamlit review UI |
| `mock.py` | **dev-only** stand-in extractor for the API/UI (not real extraction) |

---

## Setup

**Python 3.12 or newer** (developed and tested on 3.14.6).

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -e ".[dev,ui]"      # [ui] adds Streamlit; the API and CLI alone need only the base install
```

`pip install -e .` builds a small package in place. If your filesystem blocks that (we hit a Windows
`WinError 32` file lock on `src/hwsets.egg-info` on one drive), use the no-build route - it needs nothing but the
dependency list:

```bash
pip install -r requirements-dev.txt
# then tell Python where the package is:
#   Windows cmd:  set PYTHONPATH=src        PowerShell:  $env:PYTHONPATH = "src"        macOS/Linux:  export PYTHONPATH=src
```

`pytest` needs neither (it sets `pythonpath = ["src"]` itself). To reproduce the exact tested environment add
`-c constraints-tested.txt`. Verified: a fresh virtualenv installed from `pyproject.toml` alone (on newer FastAPI
than the pinned one) passes the full suite.

Put the source specbooks in `data/` (git-ignored); tests that use them skip themselves when they are absent.

### Configuration (environment variables or a `.env` file - see `.env.example`)

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY` | **Only** for live extraction. Never needed for tests, dry run, evaluation, API mock mode or the UI |
| `HWSETS_MODEL` | Claude model id for live extraction (default `claude-sonnet-5-5`) |
| `HWSETS_MOCK=1` | API dev mode: use the mock extractor (results are stamped `[MOCK]`) |
| `HWSETS_OUTPUT_DIR` | where dry-run files and stored API results go (default `./outputs`) |
| `HWSETS_API` | URL the Streamlit UI talks to (default `http://localhost:8000`) |

Never commit a real key: `.env` is git-ignored and `.env.example` holds an empty value.

---

## Offline usage (no API key, no network)

```bash
pytest                                                            # 234 offline tests (the live-API test is opt-in: pytest -m live), ~30 s

# Dry run: shows exactly what would be sent to the model. Writes outputs/<name>[_pN-M].dryrun.json and .txt
python -m hwsets "data/Project Manual (1).pdf" --dry-run --pages 285-294
python -m hwsets data/book.pdf --dry-run                          # auto-selects the hardware pages
```

The dry run prints `DRY RUN — NO EXTERNAL API CALLS` and reports, per chunk: pages, the exact user message,
every line's id / original text / model text / bounding box, detected headers, and the continuation context.

## Live usage

Requires an Anthropic API key, sent only to Anthropic's API by the SDK. Put it in the git-ignored `.env` (see
`.env.example`); if the key is not scoped to a workspace also set `ANTHROPIC_WORKSPACE_ID`.

```bash
python -m hwsets "data/Project Manual (1).pdf" --pages 285-294 -o outputs/lyons.json
python -m hwsets.evaluate --expected data/ground_truth/lyons_first5.json --actual outputs/lyons.json
```

Without a key the CLI prints a `configuration error` (exit 2) and `POST /extract` returns HTTP 503; an Anthropic-side
failure (billing, rate limit, workspace) prints one readable line (exit 3) / HTTP 502. `pytest` never calls the API:
the live test is opt-in (`pytest -m live`).

## Results of the first real runs

Model `claude-sonnet-5-5`, default settings, one run per document (Valor twice), 4-page chunks. Wall time 38-61 s per
document. **Read the caveats under each table.**

**Lyons (`Project Manual (1).pdf`, pages 285-294) - scored against hand-verified truth**

| Measure | Result |
|---|---|
| Sets found / expected by the header detector | 29 / 29, identical ids and order |
| NOT USED sets | 4 / 4 (05, 16, 21, 22) |
| Components extracted | 142; equals the number of quantity rows counted independently in the source |
| Hand-verified subset (5 sets, 27 rows, `data/ground_truth/lyons_first5.json`) | set recall 5/5, status 5/5, pages 5/5, row recall 27/27 |
| Field accuracy on that subset | qty 27/27, description 27/27, catalog 25/25, **manufacturer 27/27, finish 27/27**, mfr/finish swaps 0 |
| Agreement with an independent coordinate-based reading of all sets | 137 of 138 comparable rows; the one difference is a wrapped catalog qualifier (below) |

Caveats: only **5 of 29** sets are hand-verified, all from one clean table layout, so this is a small sample. Description
and catalog number are the keys used to match rows, so they are not independent measures; qty, manufacturer and finish
are. The coordinate-based reading is a consistency check written by us, not truth. 2 catalog cells were deliberately left
unlabelled (see below).

**Ryan (`087100 - Door Hardware-6.pdf`, pages 24-46) and Valor (`087100-DOOR-HARDWARE_Rev_2.pdf`, pages 7-18) - no
hand-labelled truth yet, so these are checks, not accuracy figures**

| Check | Ryan | Valor |
|---|---|---|
| Sets found vs header detector | 38 / 38, same ids and order | 37 / 37, same ids and order |
| Components | 270 | 243 |
| Missing quantities kept as null | 42 rows; no printed number was ever nulled and no quantity lacks a printed number on a cited line | 1 row |
| Column that does not exist stays empty | finish null on all 270 rows | manufacturer and catalog null on all 243 rows |
| Sets spanning a page break | EX-2.0 located on pages 24-25 | 10 sets located on two pages (03, 08, 12, 14, 17, 20, 25, 28, 33, 35) |
| Row warnings | 0 | 0 |

Stability: a second Valor run produced the same 37 sets and 243 rows with **identical qty, catalog, manufacturer and
finish on every row**; only 65 free-text `notes` and one description word differed.

Ryan's active Set 15.0 has no components because its source contains only `Description: Con-Ed Opening` and a note that
all hardware is by the door manufacturer.

**Known issues seen in these runs (not fixed):**
* The unit `EA` is written into `notes` on every Lyons row, in two formats (`Unit: EA` and `EA`). `notes` is the least
  stable field between runs and is left unlabelled in the ground truth.
* The same wrapped catalog qualifier `(NRP AT OUTSWING LOCKING DOORS)` was kept inside `catalog_number` in Lyons Set 15
  but moved to `notes` in Set 27. Whether it belongs in the catalog number is a labelling decision that is still open.
* The validation step wrongly flagged that wrapped catalog and a printed `---` finish as "not found in source lines".
  Both were bugs in our check, fixed and covered by tests.

## API

```bash
HWSETS_MOCK=1 uvicorn hwsets.api:app --port 8000        # dev mode; drop HWSETS_MOCK for live (needs the key)
# Windows PowerShell:  $env:HWSETS_MOCK="1"; uvicorn hwsets.api:app --port 8000
```

| Endpoint | |
|---|---|
| `GET /health` | `{"ok": true, "mock": bool}` |
| `POST /extract?pages=40-52` | multipart PDF upload -> `{id, result}` (`pages` optional; skips page detection) |
| `GET /results/{id}` / `PUT /results/{id}` | read / save a (human-corrected) `ExtractionResult` |
| `GET /results/{id}/page/{n}.png?zoom=1.5` | rendered page (404 when out of range) |

## Deploying the API on Vercel

The FastAPI backend can be hosted on Vercel. The repository already contains what Vercel needs: **`app.py`** (the
entrypoint Vercel auto-detects; it mounts `src/hwsets/api.py`), **`vercel.json`** (a 300 s function timeout, because a real
extraction takes 40-60 s) and **`.vercelignore`**. The runtime dependencies were deliberately kept small
(`requirements.txt`, about 106 MB installed) - the Streamlit UI stack is a separate extra.

1. In Vercel, import the GitHub repository. Leave the build command empty and the root directory at the repository root.
2. Add **Environment Variables** in the Vercel project settings. Never commit these; `.env` is git-ignored.

| Variable | Value | Notes |
|---|---|---|
| `HWSETS_MOCK` | `1` | **Safest public demo:** no Claude calls, no key needed. Results are labelled `[MOCK]`. |
| `ANTHROPIC_API_KEY` | your key | Only for live extraction. |
| `ANTHROPIC_WORKSPACE_ID` | `wrkspc_...` | Only if the key is not scoped to a workspace. |
| `HWSETS_ACCESS_TOKEN` | a long random string | **Set this whenever a key is configured.** `/extract` and `PUT /results` then require the header `x-access-token: <token>`; otherwise anyone with the URL could spend your API credits. |

3. Deploy, then open `https://<your-project>.vercel.app/` (a short JSON page) and `/docs` (interactive API docs).

```bash
curl -F "file=@small.pdf" -H "x-access-token: <token>" "https://<your-project>.vercel.app/extract?pages=1-5"
```

Limits to know about: Vercel functions accept only small request bodies (about 4.5 MB), so large specbooks cannot be uploaded
- send a trimmed PDF; results are stored in the function's temporary `/tmp` and are not durable; and the Streamlit UI
cannot run on Vercel (run it locally against the deployed or local API with `HWSETS_API=<url>`).

## UI

```bash
streamlit run ui/app.py            # the API above must be running
```

Upload a PDF, extract, then per set: the page image with its bounding box drawn, an editable component table with
per-row confidence and warnings, and **Save corrections**. In mock mode the sets come from a header regex only and
are labelled as such.

## Evaluation and ground truth (offline)

Ground truth is **labelled by a human**; nothing in this repo treats machine output as truth.

```bash
# 1. blank skeleton: page/line hints only, no answers, verified=false
python -m hwsets.evaluate --scaffold data/ground_truth/book.json --pdf data/book.pdf --pages 285-294 --sets 05,06
# 2. a person reads the PDF, fills the file in, sets "verified": true
# 3. score any extraction JSON
python -m hwsets.evaluate --expected data/ground_truth/book.json --actual outputs/book.json [--json report.json]
```

Format (details and an invented example in `data/ground_truth/README.md`): a field that is **absent is
unlabelled and never scored**; an explicit `null` is a real expectation (e.g. "no quantity printed"). Files that
are not `verified` or still contain `<FILL IN>` are refused.

Metrics: set recall / missing / extra, component recall, per-field accuracy for qty, description, catalog number,
**manufacturer and finish**, **mfr/finish swap count**, plus warnings. Sets match on the normalised set number;
components match by strict tiers (catalog+description, catalog, description) - **no fuzzy matching**. No single
"overall score" is computed, on purpose. Two skeletons are ready for labelling
(`data/ground_truth/lyons_project_manual.json`, `ryan_door_hardware_6.json`); both are `verified: false`.

---

## Design decisions

* **Stable source line ids + bounding boxes everywhere.** The model is shown `[12-7] text` lines and must cite ids,
  never coordinates (models are poor at geometry). A set's location is then computed from the real boxes of the
  lines it cites - per page, so a multi-page set gets one box per page - and every extracted value can be traced
  back to a spot on the page (and checked: the cited text must actually contain the catalog number / quantity).
* **Page furniture is flagged, not deleted.** Repeated running headers/footers would otherwise stretch a set's box
  over the footer and pollute the model input. But deleting them would destroy traceability, so they stay in the
  data (`Line.furniture`), keep their ids and coordinates, and are only hidden from the model and from locations.
  The rule is deliberately conservative - see the structural guard below.
* **Continuation sets.** Sets cross page breaks (in one sample, 10 of 11 page transitions began mid-set). Chunks
  overlap by one page, and each chunk carries a short deterministic block - *"this chunk begins inside set X,
  header at line [7-43]"* - derived from header order. It is recomputed for every sub-chunk when a chunk is split
  and retried, is closed by `END OF SECTION` or a gap of non-candidate pages, and flags a NOT USED set. It is
  guidance only; it never supplies quantities, components or values.
* **Manufacturer vs. finish must come from context, not a single code.** `PE` is Pemko (mfr) or Painted Enamel
  (finish); `NO` is Norton or the word "No". The model is asked to decide the *column roles* first (a column of
  MK/LCN/SCH is a manufacturer column; US26D/630/BSP a finish column) and then apply that to each row. The code
  lexicon in `codes.py` is only a cross-check (whole-column swap repair, row flags) and never classifies an
  ambiguous code on its own.
* **Null quantities are preserved.** Many rows print no quantity ("By Door Manufacturer"). The schema allows
  `qty: null`, the prompt forbids guessing, and post-processing nulls any quantity that does not appear in the
  cited source lines. The evaluator treats an expected `null` as a real expectation.
* **Header detection is deterministic and independent of the model.** It sets the expected number of sets, drives
  page selection and continuation, and is the model-free check that extraction found every set.
* **Candidate pages use several kinds of evidence** (headers, table-shaped rows, short gaps, section boundaries) so
  one unrecognised header style cannot hide pages, while prose that merely mentions hardware is not selected.
* **Furniture guard.** A repeated line counts as furniture only if it forms an unbroken run at the very top or
  bottom of the page. (A first version hid `For use on Door #(s):`, which repeats under every group header - caught
  in an audit and fixed; regression tests cover it.)

## Known limitations

Be sceptical of anything not listed as tested above.

* **Accuracy is measured on a small sample.** Real-model results are hand-verified for 5 sets of one document; the
  other two documents have consistency checks only. One run per document (two for Valor); the model can vary between
  calls, mostly in `notes`. Nothing has been run on the other 40 sample specbooks.
* **No OCR.** Scanned/image-only PDFs return a warning and no sets. None of the sample hardware sections needed it.
* **No catalog/spec-code resolution** (the challenge's bonus item).
* **Unseen set-header styles exist.** The detector covers `Hardware Group No. 21A`, `Set: EX-1.0`, `Set #AL 01`,
  `HW 04A`, `PART 79 - HARDWARE GROUP NO. C201EZ`, `3.2 Hardware Set 5` and similar. Headers with other wording
  (e.g. `Door Group 5`, `Opening Type A-1`) are not recognised - page selection then falls back to table-shaped
  rows, then keywords. Per-door schedules (`Heading #N` followed by `Item #N | door ...`) *are* detected as
  headers, but they list one block per door rather than shared sets, so whether each block should count as a
  "set" is an open modelling question. Prose whose wording mimics a header could also be mis-detected.
* **Regression-tested on three specbooks only** (out of the 43 sample PDFs, which were skimmed by hand during
  discovery). Behaviour on the others is unmeasured.
* **Manufacturer/finish ambiguity still needs semantic validation.** The lexicon is small and unexpanded (e.g. IVE, GLY
  are unknown to it), and `PE` never appears as a bare token in the three test documents, so that case is untested
  on real data.
* **Locations are line-level.** A set's box is the union of the lines it cites, not per-cell table geometry.
* **Furniture needs 3+ pages** to be detected; a page whose first line repeats identically on every page at the same
  height is indistinguishable from a running header and will be hidden.
* **Sets are matched across chunks by set number.** If the model reports a carried set under a different id, the
  merge will not join the halves.
* **Mock mode is not extraction**: it splits at header lines and copies the first cell of pipe-separated rows.
* **Packaging:** `pip install -e .` failed once on one Windows drive with a file-lock error unrelated to the code;
  see Setup for the no-build alternative.

## Repository layout

```
src/hwsets/         library + CLI (python -m hwsets)      tests/            pytest suite (offline)
ui/app.py           Streamlit review UI                   data/             specbooks (ignored) + ground_truth/ (tracked)
prompt.txt          the challenge statement               outputs/          generated, git-ignored
pyproject.toml      package + pytest config               requirements*.txt, constraints-tested.txt
```
