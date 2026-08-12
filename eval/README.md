# eval/ — offline evaluation harnesses

## Ability → dataset coverage map

Nine abilities are named across #172, #173 and #174. This table is the map from each
to the dataset that measures it, the metric that reports it, and whether that
measurement exists **today**. It is the first place to look before writing a new
fixture: four of the nine currently have no data at all, and one has a metric suite
with nothing to point it at.

Keep it current. A new dataset that does not move a cell here is a fixture nobody
asked for; an ability whose row stays `none` after its issue ships did not ship.

| # | Ability | What it asks | Dataset today | Metric | Coverage |
|---|---|---|---|---|---|
| **A** | Selection | Do the right items and skills get chosen? | `jd_dataset/` × `profiles/` | `skills.selection_ratio`, `skills.matched_recall`, `experience_allocation.allocation_correlation` | **partial** — reported, but no answer key. `skill_selection_tasks/` holds the only labelled `relevant` set and is **not wired into the tailoring benchmark** |
| **B** | Grounded inference | Can it use evidence that never names the requirement? | — | — | **none** — `keyword_coverage` is substring matching, so coverage is monotone and no edit can lower it (#127). Needs #172's β-calibrated implicitness filter |
| **C** | Abstention | Does it refuse to fabricate? | — | `llm_judge.faithfulness` (1–5, opt-in, product only); `FAITHFULNESS_MIN` (`agents/tailor.py:93`) is a product guard, not a measurement | **indirect** — no abstention fixture exists; nothing presents a claim the profile cannot support |
| **D** | Update durability | After a re-tailor, do prior decisions survive? | — | — | **none** — `_run_task` tailors once and never re-tailors (#173 chunk 1) |
| **E** | Locality | Does tailoring job B degrade job A? | — | — | **none** — JobCard injection (#137) is a live cross-job channel, unmeasured |
| **F** | Suppression | Is "that was just coursework" honoured? | — | — | **none** — needs profile-bound conversations (#178). The literature's hardest case: ImplexConv reports 55.18 supportive vs 14.84 opposed retrieval F1 |
| **G** | Redundancy | Semantic duplication, stuffing, monotony, dilution | `jd_dataset/` × 1 profile | `agents/redundancy.py` — 4 modes + 6 counting keys | **metric yes, data no** — #122 recorded the suite reports clean across the board and could not distinguish "genuinely clean" from "too simple to generate redundancy" |
| **H** | Action ranking | Does it rank the higher-value move above the lower? | — | — | **none** — needs execution-derived preference pairs (#174) |
| **I** | Personalization | applied / ignored / violated | — | — | **none** — #129 and #133 shipped the machinery; nothing checks a preference reaches the rendered resume. Needs #178 + #173's `preference_adherence` axis |

### Datasets, and what each is actually for

| Dataset | Size | Harness | Note |
|---|---|---|---|
| `jd_dataset/` | 8 | tailoring benchmark | Being rebuilt to ~100 intern/entry postings across five role families (#177) |
| `profiles/` | 1 | tailoring benchmark | Mid-level candidate; #172 replaces with 15 intern/entry profiles |
| `ku_dataset/` | 4 | knowledge-updates eval | **Scripted by default** — the task file supplies the notes *and* the decisions, so the extractor is not under test unless `--live` |
| `jobcard_dataset/` | 4 | JobCard eval | Cross-job memory, outcome-carrying |
| `skill_selection_tasks/` | 2 | skill-selection tuning | Carries a labelled `relevant` answer key; unused by the tailoring benchmark |
| `cassettes/` | 1 | replay mode | `benchmark_profile` at `--limit 3` only |
| `tests/memory_evals/` | 5 YAML | chat-memory eval | Recall across compression; not bound to any profile |

**No dataset is bound to a profile except the tailoring benchmark's own.** That is why
abilities F and I are unmeasurable rather than merely unmeasured: a preference only means
something relative to a profile that has the item being suppressed or promoted (#178).

## Tailoring efficacy benchmark (issue #51)

Measures how much the tailoring pipeline improves resumes against a versioned
dataset of real job descriptions, driving the **web API exactly as a user
would** (register → upload resume → create job → analyze → tailor → export) on
an isolated temp database.

### Execution modes (issue #171)

One harness, three modes with different evidentiary weight. **The mode is part
of every number.** It is printed before and after each run and recorded in the
JSON, the CSV and each per-task row.

| Mode | What runs | What its numbers may claim |
|---|---|---|
| `product` | Real LLM + real embeddings, the deployed path | Tailoring quality. The only mode whose numbers describe the product. |
| `replay` | Recorded LLM responses; all real deterministic code and real embeddings | Everything the recording covered, deterministically, at near-zero marginal cost |
| `plumbing` (the old `--stub`) | Canned payloads, no model | Wiring, schemas, determinism. **Explicitly not tailoring quality** |

Plumbing mode's canned payload returns every source bullet **verbatim** — it
never rewrites one. That is deliberate and pinned by tests: a stub that faked
rewriting would produce a plausible number measuring nothing. Any figure from a
plumbing run describes the harness and the deterministic post-processing
(bullet budget, one-page fitting, ordering, skill selection), never the rewrite.

```bash
python eval/tailoring_benchmark.py --mode plumbing --limit 3   # offline, free
python eval/tailoring_benchmark.py --mode product --record --limit 3
python eval/tailoring_benchmark.py --mode replay --limit 3
python eval/tailoring_benchmark.py --judge                     # product only
python eval/tailoring_benchmark.py --tasks stripe_ai_engineer --limit 3
```

`--stub` remains an alias for `--mode plumbing`.

### Replay contract

Cassettes live in `eval/cassettes/` and are committed. A cassette is keyed by
`(task scope, role, sha256(rendered prompt), occurrence index)` — the occurrence
counter matters because the tailor runs at `temperature=0.3` with
`MAX_RETRIES = 2`, so one prompt can legitimately be invoked twice and return
two different samples.

What replay **does** reproduce:

- Every deterministic line of the pipeline: parsing, matching, planning,
  post-processing, rendering, scoring, and the whole metric suite.
- Real embeddings — `sentence-transformers` must be installed. Semantic
  redundancy is *not* silently disabled in replay; a missing encoder is a hard
  error, because degrading to None would re-create #122's symptom (all four
  redundancy modes reporting clean) inside the new mode.
- Byte-identical metrics across consecutive replays, on both engines (#158).

What replay **does not** reproduce, and the conditions it requires:

- **The same task list, in the same order.** JobCard injection (#137) feeds
  earlier completed jobs into later prompts, so replaying a subset changes the
  prompts and is a cassette miss, not a silent divergence.
- **A prompt change invalidates its cassette.** That is correct behaviour: it
  makes every prompt edit an explicitly re-measured event. Re-record with
  `--mode product --record`.
- **`BulletSimilarityCache` batch composition still depends on which texts are
  already cached** (residual carried forward from #158 and #122). Sorted encode
  order fixes the batch for a given set of *new* texts, but a differently-warmed
  cache can still encode a text in a different batch. It cannot make one process
  disagree with itself, which is what replay determinism requires; bit-exactness
  across differently-warmed caches is not claimed.
- A cassette miss is **fatal** and never falls through to a live call. Misses are
  also tallied on the session and fail the run at the end, because several call
  sites catch every exception and would otherwise turn a miss into a silently
  empty parse.

Results land in `eval/results/` (gitignored): a JSON with per-task metrics +
aggregate stats, a flat CSV, and per-task rendered `.tex`/`.json` under
`results/renders/<timestamp>/`. Open **`eval/tailoring_benchmark.ipynb`** to
run the benchmark, chart the metric families, drill into per-task text
allocation, view rendered resumes, and compare runs over time.

Metric families (`eval/metrics.py`): `ats` (baseline→tailored composite delta,
per component), `experience_allocation` (does text volume track JD relevance?),
`skills` (selectivity, matched recall, organization), `redundancy`
(boundary-aware term repetition). `--judge` adds `llm_judge` scores
(relevance_balance / redundancy / faithfulness, 1–5) via `eval/llm_judge.py`.

### Adding a task

Each task is one JSON file in `eval/jd_dataset/` with keys
`id, source, company, title, location, url, description, scraped_at`.
Refresh or extend the dataset from public job boards:

```bash
python scripts/scrape_job_descriptions.py                    # default boards
python scripts/scrape_job_descriptions.py --greenhouse figma --per-board 3
```

The candidate profile the benchmark tailors is `eval/profiles/benchmark_profile.md`
(override with `--profile`).

## Knowledge-Updates regression eval (issue #21)

Does the knowledge graph **update** when a later chat turn contradicts an
earlier fact, or does it go stale (or duplicate the row)? Each task in
`eval/ku_dataset/` is a closed world — the rows already in the graph (`seed`),
a short transcript whose last turn revises one of them (`turns`), and the
required post-state (`expect`). The eval seeds a throwaway profile, runs the
Chain-of-Note pipeline (`agents/knowledge_extractor.py`), applies every proposal
through `services.apply_artifact_decision` (the same explicit-accept path the
chat panel uses), then reads the rows back.

```bash
python eval/knowledge_updates_eval.py           # offline, deterministic
python eval/knowledge_updates_eval.py --live    # real LLM (needs API keys)
python eval/knowledge_updates_eval.py --tasks promotion_supersedes_experience
```

Metric: `update_accuracy` — the fraction of tasks whose post-state matches
`expect` exactly. A stale graph and a duplicated row both score 0. Results land
in `eval/results/` (gitignored).

Default mode is **scripted**: the task file supplies the notes and decisions, so
what is pinned is the pipeline contract (the deterministic decide layer and the
supersede persistence), not a model's output on the day — which is what makes it
a regression eval rather than a benchmark. `--live` swaps in real extractors
through the #142 seam to measure the model itself. `tests/test_knowledge_updates_eval.py`
runs the scripted mode as part of the suite.

### Adding a task

One JSON file in `eval/ku_dataset/` with `id`, `description`, `seed`, `turns`,
`notes`, `decisions`, and `expect`. `expect` supports `decisions` (the expected
add/supersede/no_op multiset), per-kind row assertions with `count` and field
equality (or `<field>_contains`), and `skills_total` / `experiences_total` /
`projects_total` to catch stray rows.

## JobCard card-quality eval (issue #137)

Is a completed job summarized *well enough*? A JobCard claims to be a sufficient
statistic for the next tailoring decision, so the claim is measured. Each task in
`eval/jobcard_dataset/` pairs a finished job (`prior_job`) with the next job it
should inform (`next_job`). Two arms plan `next_job` — one reading the compiled
card, one reading the raw finished result — and the plans are compared as **typed
ops per item**, not text.

```bash
python eval/jobcard_eval.py                 # offline, deterministic
python eval/jobcard_eval.py --live          # real TailorPlanner (needs API keys)
python eval/jobcard_eval.py --tasks rejected_project_recurs
python eval/jobcard_eval.py --no-ablation   # skip the field sweep
```

Metrics:
- `card_quality` — weighted agreement between the arms. A miss on a
  **user-rejected item that recurs** in the next job weighs `NEGATION_WEIGHT`
  (3x) against 1x for a generic-recap miss, because opposed cases are where
  summarization silently fails (#129 finding 1).
- `functional_equivalence` — unweighted per-item plan agreement.
- `outcome_delta` / `relevance_delta` — the downstream arm. Both are reported
  because the ATS composite is a *coverage* measure and is structurally blind to
  an off-topic item being removed: it reads 0.0 on every task here, while JD
  keyword density moves +0.104 on the negation task.
- Per-field **ablation** table (mean and worst case): blank each card field in
  turn and re-run, so the report shows which fields carry the signal.

Default mode is **scripted**: the planner arm is a deterministic probe rule over
a structured memory record, and `role_family` is pinned by the task file, so what
is pinned is the compile's information-preservation contract.
`tests/test_jobcard_eval.py` runs it in-suite and proves it goes red when the
negation signal is dropped.

### Adding a task

One JSON file in `eval/jobcard_dataset/` with `id`, `description`, `prior_job`
(a finished job: `tailored_resume_content`, `tailoring_decisions`,
`tailored_score_breakdown`, `role_family`), `next_job` (`description`,
`matched_skills`, and the candidate `items` to plan over), optional
`recurring_rejected` (item keys the prior user rejected that reappear here), and
`expect.min_card_quality`.

## Skill-selection tuning harness (issue #54 Phase 4)

`python eval/skill_selection_eval.py` — LLM-free comparison of skill-scorer
weight presets over `eval/skill_selection_tasks/` fixtures (recall + rendered
count per preset).
