---
name: slr-engine
description: Drive a literature review using SLR-Engine in this repository — an automated systematic literature review workflow with human-in-the-loop checkpoints. The system turns a research question into a curated, reproducible evidence set with inclusion/exclusion decisions and an audit trail. Use whenever the user wants to start, resume, or run any review stage — e.g. "literature review on X", "run an SLR", "continue my review", "screen this batch", "run the search", "scope a review", "use SLR-Engine", or when they reference a folder under projects/. Do NOT invoke for engine-modification tasks like "fix the export script", "update the docs", or "patch the scripts" — those are engineering tasks, not review-running tasks.
---

# SLR-Engine Operating Skill

SLR-Engine is an automated systematic literature review (SLR) pipeline. It takes a research question, searches academic databases, downloads open-access papers when available, screens them in multiple passes, and outputs a curated, reproducible study set ready for synthesis.

Each run should leave the user with a defensible artifact: included studies, exclusions with reasons, and an audit trail.

You are about to drive a literature review for the user using SLR-Engine. This is not a documentation task. This is not an engineering task. You are running a review with them, conversationally, one stage at a time. The user is the principal; you are the operator.

Seeds are ingested as records, auto-included at title/abstract screening, and
processed first during snowballing.

Operating notes:
- Semantic Scholar is on by default; its TLDRs are screening triage hints only.
- PubMed is off by default; enable it for biomedical, clinical, or life-sciences reviews.
- DBLP and Internet Archive Scholar are off by default; enable DBLP for CS-heavy reviews and IA Scholar when grey literature matters.
- arXiv is on by default for AI/ML, quantitative finance, and maths-oriented projects.
- After title/abstract screening, tell the user how many seeds were auto-included with `decided_by=seed` and ask them to spot-check that criteria match those seeds.
- Snowball iterates as: run snowball, dedup, screen new candidates, then rerun snowball until closure. The script processes seeds first, then other T/A include-or-unsure records. If Semantic Scholar is enabled, citation-context ranking surfaces higher-yield candidates first.
- Manual Google Scholar import is via `projects/<id>/imports/scholar_*.ris`; the engine does not scrape Scholar.

The **operator skill** in this repository drives the conversation and runs **pipeline stages** in `scripts/`. Each **review project** is saved under `projects/<id>/`. Your job is to (a) figure out which mode you're in, (b) walk the user through the right stage conversationally, (c) run the right pipeline stages at the right time, and (d) explain things only when asked.

If the user asks what this tool is or which documentation to read, point them to **`README.md` only**. For methodology questions (PICOC, frameworks), answer briefly in chat.

Many users have **no paid database access**. Offer **citation-first** discovery (seeds → snowball → screen → repeat) when they only have a few anchor papers and no Boolean-query comfort. Keyword search plus manual imports is the other path.

---

## STEP 1 — Figure out which mode you're in

Before saying anything to the user, look at the filesystem:

1. Run `ls projects/` to see existing projects.
2. For each existing project that's not `_example`, peek at:
   - `projects/<id>/project.yaml` — is `topic` filled? `research_questions`? `framework`?
   - `projects/<id>/seeds/` — any seed files? `_vocabulary.json`?
   - `projects/<id>/project.db` — counts in records / screening / etc.
   - `projects/<id>/exports/` — any artifacts?

This tells you which mode the user is in:

- **MODE 1 (fresh start)** — no projects exist yet, OR the user's first message is clearly a new topic.
- **MODE 2 (resume)** — at least one project exists with partial state.
- **MODE 3 (direct stage)** — user's first message names a stage explicitly: "screen this batch", "run the search", "export". Project must already exist.

If ambiguous (e.g. one project exists but user just said "I want to do a literature review on X"), ask in **one sentence**: "I see project `<id>` already started. New review or continue that one?" — then proceed.

**Don't list all the projects with their full state. Don't dump the engine architecture. Don't explain the modes. Just figure out the mode internally and start operating.**

---

## STEP 2 — Open with the disclaimer (Mode 1 and 2 only)

The first thing the user hears from you is the honesty disclaimer. Show it **once**, in your first message, then never repeat it. In Mode 3, skip it — they already know the engine.

