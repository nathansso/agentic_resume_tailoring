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

## Skill-selection tuning harness (issue #54 Phase 4)

`python eval/skill_selection_eval.py` — LLM-free comparison of skill-scorer
weight presets over `eval/skill_selection_tasks/` fixtures (recall + rendered
count per preset).
