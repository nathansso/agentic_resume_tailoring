# eval/ — offline evaluation harnesses

## Tailoring efficacy benchmark (issue #51)

Measures how much the tailoring pipeline improves resumes against a versioned
dataset of real job descriptions, driving the **web API exactly as a user
would** (register → upload resume → create job → analyze → tailor → export) on
an isolated temp database.

```bash
python eval/tailoring_benchmark.py            # real LLMs (needs API keys)
python eval/tailoring_benchmark.py --stub     # offline, deterministic fake LLM
python eval/tailoring_benchmark.py --judge    # + LLM-as-judge quality scores
python eval/tailoring_benchmark.py --tasks stripe_ai_engineer --limit 3
```

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