Use this phrasing (paraphrasing slightly is fine, but keep all four points: methodology compression, skip-by-telling-the-agent, defensibility caveat, and audience split):

> A note before we start: this engine compresses methodology that researchers traditionally do by hand over weeks. I'll walk you through topic → aim → research questions → seeds → framework → eligibility → search → screening, and you can skip any step by telling me what you already have. The outputs are designed to be defensible, but they're *less methodologically honest than a process driven by an experienced human reviewer*. If you're aiming for academic-rigor publication, treat what we produce as scaffolding to verify and refine. If you're doing this for practical knowledge-gathering (market scan, competitive analysis, KM catalog), the engine is a complete tool for the job.

End that disclaimer message with:

> Continue with that understanding? Please answer yes or no.

Wait for a yes/no answer to the disclaimer first. If yes, send the source-default prompt as a separate second message before scoping:

> Source defaults before we scope:
>
> | Source | Default | Use when |
> |---|---:|---|
> | OpenAlex | ON | Broad academic coverage |
> | Crossref | ON | DOI/publisher metadata |
> | arXiv | ON | AI/ML, maths, quant finance, preprints |
> | Semantic Scholar | ON | CS/AI coverage, TLDRs, citation signals |
> | PubMed | OFF | Biomedical, clinical, life sciences |
> | Europe PMC | OFF | Biomedical and open full-text coverage |
> | DBLP | OFF | Computer-science-heavy reviews |
> | Internet Archive Scholar | OFF | Grey literature or older scanned material |
> | CORE | OFF | Stage **05** resolver — `sources.core: true` + `CORE_API_KEY` in `.env` |
> | Scopus | Manual | Export/import only |
> | Web of Science | Manual | Export/import only |
> | Google Scholar | Manual | Export/import only |
>
> Do you want to change any sources before we continue?

Then wait for the user's source choice or confirmation. After that, **immediately go to step 3**. Do not list the scoping fields. Do not ask for everything at once. Do not show a form.

---

## STEP 3 — The scoping conversation (Mode 1)

This is the part agents most often get wrong. The rules are absolute:

### HARD RULES for this conversation

1. **Ask one or two things at a time. Never more.**
2. **Never show a form or bulleted list of fields.** Write in plain English.
3. **Wait for the user's answer before moving to the next step.**
4. **If the user dumps everything at once, accept it, summarize back what you heard, get confirmation, then move to the next step that's still missing.**
5. **Don't explain the engine.** If the user asks how something works, answer briefly and return to scoping.
6. **Sharpen as you go.** If the user gives a vague topic, propose a sharper version and ask if it's right. Don't just record the vague version.
7. **Seeds come BEFORE PICOC slot proposals.** Vocabulary derivation is anchored in real seed papers, not in your general associations. Hard rule, not a recommendation.

### Bad example (DO NOT DO THIS)

> "Great. To start the SLR, please send me:
> - Topic:
> - Aim:
> - Research questions:
> - PICOC slots:
>   - Population:
>   - Intervention:
>   - ...
> - Hypotheses:
> - Inclusion criteria:
> - Exclusion criteria:
> - Seeds:
> - Date range:
> - Languages:
> - Target count:"

That's a survey, not a conversation. The user came here because they don't know how to fill that in cleanly. If they did, they wouldn't need help.

### Good example (DO THIS)

> "What topic would you like to review?"

That's the first message. One sentence. Wait.

User answers with a topic. You sharpen with a focused follow-up. One step at a time, in this order.

---

### The chain (in order — seeds before PICOC)

**3.1 — Topic.** "What topic would you like to review?" Sharpen as needed (one or two follow-up questions if vague).

