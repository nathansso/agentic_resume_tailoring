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
| **A** | Selection | Do the right items and skills get chosen? | `jd_dataset/` × `profiles/`, with the `--distractors N` dial (#172 chunk 5) | `skills.selection_ratio`, `skills.matched_recall`, `experience_allocation.allocation_correlation` | **partial, now controllable** — the distractor pool decouples haystack size from the true-positive set, so `selection_ratio` is comparable across profile sizes and `MAX_SKILLS` can be made to bind. Still no per-task answer key: `skill_selection_tasks/` holds the only labelled `relevant` set and is **not wired into the tailoring benchmark** |
| **B** | Grounded inference | Can it use evidence that never names the requirement? | `eval/implicitness.py` — 31 labelled `(requirement, bullet)` pairs across `explicit` / `implicit` / `unrelated` | `is_implicit` (lexical); cosine reported per pair as a diagnostic | **filter yes, tasks no** — the filter ships and is verified (#172 chunk 6), but nothing yet *runs* the pipeline against implicit-only requirements. **β does not exist on this encoder**: explicit and implicit do not separate (AUC 0.918 but overlapping tails), and the evidence floor is too weak to gate on (AUC 0.709, best accuracy 76.2% vs a 52.4% baseline). The shipped filter is lexical only |
| **C** | Abstention | Does it refuse to fabricate? | — | `llm_judge.faithfulness` (1–5, opt-in, product only); `FAITHFULNESS_MIN` (`agents/tailor.py:93`) is a product guard, not a measurement | **indirect** — no abstention fixture exists; nothing presents a claim the profile cannot support |
| **D** | Update durability | After a re-tailor, do prior decisions survive? | — | — | **none** — `_run_task` tailors once and never re-tailors (#173 chunk 1) |
| **E** | Locality | Does tailoring job B degrade job A? | — | — | **none** — JobCard injection (#137) is a live cross-job channel, unmeasured |
| **F** | Suppression | Is "that was just coursework" honoured? | — | — | **none** — needs profile-bound conversations (#178). The literature's hardest case: ImplexConv reports 55.18 supportive vs 14.84 opposed retrieval F1 |
| **G** | Redundancy | Semantic duplication, stuffing, monotony, dilution | `jd_dataset/` × the 5 `redundancy: bearing` profiles, each paired with its clean twin | `agents/redundancy.py` — 4 modes + 6 counting keys | **partial** — dilution and monotony now separate the pair cleanly (`mean_new_information` 1.000 → 0.860, `leading_verb_entropy` 1.000 → 0.889, `mtld` 336.5 → 148.8 on the same 5 tasks). **Stuffing still does not fire** on either population: additive-only injection cannot push a term past a 0.5 bullet document frequency without rewriting the base bullets, which would break the matched pair. A stuffing-bearing variant is unfiled |
| **H** | Action ranking | Does it rank the higher-value move above the lower? | — | — | **none** — needs execution-derived preference pairs (#174) |
| **I** | Personalization | applied / ignored / violated | — | — | **none** — #129 and #133 shipped the machinery; nothing checks a preference reaches the rendered resume. Needs #178 + #173's `preference_adherence` axis |

### Datasets, and what each is actually for

| Dataset | Size | Harness | Note |
|---|---|---|---|
| `jd_dataset/` | 150 | tailoring benchmark | Intern/entry postings, 30 per role family (#177), replacing the 8 mid-level-and-senior postings every pre-2026-08-11 figure was measured on. Audit its parse fidelity with `python scripts/audit_jd_corpus.py` |
| `profiles/` | 20 + 1 retired | tailoring benchmark | 15 authored people (5 families × 3 variants) + 5 redundancy-bearing derivatives (#172). Generated from `profile_banks.py`; the mid-level `benchmark_profile.md` is retired but still runnable by name. An unqualified run measures `data_science_specialist_priya_raman.md` (#182) — specialist / metric_rich / clean, so it isolates no hazard |
| `ku_dataset/` | 4 | knowledge-updates eval | **Scripted by default** — the task file supplies the notes *and* the decisions, so the extractor is not under test unless `--live` |
| `jobcard_dataset/` | 4 | JobCard eval | Cross-job memory, outcome-carrying |
| `skill_selection_tasks/` | 2 | skill-selection tuning | Carries a labelled `relevant` answer key; unused by the tailoring benchmark |
| distractor pool (`distractors.py`) | 31 admitted of 56 proposed | tailoring benchmark, via `--distractors N` | Audited against every posting: no shared keyword, and below the matcher's own semantic threshold. Injected as `UserSkill` rows post-ingest, never written into the résumé, so one profile runs with and without |
| implicitness pairs (`implicitness.py`) | 31 | — | `explicit` / `implicit` / `unrelated` `(requirement, bullet)` pairs; the calibration set, not a task set |
| `cassettes/` | 0 | replay mode | **None committed.** #182 deleted the #158 recording rather than refresh it: its three task ids came from the corpus #177 replaced and its profile was retired by #172. **Not due yet either:** recording is gated on #181 — `_detect_level` mislabels 128/150 postings and `role_level` is 0.10 of the composite on both sides of every match, so recording first would bake a known-wrong component into a paid artifact. Once it lands: `python eval/tailoring_benchmark.py --mode product --record --tasks <id> <id> …`, naming the tasks rather than using `--limit`, which takes an alphabetical prefix of the corpus. Both replay tests skip while the directory is empty, and say so |
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
python eval/tailoring_benchmark.py --tasks arizent_data_ai_engineer
```

`--stub` remains an alias for `--mode plumbing`.

### Replay contract

Cassettes live in `eval/cassettes/` and are committed — **none is committed
today** (#182; see the datasets table above for how to record one). A cassette
is keyed by `(task scope, role, sha256(rendered prompt), occurrence index)` —
the occurrence counter matters because the tailor runs at `temperature=0.3` with
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

### The JD corpus (issue #177)

The corpus is restricted to the product's actual target population: **intern and
entry-level roles in five families**. Before #177 it was not — the scraper's own
exclusion regex listed `intern`, so it filtered out that population, and the
corpus contained a *Senior* AI Engineer while the benchmark profile was a
four-year mid-level candidate. `role_level` carries weight 0.10 in
`agents/ats_scorer.py::_WEIGHTS` and was scored against that mismatch on every
run.

Each task is one JSON file in `eval/jd_dataset/`:

| field | meaning |
|---|---|
| `id, source, company, title, location, url, description, scraped_at` | as before |
| `role_family` | `data_science` · `data_engineering` · `ml_engineering` · `software_engineering` · `ai_engineering` |
| `level` | `intern` · `entry` |
| `posted` | ISO date, or null when the feed gave none |
| `verified` / `verification` | structural evidence the employer exists — see below |

`role_family` and `level` are **written to the file, not recomputed at load
time**: a task file must be reviewable and stable, and a classifier that ran at
load time would silently re-label the whole corpus when it changed. They exist
because per-stratum reporting has a JD side as well as a candidate side.

**Every posting is verified** (`scripts/job_verification.py`). A fabricated or
lead-generation listing is not a job description, and tailoring against one
measures the pipeline's response to marketing copy. The signals are structural,
never reputational — does the employer run its own ATS, is the posting
corroborated across independent feeds, does it state a salary. Admission
requires the description to have been fetched from the live URL *and* either an
ATS host or ≥2 independent feeds.

**Coverage comes from the aggregators, not from a token list.** A hand-curated
list of ATS tokens cannot be comprehensive — 32 of 80 guessed tokens 404'd, and
across ~110 boards the old approach reached 6 entry-level AI-engineering
postings. So `scripts/job_sources.py` reads the community GitHub boards first,
mines their apply links for the ATS tokens that actually exist, then reads those
boards in full. Aggregator rows are a title and a link, so
`scripts/job_descriptions.py` backfills bodies through a URL-keyed SQLite cache
(`eval/.jd_cache.db`, gitignored — a build artifact, not source).

```bash
python scripts/scrape_job_descriptions.py              # refresh the corpus
python scripts/scrape_job_descriptions.py --dry-run    # report, write nothing
python scripts/scrape_job_descriptions.py --per-family 50
```

A refresh **replaces** the corpus rather than layering on top of it; leaving
stale files behind is how senior postings would survive a domain restriction.
Pass `--keep-existing` to override.

Sizing: the JD is the *item* and the profile is the *subject*, so a per-family
claim's sample size is the number of JDs in that family. The default 30 detects
a moderate paired effect (d≈0.50) and matches LongMemEval's 30-question
per-ability slice.

#### Corpus audit — is it what a user would paste? (issue #172)

```bash
python scripts/audit_jd_corpus.py                  # summary, exit 1 on errors
python scripts/audit_jd_corpus.py --detail truncated
```

`_run_task` posts each `description` to the same endpoint the paste box calls,
so the corpus is only a valid measurement if every body is the text a browser
would have put on the clipboard. Assembly from ATS APIs does not guarantee that,
and nothing in a metrics table shows when it fails — every number stays
well-formed while the input degrades. The audit checks schema, fidelity
(truncation, HTML leakage, encoding damage, fused words, stripped apostrophes),
structure (do the section and list boundaries `jd_profile` keys on still exist?)
and content (is it a job description, and does its stated seniority match the
label it is filed under?).

First run against the corpus as #177 committed it, and after the repair below:

| finding | before | after | what it was |
|---|---|---|---|
| cut at a fetch ceiling | 62 | **23** | 48 cut at exactly 6,000 mid-word; 14 more *exceeded* it, because the feed adapters never applied it — the corpus carried two ceilings |
| literal HTML in the body | 18 | **2** | `strip_html` stripped tags *before* unescaping entities, so every double-escaped body emerged as its own `<li>` markup |
| unescaped entities | 3 | **1** | a single unescape of a double-escaped body leaves `&#xa0;` behind — and the audit's first entity pattern matched only the named and decimal forms, so the check passed while the residue sat in the corpus |
| no bulleted lines | 91 | **66** | correlates with `source`, which correlates with `role_family` — a per-family contrast is partly a contrast between text formats |
| invisible characters | 80 | **63** | NBSP and zero-width joiners surviving the unescape |
| `level` label ≠ detected tier | 124 | 124 | not a corpus defect — see below |

**Repairing:**

```bash
python scripts/repair_jd_bodies.py                          # dry run
python scripts/repair_jd_bodies.py --only truncated,html_tag --apply
```

`scripts/repair_jd_bodies.py` re-fetches bodies **in place**: it touches
`description` and nothing else, so ids, labels, verification, ordering and
membership are unchanged. Re-running `scrape_job_descriptions.py` instead would
redo discovery and selection and hand back a materially different 150 postings —
a change to *which* postings are measured, bundled with a fix to *how their text
was extracted*, with no way to attribute a moved number to either. A re-fetch
that returns a much shorter body (an expired listing's "no longer accepting
applications" page) or that introduces a new audit finding is discarded and the
committed body kept.

**25 postings could not be repaired** — 2 hosts return 403 and the rest have
expired, so a re-fetch yields a stub. They keep their cut bodies rather than
being dropped: they are spread across all five families (DS 8, DE 6, AI 5,
MLE 3, SWE 1) and dropping them would break the 30-per-family balance the
primary stratum rests on. `tests/test_jd_corpus.py` pins the count so it can
only go down.

**The corpus is intern/entry-only, and that is now an invariant.** `role_family`
and `level` are stored rather than recomputed (#177), which is right for
stability and wrong for drift — nothing re-checked them after the scrape. The
audit re-decides both from the committed title and body on every run: 150/150
still classify as filed, no title carries a seniority marker, and every
posting whose body states more than `MAX_ENTRY_YEARS` was read by hand (all six
are regex false positives — company heritage lines, an age question, a UK
residency rule, and one "**no more than** 3 years of professional experience",
which is an entry-level constraint).

**The `level` mismatch is a product finding, not a corpus one.**
`_detect_level` returns the highest tier whose keyword appears *anywhere*, and
its `lead` tier matches the bare substring `lead` — so "leadership", "leading"
and "hiring manager" promote an entry-level posting. 60 of 150 postings read as
`lead` and 27 as `manager`; only 22 are read at the tier they are filed under.
The same defect hits the candidate side: the retired `benchmark_profile.md`
reads as `lead` because one bullet says "saving staff ten hours weekly". So
`role_level` (weight 0.10) has been comparing two mostly-wrong tiers for the
whole life of this benchmark. Fixing the detector belongs to the product-scoring
issues (#124/#126/#151/#152), not here — filed as **#181**. Note that
`scrape_job_descriptions.py::classify_level` reads the same 150 postings
correctly, so the corpus labels are sound and only the scorer is wrong.

### The profile set (issue #172)

Twenty profiles: **15 authored people** — 5 role families × 3 variants — plus
**5 derived redundancy-bearing variants**.

| variant | breadth | evidence density | redundancy | differs from `specialist` on |
|---|---|---|---|---|
| `specialist` | specialist | metric_rich | clean | — (the base) |
| `generalist` | generalist | metric_rich | clean | breadth |
| `metric_poor` | specialist | metric_poor | clean | evidence density |
| `…_redundant` *(derived)* | specialist | metric_rich | **bearing** | redundancy |

Each variant moves exactly one axis, so a contrast against the base isolates it.
The redundancy variant is derived rather than authored — the same synthetic
person with restating bullets **appended** to the first role and first project —
which makes that contrast a matched pair rather than a comparison between two
different people's prose.

```
eval/profile_banks.py       authored content: 15 people + family vocabularies
eval/profile_generator.py   composition, hazard transforms, rendering
eval/profile_checks.py      declared strata verified against the rendered text
eval/profiles/<slug>.md     generated — never hand-edit
eval/profiles/<slug>.meta.json  strata, GitHub metrics, distractors
```

```bash
python eval/profile_generator.py --write   # regenerate after editing the bank
python eval/profile_generator.py --check   # fail if a committed file drifted
python eval/profile_checks.py --verbose    # every stratum measurement
python eval/benchmark_suite.py --mode plumbing --limit 25
```

**The markdown is generated, so editing it is a defect.** The hand-review
discipline the issue borrows from LongMemEval applies to the *bank*; a rendered
file that drifts from it becomes the real dataset while the bank silently
becomes a stale comment. `--check` and `tests/test_profile_set.py` both fail on
drift.

**Declared strata are verified, not trusted.** A sidecar claiming
`metric_poor` while every bullet carries a number would report under a slice it
does not belong to, and the table would still look complete —
`eval/profile_checks.py` measures each claim against the text (digit share,
skill count, redundancy modes, detected seniority tier) with thresholds set from
the measured set rather than borrowed.

Two vocabulary hazards constrain every profile, both consequences of
`_detect_level` returning the *highest* tier matched anywhere: no profile may
contain `senior`, `staff`, `principal`, `manager` or any `lead…` word (so no
"principal component analysis", no "leadership", no "staffing"), and an
intern-level profile may not say `junior` or `associate`. Tests assert no
profile reads above `junior`.

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

## Distractor pool (issue #172, chunk 5)

`eval/distractors.py` scales the haystack without moving the answer key.
`--distractors N` injects `N` audited distractor skills as ingested rows after the
profile is parsed, so the same profile runs with and without and `N` is a run
parameter rather than a fixture rebuild.

```bash
python eval/tailoring_benchmark.py --mode plumbing --limit 3 --distractors 25
python eval/distractors.py              # audit the bank against the corpus
python eval/distractors.py --verbose    # show rejections and their reasons
python eval/distractors.py --emit       # ADMITTED literal to paste back
```

**Admission is exact, not a similarity judgement.** `SkillMatcherAgent.match`
iterates over JD skills, so adding a profile skill can only flip one from
*missing* to *matched*, never the reverse — which reduces "the answer key does not
move" to "no JD skill becomes matched", and that decomposes onto the matcher's
channels:

| rule | check | channel it protects |
|---|---|---|
| **L** | shares no keyword with any posting, under `ATSScoringEngine._extract_keywords` | `keyword_coverage`; also direct and name matching, since a JD skill name comes from the posting text |
| **S** | cosine below `SkillMatcherAgent.SEMANTIC_THRESHOLD` against every corpus keyword | the matcher's semantic channel |
| **G** | project text names no corpus keyword | the indirect (knowledge-graph) channel, whose edges are built by substring match |

Rule S is not in tension with chunk 6's finding below. It does not ask the encoder
whether something is *relevant*; it asks whether `_check_semantic_match` **would
fire**, and computes precisely that at the production threshold. The encoder is
replayed rather than trusted, and its errors run conservative: every semantic
rejection in the committed audit is an orthographic artefact — `CATIA` blocked by
*scania*, `QuickBooks` by *playbooks* — so a false block costs a candidate while a
false admission would corrupt labels.

56 candidates proposed, **31 admitted**, 25 rejected with reasons retained in
`REJECTED` so the bank records what was tried. Verified end to end: the same
profile and tasks with and without 25 distractors produce identical `ats_delta`,
`baseline_composite`, `tailored_composite` and `matched_recall`, while
`total_profile_skills` goes 15 → 40.

**Report the distractor count with every number.** A `selection_ratio` measured on
a padded haystack is not comparable with one measured without; the count is
recorded in the results JSON as `distractors`.

## Implicitness filter (issue #172, chunk 6)

`eval/implicitness.py` decides whether a `(JD requirement, résumé bullet)` pair is
**implicit** — the bullet demonstrates the requirement without naming it, so
`keyword_coverage` can score nothing from it. That is the property ability **B**
needs, and the filter is what a future implicit-only task set is admitted by.

```bash
python eval/implicitness.py              # distributions, AUCs, violations
python eval/implicitness.py --remeasure  # score against the live encoder
python eval/implicitness.py --emit       # MEASURED literal to paste back
```

**The filter is lexical, and that is a measured decision rather than a shortcut.**
#172 specifies a β threshold on encoder cosine, adapted from ImplexConv's 0.4.
Measured on ART's own `all-MiniLM-L6-v2` over 31 hand-labelled pairs, the
populations do not separate — explicit's minimum (0.1144) sits below implicit's
maximum (0.2501), so no threshold classifies the set. Cosine still *ranks*
explicit above implicit well (AUC 0.918); it is the tails that interleave, and a
filter operates on tails. A fallback evidence floor was measured against a third
`unrelated` population and came back too weak to gate on (AUC 0.709, best accuracy
76.2% against a 52.4% baseline). So `is_implicit` uses zero shared keywords under
`ATSScoringEngine._extract_keywords` — the metric's own vocabulary — and cosine is
carried per pair as a reported diagnostic. Full reasoning in the module docstring.

Every pair quotes a real posting and a real profile, and tests assert both quotes
still appear in their sources. Labels are **re-derived, never trusted**:
`label_violations` fails any pair whose declared label disagrees with the measured
overlap, which caught 7 of the first 12 `explicit` pairs.

## Skill-selection tuning harness (issue #54 Phase 4)

`python eval/skill_selection_eval.py` — LLM-free comparison of skill-scorer
weight presets over `eval/skill_selection_tasks/` fixtures (recall + rendered
count per preset).