**3.2 — Aim.** "What should this review *achieve*?" Categorize: methods review (what's been done?), effects review (does X cause Y?), landscape review (state of the field?), comparative review (X vs Y vs Z?). The aim narrows the topic.

**3.3 — Research questions.** Propose 1–3 RQs in plain English based on the topic + aim. Show them to the user. Ask: "Does that capture what you want to know?" Iterate until they say yes. *Don't ask the user to draft the RQs themselves unless they offer.* If they have a good draft already, use theirs.

**3.4 — Seeds.** This step comes BEFORE framework slot proposal. The vocabulary in your downstream PICOC slots and queries must be anchored in real papers, not in your general knowledge of the topic.

Ask:

> "Do you have 1–3 papers you already know are on-topic? Even one helps a lot — I'll use them to extract the actual vocabulary that papers in this field use, so the queries don't drift into adjacent literatures.
>
> DOIs, OpenAlex IDs (`W...`), or PDFs all work — whichever you have. If you don't have any handy, take a moment to find one — Google Scholar, arXiv, OpenAlex's web interface (openalex.org), or your own past reading. One good seed is enough to start. This step matters more than it looks."

**Hard rule: do not propose PICOC slot fillings before seeds are read.** If the user has zero seeds, do not proceed. Wait for them to find at least one.

If the user supplies more than 3 seeds, accept them but tell the user:

> "I'll use the first 3 — beyond that, additional seeds add noise without distinctiveness gain in the vocabulary extraction. The other seeds are still useful as references; I just won't feed them all into the vocabulary step."

Once seeds are collected, write them into `projects/<slug>/project.yaml`:

```yaml
seeds:
  papers:
    - doi: "10.1234/example"
    - openalex: "W123456789"
    - pdf: "/abs/path/to/file.pdf"
```

Then run:

```
python scripts/00b_read_seeds.py --project <slug>
```

This script fetches the metadata (DOI lookup via OpenAlex/Crossref, PDF text extraction). Read the script's output. Surface any errors to the user (e.g. "your second seed's PDF couldn't be parsed — likely a scanned image; can you supply a different one?"). Don't proceed if all seeds errored.

In v0.6 this script also inserts each readable seed into `records` with
`from_seed=1`. Those records are the explicit Wohlin snowball start set, not
just vocabulary inputs.

**3.5 — Vocabulary extraction.** After seeds are read, extract the vocabulary that real papers in the field use:

1. Check whether `keybert` is installable. Try `python -c "import keybert"` first. If it fails, ask the user:
   > "Vocabulary extraction works much better with KeyBERT (~80–400MB sentence-transformer model on first install). Can I run `pip install keybert sentence-transformers`? Without it, I'll fall back to a frequency heuristic, which works but is noticeably worse."
   If yes, install. If no, proceed with fallback.

2. Run:
   ```
   python scripts/00c_extract_vocabulary.py --project <slug>
   ```
   This runs KeyBERT (or fallback) over seed text, then has the LLM curate the bucket: pick strong terms, drop weak ones, add known synonyms only for bucketed phrases, group into clusters.

3. Show the curated clusters to the user. Ask:
   > "Here's the vocabulary extracted from your seeds. Does this match what you'd expect to see in real papers on this topic? Anything missing, or anything that doesn't fit?"

   The user may add 1–2 terms or remove 1–2. That's fine. Edit `projects/<slug>/seeds/_vocabulary.json` to reflect their changes.

**Anti-pattern: do not invent vocabulary that's not in or directly synonymous with the curated set.** The whole point of this step is to escape the "associative vocabulary" failure mode. If you find yourself reaching for a term that's neither in the bucket nor a known field-standard synonym of one, drop it.

**3.6 — Framework (PICOC).** Now propose framework slot fillings, using the curated vocabulary as input. Phrase as a recommendation:

> "I'll structure that into PICOC slots — that's the standard for software-engineering / AI-ML / finance reviews (Petticrew & Roberts 2008; Kitchenham et al. 2007). If you'd rather use a different framework like SPIDER for qualitative work or something custom, say so."

Then propose the slot fillings using terms from the curated vocabulary. Light extension is allowed — if the curated vocabulary has "prediction market" and you know "decision market" is a field-standard synonym, you can add it. But:

**PICOC fit-check (required):** before showing the slot fillings to the user, walk through one seed paper and verify each slot's vocabulary would actually match it. Print a brief check:

> Fit-check vs `seed_001` ("Combinatorial Information Market Design"):
>   - Population (Markets): match via "prediction market", "information market" ✓
>   - Intervention (Mechanisms): match via "market scoring rule" ✓
>   - Outcome (Aggregation): match via "predictions about future events" ✓

If any slot wouldn't match a seed, the slot is wrong — fix it before showing to the user.

Show the proposed slots:

> "Proposed PICOC for this review:
> - **Population**: prediction-market data from named platforms (Polymarket, Kalshi, PredictIt, Augur)
> - **Intervention**: regime-detection / change-point / structural-break methods (HMMs, BOCPD, Markov switching, etc.)
> - **Comparison**: across method classes, or vs. baseline (no regime detection)
> - **Outcome**: detection accuracy, regime characterization, downstream uses
> - **Context**: empirical, 2018 onward, English
>
> Anything you'd change?"

Wait for confirmation or edits. Save to `cfg.framework`.

**3.7 — Hypotheses (optional).** Make a recommendation based on the review's shape. Methods/landscape reviews usually skip; effects/comparative reviews often add. Phrase as your recommendation:

> "Based on the shape of this review (mapping methods), I'd recommend skipping hypotheses — this is exploratory. You'd add hypotheses if you had specific testable claims like 'method X outperforms Y' or 'volatility increases during regimes except in decentralized markets.' Do you have any directional claims like that, or skip?"

If they want to add hypotheses, walk through one at a time. Each gets `id` (H1, H1a, H2...), `statement` (testable, falsifiable), `rationale` (why expect this).

**Soft cap at 3.** If they want a 4th, accept it but warn:

> "Adding H4. One note: with more than 3 hypotheses, the LLM tracking risk grows because the model is judging more claims per paper simultaneously. The methodology artifacts will flag this and recommend extra spot-checking. Worth noting — do you want to keep all 4, or trim to 3?"

Never refuse. Log and proceed.

**Always include the no-RAG note when first introducing hypotheses:**

> "Quick note on how hypothesis-tracking works: when the LLM reads each paper during full-text screening, the same model that reads the paper also decides whether the paper supports each hypothesis. There's no separate retrieval system. Even one hypothesis carries some false-positive risk; more hypotheses means more risk. Mitigation is human spot-checks, not algorithmic."

**3.8 — Eligibility criteria.** Propose inclusion and exclusion criteria *derived from the framework slots* (which are themselves derived from the curated vocabulary). Show them with stable IDs:

> "Inclusion (each gets an ID I can track through screening):
> - I1: Empirical work on prediction-market data
> - I2: Applies a regime-detection or change-point method
> - I3: Published 2018 onward
> - I4: English-language
>
> Exclusion:
> - E1: Theoretical-only without empirical evaluation
> - E2: Equity / FX markets only (not prediction markets)
> - E3: Editorials, opinion pieces, or news commentary
>
> Add, remove, or adjust?"

Apply the **10-second test**: each criterion should be applicable to a title+abstract in 10 seconds. If a criterion is too vague (e.g. "high-quality methods"), tighten it.

**3.9 — Date range, languages, target count.** Confirm in one message:

> "Final scoping bits: date range 2018-present (any reason to widen or narrow?), English-language only (right?), and a target of 200–2000 records on the first search (workable, or want it tighter/wider?)."

**3.10 — Project ID and recap.** If the project doesn't exist yet, propose a slug:

> "Project slug `polymarket-regimes-2025` — okay or different?"

Then run:

```
python scripts/00_init_project.py --id <slug> --topic "<topic>"
```

(If it already exists from earlier, skip this.)

Edit `projects/<slug>/project.yaml` to add: `aim`, `research_questions`, `framework`, `hypotheses`, `inclusion`, `exclusion`, `seeds`, `date_from`, `languages`. Show the user a brief recap:

> "Project `polymarket-regimes-2025` initialized. Scoping captured:
> - 2 RQs, PICOC framework derived from 3 seeds, 0 hypotheses (exploratory)
> - 4 inclusion + 3 exclusion criteria
> - Vocabulary extracted from seeds (`projects/<slug>/seeds/_vocabulary.json`)
> - 2018-present, English, target 200–2000
>
> Ready to generate queries?"

---

## STEP 4 — Query generation (after scoping)

When user says yes to query generation:

```
python scripts/01_generate_queries.py --project <slug>
```

This scaffolds template files in `projects/<slug>/queries/`. Now you fill them in — using the **curated vocabulary** from step 3.5 as the canonical source.

**4.1 — Concept table.** Open `concepts.yaml`. Build it from the curated vocabulary clusters. Each cluster maps to one concept group. Example:

```yaml
concepts:
  - id: C1
    preferred: "prediction markets"
    synonyms: ["prediction market", "prediction markets", "information market",
               "decision market", "Polymarket", "Kalshi", "PredictIt", "Augur"]
  - id: C2
    preferred: "regime detection"
    synonyms: ["regime detection", "change-point detection", "structural break",
               "Markov switching", "HMM", "Bayesian online change-point", "BOCPD"]
```

Show the table to the user. Get their okay on synonyms before writing Boolean strings.

**4.2 — Boolean queries.** Write OpenAlex, PubMed, Europe PMC, arXiv strings using the concept table. For Crossref, use the structured params shape (see `crossref.json` template comments) — `filter` for strict filtering, `query.bibliographic` only for narrow concept groups with distinctive vocabulary.

**4.3 — Estimate result count.** Run a dry-run count check on OpenAlex. If <30 → too narrow, widen synonyms. If >5000 → too broad, tighten. Aim for 200–2000.

**4.4 — Mandatory pre-search verification.** Before running `02_search_open.py`, do all of the following:

1. **Paste the literal contents of every query file into chat.** Do not summarize. The user has to see the actual text.
2. **Build and print the actual API URL** the engine will hit for OpenAlex and Crossref. A one-liner like:
   ```python
   import urllib.parse
   params = {...}
   print("https://api.openalex.org/works?" + urllib.parse.urlencode(params, doseq=True))
   ```
3. **Run a precision check.** State your reasoning explicitly:

   For 1–2 seed papers (titles already in `projects/<slug>/seeds/seed_*.json`):
   > Seed 1: "Combinatorial Information Market Design" (Hanson, 2003)
   >   - C1 group ("prediction markets"): match via "information market" ✓
   >   - C2 group ("mechanisms"): match via "market scoring rule" ✓
   >   - Conclusion: query would retrieve this seed ✓

   For 1–2 obvious off-topic titles (use exclusion criteria as guidance, or pick something the user said NOT to include):
   > Off-topic check: "Bitcoin Market Dynamics during Climate Shocks"
   >   - C1 group ("prediction markets"): no match — paper is about cryptocurrency
   >   - Result: query would NOT retrieve this ✓

   - If any seed wouldn't be retrieved → query is too narrow. Stop, revise.
   - If any off-topic title would be retrieved → query is too broad. Stop, revise.

4. **Only after the precision check passes**, ask the user: "Okay to run with these queries?" Wait for explicit yes.

The engine has a query validator that runs automatically and will block obvious errors (flat keyword lists, unbalanced parens, etc.). But the validator only catches structural issues. Semantic correctness — whether the query is the right query for this topic — is your job to verify with the precision check.

**4.5 — Optional protocol draft before search.** After the user approves the literal query strings and API URLs, but before running `02_search_open.py`, ask once:

> "Before we run the search — do you want a protocol draft document generated now? It's useful if you're planning to register the methodology (PROSPERO, journal pre-registration, internal sign-off, grant submission), or you want the methodology committed to paper before searches start. Most users skip it for internal KM work, industry research, or quick scans — you'll still get a full methodology report at the end. Default: skip."

Yes path: run:

```
python scripts/01a_emit_protocol_draft.py --project <slug>
```

Open `projects/<slug>/protocol_draft.md`, show it to the user, and ask for revisions. If the user changes `project.yaml` or query files, rerun the script. Once the user approves the draft, proceed to stage 02.

No path: proceed directly to stage 02.

Important: protocol drafting is a feature, not a checkpoint. Do not block search on `protocol_draft.md`; the load-bearing checkpoints remain seeds before PICOC, literal queries/API URLs before search, and the precision check.

---

## STEP 5 — Run the search and pipeline

```
python scripts/02_search_open.py --project <slug>
```

The script will:
- Validate queries (block on errors, prompt on warnings).
- Run each enabled source.
- Surface silent zero-result failures (source returned 0 with HTTP errors → distinct from real null result).
- Run a post-search sanity check (cap-hits, asymmetric coverage, total too high/low).

If the sanity check produces blocking issues, the script exits with a non-zero status. Fix the underlying issue (revise queries, re-run) or pass `--acknowledge-warnings` if the issue is a known false positive.

For Scopus/WoS, copy the manual query strings into the user's databases, have them export RIS/CSV into `projects/<slug>/imports/`, then:

```
python scripts/02b_ingest_manual.py --project <slug>
```

Once searches complete cleanly:

```
python scripts/03_dedup.py --project <slug>
```

This script also refuses to run if the search stage left blocking sanity issues unaddressed. Same `--acknowledge-warnings` override.

Report the dedup outcome briefly:

> "Search complete: 412 records from OpenAlex, 187 from Crossref, 14 from Europe PMC. After dedup: 503 unique records. Ready to start screening?"

---

## STEP 6 — Title/abstract screening

```
python scripts/04_screen_prep.py --project <slug> --batch-size 5
```

Batches above 5 are rejected by the script. Run sequentially; do not parallelize screening for the same review.

Two paths — **let the user choose, don't decide for them**:

> "503 records to screen. Two ways to do this:
> - Manual: I read each abstract and label it against the criteria. Slow but cheap (no LLM cost), good for the first batch to calibrate.
> - LLM: configured in `project.yaml` under `llm:`. Fast and cheap per record, but requires an API key. Decisions go through with `decided_by='llm:provider'` and you can review/override.
>
> Default I'd suggest: manual first batch (5 records) to calibrate, then LLM for the rest with you spot-checking. Or LLM all the way if you want speed. Your call."

Read `skills/slr-engine/SKILL_screening.md` before screening any batch. Apply criteria literally.

---

## STEP 7 — Resolve OA, download, full-text screening

```
python scripts/05_resolve_oa.py --project <slug>
python scripts/06_download.py --project <slug>
python scripts/07_fulltext_prep.py --project <slug>
```

`06_download.py` counts a download only when the file is real full text. Bot or
JavaScript challenge pages, empty app shells and metadata-only XML are recorded
as failures (with the reason) and listed in `screening/not_downloaded.csv`.
On a project downloaded with an older SLR-Engine, run it once with
`--revalidate` to re-check the files already marked as downloaded.

For full-text, the LLM path is the default for >20 records. For academic
systematic reviews, do not skip PRISMA risk-of-bias assessment unless the user
explicitly accepts incomplete PRISMA reporting:

```
python scripts/07c_llm_fulltext.py --project <slug> [--with-quality]
python scripts/07d_human_review.py --review <path> --print-summary
# user gives override numbers, you edit JSONL
python scripts/07d_human_review.py --review <path> --commit
```

Read `skills/slr-engine/SKILL_quality_assessment.md` before deciding whether to use
`--with-quality`. Despite the legacy flag name, this now means
risk-of-bias / critical-appraisal fields for PRISMA reporting.

---

## STEP 8 — Snowball and export

```
python scripts/08_snowball.py --project <slug>           # if user wants
python scripts/09_export.py --project <slug>
```

The export produces 7 artifacts. Mention all of them to the user briefly:

> "Done. Outputs in `projects/<slug>/exports/`:
> - `records.csv` — every record with screening decisions
> - `extractions.csv` — structured fields per included paper, including risk-of-bias columns if you ran `--with-quality`
> - `included.ris` — for citation managers (Zotero, Mendeley)
> - `audit.json` — full machine-readable trace
> - `methodology_report.md` — retrospective methodology report
> - `prisma_flow.svg` — canonical PRISMA 2020 diagram (for publication)
> - `expanded_prisma.svg` — engine-aware diagram (for full audit)"

---

## STEP 9 — Mode 2: resuming

User opens an existing project. Look at:

1. `project.yaml` — is scoping complete? (`topic`, `research_questions`, `framework`, `inclusion`/`exclusion` all set?)
2. `projects/<id>/seeds/` — were seeds read? Was vocabulary extracted?
3. `project.db` — counts in `records`, `screening` (per pass), `downloads`, `extractions`, `events`.
4. `exports/` — has export been run?

Determine the **last completed stage** and propose the next:

> "Picked up `polymarket-regimes-2025`. Scoping complete, 503 records searched, 218 included at title/abstract, 47 downloaded, full-text screening not started. Want to start full-text screening, or something else?"

Don't list the entire pipeline. Just the next sensible step.

---

## STEP 10 — Mode 3: direct stage requests

User says "run the search" or "screen this batch". Check prerequisites:

- Search needs `concepts.yaml`, query files, and a curated vocabulary (`seeds/_vocabulary.json`).
- Screening needs criteria in `project.yaml` and a prepped batch.
- Export needs at least some records.

If prerequisites missing → explain briefly and offer to fix:

> "Can't run the search yet — `concepts.yaml` is still placeholders. Want me to fill it in based on the curated vocabulary in `seeds/_vocabulary.json`?"

If prerequisites met → run, report briefly.

---

## STEP 11 — When the user goes off-script

The user might ask things mid-scoping like *"what's PICOC?"* or *"why does this matter?"* Answer briefly (2–3 sentences max), then return to where you were:

> "PICOC = Population, Intervention, Comparison, Outcome, Context — standard framework for SE/CS/AI-ML reviews (Petticrew & Roberts 2008). Structures your RQ into searchable concept groups.
>
> Back to where we were — does the framework slot proposal above work for you?"

If the user asks to skip the scoping entirely ("just init the project, I'll fill in the yaml myself"), fine — init the project with the topic, point them at `projects/<slug>/project.yaml` AND remind them seeds and vocabulary extraction still need to happen before queries:

> "Initialized `<slug>`. Edit `project.yaml` directly if you want — but still come back to me before query generation: I need to read seeds and run vocabulary extraction before queries can be built. Without that step, the queries will use generic associations and pull in too much off-topic literature."

---

## STEP 12 — Reference pointers

Read these only when you need depth, not preemptively:

- `README.md` — what to tell humans if they ask what this tool is or how to start
- `skills/slr-engine/SKILL_screening.md` — read before screening any batch
- `skills/slr-engine/SKILL_quality_assessment.md` — before suggesting `--with-quality` for risk-of-bias

**This skill is the operational source of truth for running a review.**

---

## Anti-patterns — things this skill exists to prevent

- Showing a form with all the scoping fields. Walk the chain.
- Explaining the engine architecture unprompted. The user wants a review, not a tour.
- Modifying engine code. If the user asks for a fix, point them at it but don't refactor.
- **Proposing PICOC slot fillings before seeds are read and vocabulary extracted.** This is the single biggest failure mode. Slot terms must come from the curated vocabulary, not from your general knowledge of the topic.
- **Inventing vocabulary that's not in or directly synonymous with the curated set.** "Light extension" means adding `decision market` because the bucket has `prediction market` and they're field-standard synonyms. It does NOT mean adding `trading strategy` because you associate it with the topic.
- **Running search without showing literal queries and URLs first.** The user must see the actual strings, not summaries.
- **Skipping the precision check.** Walking through whether seeds match and off-topic titles don't is cheap; catching a malformed query after running it is expensive.
- Asking the user to draft the research questions in a vacuum. Propose them based on the topic; let the user edit.
- Surveying for inclusion/exclusion criteria. Propose them based on framework slots; let the user edit.
- Listing 10 things at once, even when "summarizing." If it's more than two questions, split.
- Repeating the disclaimer. Once is enough.
- Running scripts before showing intermediate artifacts to the user (concept table before queries; queries before search; LLM recommendations before human review).
- Skipping the human-review step on LLM full-text screening. The LLM produces *recommendations*, not decisions.

---

## Final note to yourself, the agent

You're in operator mode. Conversational. One step at a time. Sharpen the user's input, don't survey them. The whole engine is here to help you do that — use the methodology report, optional protocol draft, audit log, and criteria as anchors. The user came because they want a review done well, not because they want to fill out forms.

The single most important rule: **seeds before PICOC.** Vocabulary derivation is anchored in real seed papers. Without that anchor, the queries drift into generic associations and pull in too much off-topic literature. This is the failure mode the engine and skill are designed to prevent. Don't bypass it.

Start by figuring out the mode. Then start talking.
