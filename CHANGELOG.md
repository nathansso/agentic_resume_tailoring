# Changelog

All completed deliveries are recorded here. Forward-looking specs live in GitHub issues and the ART Development Plan board.

Entries titled `PRD NN — …` are historical: they predate the move to issue-driven planning, and the PRD documents they reference were removed from the repo. They are kept as an accurate record of past work.

---

## Issue 149 — Postgres parity for local dev, the test suite, and the benchmark
**Status:** complete | **Tests:** 844 pass on Postgres / 836 on SQLite (8 new)

Production is Supabase Postgres. Every developer machine, all 836 tests, and both eval harnesses ran on SQLite, so the suite could not see the engine we ship on. The repo had already paid for that three times: `_migrate_pg_uuid_columns` exists purely to repair TEXT columns that should have been `uuid` (invisible on SQLite, a 500 on every filtered query in production); `_migrate_pg_vector_columns` adds `vector(384)` columns no SQLite test could exercise; and #137 shipped a consumer against `search_similar`, which returns `[]` on SQLite while its tests stay green.

### What shipped
- **Postgres for local development.** `docker-compose.yml` had no database service at all — the `art` container just mounted a volume for the SQLite file. Adds `pgvector/pgvector:pg16` with a healthcheck, gates `art` on it, and points the container's `DATABASE_URL` at it. `config.py` keeps its SQLite default: `cli.py` and the no-Docker contributor path depend on it.
- **The suite runs on Postgres** when `ART_TEST_DATABASE_URL` is set, SQLite when it isn't. Isolation is a **throwaway schema per test**, not a database per test — far cheaper across 800+ tests, and it keeps `_migrate_pg_uuid_columns`'s `WHERE table_schema = current_schema()` filter correct with no production change. `public` stays on the search_path after the test schema so the `vector` type resolves while `create_all` still lands in the test schema. The drop runs in a `finally`, so a failing test cannot leak.
- **Opt-in, and a separate variable from `DATABASE_URL`.** Auto-detecting a reachable server would make `python run_tests.py` mean different things on different machines — the exact ambiguity this issue exists to remove. And `DATABASE_URL` may hold production Supabase while the suite creates and drops schemas, so the two must never collide.
- **The migration chain is now tested (5 new).** No test had ever called `init_db()` — `conftest` went straight to `create_all`, which builds columns from the models and so always gets the types right, leaving the raw `ALTER TABLE` list that runs against real databases covered by nothing. That is how the UUID defect shipped invisibly. Covered from both directions: a forward invariant that no `sa.Uuid` model column may exist as text after `init_db()`, and a regression that the self-heal repairs a legacy column. The repair test asserts the failing **query** (`operator does not exist: text = uuid`), not just `information_schema`, because the 500 was the symptom. Every DDL site first asserts it is on a throwaway `art_test_*` schema.
- **The pgvector branch runs for the first time (3 new).** #142 validated only the numpy side and deferred `<=>` to staging. Asserts top-k ordering and scores match the numpy branch, that the pgvector path actually executed (a non-empty result proves it — the numpy fallback returns `[]` for this call shape), and that NULL vectors are excluded rather than scored as the origin. Equivalence holds on **normalized** vectors only: `<=>` is cosine distance, the numpy path a raw dot product.
- **Both eval harnesses measure on Postgres.** New `eval/eval_db.py` gives each run a throwaway database (a whole database per run is fine at that granularity and is the stronger isolation). The drop terminates stragglers first — `DROP DATABASE` fails while any connection remains — and never raises.
- **CI, which did not exist.** `.github/workflows/` held only `add-to-project.yml`; nothing ran the suite on push. Adds a two-leg matrix, `fail-fast: false` so one leg cannot hide the other.

### Verification
Postgres leg 844 passed / 8 skipped; SQLite leg 836 passed / 16 skipped (the 8 Postgres-only tests skip correctly). No leaked schemas or eval databases after a full run, and `public` holds zero tables. `knowledge_updates_eval` reports `update_accuracy: 1.0` on Postgres, matching its SQLite baseline.

### Deviations from spec
- **The predicted dialect fallout did not materialise.** #149 said to "expect fallout, and treat each as a found bug"; the suite went green on Postgres with **zero** dialect failures. The reason is structural: `conftest` builds schemas with `create_all` from the models, which is correct on both engines, and the application code is almost entirely ORM. Every bug the issue cites lived in the raw-migration path the suite never touched — which is exactly the gap the new migration-chain test now closes. Worth stating plainly rather than claiming a clean port: the absence of failures reflects where the tests point, not proof that the two dialects agree everywhere. Ordering in particular is unproven — Postgres often returns insertion order for small unmodified tables, so an unordered query can pass here and diverge in production after an UPDATE or VACUUM.
- **The `_search_pgvector` equivalence test did not need #60.** The issue's comment lists it as dependent on the `embedding_vec` write path. It isn't: the test needs vectors *in the column*, not the pipeline that puts them there, so it writes them with the existing `vector_literal` helper. #60 keeps ownership of the production writer.
- **Host port moved to 5433.** A developer-installed Postgres commonly holds 5432; when it does, `localhost` resolves to it and the failure is a misleading "password authentication failed for user art" from a server with no such role. Hit exactly this locally. CI moved too, so one DSN string works in both places.
- **A nondeterminism bug was found and filed, not fixed — #158.** Comparing benchmark runs across engines showed `skills_rendered` differing (13 vs 10). It is **not a dialect bug**: two consecutive `--stub` runs on the *same* engine differ just as much (SQLite `[11,11,13]` then `[8,10,9]`), with `total_profile_skills` at 30 throughout and every other metric bit-identical. The first-wins merge over an unordered query in `agents/tailor.py::_load_skill_rows` is one confirmed contributor — `id_by_name` decides which cached embedding feeds the semantic score — but ordering it by `Skill.name` was tried and is demonstrably **not sufficient**, so the fix is a real investigation rather than one `ORDER BY`. Filed with the evidence and the ruled-out candidates. It matters for #51 Phase 2, which cannot tune against a metric that moves on its own.

---

## Issue 155 — GitHub authorship signals: score what the candidate wrote, not what got starred
**Status:** complete | **Tests:** 844 pass (14 new)

HackerRank open-sourced `interviewstreet/hiring-agent` (MIT, 6.6k stars). Reviewed for an ATS algorithm to adopt; there isn't one. Their README says outright it is not an ATS, there is no job description anywhere in the codebase, and the only deterministic code is ~30 lines of clamp-and-sum in `score.py:62-90` wrapped around a single LLM call against a static role rubric. Their own README links an analysis showing 90/74/88/83 across repeat runs of one resume.

What did survive the review is **one objective signal they collect and we didn't**: contributor counts. It fixes a real defect in ART. `agents/project_scorer.py::_github_signal()` averaged `stars`, `languages`, `readme_length` — all measures of a repo's *popularity or polish*, none of the candidate's *input into it*. A starred fork of a tutorial outranked a project they wrote alone.

### What shipped
- **`ingestion/github.py::_fetch_contributors()`** hits `/repos/{owner}/{repo}/contributors` and returns `{contributors, author_commits, total_commits}`, matching the author case-insensitively on `login`. Returns `None` on an empty repo, a non-200, or any parse failure, so an unavailable signal is *omitted* rather than scored as a zero. `GitHubRateLimitError` re-raises through it like its `_fetch_tree` / `_fetch_readme` siblings.
- **Gated on the existing `should_deep_scan` language whitelist**, so repos the scanner already skips cost zero extra API calls. The call is one per deep-scanned repo.
- **`services.py::_build_repo_metrics()`** merges the authorship keys in only when the ingestor returned them, and derives `project_type` = `open_source` if `contributors > 1` else `self_project` — hiring-agent's one genuinely objective classification (`github.py:246`).
- **`_github_signal()` is now an authorship-weighted mean, not a flat average.** Three new sub-signals — `author_commits` (saturating at `AUTHOR_COMMITS_CAP = 100`), `commit_share` (`author_commits / total_commits`, which hiring-agent does not compute), and `collaboration` (from `project_type`) — carry weight 1.0 / 1.0 / 0.6 against `languages` and `readme_length` at 0.6 and **`stars` demoted to 0.3**. Weights renormalize over whichever signals are actually present.
- **No migration.** `Project.metrics` is a JSON column, so the new keys are additive and pre-change rows keep scoring on the signals they have.
- **Tests (14 new).** 7 on the ingestion side (author/total arithmetic, case-insensitive login match, empty-repo and error-status `None` paths, rate-limit propagation, `project_type` derivation, authorship omitted when absent) and 7 on scoring (no metrics → `None` not zero, legacy metrics still score, an authored repo beats a drive-by contribution, `commit_share` rewards solo authorship, authorship outweighs stars, `total_commits: 0` doesn't divide by zero, and a guard that `_GITHUB_WEIGHTS` covers every signal `_github_signal` can emit).

### Benchmark
`PYTHONHASHSEED=0 python eval/tailoring_benchmark.py --stub`, before (`6720660`) → after — every metric identical (`ats_delta` +21.925, `baseline_composite` 49.525, `tailored_composite` 71.45, `skills_matched_recall` 1.0, `skills_rendered` 12.125). Expected: the harness profile carries no ingested GitHub metrics, so `_github_signal()` returns `None` for every fixture project and the component is omitted from `_complexity()` exactly as before. The harness cannot observe this change — a fixture with GitHub metrics is the missing capability.

Verified live against real repos instead: `nathansso/agentic_resume_tailoring` → `{contributors: 1, author_commits: 188, total_commits: 188}` → `self_project`; `nathansso/RollAway` → `{contributors: 3, author_commits: 55, total_commits: 170}` → `open_source`.

### Deviations from spec
- **The plan said projects ingested before this change must "match today's output exactly." They don't, and can't.** Demoting stars and preserving legacy scores byte-for-byte are mutually exclusive — `stars` is one of only three signals present in pre-change data, so re-weighting it necessarily moves those scores. Demoting stars *is* the change, so it was taken and this bullet is the flag. The contract actually preserved is the narrower, non-conflicting one: a project with **no** metrics yields `None` (component omitted) rather than a zero, covered by `test_github_signal_none_without_metrics`.
- **A solo repo can outscore a collaborative one**, and this is intended, not a bug: the live check above scores the 188-commit solo repo at 0.564 against the 55-of-170 collaborative one at 0.435. `collaboration` credits co-contributors, but `commit_share` is 1.0 for the solo author and 0.32 for the partial one. The component answers "how much of this is theirs," so this ordering is correct for it.
- **Non-whitelisted languages get no classification.** A Go or Rust repo with real co-contributors is invisible to the gate. Pre-existing limitation of the whole scanner, not introduced here, and fixing it means re-scoping the language whitelist.
- **Two items were cut from scope after review, deliberately.** *Rubric-as-data* (moving `_WEIGHTS` into a loadable manifest): its only rationale was JD-derived weights, and `score_tailored()` computes `delta` against a baseline read from the same weights (`ats_scorer.py:165-168`) — per-job weights would measure the baseline and tailored runs on different rulers, making the #51 benchmark's core metric meaningless. What remained was a config refactor with identical values and no consumer varying them. *The LLM judge* was scoped out by the issue; scoring stays deterministic.
- **Deduction heuristics were brainstormed, not implemented**, per direction. Recorded on the issue: no verifiable link (`repo_url`/`demo_url` both empty — already in the scoring dict at `tailor.py:211-212`), repo-without-demo, generic project name, all-projects-solo (only computable after this change). Explicitly rejected: a thin-description penalty, which double-counts the existing positive `text_richness` signal. Settle the double-counting question on the #51 harness before any of these becomes code.
- **One finding recorded for any future LLM judge.** hiring-agent's `CategoryScore` is declared `score, max, evidence` (`models.py:210-213`) — score first, so the model commits to a number before writing a word of justification and `evidence` is post-hoc rationalization. Constrained decoding costs ~10–15% reasoning accuracy through this premature-serialization mechanism, and it recovers when free-form reasoning precedes the structured field. **If ART ever adds an LLM judge, rationale fields must precede the number.** Related: plain text beats JSON for reasoning-heavy *input*, which argues against restructuring how the JD reaches the tailoring agents — `tailor.py:664` keeps interpolating raw text.

## Issue 115 — Faithful keep: carry prior tailored bullets, section order, and ranked skills across re-tailors
**Status:** complete | **Tests:** 822 pass (9 new)

The planner's `keep` op is supposed to mean "leave this item alone." For projects it did. For **experiences** it meant the opposite: the item was reset to its raw knowledge-graph source bullets, discarding the tailoring. Users lost work they never asked to change, on every chat re-tailor.

This is Stage 0.5 of the #114 policy arc — a correctness floor, not a polish item. Stages 2 (#113) and 3 (#51 Phase 2) both read the reward attached to a `keep` action, and that reward was measuring an unintended reset. #137 widened it further: its `PRIOR SIMILAR JOBS` block instructs the planner to "prefer the framings and items that scored well before," biasing it toward the one op that was broken — and a JobCard records the run's ATS composite as the outcome of its plan, so the card credited an outcome to a plan that was not executed and re-injected it as "what works for this candidate." Per-run corruption became cross-job.

### What shipped
- **Leak 1 — experiences now carry prior tailored bullets (`agents/tailor.py`).** `_apply_plan_to_inputs` built `prior_bullets_by_key` from `prior_content["projects"]` only; it now builds the matching `_exp_key`-keyed lookup from `prior_content["experiences"]` and attaches `prior_bullets` to kept experiences exactly as the project loop already did. `_enforce_plan`'s experience branch prefers `src["prior_bullets"]` over the raw source, keeping the `bullet_budget` trim so a budget reduction still applies to carried-forward bullets. Both branches now read identically (prior wins, then trim); `bullet_budget` is experience-only today, so adding the trim to the project branch is a no-op that future-proofs it.
- **The `_enforce_plan` docstring stated the bug as if it were intended** ("keep experiences get their *source* bullets restored verbatim … keep projects restore prior tailored bullets"). Rewritten to state the actual rule, so the code no longer reads as contradicting its own contract.
- **Leaks 2 and 3 — section order and ranked skills are frozen across re-tailors.** `_rank_skills` and `_ranked_section_order` ran unconditionally, so a re-tailor reordered sections and re-ranked skills the user was happy with. Both are now carried forward from `prior_content`, with a recompute forced when the plan contains a `delete`/`replace` — the actions that change the content the signals were derived from. The gate lands before `fit_content_to_one_page`, which consumes `_section_order`.
- **A carried-forward order can never name a section this run does not have.** `_expected_sections` extracts the membership rule that `_ranked_section_order` already encoded (reorderable sections, minus `achievements` when the user has none, plus the pinned ones) into one classmethod that both the validity check and the ranker read, so the reconciliation is not duplicated. A stale order is rejected and recomputed rather than reaching the formatter.
- **Achievements are unchanged** — they load verbatim from the graph and are keep-all. A carried order preserves the position of the section; item order inside it is never touched.
- **Tests (9 new).** The regression test runs `_apply_plan_to_inputs` → `_enforce_plan` together, because each half looked correct in isolation and the bug lived in the seam. Plus: the first-run fallback to source bullets still holds, `bullet_budget` still trims carried-forward bullets, order and skills survive an all-`keep` re-tailor byte-identical, a structural action forces a recompute, a stale order naming a departed section is recomputed, and `_expected_sections` tracks achievements.

### Benchmark re-baseline
`python eval/tailoring_benchmark.py --stub` with `PYTHONHASHSEED=0`, before (`2a04d63`) → after — every metric identical:

| metric | before | after |
|---|---|---|
| `ats_delta` | +21.925 | +21.925 |
| `baseline_composite` | 49.525 | 49.525 |
| `tailored_composite` | 71.45 | 71.45 |
| `skills_matched_recall` | 1.0 | 1.0 |
| `skills_rendered` | 12.125 | 12.125 |

`python eval/jobcard_eval.py`: 4/4 PASS, `card_quality` 1.0, `functional_equivalence` 1.0, `ats_composite_delta` still `+0.0` on the negation task — #127's monotonicity defect unchanged.

### Deviations from spec
- **The benchmark cannot observe this fix either, for the same reason as #150.** `eval/tailoring_benchmark.py::_run_task` tailors **once** per task and never re-tailors, so a re-tailor-only fix leaves every aggregate untouched. The numbers are reported unmoved rather than a delta being manufactured; the fix is evidenced by the regression tests. This is now the second consecutive issue whose effect the #51 harness structurally cannot measure — **a re-tailor arm is the missing capability** and should be filed.
- **The two `_enforce_plan` branches were made to read identically for the carry-forward rule only, not the first-run fallback.** The issue asked to "make the two read identically." Taken literally that would also align the fallback — but an experience with no prior content falls back to its *source* bullets (what `keep` means before anything is tailored) while a project falls back to the *generated* bullets, and changing the project fallback would alter first-run project output, which is outside this issue. The remaining difference is now documented in the docstring as deliberate.
- **The structural-recompute test asserts on `skills_ranked`, not on `_section_order`.** Deleting a project legitimately re-ranks the remaining sections into the same permutation the frozen order used, so asserting the order "differs" would have been a coincidence-dependent test. `skills_ranked` is the unambiguous witness (frozen `Rust` vs. the recomputed profile skill), and the order path is covered by the carry-forward and stale-order tests.
- **The new end-to-end tests stub `services.rebuild_job_card`.** The #137 card rebuild classifies role family through a live model call; the pre-existing `tailor()` tests in this file make that network hop on every run. The new ones do not.
- **Two test-harness constraints worth recording**, both found by tests failing honestly rather than being tuned around: `validate_plan` refuses to delete the last item of a section, so a structural-action test needs two projects seeded or the delete silently degrades to `keep`; and a `plan_override` action is dropped entirely unless real rows back its item key.
- **No schema change, no UI.** Backward compatible: a result row with no `prior_content` takes the existing first-run path unchanged. Out of scope and untouched: persisted user overrides of order/skills (#118), the planner's keep bias (#117), #113's incremental controller, any scoring weight (#152).

## Issue 150 — `score_tailored` counted the `_explainability` metadata key as a missing required skill
**Status:** complete | **Tests:** 813 pass (9 new)

`UserJobResult.matched_skills` is a `{skill_name: match_info}` dict that also doubles as a carrier for internal metadata: after a chat-driven tailor, `agents/chat.py` merges an `_explainability` block into it. `ATSScoringEngine.score_tailored` treated every key as a skill name, so on the *next* tailor of that job it counted `_explainability` as a required skill that is never present in resume prose — a permanent phantom coverage gap deflating the 0.45-weighted `skill_coverage` by `n/(n+1)`.

The reason this rated a fix ahead of the policy arc rather than beside it: the metadata is only ever written on the chat path, so the deflation fires **on re-tailors only**. A constant offset would be survivable; an error that correlates with `is_revision` is a learnable spurious feature for anything trained on the logged reward — #113's per-edit mechanism, #127's net objective, #51 Phase 2's rule induction, and #137's JobCards, which record a run's ATS composite as the outcome of its plan and re-inject it into future planning.

### What shipped
- **One shared helper (`agents/skill_selection.py`, new).** `is_metadata_key` / `skill_names` / `visible_matched_skills`, deliberately dependency-free (stdlib `typing` only) so the scorer, the FastAPI routers, the eval harness and `services.py` can all import it without a cycle. It could not live in `agents/skill_scorer.py`, which already imports `ATSScoringEngine`. `web/routers/jobs_router.py:63` was already the *second* hand-rolled copy of this filter, so the fix extracts one helper and routes every site through it rather than adding a third — the module #151 grows its required/preferred decomposition into.
- **The fix (`agents/ats_scorer.py`).** `score_tailored` derives `covered` / `gaps` / `total` from the filtered name list. On the issue's own reproduction the polluted breakdown goes from `{score: 75.0, total: 4, gaps: ['_explainability']}` / composite 73.8 to byte-identical with the clean call: `{score: 100.0, total: 3, gaps: []}` / composite 85.0.
- **Four more consumers with the same assumption, found by the audit and fixed.** `eval/metrics.py` (the key inflated the `matched_recall` denominator — a *reported benchmark metric*), `agents/tailor.py::_score_section_relevance` (it injected an `explainability` token into the section-relevance term set), and `services.py` + `agents/chat.py`, which printed `_explainability` in user-facing matched-skill lists.
- **Two consumers audited and deliberately left alone.** `agents/job_card.py` reads `matched_skills["_explainability"]` *on purpose*; the helper filters name lists and never strips the key from the dict, which the existing `test_compile_carries_the_sufficient_statistics` already pins. `cli.py` iterates fresh matcher output, where the key cannot be present.
- **Tests (9 new).** The acceptance test asserts the *entire* breakdown — not just `skill_coverage` — is identical with and without the key; plus metadata-is-never-a-gap, a metadata-only dict scoring as empty (100.0, not 0.0) rather than dividing by zero, `delta` no longer one-sidedly biased, and unit coverage of the three helpers including non-mutation of the caller's dict.

### Benchmark re-baseline
`python eval/tailoring_benchmark.py --stub`, before → after, on `167f0e9`:

| metric | before | after |
|---|---|---|
| `ats_delta` | +21.925 | +21.925 |
| `baseline_composite` | 49.525 | 49.525 |
| `tailored_composite` | 71.45 | 71.45 |
| `skills_matched_recall` | 1.0 | 1.0 |

`python eval/jobcard_eval.py`: 4/4 PASS, `card_quality` 1.0, `functional_equivalence` 1.0, and `ats_composite_delta` still `+0.0` on the negation task — #127's monotonicity defect is unchanged, as required.

### Deviations from spec
- **The ATS numbers do not move, and that was predicted rather than discovered.** `eval/tailoring_benchmark.py::_run_task` drives `analyze → tailor` **once** per task and never goes through chat, so `_explainability` is never written and the bug cannot fire. The issue's Impact section claims the #51 aggregate is polluted; with a single-tailor harness it is not, and `skills_matched_recall` was already 1.0 for the same reason. The fix is evidenced by the direct reproduction above and by the regression tests instead. **Adding a re-tailor arm to the benchmark would make this class of bug measurable and should be filed** — it is real scope growth, not a line change.
- **The recorded ≈+42.4 baseline is a real-LLM run and was not overwritten.** `CHANGELOG.md` records it as "Baseline measurement (real-LLM run, 8/8 tasks): composite 33.0 → 75.4 (mean delta +42.4)". The `--stub` configuration measures +21.925 on unmodified `167f0e9`. These are different configurations, not a regression; the stub figure is recorded here as the stub baseline and the real-LLM figure is left standing.
- **`skills_rendered` / `skills_selection_ratio` are nondeterministic in the harness, independently of this change.** Two consecutive runs of the *same* post-fix tree gave 9.625 and 12.375. Pinning `PYTHONHASHSEED=0` makes them reproducible (12.125 twice), so the cause is set/dict iteration order somewhere in skill selection — pre-existing, unrelated, and worth its own issue. Every ATS metric is bit-identical across all runs regardless of seed, which is why the table above is trustworthy.
- **`score()` needed no change, but the `delta` was biased anyway.** It takes `skill_coverage_score` as a pre-computed float, derived in `agents/matcher.py` from a freshly built `matched_skills` dict before chat ever merges metadata. So the baseline side was always clean — which is precisely why the deflated tailored side biased `delta` *on top of* the component rather than cancelling out. Fixing the tailored side fixes both, and a test now pins it.
- **No decomposition of `skill_coverage` into required/preferred (#151), no scoring weight touched (#152), no schema change.**

## Issue 137 — JobCard: distill completed jobs into sufficient-statistics cards + relevance-ranked injection
**Status:** complete | **Tests:** 800 pass (54 new)

Stage 3 of the Phase 1 epic (#140). The knowledge graph holds durable facts and the active job chat holds the live transcript, but nothing carried *what happened last time you tailored to a similar role*. Job chats are capped by the re-tailor limit, so within-session context management is a non-problem; the axis that scales is **jobs-per-user**. This adds the episodic memory tier: one deterministic card per completed job, and a bounded, relevance-ranked injection of the most relevant cards into the planner context #138 opened.

### What shipped
- **`JobCard` model (`database/models.py`), additive.** One row per (user, job): the compiled `payload`, its `payload_hash`, `index_keys`, the cached `role_family` with its invalidation key and version, and `source_updated_at`. A new table, so `SQLModel.metadata.create_all` in `init_db` picks it up and no `ALTER` is needed — an existing SQLite or Postgres DB gains an empty table and behaves exactly as before.
- **Deterministic compile (`agents/job_card.py`, new).** `compile_card_payload` projects a finished `UserJobResult` into a typed card: ATS composite + per-component breakdown, emphasized experiences/projects/skills (including what the resume *led* with), user-rejected items, the 1–5 `user_score`, terminal status, timestamps. No LLM sees the transcript, so two compiles of the same result are byte-identical under `payload_digest`. The module writes nothing to the database, mirroring how `agents/knowledge_extractor.py` stays pure against `services.apply_artifact_decision`.
- **The negation signal, with provenance and recency.** Rejections are derived from decision-log `delete`/`replace` ops, keeping only each item's **latest** action — an item deleted on one run and restored on the next is not a standing rejection — and tagged `user` (a `chat_approved` plan or non-empty `revision_notes`) versus `planner`, so the planner's own per-job optimisation is never mistaken for a stated preference. They are deliberately **excluded from `index_keys`**: a similarity index cannot represent "not this" (#129 finding 1), so rejections are *pushed* with the card rather than left to be retrieved, and `render_cards` will not drop a user-sourced rejection to fit a budget.
- **One cached, versioned LLM call.** `classify_role_family` goes through the #142 `get_extractor` seam with an enum-validated `RoleFamily` schema, keyed by a digest of the classify inputs and gated on `ROLE_FAMILY_VERSION`, so a card rebuild costs nothing and any failure degrades to `"other"`. Everything else in the compile is deterministic.
- **Event-driven rebuild (`services.rebuild_job_card` / `load_job_cards`).** Hooked in `tailor()` right after the result commit — every surface (web, chat, CLI) reaches tailoring through it — and in `_record_tailor_score`, because the 1–5 score arrives *after* the run that built the card and every card would otherwise record a null `user_score` forever. Event-driven rather than lazy is load-bearing, not stylistic: per the #109 amortization amendment, distillation only pays when its prefill cost sits outside the inline latency budget, so compiling at next-tailoring time would put the cost back inline and break the condition that justifies distilling at all.
- **Relevance-ranked, bounded injection.** `select_cards` blends role-family match, JD-embedding similarity, fact-level key overlap and recency (LongMemEval #108, findings 1–2), and `render_cards` caps the result at a token budget, so prompt cost is flat in the number of accumulated jobs. Similarity goes through the #142 seam in **candidates mode** with card vectors held in memory. Cards reach the planner beside `graph_evidence` in `_load_inputs` — the seam #138 opened, not a parallel path — and `_llm_plan` renders them into a `PRIOR SIMILAR JOBS` block instructing the planner to treat "User removed" as a standing preference. `n_job_cards` joins `n_graph_evidence` in the decision log.
- **Card-quality eval (`eval/jobcard_eval.py` + `eval/jobcard_dataset/`, 4 tasks).** Two arms plan the same next job — one reading the compiled card, one reading the raw finished result — compared as **typed ops per item**, not text, per the issue's own note that a structural diff is cheaper and less noisy than trajectory similarity. Misses on a user-rejected item that *recurs* weigh 3×, so a card that forgets a rejection cannot score near a correct one. Reports `card_quality`, `functional_equivalence`, both downstream arms, and a per-field ablation table (mean **and** worst case). `tests/test_jobcard_eval.py` runs it in-suite and proves it goes red when the compile drops rejections.
- **Tests (54 new).** Deterministic compile; the negation guard end-to-end (a user rejection round-trips and survives rendering at the tightest budget); provenance and reversal semantics; `role_family` caching and its invalidation; selection ordering stable and independent of input order, exercising the numpy candidates path with real vectors on SQLite; **absent-card back-compat asserted literally** — the planner prompt built with an empty card set is string-identical to the prompt built with no card argument at all; graceful degradation when the card store fails.

### Deviations from spec
- **The issue body predates parts of `main`; the compile was written against what is actually there.** `user_score` is not a column — it lives at `tailoring_decisions[-1]["reward"]["user_score"]`, written by the chat score prompt — so the compile reads the decision log and **no column was added**. `_explainability` is a key *inside* the `matched_skills` JSON dict, not a field beside it. `role_family` did not exist anywhere and is created here.
- **Ranking uses the numpy candidates path, not the pgvector column.** #142's infra note called this "the cleanest pgvector consumer in the whole P1 arc", but `embedding_vec` has no write path yet, and `search_similar`'s table-scan mode is Postgres-only and returns `[]` on SQLite — so routing through it would have silently yielded zero cards in local dev and across the entire suite while the tests still passed. Cards number in the tens per user, so numpy is free and ANN buys nothing. Cards rank against their source job's already-cached `JobDescription.embedding`. **Populating `embedding_vec` (dual-write on ingest + backfill) is a separate write-path issue and should be filed.**
- **Rejections carry a `source` the issue did not ask for.** A flat "user-rejected" list would have conflated a human saying "drop the game project" with the planner dropping an item to fit a budget. The second is a per-job optimisation, and promoting it to a standing preference would teach the next job something the user never said. Only `user`-sourced rejections are exempt from budget truncation and drive the eval's negation weighting.
- **The ATS composite delta is 0.0 on every eval task, and that is reported rather than hidden.** Every ATS component is a *coverage* measure: honour a rejection, drop an off-topic project, and coverage is unchanged. The eval therefore reports a relevance-density arm beside it (reusing `eval/metrics._keyword_relevance`), which does move — +0.104 on the negation task. Reporting only the composite would have hidden the benefit; reporting only the density would have hidden the null result.
- **`user_score` and `ats` ablate to +0.000.** The offline probe rule consumes only `emphasized` and `rejected_items`. This is a statement about that metric, not a verdict that the fields are dead weight — they are carried for the planner prompt and for #51 Phase 2 / #119 to consume; a `--live` run is what would put a number on them. Noted at the ablation table so the zeros are not misread.
- **The offline planner arm is a probe rule, not the production planner.** Deliberate: it makes plan divergence mean information loss and nothing else, and it keeps the default mode deterministic — the same choice #21's KU eval made. The issue's own note requires temperature-0 replay for this metric, which `--live` does. A test pins that the probe is not a constant function, since a memory-ignoring rule would score every card 1.0 including an empty one.
- **Role-family resolution for the *active* JD is cache-first and gated.** A user with no prior cards never classifies (nothing to rank), and `plan_preview` is cache-only, so a preview never spends a model call on ranking. A novel JD classifies once and `tailor()` hands the label to `rebuild_job_card` so a run never pays twice.
- **No UI.** Cards are planner-facing only; surfacing them is a follow-up, per the epic's one-chunk-one-PR rule.

## Issue 21 — Extract knowledge artifacts from chat into the knowledge graph
**Status:** complete | **Tests:** 746 pass (54 new)

Stage 2 of the Phase 1 epic (#140), unblocked by foundation #142. #138 made the knowledge graph a mandatory planner evidence step; this is the production side that fills it. A partial version of this issue already sat on `main` from the TUI extraction (`create_artifact_from_chat` + a `/save` command), but it extracted with a hand-rolled prompt, markdown-fence stripping, and `json.loads` — the exact path #142 exists to delete, and one of the extractors the #142 PR did not migrate. It also had no *reason* step at all: every candidate was implicitly an add, so the case that matters most — a later turn contradicting an earlier fact — either duplicated a row or dead-ended on "already in your profile". And `source_context` was accepted by the service and silently dropped, so the origin-chat back-reference the issue asks for was never actually stored.

### What shipped
- **Chain-of-Note extract-then-reason extractor (`agents/knowledge_extractor.py`, new).** Two passes: `extract` writes grounded notes about what the transcript claims (each requiring a verbatim evidence quote — an ungrounded note is dropped rather than offered), then `reason` judges each note against the facts already in the user's graph. Both go through `llm.get_extractor` (#142), so output is schema-validated by `with_structured_output` and LangSmith traces both; there is no `JsonOutputParser` and no `json.loads` on this path, and a test parses the module's AST to keep it that way. Typed schemas (`ChatArtifactNote`, `ArtifactDecision`, + list wrappers) live in `agents/extraction_schemas.py` alongside the existing ones. Splitting extraction from judgement is the P0 arc's finding (LongMemEval, #108): a single pass conflates "what was said" with "what should change" and reliably misses the contradiction case.
- **A deterministic third node, not an LLM call.** `decide` normalizes the model's free-form decision strings and splits authority by what each layer is actually good at. On an exact name match the deterministic rules win outright — a match that changes nothing becomes `no_op` (so a hallucinated `add` can never duplicate a row), a match that changes something becomes `supersede`. Where equality *cannot* match, because a promotion or rename changes the very name it would key on, the model's `supersede` is honored if its `target` resolves to a real fact of the same type. Anything else falls back to `add`.
- **LangGraph, in exactly one place.** Composition is a thin `StateGraph` (`extract → reason → decide`). Per the framework-adoption decision this is the one surface in ARTie that adopts LangGraph; arbitration and grouping elsewhere stay deterministic. The nodes just call plain module-level functions, so each step is unit-testable alone and the graph supplies only composition and per-node trace spans. It also degrades: if LangGraph is unavailable the pipeline runs the same nodes in sequence. State is a `TypedDict`, not a bare `dict` — LangGraph merges per declared channel, and a bare-dict schema makes each node's return *replace* the whole state, so `decide` would never see the notes.
- **`services.apply_artifact_decision` (`services.py`).** Where a *confirmed* proposal becomes rows: `add` delegates to the existing `create_artifact_from_chat`, `no_op` says so in plain English, `supersede` updates the matching row in place — a promotion moves the title on the existing Experience rather than adding a second row for the same job. Matching reuses the ingestion deduper (`ResumeParserAgent._projects_match` / `._experiences_match` / `._names_match` / `._institutions_match`) rather than inventing a second notion of "same artifact"; where a rename defeats those, the decision's target label names the row. Plain-English return, never raises, per the repo's tool-wrapper convention.
- **The origin-chat back-reference is now actually stored.** Nullable `source_context TEXT` on `userskill`, `project`, and `experience`, added to the existing additive `_migrate_db()` list. Free-form text and **not** a foreign key by design — the artifacts belong to the user profile, not the job, so there is no cascade to follow and the reference is allowed to dangle. `create_artifact_from_chat` now persists the argument it had always accepted and discarded.
- **Explicit confirmation, unchanged as the default.** `/save` lists candidates labelled `Add` or `Update` (naming the fact an update replaces), filters out `no_op`s, and writes only on a numbered pick or `all`; `skip` dismisses. Backend API for the follow-up UI, split so proposing can never write: `POST /api/chat/{job_id}/artifacts/propose` (stateless candidates, no writes) and `POST /api/chat/{job_id}/artifacts/decide` (`accept` writes, `dismiss` does not). Both owner-scoped for job chats via a `_require_own_job` helper factored out of `get_history`, so the issue #73 check is stated once.
- **Knowledge-Updates regression eval (`eval/knowledge_updates_eval.py` + `eval/ku_dataset/`).** The LongMemEval-derived regression the epic calls for. Each task is a closed world — the rows already in the graph, a transcript whose last turn revises one of them, the required post-state — run through the real `apply_artifact_decision` path and read back. `update_accuracy` scores a stale graph and a duplicated row both at 0. Four tasks: a promotion superseding a title, a corrected proficiency superseding a skill, a genuinely new fact still being added (control — a pipeline that superseded everything would pass the update tasks and fail here), and a restated fact staying a `no_op` even though the scripted model says `add`.
- **Tests (54 new).** Ungrounded notes dropped; both directions of the deterministic override; the subgraph composing end to end; supersede per artifact type; `source_context` persistence; the accept/dismiss API including cross-user rejection; the eval green in-suite plus a test that neuters the supersede path to prove the eval actually goes red when an update is dropped. **The non-deletion invariant** gets its own test: create artifacts from a chat (including a superseded row), delete the job *and* its chat history, and every knowledge-graph row survives with its update intact.

### Deviations from spec
- **The issue body predates the web migration and the P0 synthesis; the epic's stage-2 row is what was built.** `tui/services.py` is `services.py` (the TUI was removed), and the "chat panel suggestion block" it describes is the React web chat. This PR is backend-first — extraction, reasoning, persistence, API, eval — and **the inline chat-panel UI is deliberately left to a follow-up issue**, per the epic's one-chunk-one-PR rule.
- **Three of the issue's five acceptance criteria were already met on `main`** by the TUI-era `create_artifact_from_chat` and its tests. What was missing against the epic's stage-2 scope was the reason step, the supersede persistence path, the stored back-reference, and the KU eval — which is what this PR is.
- **`decide` is not purely deterministic, and shouldn't be.** The equality check the issue scopes ("no duplicate detection beyond a simple name/category equality check") cannot see a promotion, because the name is exactly what changed. So the LLM keeps authority over precisely that case, guarded by requiring its `target` to resolve to a real fact. The equality check remains the duplicate guard it was scoped as.
- **The employer-only fallback for superseding an experience only fires when unambiguous.** With two roles at one company and no resolvable target there is no way to tell which the user meant, so a new row is added rather than the wrong one overwritten. Likewise an update carrying no description leaves an existing description alone instead of overwriting it with the evidence quote.
- **The eval's default mode is scripted, not live.** The task files supply the notes and decisions, so what is pinned in CI is the pipeline contract — the deterministic layer and the supersede persistence — rather than a model's output on the day; that is what makes it a regression eval rather than a benchmark. `--live` swaps in real extractors through the #142 seam to measure the model itself.
- **`dismiss` is an action on `/artifacts/decide`, not its own endpoint.** Proposals are stateless, so a separate dismiss endpoint would have had nothing to do; folding it in as an action keeps the accept/dismiss pair explicit without dead code.

## Issue 142 — Foundation: structured-extraction seam + pgvector dual-path retrieval + LangSmith scaffold
**Status:** complete | **Tests:** 700 pass (19 new)

The P1 capture/knowledge issues (#121/#21/#129/#133/#137) each needed the same three pieces of plumbing — one validated extraction path, one vector-search path, and tracing — and would otherwise have re-implemented them three ways. This is that shared foundation and has no user-facing behavior of its own. Grounded in the 2026-07-23 framework-adoption review: on a LangChain stack, `.with_structured_output` gives schema-validated Pydantic output natively and stays on the LangChain path (so LangSmith traces it for free), which is why it was chosen over Instructor.

### What shipped
- **Structured-extraction seam (`llm.py`).** New `get_extractor(role, schema)` wraps `get_llm(role).with_structured_output(PydanticModel)` in a `StructuredExtractor` with a bounded validation-retry: it retries once on a validation/parse miss, then re-raises so each call site's existing `try/except → []` graceful-degrade still holds (an extraction failure never crashes ingestion). Typed schemas live in `agents/extraction_schemas.py`.
- **All 8 extractors migrated off `JsonOutputParser`.** `agents/parser.py` (6: experiences, education, achievements, projects, skills, repo-skills) and `agents/job_analyzer.py` (2: metadata, JD skills) now return schema-validated Pydantic models, `.model_dump()`'d back to the `List[Dict]` the `_save_*`/dedup/heal persistence layer already consumes — so that heavily-tested layer is untouched.
- **pgvector dual-path retrieval (`database/vector_search.py`).** One `search_similar()` / `cosine_sim()` seam branching numpy dot-product (SQLite / in-memory) vs pgvector `<=>` ANN (Postgres). A Postgres-only guarded migration (`_migrate_pg_vector_columns`, mirroring `_migrate_pg_uuid_columns`'s `engine.dialect.name` guard) runs `CREATE EXTENSION IF NOT EXISTS vector` and adds a `vector(384)` `embedding_vec` column beside the JSON `embedding` TEXT column — which stays the SQLite path and portable source of truth. `matcher._check_semantic_match` and `skill_scorer._semantic_similarity` now route through the seam (numpy branch, behavior identical); pgvector is the accelerated path for the #137/#121 consumers.
- **LangSmith scaffold (`config.py`, `.env.example`), OFF by default.** Env-gated `langsmith_enabled()` reads `LANGCHAIN_TRACING_V2` live; nothing writes to `os.environ`, so tracing turns on only on explicit opt-in. Because extraction stays on the LangChain path, one trace covers extraction + chat + tailor uniformly. PII caveat documented: traces carry resume + JD text — do not enable in prod until P3.
- **Tests (19 new).** Extraction-seam retry fires on malformed output then recovers, exhausted retries re-raise, schema `model_dump` keeps persistence keys; vector numpy top-1 is bit-identical to the old `np.dot`/argmax and `cosine_sim` matches the old raw dot for normalized vectors, a SQLite `model_cls` query never emits vector SQL, and the guarded migration is a no-op on SQLite; LangSmith is disabled for unset/falsey env and never force-enabled on import.

### Deviations from spec
- **`_coerce_records` stays.** The issue said to delete the coercion the schema now covers, but it still maps the *deterministic* Bright Data / LinkedIn structured records in `_save_linkedin_structured` — which is not LLM output — so only the 6 LLM extractors stopped using it. Its docstring and tests remain.
- **Extractors return `List[Dict]`, not raw Pydantic models, at the method boundary.** The seam returns validated models; each `_extract_*` `.model_dump()`s them so the persistence/dedup/heal layer keeps its `List[Dict]` contract untouched. This honors "keep changes scoped / preserve backward-compat" — converting every `_save_*` to consume models was a far larger, higher-risk blast radius for no functional gain here.
- **pgvector is not yet consumed in scoring or populated on ingest.** This issue produces the column + helper only; writing vectors into `embedding_vec` and reading them in scoring is #121/#137 downstream (explicit non-goal). The PG `<=>` branch is validated on staging Postgres, not in the SQLite suite.
- **The benchmark stub LLM gained `with_structured_output`.** `eval/tailoring_benchmark.py`'s stub was a `RunnableLambda` and had to become a small `_StubLLM` Runnable to serve both the extraction seam and the tailor `JsonOutputParser` chain; prompt markers are unchanged.

## Issue 138 — Wire the knowledge graph into the tailoring planner as a mandatory evidence step
**Status:** complete | **Tests:** 674 pass (12 new)

The knowledge graph informed skill *matching* (`agents/matcher.py`) but never the *tailoring decision* that writes the resume. `tailor.py::_load_inputs` read flat `Experience`/`Project`/`UserSkill` rows and pre-selected projects by JD-keyword overlap, so a project that evidences a *required* JD skill whose name the JD's own prose never repeats scored low, dropped into the replacement pool, and the planner never saw it. This adds a mandatory KG-evidence node to the tailoring pipeline so "the graph is actually consulted during tailoring" is a guarantee, not incidental — the facts-side mirror of the planned mandatory persona node (#129/#133).

### What shipped
- **One shared evidence-traversal surface (`knowledge_graph/builder.py`).** `SkillGraphBuilder` gains `get_experiences_using_skill()` (the experience-side mirror of the existing `get_projects_using_skill()`, returning `{title, company}`) and `evidence_for_skills(skill_names)` → `{skill: {projects, experiences}}`, omitting skills with no evidence edge. The matcher and the planner now reach the graph through this one builder; no traversal is duplicated into `tailor.py`.
- **A mandatory KG-evidence node in `_load_inputs` (`agents/tailor.py`).** After project pre-selection, tailoring always builds the user's graph and gathers evidence for the active JD's skills (`JobSkill → Skill.name`), then:
  - *promotes* a pooled project the graph ties to a JD skill no already-selected item covers into the candidate set the planner sees (and drops it from the replace pool), capped at `MAX_PROJECTS`;
  - *annotates* each evidenced candidate with a `graph_evidence` list.
  Wrapped in try/except → `{}`, so a build failure or sparse graph degrades gracefully.
- **Planner consumption (`agents/tailor_planner.py`).** `_planner_items` carries `graph_evidence` onto each item; `_llm_plan` includes it in the per-item planning payload and the prompt instructs the planner to treat it as strong relevance evidence — prefer keep/revise over delete, never replace an item that uniquely evidences a required skill. `plan_preview()` inherits this. `n_graph_evidence` is added to the decision-log context features.
- **Backward compatibility as a hard guarantee.** Every touch is additive and gated on non-empty evidence: an empty graph means no promotion, no `graph_evidence` keys, and byte-for-byte-identical planner/generator payloads. The deterministic `default_plan`/`validate_plan` never read the new field. Covered by a test asserting the candidate set is unchanged and deterministic when evidence is empty.

### Deviations from spec
- **Promotion is projects-only.** All experiences already reach the planner (they are budgeted, not pooled), so for experiences the graph value is annotation; only projects get dropped into a pool, so only they need promotion. The issue names experiences too — they are covered by annotation, not promotion.
- **The graph-only demonstration uses a required-skill-not-in-JD-prose path**, not GitHub provenance. Provenance edges (the strongest graph-only signal) live in the unmerged #136 branch; this change is based on `main`, so the test evidences the same "text pre-selection misses it, the graph catches it" gap with the substring graph `main` already has. On top of #136's richer builder the same code additionally surfaces provenance-only ties for free.
- **All JD skills feed evidence, not only `required` ones** — a graph tie is a tie; keeps the surface minimal.

## Issues 83 & 134 — Landing page, Tailwind migration, and the Supabase-light theme
**Status:** complete | **Tests:** 670 Python pass (5 new), 90 frontend pass (3 new)

A first-time visitor previously hit `/` and was bounced straight to a login form with no framing at all. This adds the public landing page that precedes auth, and — because every component worth borrowing for it ships as Tailwind + shadcn — migrates the frontend off its hand-rolled `CSSProperties` style objects. The theme moves from the terminal-green palette inherited from the removed TUI to a Supabase-style light theme, which suits the audience the page is actually addressed to: students writing a resume for the first time.

### What shipped
- **`web/frontend/src/pages/LandingPage.tsx` (new).** Public marketing page at `/`. `Root` renders it for signed-out visitors and the app shell for signed-in ones, so a returning user never sees marketing. Hero states the audience (entry-level internships and roles); the method is explained in four beats — knowledge graph (#21, #92) → JD profile (#121) → planned edits (#114–#117) → check and score (#122–#126) — followed by the no-fabrication section that carries the actual differentiator.
- **Tailwind + shadcn foundation.** `tailwind.config.js`, `postcss.config.js`, `src/index.css`, and `cn()` in `src/lib/utils.ts`. Every page and component under `src/pages/` and `src/components/` converted off the `const s: Record<string, CSSProperties>` pattern; `theme.ts` deleted with no importers remaining.
- **Supabase-style light theme, dark retained.** Palette lives in `index.css` as HSL custom properties: white ground, `#171717` ink, `#E6E8EB` hairlines, mint `#3ECF8E` on CTAs with a deep `#006239` label, 6px radius. Dark is a maintained counterpart under `.dark` — not an inversion — with `lib/theme.ts` (pure resolution, unit-tested), `ThemeContext`, and a `ThemeToggle` in both the landing nav and the app header. An inline script in `index.html` stamps the class before first paint so a dark-mode user never sees a white flash.
- **Switzer, self-hosted.** Five weights in `public/fonts/` via `@font-face`. noahpdillon.com uses Suisse Intl, which is commercial; Switzer (Fontshare, free for commercial use) is drawn as a Suisse-style neo-grotesque and is the closest free equivalent. No CDN dependency; only the monospace face still comes from Google Fonts.
- **Contrast correctness as an explicit rule.** Mint measures ~1.8:1 as text on white, so `bg-primary` is fill-only (always paired with `text-primary-foreground`) and green *text* uses `text-accent` (deep green in light, light green in dark). `warning` was split into its own token so mid-range ATS scores and incomplete-record badges stop borrowing the brand hue. The PDF gutter became theme-aware instead of a hardcoded `#525659` charcoal.
- **`AuthLayout`.** The four signed-out pages were near-identical copies; the card, brand mark, and control styling now live in one place.

### Deviations from spec
- **`DataExplorer.tsx` still uses the old style-object pattern.** Converting ~650 lines of table, chart, and inline-edit-form JSX carried more regression risk than the rest of the migration combined, so its tokens were rebound to the new palette instead — it themes correctly and imports nothing, but the JSX conversion remains open on #134. Noted in `web/CLAUDE.md`.
- **Inline `style` is retained for runtime-computed geometry** — pane widths and split fractions (`JobWorkspace`, `ResumeSplit`), chat padding from `paneResize`, and the PDF drag bands in `PdfDragOverlay`, whose positions come from PDF text metrics. Tailwind utilities cannot express these; each site carries a comment saying why.
- **#83 was re-phased from P4 to P0** on 2026-07-21 and pulled ahead of the normal phase sequence at the owner's direction. Its issue body was empty and was written as part of this work; #134 was split out so a whole-app refactor did not ride inside the landing-page diff.
- The theme was verified in-browser across chat, data (all seven sub-tabs), ingest, profile, and the job workspace in both themes, with a per-node WCAG sweep. Responsive behaviour below tablet width rests on standard Tailwind breakpoints and was not observed directly.
## Issues 130 & 131 — User-scoped tailoring artifacts and fail-closed user resolution
**Status:** complete | **Tests:** 665 pass (8 new)

Two single-user assumptions that survived into the multi-user web deployment. #130 is the instance — tailoring wrote user content to a process-global path; #131 is the class — the user-resolution helper underneath it silently substituted an arbitrary user when nothing was bound. Same root shape as #73, which fixed its call sites without removing the fail-open default they sat on.

### What shipped
- **Chat/web tailoring writes nothing to disk (`agents/chat.py`).** `run_tailor` wrote `tailored_output.json` and `tailored_resume.tex` to the CWD — one location shared by every user of the instance, where concurrent runs overwrite each other. A repo-wide search found no consumer of either file on the web path (tailored content is already persisted to `UserJobResult`), so the write was removed rather than relocated under a per-user directory. A file nobody reads is not worth making user-scoped.
- **CLI artifact behavior unchanged.** `cli.py`'s `tailor` command has its own write and never called `run_tailor`, so the two paths were already independent. `python cli.py tailor <job>` produces its artifacts exactly as before.
- **`require_active_user()` fails closed (`database/user_utils.py`).** Raises `NoActiveUserError` instead of resolving to `select(User).limit(1)`. The old fallback both returned someone else's user *and* wrote that choice into the global `ACTIVE_PROFILE_FILE` pointer, which `get_active_profile()` falls back to whenever the request ContextVar is unbound — so one bad resolution poisoned every later lookup in the process.
- **Library code opts out of the fallback.** `agents/parser.py` (`ResumeParserAgent.__init__`) and `graph/pipeline.py` (`ingest_resume_node`) now call `require_active_user()`. The parser writes rows under `self.user`, and the pipeline node sets `state["user_id"]` for every downstream node, so a wrong resolution in either misattributes an entire run.
- **The CLI opts in explicitly (`cli.py`).** `get_or_create_default_user()` is renamed `get_or_create_cli_user()` and is now the only place allowed to adopt an existing row or write the pointer file — justified because the CLI is genuinely single-user. `main()` binds it via `set_request_user()` for the eight commands in `_USER_SCOPED_COMMANDS`.

### Deviations from spec
- #130's acceptance criteria asked for a test that two runs under different `user_id`s do not overwrite each other's artifacts. Once the write is deleted that test cannot exist, so it was replaced with `test_run_tailor_writes_nothing_to_cwd` — the stronger invariant.
- The binding in `cli.py` is per-command rather than unconditional in `main()`. Binding on every invocation would make `--help` and `supabase-setup` create a `Default User` row on an empty DB, which they do not do today.
- **`services.py::compute_app_state()` still uses `select(User).limit(1)`** and was left alone: it has zero callers in the repo. It is the same fail-open shape and should be deleted, but that is out of scope here.
- #131 is filed and shipped as **latent hardening, not an active leak.** Every reachable web path was traced and found correctly bound (`jobs_router` passes `user.user_id` explicitly and never enters the pipeline; `chat_router`, `ingest_router`, and the LinkedIn background task all bind). The defect was that nothing *enforced* it.

---

## Issue 112 — ε-greedy exploration over revision strategies
**Status:** complete | **Tests:** 655 pass (9 new)

The decision log shipped with #91/#51 recorded a propensity of 1.0 on every action, because the policy was deterministic: the same context always produced the same strategy. A log with no action variance cannot train a bandit. This adds ε-greedy sampling over the revision strategy, gated behind an off-by-default exploration mode that also suspends the best-of-N retry loop — the two must move together, since best-of-N corrupts the reward that exploration exists to measure.

### What shipped
- **ε-greedy strategy sampling (`agents/tailor_planner.py`).** `_choose_strategy(item, knobs, rng, explore)` is the single source of truth for the strategy decision and its propensity, replacing the `_FIXED_PROPENSITY` constant that was written in two places. Sampling is per item and independent, so each action carries its own propensity — the granularity #113's per-edit reward needs. The greedy arm is context-dependent (`default_revise_strategy` for items with assigned keywords, `tighten` for those without), so propensity is computed against the arm that is greedy *for that item*. ε defaults to 0.2 via `TAILOR_EXPLORE_EPSILON`; the RNG is injected into `TailorPlanner.__init__` so exploration is reproducible under a seed.
- **Scope is the `strategy` field only, for `op == "revise"`.** Ops stay with the planner: exploring over `delete`/`replace` risks structurally damaging a resume for exploration's sake.
- **Sampler authority over the LLM.** On an exploration run the sampled strategy overrides whatever `validate_plan` parsed from the model — without the override we would log a known density for a decision whose distribution we cannot observe. The LLM remains the proposal distribution for `op`, `replacement_key`, and `keywords`. Actions now record `strategy_source` (`sampled | llm | default`) and retain the model's own pick as `llm_strategy`, free off-policy data on its implicit policy.
- **N=1 in exploration mode (`agents/tailor.py`).** `_max_attempts()` returns 1 when exploration is on. Best-of-N makes the logged reward a max over N draws rather than a sample of `E[reward | plan]`, and N is itself an outcome (a strong first draw exits early, a weak one spends the budget), so conditioning on the reward conditions on a collider — no post-hoc logging of per-attempt scores fixes that. Generator temperature stays at 0.3: N=1 there is unbiased but noisy, and variance costs samples where bias costs correctness.
- **User-in-the-loop paths never explore.** A run carrying `revision_notes` takes the greedy arm at propensity 1.0 (sampling `tighten` when someone asked for more numbers is user-hostile); `plan_preview()` opts out via `allow_explore=False`; and a `chat_approved` plan logs propensity `null` rather than 1.0, marking it as off-policy rather than attributing a human's choice to the policy.
- **Mode recorded per entry.** `decision_log_entry` writes `exploration_mode` and `n_attempts` on every tuple, so exploration data stays separable from historical best-of-N data instead of being inferred from the attempt count.

### Deviations from spec
- `TAILOR_EXPLORATION_MODE` and `TAILOR_EXPLORE_EPSILON` are read per call rather than cached at import, so the mode can be flipped without a reload; the rest of the module's knobs still resolve at import.
- **This deliberately suspends #58's "never ship a worse output than one we already produced" guarantee while the mode is on.** That is a real product regression, which is why the mode is off by default and must be time-boxed: restore best-of-N once the log carries enough (context bucket, strategy) coverage for #51 Phase 2 induction. The coverage target is set once the first numbers land.

---

## Issues 91 & 51 — Typed tailoring actions, chat re-tailoring action set, RL-ready decision log
**Status:** complete (slices A+B of the arc; the #51 bandit itself is follow-up) | **Tests:** 621 pass (29 new)

Tailoring is no longer an implicit whole-resume rewrite. Every run is now planned as strict, typed, per-item edit actions with rationales; chat re-tailoring proposes that plan as a reviewable delta against the current tailored resume (the source of truth) instead of regenerating it — the root cause of #91's "new feedback, same resume" bug. Every run logs a `(context, actions, propensity, reward)` tuple, giving #51 Phase 2 its offline contextual-bandit dataset, with the ATS composite delta as the tailoring reward and an explicit 1–5 chat score as the revision reward.

### What shipped
- **`agents/tailor_planner.py` (new).** `TailorPlanner` emits one action per resume item — `keep | revise | replace | delete` — with a revision strategy (`keyword_weave | quantify | tighten | reframe`), keywords to weave, a replacement candidate from the unselected project pool, and a one-sentence rationale. LLM-planned with a deterministic fallback; validated so unknown items are dropped, replace is pool-only, and a section can never be emptied. `decision_log_entry` builds the per-run log tuple.
- **Planner integration (`agents/tailor.py`).** Input loading extracted to `_load_inputs` (shared with the new `plan_preview()`); plans are applied structurally to the generator inputs (deletes removed, replacements swapped in), rendered as per-item prompt instructions, and enforced deterministically on the output (deleted items stay out even if regenerated; `keep` restores source bullets verbatim). Re-tailors plan a delta against the prior tailored content. `tailor(plan_override=...)` executes a chat-approved plan instead of re-planning.
- **Decision log.** New `UserJobResult.tailoring_decisions` JSON column (append-only) records context features, executed actions with propensities, and the per-component ATS delta reward for every run.
- **Job-chat action set (`agents/chat.py`).** `tailor <request>` on an already-tailored job now runs PROPOSE_PLAN → shows the per-item delta with rationales → `1` applies exactly that plan (threaded through the pipeline as `plan_override`), `2` cancels. New `explain` (rationales from the decision log), `what changed` (diff, ops only), and `revert` (one-level undo via the new `tailored_resume_previous` snapshot column) commands, each with router-LLM tool equivalents for freeform phrasing. The router prompt is grounded with a summary of the current tailored resume so the bot answers about it instead of offering to "look it up".
- **1–5 score channel.** After each revision run the chat asks for a 1–5 score; the reply is written as `user_score` into that run's decision-log entry (one-shot — any other message dismisses the prompt). This is the chat-side reward for score-driven tuning.
- **README** rewritten around the architecture: pipeline stages, chat action set, learning loop, and eval harness.

### Deviations from spec
- #91 asked to "design a policy that interprets what action should be taken based on chat" — the deterministic policy scaffolding (typed actions, propensity logging, strategy knobs) shipped here; the *learned* policy (Thompson sampling over knobs + off-policy evaluation) is the remaining #51 Phase 2 scope.
- Revert is one-level (swap with the previous version) rather than full history — each run snapshots only what it replaced.
- First-time tailoring still runs directly without a proposal step; only re-tailors propose first, since a first run has no delta to review.

---

## Issues 69, 96, 92 & 85 — Ingestion quality and manual knowledge-graph editing
**Status:** complete | **Tests:** 616 pass (24 new)

A cohesive arc on LinkedIn/knowledge-graph ingestion quality plus the durable manual-correction fallback. Bright Data nests multiple roles at one employer under a `positions` array, so all-but-one role per employer was silently dropped (the "missing UCSD role" half of #95); "essentially empty" experience stubs were auto-added; and there was no way for a user to correct a bad row short of wiping their data. This arc fixes the root cause, guards against empty stubs, and adds user-scoped edit/delete that survives re-ingest.

### What shipped
- **Persist + replay raw LinkedIn scrapes (#69).** The raw Bright Data record is stored on the user row at ingest (`User.linkedin_raw_record`), and `services.replay_linkedin` / `cli.py replay-linkedin` re-run the structured mapping against it with no new (paid) scrape — so mapping improvements can be applied to an existing profile and #96 has a real record shape to regression-test against.
- **Nested `positions` traversal + bullets capture (#96).** `_flatten_linkedin_experiences` expands each nested role into its own Experience (backfilling the parent company), so a multi-role employer yields one row per role. `_linkedin_bullets` captures bullets from a multi-line role description so a role isn't reduced to a content-empty stub the tailor drops. A pinned fixture (`tests/fixtures/linkedin_nested_positions.json`) and an end-to-end test prove both UCSD roles survive ingest → tailor.
- **Manual edit & delete of KG rows (#92).** Caller-scoped `PATCH`/`DELETE` for a user's own Experience/Education/Project rows, surfaced as inline edit + delete-with-confirm in the Data Explorer. Two protections keep corrections durable across re-ingest: a `manually_edited` flag (an edited row wins dedup survivorship and is never reverted/enriched by merge-on-save or self-heal), and a `DeletedEntry` tombstone table (every save path skips recreating a deleted row, reusing the existing fuzzy-match functions so name variants and shared repo URLs are also blocked). Endpoints reject a non-owner id (404) and empty required fields (400).
- **No auto-added empty experiences; flag incomplete ones (#85).** `_experience_is_includable` blocks an essentially-empty stub (no dates/description/bullets and no complete title+company identity) from being auto-added, while keeping a legitimately minimal `Title @ Company` row. The experiences getter reports `incomplete`/`missing` (title/company/dates/details) and the Data Explorer renders a warning badge so the user can complete (edit) or delete such rows.

### Deviations from spec
- **#92 delete design:** used a `DeletedEntry` tombstone table (hard delete + tombstone) rather than a soft-delete flag, so the ~20 existing read sites (tailoring, formatter, scorer, chat, graph) stay untouched — deleted rows genuinely leave the entity tables, and only the delete endpoint plus the six save-loops became tombstone-aware.
- **#85:** rather than discarding all sparse rows, the methodology keeps a legitimately minimal role and surfaces incompleteness for the user to resolve; the tailor's existing content-empty filter remains the second line of defense for output.
- **#69:** the "replay path" is exposed via a service function and a CLI command (no web endpoint), which is sufficient for the mapping-iteration use case and avoids adding an unused UI surface.

---

## Issue 90 — Draggable pane resizing on the Jobs tab
**Status:** complete | **Tests:** 87 frontend pass (28 new); Python suite unchanged

The Jobs workspace panes were fixed-width: the chat column auto-sized (400px, or `calc(50% + 200px)` when a single resume pane was shown) and the split-view editor/preview were locked at 50/50. Users can now drag the boundaries to size the panes themselves — the chat ↔ resume divider in every pane layout (preview, source, and split), and the editor ↔ preview divider within split view. Each pane clamps to a pixel floor so a drag can never squash it past usability, and the compiled preview auto-scales to fit whatever pane it gets, so the whole page stays visible at any width. Both preferences persist across reloads (localStorage) and reset to the automatic default on double-click.

### What shipped
- **`lib/paneResize.ts`.** Pure, DOM-free geometry, unit-tested in `lib/paneResize.test.ts` (28 cases):
  - `chatWidthFromPointer` — chat clamps to ≥760px (doubled from the original 380 after it kept condensing too far) and leaves `minResumeWidth(view)` for the resume side.
  - `minResumeWidth` — pixel floors only: the editor's 280px, and a large 900px preview floor in **both** split and Preview-only views (`MIN_PREVIEW_WIDTH` / `MIN_PREVIEW_ONLY_WIDTH`, ~⅓ of a wide screen) since the compiled resume *is* the product and should stay big and readable, not merely legible; never below the 320px resume floor. Error-safe: a window too small to honor a floor keeps the chat at its 760 minimum and gives the resume the remainder (no overflow, no frozen divider), so it degrades gracefully rather than breaking. The 900px split-view preview floor is demanding — split view wants a wide (~1964px) workspace, below which the editor↔preview band collapses and that divider pins in place (still no overflow).
  - `editorFractionBounds` / `clampEditorFraction` / `editorFractionFromPointer` — the editor's split share is bounded below by the 280px editor minimum and above by the 900px preview minimum, derived from the live split **width**. (An earlier build tied the upper bound to `PAGE_ASPECT × height` to keep the preview at full page height; in a tall split that collapsed the band to a single value and froze the editor ↔ preview divider. The preview already fits both axes, so the reservation was unnecessary and was removed.)
  - `chatHPadding` — reclaims the chat scroll area's side padding (16px → 6px) as the column narrows from 400px to its 380px floor, so the response bubble keeps its initial content width and text never wraps tighter than on first render.
  - `messageGap` — interpolates the chat's inter-message spacing from 8px (wide, ≥620px) to 15px (narrow, ≤360px).
- **`ResizeDivider.tsx`.** Reusable vertical grab handle that owns the document-level mousedown→mousemove→mouseup drag lifecycle (col-resize cursor, text-selection suppressed during drag, accent-lit on hover/active), with double-click-to-reset. Panes only supply the geometry via `onDrag(clientX)`.
- **`JobWorkspace.tsx`.** Chat-column width becomes user-draggable; `chatWidth` is `null` until the user drags (preserving the existing automatic sizing), then a persisted fixed px. A `ResizeObserver` re-clamps a stored width when the window resizes or the layout switches to Split. The *undragged* single-pane width is capped at `min(calc(50% + 200px), calc(100% − MIN_PREVIEW_ONLY_WIDTH))` so the default (not just dragged) Preview keeps the resume above its 900px floor — previously the `calc(50% + 200px)` default squeezed the preview below the floor on narrower windows. **Clicking into Split condenses the chat** to its 760px floor (persisted, so it sticks) so the freed width flows into the resume area — paired with the editor auto-expand below, the source pane visibly slides out to fill the gap. Width transition is disabled mid-drag; the redundant `chatCol` right border is dropped in favor of the divider.
- **`ResumeSplit.tsx`.** Editor pane's split share (`editorFrac`, default 0.5, persisted) is draggable via a divider rendered only in split view. The split width is observed and the *applied* fraction is re-fitted to the live pane, so widening the chat retracts the editor instead of dropping the preview below its floor; the user's stored preference is restored when there's room again. **Entering Split expands the editor** to `MAX_EDITOR_FRACTION`, which `clampEditorFraction` pins to the preview's 900px floor and hands the rest to the source — so the source "slides out to fill the gap" the condensing chat opens up. It fires only on entry, so the editor↔preview divider stays freely draggable afterward. The source textarea stays no-wrap, so widening the editor reveals full `.tex` lines that were previously clipped.
  - **Divider-freeze fix (the headline #90 follow-up bug).** The split's `ResizeObserver` was attached in an empty-deps mount effect, but the split div only renders *after* the tex loads (the component early-returns a "Loading…" placeholder first), so the effect ran while the node was absent and never re-bound — `splitWidth` stayed `0`, which collapsed the fraction band to a single value and froze the editor↔preview divider entirely. The observer is now attached via a **callback ref**, which fires whenever the node actually mounts, so `splitWidth` is always measured. Verified end-to-end by driving the real app: the divider now sweeps the editor from 0.29 → 0.70 of the split (280px → 673px) instead of being pinned at the 0.2 minimum.
- **`PdfPreview.tsx`.** Fits each page to the pane on **both** axes (`min(fit-width, fit-height)`) so a page stays fully visible without scrolling at any pane size; height-constrained pages are centered, and the trailing page's bottom margin is dropped so a one-page resume shows no scroll sliver.
- **`ChatPanel.tsx`.** Self-measures its width and applies the `messageGap` spacing and `chatHPadding` side padding.

### Deviations from spec
- Preferences persist globally (per-browser) rather than per-job — a consistent workspace layout is the more intuitive default.
- Only horizontal (width) resizing is implemented, matching the issue text ("resize the width"); pane heights are unchanged.
- The preview is kept fully visible by auto-scaling to fit the pane (it shrinks when the pane is narrow) rather than by reserving a minimum width for full page height — the latter approach froze the split divider and is what this revision replaced.

---

## Issue 95 — Institution canonicalization (ROR) + degree-distinct education dedup
**Status:** complete | **Tests:** 592 pass (14 new)

Fixes the two dedup gaps the previous entry explicitly deferred: the same UCSD bachelor's ingested as `University of California, San Diego` (resume) and `UC San Diego` (LinkedIn) survived as two rows, because fuzzy string matching can't bridge the acronym `UC` → `University of California`. Institution names are now resolved to a stable canonical key via ROR (the Research Organization Registry) affiliation matcher and deduped on that key, while degree matching is tightened so two genuinely different degrees at one school stay separate.

### What shipped
- **Canonical resolver (`institution.py`).** `canonicalize_institution(name)` returns a ROR id for a confident affiliation match (so all of `UC San Diego` / `UCSD` / `University of California, San Diego` share one key) and the normalized name otherwise. The lookup is paid **once per distinct name**: memoized in-process and persisted in the new `InstitutionCanonical` cache table, so re-ingests and the O(n²) self-heal comparisons never re-hit the network. Transient network failures fall back to the normalized name **without** caching so a later ingest retries; `ROR_LOOKUP_ENABLED=0` forces the offline normalized-matching path.
- **Institution matching (`agents/parser.py`).** New `_institutions_match` (canonical-key equality, else the prior fuzzy `_names_match`) now backs both `_education_match` and `_experiences_match`. Academic employers (e.g. the Financial Assistant role at UCSD) dedup across name forms; non-academic companies ROR can't resolve fall back to the unchanged fuzzy behavior — no regression.
- **Degree distinctness.** Same-level degrees now merge only when they name the same field: `_degrees_compatible` accepts fuzzy/containment matches and acronyms (`CS` == `Computer Science`) but keeps distinct majors (B.S. Math vs B.S. Physics) and distinct same-level degrees (MBA vs M.S.) apart. A blank/unknown degree on either side still folds into the fuller one and backfills its blanks (the literal #95 LinkedIn row). Existing B.S./M.S.-by-level separation is preserved.
- **Model.** New `InstitutionCanonical` table (`raw_norm` PK → `canonical_key`, `display_name`); a new nullable table, so it self-creates on existing SQLite/Postgres DBs with no migration.

### Deviations from spec
- ROR canonicalization is applied to experience employers too (not just education) via the shared matcher, since it's the same one-line change and dedups academic employers; regular companies are unaffected.
- The issue also reported a *missing* LinkedIn experience (Financial Assistant). This change fixes the name-variant **duplication** class, but a genuinely absent role most likely reflects a Bright Data record shape (roles nested under a company's `positions`) rather than a dedup bug — left as a follow-up pending inspection of the raw scrape, since it can't be reproduced without it.

---

## Issue-level — Achievements ingestion, cross-source dedup, and tailoring-placed rendering
**Status:** complete | **Tests:** 578 pass (9 new)

Adds an achievements/honors/awards section as a first-class knowledge-graph entity, mirroring the experience ingestion path. Achievements are ingested from resume text and LinkedIn (`honors_and_awards`), fuzzy-deduped across sources, scoped per-user, and rendered into tailored resumes. Per user direction: content is kept verbatim (keep-all — never LLM-rewritten, filtered, or fabricated), and the tailoring pipeline only decides *where* the section is placed, defaulting to the section's position in the ingested resume when the JD gives no strong signal.

### What shipped
- **Model.** New `Achievement` table (`title`, `description`, `issuer`, `date`) with an indexed per-user FK (the #73 isolation lesson) and a `User.achievement_entries` relationship. New nullable table → self-creates on existing SQLite DBs.
- **Ingestion + cross-source dedup (`agents/parser.py`).** `_extract_achievements` (resume LLM extraction, skipped for GitHub), `_save_achievements` (dedup + enrich-blanks), `_heal_achievements` (wired into the `parse_and_save` self-heal block), and a shared `_achievements_match` (title fuzzy-match via `_names_match`, issuer as an enricher/tiebreak) used at both save and heal time. `_save_linkedin_structured` maps Bright Data `honors_and_awards` into `Achievement` rows, folding a LinkedIn entry into its resume line instead of duplicating.
- **Tailoring + placement.** `achievements` added to `REORDERABLE_SECTIONS`; DB achievements are copied verbatim into `tailored_content["achievements"]` before ranking. `_ranked_section_order` now seeds its tie-break from the user's ingested `section_order` (so a low-JD-signal section lands where the resume had it) and only includes achievements when the user has some. `SECTION_KEYWORDS` recognizes achievements/awards/honors headings so ingestion captures the section's label and position. `ATSScoringEngine.flatten_section_text` scores the new section.
- **Rendering.** `_build_tex_achievements` (bulleted list with `%% ART-SECTION: achievements` marker for drag-reorder parity), plus the docx and markdown branches. Section omitted entirely when the user has no rows — never fabricated (mirrors education).
- **Surface.** `services.get_achievements` + `GET /api/profile/achievements`; frontend `AchievementRow` type, `getAchievements()`, and an Achievements tab in `DataExplorer`.

### Deviations from spec
- The JD-relevance filter discussed in planning was intentionally deferred: v1 is keep-all, matching the Education model, so no achievement is ever dropped at tailor time.
- Achievements pass through `tailored_content` (like experience/projects) rather than being read from the DB by the formatter (like education), because a reorderable, JD-scored section needs scannable text; the content is still copied verbatim, never rewritten.

---

## Issue-level — Ingestion dedup parity across experiences, projects, and education
**Status:** complete | **Tests:** 569 pass (8 new)

Follow-up to #72/#73 fixing duplicate/malformed ingested rows that survived earlier hygiene work: an undergrad education entry ingested twice, a junk `Unknown Position @ IDXExchange / ?` experience alongside the real `Data Science Intern @ IDX Exchange`, and duplicate projects across GitHub/resume/LinkedIn. Root cause was uneven dedup: the placeholder-title merge rule lived only in the LinkedIn experience save path (not in `_save_experiences` or `_heal_experiences`), projects deduped on name only (ignoring a shared repo URL), and education used brittle exact `institution+degree` matching with no self-healer.

### What shipped
- **Shared matchers, used at both save time and heal time** so the two can no longer drift apart: `_experiences_match` (company fuzzy-match AND (title match OR either title a placeholder)), `_projects_match` (shared `repo_url` OR fuzzy name), and `_education_match` (institution fuzzy-match AND same degree *level*). Placeholder detection (`_is_placeholder_name`) and degree-level extraction (`_degree_level` → bachelor/master/phd/associate) are centralized.
- **Experiences.** `_save_experiences`, the LinkedIn save path, and `_heal_experiences` all route through `_experiences_match`; the real title is promoted over a placeholder on merge, and `_exp_row_richness` ranks a real title first so the good row always survives. `?` added to `_PLACEHOLDER_DATE_TOKENS`.
- **Projects.** All three paths (resume/GitHub, LinkedIn, `_heal_projects`) match on `repo_url` first, merging a GitHub-ingested repo with its resume line even when names diverge past the fuzzy threshold.
- **Education.** `_save_education` rewritten to fuzzy-dedup-and-enrich; new `_heal_education` wired into the self-heal block. Degree-level matching merges `BS, CS` with `B.S. Computer Science` while keeping an `M.S.` distinct from a `B.S.` at the same school (the double-undergrad fix). The LinkedIn education path no longer drops a second degree at a known institution.

### Deviations from spec
- Institution abbreviations that are not spacing/containment variants (e.g. `UC San Diego` vs `University of California, San Diego`) are still not auto-merged — that acronym case is left to manual edit/delete rather than a speculative heuristic.
- Two genuinely different degrees at the same institution sharing a level (e.g. two distinct master's programs) would merge; treated as an acceptable trade-off against the M.S./B.S. collapse it prevents.

---

## Issue 72 — Project & experience tailoring: fewer, better-described, truthful
**Status:** complete | **Tests:** 561 pass (26 new)

Reported set of tailoring-quality defects on the experience/project sections: too many (and malformed) experiences, malformed dates, dropped bullets leaving blank space, oversimplified/over-many projects, "rewrite" instead of "revise", and blindly-attached ATS keywords. Root causes were verified against real rows in the local DB (a 0-bullet `IDXExchange` near-duplicate of `IDX Exchange`, a `Not specified -- Present` date rendered into shipped resumes). Delivered as three stacked PRs.

### What shipped
- **Deterministic experience/project guards + selection tuning (PR1).** Tailor-time experience filter drops content-empty stubs, fuzzy-dedupes near-duplicate rows (keeping the richest), and coerces placeholder dates to None; dates and canonical title/company are re-attached from the DB after generation so the LLM can no longer author/malform them; experiences the model silently drops are restored (bounded by how many it omitted, so a rename is not duplicated); the per-experience bullet floor rose 1 → 2. Project selection capped 5 → 3 with a new recency component so a strong active project is not displaced by a weaker-but-relevant stale one. The formatter skips the itemize for zero-bullet entries (was emitting empty `\resumeItemListStart/End`), and the one-page trim ladder inverted: shave bullets only to a floor of 2, then drop the entire weakest project before starving survivors; experiences protected over projects.
- **Revise-not-rewrite contract + contextual keyword placement (PR2).** New `agents/keyword_planner.py` (pure): `score_keywords` ranks missing JD keywords by JD TF × corpus IDF with a skill-graph boost and boilerplate penalty; `assign_keywords` places each keyword on the single experience/project whose own source text supports it (direct hit, else JD-neighborhood overlap), dropping keywords that fit nowhere rather than stapling them onto the wrong item; `evaluate_placement` scores whether each keyword landed in its assigned item. Each item carries per-item `suggested_keywords` into a rewritten prompt that revises source bullets (keep facts/numbers/meaning) instead of rewriting; the evaluate node scores keyword *placement* (not mere presence) plus a deterministic faithfulness check, both feeding per-item retry feedback.
- **Ingestion hygiene (PR3).** Placeholder-date coercion and fuzzy dedup moved into the parser save paths (resume + LinkedIn) so new ingests stop creating the junk; `_heal_experiences`/`_heal_projects` merge pre-existing fuzzy-duplicate rows and coerce dates at the end of `parse_and_save`, cleaning up earlier junk without a from-scratch re-import (no-op on clean data).

### Deviations from spec
- Near-duplicate rows whose company strings differ non-trivially (e.g. `UCSD's Department of Economics` vs `UCSD Department of Economics`) are not merged by the fuzzy matcher; their placeholder dates are still coerced, and the tailor-time filter bounds the impact.
- Keyword faithfulness is a soft steering signal (lenient threshold), not a hard gate — legitimate keyword insertion and tightening lower source overlap by design.
- Self-heal dry-run against the live local `art.db` was blocked by pre-existing dev-DB schema drift (missing `project.demo_url` column), unrelated to this change; heal logic is covered by unit tests on the current schema instead.

---

## Issues 70 & 71 follow-up — Overleaf-style workspace: live compile, drag-on-PDF reorder, chat-centric insights
**Status:** complete | **Tests:** 519 Python pass (5 new) + 59 vitest (10 → 59)

Redesign of the job workspace shipped in #81, in three phases. The Resume/Overview tabs are gone: the workspace is now three always-visible panes — insights + chat (narrow) | `.tex` source | compiled preview — with the editor behaving like Overleaf (auto-save + auto-compile) and reordering done by dragging directly on the rendered PDF.

### What shipped
- **Three-pane layout, no tabs.** `JobWorkspace` renders a fixed-width chat column beside `ResumeSplit` (editor | preview, with a Split/Source/Preview view toggle). The jobs sidebar collapses to a slim rail that expands on hover (pinned open while the create form is in use). Job insights — skills match, what the last tailoring run changed, and the score breakdown — render as assistant briefing bubbles pinned at the top of the job chat (`lib/insightMessages.ts`, derived live from job state) instead of a separate dashboard card. Export links moved into the workspace header. `/api/jobs/{id}` now surfaces `explainability` from `UserJobResult.matched_skills._explainability`, and underscore-prefixed internal keys are filtered out of `matched_skills` (fixing a latent leak of `_explainability` as a skill chip).
- **Job-scoped chat welcome.** Empty job chats open with state-aware guidance from `lib/welcome.ts` (paste-JD → tailor → `"tailor emphasize Python more" (N runs left)` → budget exhausted) instead of the generic landing text; the welcome is rendered, not stored, so it tracks job-state changes live.
- **Live compile + auto-save (Compile/Save buttons removed).** `PdfPreview` renders via `pdfjs-dist` canvases (no iframe → no browser PDF chrome), flicker-free swap, last good render survives failures. `CompileScheduler` (pure, fake-timer-tested) debounces 1.8s trailing-edge, skips unchanged buffers, coalesces in-flight compiles (protecting the 2-slot semaphore), discards stale results, and pauses on 429 until a manual Recompile. Edits auto-save on the same settle with a Saving…/Saved indicator; Discard-edits remains. `COMPILE_DAILY_LIMIT` default 200 → 500.
- **Drag-and-drop reordering on the compiled PDF (ReorderPanel deleted).** `lib/pdfOverlay.ts` maps page-1 text geometry back onto the tex structure (NFKD/alphanumeric normalization, ordered-cursor heading matching against each block's own `\section{...}`, bullet prefix anchoring absorbing wrapped lines) with graceful degradation down to a disabled overlay. `PdfDragOverlay` is hand-rolled pointer drag over transparent bands — sections via a left-edge handle, bullets within their group, accent drop indicator. Drops apply `moveSectionTo`/`moveBulletTo` (new move-to-index primitives replacing the adjacent-swap ones) to the buffer and flush an immediate recompile; drags are enabled only while the preview matches the live buffer.

- **Round-2 fixes from user exploration.** Sections are now draggable by their rendered heading line (full-width grab band; the near-invisible edge strip alone was missed — it stays, made more visible). A dimmed "Updating preview…" veil covers the stale render during the post-drop/post-edit compile round-trip, replacing the too-subtle status text that made drops look like no-ops. Double-clicking a bullet or section on the PDF jumps the source editor to the matching tex line (Overleaf-style sync; auto-switches Preview → Split). The workspace opens on **Preview** — the chat column absorbs the hidden source pane's width and the single visible pane keeps its exact Split-mode size pinned far right (Source-only likewise); switching views slides the chat (0.25s width transition). The JOBS rail label reads horizontally on a slightly wider (3.75rem) rail. One-page enforcement moved to the source: `fit_content_to_one_page` runs at tailor time (`ResumeTailorAgent.tailor`), so the stored content — and therefore the editor `.tex`, live preview, and all exports — fits one page, not just the `format_pdf` path (no-op without a LaTeX engine).
- **Optimistic drag reorder.** A drop now updates the preview instantly instead of waiting the multi-second compile round-trip: a reorder is a pure permutation of horizontal page slices, so `reorderPatch` (pdfOverlay) computes where each band's pixels land — bands repack at content height while each slot's trailing gap stays positional (the page-bottom whitespace never travels with a moved section) — and `PdfPreview` re-composites the page-1 canvas from a snapshot before kicking off the confirming compile. The wait veil skips its dim after an optimistic drop (badge only, over an already-correct preview); typing edits still dim.

### Deviations from spec
- Consecutive drags still wait one compile round-trip (drag re-enables when the confirming render lands); the preview itself updates instantly via the optimistic canvas patch.
- DOCX export still regenerates from tailored JSON and ignores manual `.tex` edits (unchanged from #71, tooltip retained).
- Verified end-to-end with Playwright against a live server + tectonic: welcome, insights, first compile, section drag (order changed in tex and PDF), auto-save (`has_manual_edits` flips), broken-tex error with retained preview, discard-edits restore.

---

## Issues 70 & 71 — Job workspace + manual .tex resume editing
**Status:** complete | **Tests:** 514 pass (28 new Python) + 10 vitest

Two-issue arc shipped as four stacked PRs (#77–#80). #70 rebuilt the Job tab into a per-job workspace (JD at creation, auto analyze+tailor, job-scoped chat driving capped iterative re-tailoring); #71 added a manual `.tex` editor with compile preview, save/export of the edited source, and section/bullet reordering inside that workspace.

### What shipped
- **JD at creation + auto-pipeline (#70).** `POST /api/jobs/` accepts an optional `description`; the sidebar create form gained a JD textarea. Jobs created with a JD route straight to the Job tab and auto-run analyze → tailor with staged progress; JD-less jobs get a paste-JD panel wired to the same chain.
- **Job workspace (#70).** New `JobWorkspace` replaces the JobDetailPanel stepper: job-scoped chat on the left (the top-nav Chat tab is now always the landing chat), resume pane on the right with Resume/Overview tabs, skills chips, score breakdowns, and the retained PDF/LaTeX/DOCX export buttons. The Re-tailor button is removed — the chat drives revision.
- **Capped, instruction-driven re-tailoring (#70).** With an active job, chat `tailor <text>` (plus `re-tailor`/`retailor`) re-runs tailoring with `<text>` as revision instructions, threaded into the generation prompt and persisted on the previously-dead `UserJobResult.revision_notes` column. New lifetime per-job budget: `JobDescription.retailor_count` + `JOB_TAILOR_LIMIT` env (default 5); the router returns 409 at the cap and the budget shows in the workspace header.
- **Persisted manual `.tex` (#71).** `UserJobResult.edited_tex` (+ timestamp) with owner-scoped endpoints: `GET /tex` (seeds from `format_tex` when no edits), `PUT /tex`, `DELETE /tex`, and `POST /preview` compiling the posted buffer behind a `Semaphore(2)` + generous compile quota; compile failures return 422 with the LaTeX log tail. Exports serve `edited_tex` for tex/pdf (DOCX stays JSON-generated, noted in the UI).
- **Editor UI (#71).** `ResumeEditor` in the Resume tab: monospace buffer, Save / manual Compile-preview / Discard, PDF preview in an iframe, error surface. Section and bullet reordering via `%% ART-SECTION` markers emitted by `_build_tex` — pure text-block moves (`lib/texStructure.ts`, vitest-covered) that survive hand-edits and degrade gracefully when markers are removed.
- **Warn-then-discard.** Re-tailoring clears `edited_tex` at the tailor save block; the chat path asks a 1/2 confirmation first, and UI-initiated retries `window.confirm` when edits exist.

### Deviations from spec
- Preview is a manual "Compile preview" button (user's pick) rather than literal real-time rendering — pdflatex on the 512MB VM takes seconds per run.
- One-page auto-fit does not apply to edited-`.tex` exports (trimming operates on the tailored JSON, not raw source); overflow is visible in the preview.
- vitest added as the first frontend test infrastructure to cover the reorder logic.

---

## Issue 74 — GitHub Single Repo Ingestion
**Status:** complete | **Tests:** 485 pass (9 new)

Issue reported single-repo GitHub ingestion "doesn't seem to be working." Root cause: production has no `GITHUB_TOKEN` secret, so any user who hasn't personally connected GitHub OAuth hits GitHub's API fully unauthenticated — capped at 60 requests/hour **shared across the entire deployed app**. One single-repo import cost ~38 API calls (verified by instrumenting `requests.get` against a real repo), unbounded by repo size, because of an 8-request blind dependency-file check and a recursive one-call-per-directory-and-per-file import scan. One or two imports exhausted the whole app's quota for up to an hour, for every user — and the failure was silently mislabeled as "Could not fetch {owner}/{repo}. Check the owner/repo name," actively misdirecting the report.

### What shipped
- **Bounded, single-call file discovery.** `ingestion/github.py::GitHubIngestor` gained `_fetch_tree()`, using GitHub's Git Trees API (`git/trees/HEAD?recursive=1`) to list a repo's whole file tree in one call. `_extract_imports_from_repo` and `_fetch_dependency_files` now consume that tree instead of recursively walking directories (`_scan_directory_for_imports` deleted) — dependency-file checks only fire for filenames confirmed present (down from 8 blind requests every time), and import scanning is capped at `MAX_IMPORT_SCAN_FILES` (15) regardless of repo size. Verified call count for `openai/evals`: 38 → 20, now bounded instead of scaling with repo size.
- **Rate limits surfaced clearly instead of mislabeled.** New `GitHubRateLimitError`, raised from a single centralized `_get()` checkpoint when GitHub responds 403 with `X-RateLimit-Remaining: 0`. Propagates through `fetch_repo()`/`ingest()` (each already had a broad catch-all that was silently swallowing it into `None`/`[]`) to `services.ingest_github_repo()`/`ingest_github()`, which now return an actionable message ("try again in a few minutes, or connect your GitHub account for a much higher limit") instead of the misleading "check the owner/repo name" text — while preserving both functions' documented never-raise contract (`agents/chat.py::run_ingest_github_repo` and the FastAPI router rely on always getting a plain string back).
- **Tests (9 new).** `tests/test_ingestion_github.py` — rate-limit detection (true positive on 403+header, no false positive on plain 403/404), `fetch_repo` propagating the rate-limit instead of swallowing it, tree-based scan making exactly one tree call and staying bounded, dependency-file fetch only firing for tree-confirmed files, and the no-tree fallback still checking all known filenames. `tests/test_services.py` — rate-limit message clarity for both the single-repo and account-wide ingest paths.

### Deviations from spec
- Setting an actual `GITHUB_TOKEN` secret on the deployment (60/hr → 5000/hr) is the highest-leverage fix but requires generating a PAT and setting it in the host's secret store — flagged as a recommended follow-up, not implemented here (infra action, not a code change).

---

## Issue 75 — Unwanted resume link removal
**Status:** complete | **Tests:** 476 pass (10 new)

Tailored resumes were losing links: header contact links (LinkedIn/GitHub/portfolio) were missing from exports, and project repo/demo links were never surfaced at all.

### What shipped
- **Root cause for the header.** The tailoring LLM never touches the header — `_build_tex_header` always renders it fresh from the `User` row. The real gap was upstream: resume ingestion detected *which* contact field types were present in the header (email/linkedin/github/phone/location) but discarded the actual values, so `User.linkedin_url`/`github_username` stayed empty unless manually retyped into the profile form.
- **Header contact backfill.** `ingestion/resume.py::extract_style_profile` now also returns `header.contact_values` (parsed email, LinkedIn URL, GitHub username, phone, and a new generic portfolio/website URL). `services.py::ingest_resume_file` backfills `User.linkedin_url`/`github_username`/`phone`/`location`/`portfolio_url` from it — **only when the field is currently empty**, so re-ingestion never clobbers a manually-curated value. New `User.portfolio_url` field, rendered in both the LaTeX and DOCX headers and exposed via `PATCH /api/profile/`.
- **Inline links preserved in body content.** The resume-parsing and tailoring LLM prompts now explicitly instruct link preservation (`[text](url)` markdown), and `agents/formatter.py::_convert_inline` gained markdown-link → `\href` conversion (previously it only handled `**bold**`/`*italic*` and silently dropped any embedded link).
- **Project repo/demo links auto-embedded (the issue's second ask).** New `Project.demo_url` field. `repo_url`/`demo_url` are no longer dropped before reaching the tailoring pipeline, but — since the LLM rewrite step is unreliable for verbatim field passthrough — they're re-attached deterministically after generation (`ResumeTailorAgent._merge_project_links`, matching the existing `_order_projects_by_selection`/`_enforce_bullet_budgets` guardrail pattern) rather than trusted to the model's JSON output. Rendered as `\href` links in the LaTeX projects section and as plain-text URLs in the DOCX export.
- **Tests (10 new).** Markdown-link → `\href` conversion, header portfolio-link rendering, project repo/demo link rendering (tex + docx) and omission when absent, contact-field backfill on ingest, no-clobber on re-ingest, and `_merge_project_links` passthrough (including the unrecognized-name case).

### Deviations from spec
- None — both asks in the issue (header preservation, auto-embedded project links) are addressed as scoped.

---

## Issue 73 — Data leakage across users
**Status:** complete | **Tests:** 466 pass (25 new)

A user reported their tailored resume showing another user's education (UCSD B.S. Math-Econ / M.S. Data Science). Diagnosis found three distinct isolation defects; all are fixed.

### What shipped
- **Per-user education storage (the reported symptom).** The resume formatter had one user's education *hardcoded* in all three export paths (LaTeX/PDF, DOCX, Markdown) — every user's export got it. New `Education` table (institution, degree, location, dates, GPA) keyed by `user_id`; resume ingestion extracts education via the LLM parser (with dedup on re-ingest), LinkedIn ingestion maps Bright Data's structured `education` records deterministically (merging with resume-ingested rows). The formatter renders the acting user's rows and **omits the section entirely when a user has none** — education is never fabricated. Existing users' education stays empty until they re-ingest a resume.
- **Knowledge graph scoped per user.** `SkillGraphBuilder` selected *all users'* skills/projects/experiences into one graph, contaminating the skill matcher's indirect-match check, the Data Explorer graph view, and the chat graph tool. It now requires a `user_id` and filters every query (skills joined through `UserSkill`); each build sees only that user's rows.
- **Request-scoped user binding replaces the global pointer file.** Web routers used to write the authenticated user's ID into the server-global `~/.art/active_profile_id` file that ~25 downstream `get_active_profile()` call sites re-read — concurrent users raced over one slot, cross-contaminating reads *and* ingestion writes. `set_request_user()` (a `ContextVar`) now binds the acting user per request context; bindings are set in async endpoint bodies (not the sync `get_current_user` dependency, which FastAPI runs in a threadpool where ContextVar writes don't propagate back) and flow through `asyncio.to_thread` into agent/service code. The pointer file survives purely as the single-user CLI fallback.
- **Chat history isolated.** Landing-context chat (`job_id=None`) was one shared conversation across *all* users, and `GET /api/chat/{job_id}/history` never checked job ownership. `ChatMessage` rows are now stamped with `user_id` (nullable column + migration; legacy NULL rows stay hidden from authenticated users), landing history is filtered by owner, and job history 403s for non-owners / 404s for unknown jobs.
- **Education tab in the Data Explorer.** `GET /api/profile/education` + an Education tab (between Experiences and Projects) so users can visually confirm their education ingested correctly — institution, degree, location, GPA, and dates rendered verbatim (dates are stored as free-form strings exactly as the resume wrote them: "June 2025", "Expected June 2027", or bare "2027"). The empty state notes that tailored resumes omit the education section until ingestion.
- **Tests (25 new).** `tests/test_education.py` — education rendering in all three formats, omission when absent, cross-user render isolation, parser save/dedup, LinkedIn mapping, service shape, endpoint caller-scoping. `tests/test_user_isolation.py` — graph node/edge scoping, graph-summary scoping, ContextVar-beats-pointer-file regression, landing-history isolation, legacy-row hiding, router ownership checks (403/404).
- **Live API verification.** Full register→history→graph→job→ownership smoke test against a running server on a scratch DB: 13/13 checks pass.

### Deviations from spec
- The issue hypothesized non-isolated knowledge graphs; that was real, but the reported symptom itself was hardcoded education in the formatter, and a third defect (shared landing chat + unchecked job-history ownership) was found and fixed in the same arc.
- Physically separate per-user graph stores were considered and rejected: the graph is an ephemeral in-memory projection of already-`user_id`-keyed relational rows, so scoping the builder's queries achieves full isolation without new infrastructure.

---

## Issue 68 — UI overhaul: OAuth-first GitHub ingest, profile menu, progress indicators
**Status:** complete | **Tests:** 441 pass (24 new)

The web UI still reflected pre-OAuth design: the GitHub ingest tab asked for a raw username even when the account was OAuth-connected, Profile sat as a left nav tab instead of under the user's name, long-running actions gave only a static text label, and ingest results showed the server temp filename (`tmpXXXX.pdf`) instead of the uploaded one.

### What shipped
- **OAuth-first GitHub ingest.** The GitHub tab now reads `/api/auth/github/status`: not connected → a "Connect GitHub" button that starts the OAuth flow; connected → one-click "Import My Repositories" (no username field). The OAuth callback redirect (`/?github_connected=1`) lands the user back on the GitHub ingest tab. A single public `owner/repo` import remains as a secondary option, and the raw-username form survives only as the fallback when OAuth isn't configured (local dev).
- **`POST /api/ingest/github` username optional.** Defaults to the connected account's `github_username`; 400 when neither a body username nor a connection exists.
- **Profile under the top-right user menu.** The header name is now a dropdown (Profile / Sign out); Profile removed from the left nav tabs. GitHub connection management stays on the Profile panel.
- **Progress indicators.** New `ProgressBar` component (indeterminate sweep + elapsed-time counter) shown during resume/GitHub/LinkedIn ingest, job analyze, and tailor; the background LinkedIn import status on the Profile panel uses it too.
- **Original filename in ingest results.** `ingest_router` passes `file.filename` through to `services.ingest_resume_file` / `ingest_linkedin_pdf` as a display name.
- **Parser hardening against malformed LLM output.** LinkedIn URL import crashed with `'str' object has no attribute 'get'` when an extraction model returned a wrapper object (`{"skills": [...]}`) or bare strings instead of a list of objects. `ResumeParserAgent._coerce_records` now normalizes all four extraction chains' output to a list of dicts, and `postprocess_skills` tolerates bare-string and non-dict items.
- **Deterministic LinkedIn entity mapping.** Bright Data's structured record (`projects`, `experience`) is now saved directly to Project/Experience rows instead of a lossy text → LLM → structure round trip — an audit showed the flattener discarded the four richest sections of a real profile (4,331 chars reduced to 336). Merge-aware upserts enrich entities already ingested from other sources (normalized/containment name matching, fill-missing-fields, `[LinkedIn]`-tagged description appends, idempotent on re-ingest) rather than duplicating them. LLM extraction still runs for skills only.
- **Lossless LinkedIn flattener.** `_brightdata_to_text` now includes projects, courses, honors and awards, and bio links, so skill extraction sees the whole profile. Verified by replaying a real scrape: 24 skills extracted vs 0 before.
- **Tests (24 new).** `tests/test_ingest_router.py` — username defaulting, explicit-username precedence, 400 without any username, filename pass-through for resume and LinkedIn PDF uploads. `tests/test_parser_coercion.py` — wrapper-object unwrapping, bare-string mapping, garbage rejection, `postprocess_skills` guards. `tests/test_linkedin_ingest.py` — lossless flattening, verbatim project saves, merge-with-existing (name and company variants, placeholder titles), idempotent description appends, LLM-bypass proof for structured entities.

### Deviations from spec
- The parser hardening and deterministic LinkedIn mapping were not in the #68 scope — they fix a production crash and a data-loss defect the user hit while testing LinkedIn URL ingestion during this arc. An Education table and raw-scrape persistence were deliberately deferred to a follow-up issue (schema changes).

---

## Issue 67 — web ingestion OOM and jobs API 404 in production
**Status:** complete | **Tests:** 416 pass (8 new)

Resume ingestion on the web OOM-killed the 512 MB production VM: `requirements-core.txt` shipped docling, whose converter pulls in PyTorch and loads layout models at parse time. Separately, `GET /api/jobs` 404'd because the SPA catch-all route (ES256 outage fix) fully matches slash-less `/api/*` paths before FastAPI's automatic trailing-slash redirect can fire, and `jobs.ts` fetched the slash-less form — so the job list never loaded.

### What shipped
- **docling demoted to a full-only dependency.** Removed from `requirements-core.txt` (the Docker image), added to `requirements-full.txt`; `pypdf` added to core as the lightweight fallback.
- **`ingestion/document_text.py`** — shared extraction helper: docling when installed, otherwise pypdf (PDF) / python-docx (DOCX) / plain read. Used by `ResumeIngestor` (with a new line-based section segmentation fallback) and `LinkedInIngestor.ingest_pdf`.
- **`/api/jobs` slash tolerance.** List/create routes answer with and without the trailing slash (`include_in_schema=False` aliases); `jobs.ts` now follows the repo's trailing-slash convention.
- **Tests (8 new).** `tests/test_ingest_fallback.py` — docling-free resume/LinkedIn ingestion, pypdf path, jobs-route slash regression; `tests/test_deps_split.py` — docling excluded from core, pypdf present in core, docling present in full.

### Deviations from spec
- None. The ES256 outage fixes referenced in the issue were committed separately (`3585ecd`).

---

## Issue 51 (Phase 1) & issues 15/26/27/54/58 reconciliation — tailoring efficacy benchmark, analyze fix, allocation & redundancy improvements
**Status:** complete | **Tests:** 387 pass (25 new)

Built the standing apparatus for measuring and improving tailoring quality, and fixed the three reported failure modes (experience text not tracking relevance, unselective skills, term over-repetition) plus a production-grade bug the benchmark's first run exposed.

### What shipped
- **JD dataset + scraper.** `scripts/scrape_job_descriptions.py` pulls real postings from public Greenhouse/Lever board APIs (role-filtered, HTML-stripped, deduped); 8 SWE/ML postings checked in under `eval/jd_dataset/` with a documented schema, plus a synthetic candidate profile (`eval/profiles/benchmark_profile.md`).
- **Benchmark harness (#51 Phase 1).** `eval/tailoring_benchmark.py` replays the exact user flow **through the web API** (register → login → upload resume → create job → paste JD → Analyze → Tailor → Export) via FastAPI `TestClient` on an isolated temp DB/env — production data, `~/.art`, and the deployed site are untouched. `--stub` runs fully offline behind a deterministic fake LLM + hash embedder; default mode uses real LLMs. Emits per-task + aggregate JSON, a flat CSV, and per-task `.tex`/`.json` renders under `eval/results/` (gitignored).
- **Quality metrics.** `eval/metrics.py`: ATS baseline→tailored composite/per-component deltas; experience-allocation balance (Spearman between per-experience JD relevance and bullet-word share); skills selectivity/organization (rendered count vs cap bounds, matched-skill recall, selection ratio, category order); redundancy (boundary-aware term counts — "sql" never matches inside "mysql" — over-repetition rate, bullet type-token ratio).
- **LLM-as-judge (carries #27's aim).** `eval/llm_judge.py` + `--judge`: 1–5 scores with rationales for relevance_balance / redundancy / faithfulness; malformed judge output rejected; real call integration-gated.
- **Notebook.** `eval/tailoring_benchmark.ipynb` drives the benchmark and visualizes results: aggregate charts, per-task text-allocation drill-down, tailored-resume viewer over the run's renders, cross-run trend view.
- **fix(analyze): web jobs got zero skills.** `POST /api/jobs/{id}/analyze` passed `job_id`, but `JobAnalyzerAgent.analyze_and_save` ignored it and attached all extracted `JobSkill` rows to a new orphan `JobDescription` — so web-created jobs matched with **0 skills** and tailoring ran without skill signal. Existing jobs are now analyzed in place (skills replaced idempotently, user's title/company preserved, cached JD embedding invalidated). Found by the benchmark's first run.
- **feat(tailor): relevance-based bullet budgets.** Experiences are JD-relevance ranked with per-experience `bullet_budget` (up to `TAILOR_MAX_EXP_BULLETS` for the most relevant, down to `TAILOR_MIN_EXP_BULLETS`), injected into the prompt **and enforced deterministically** post-generation.
- **feat(tailor): anti-redundancy.** Prompt rule against term stuffing; evaluator flags terms mentioned more than `TAILOR_MAX_TERM_MENTIONS` times (boundary-aware) into retry feedback.
- **Tests (25 new).** `tests/test_tailoring_benchmark.py` (metrics units, stub determinism, dataset sanity, subprocess end-to-end smoke, judge parsing/rejection + integration-gated real call) and `tests/test_prd04.py` additions (analyzer job_id regression ×3, budgets/enforcement/redundancy ×6).
- **Baseline measurement (real-LLM run, 8/8 tasks):** composite 33.0 → 75.4 (mean delta **+42.4**); allocation correlation mean 0.55/median 0.6; over-repeated terms ≤ 1 per task (mean 0.25); matched-skill recall mean 0.90.

### Issue reconciliation
- **#54, #58** — already shipped on `main`; closed and moved to Done (they were missing from the project board entirely; added).
- **#26** — memory-eval framework already shipped; closed.
- **#27** — closed as superseded: the LLM-as-judge apparatus landed in the tailoring benchmark instead of the chat-memory eval (re-file if chat-memory coherence becomes active).
- **#15** — closed without the online implementation: its measurable core (a dataset of resume/JD/output/score tuples) now exists offline via the benchmark artifacts; SaaS-scale telemetry + consent/GDPR scope conflicted with the offline-first direction and depended on unbuilt #14.
- **#51** — Phase 1 delivered by this entry; remains open (In progress) for Phase 2 score-driven tuning.

### Deviations from spec
- #51 proposed `eval/ats_tasks/` + `eval/ats_efficacy.py`; shipped as `eval/jd_dataset/` + `eval/tailoring_benchmark.py` with a strictly larger metric surface (allocation/skills/redundancy/judge on top of the composite deltas).
- The end-to-end pytest runs the harness in a **subprocess** with its own temp DB rather than on the in-process `isolated_engine` fixture — too many modules bind `engine` at import time for safe in-process rebinding; the isolation goal is met either way.
- Known tuning observation from the first real run: the skills cap saturates at `MAX_SKILLS` (18) on every task — drop-off rule never fires for a 37-skill profile. Left for #51 Phase 2 calibration.

---

## Issues 61 & 62 — Supabase-only auth migration + password recovery
**Status:** complete | **Tests:** 362 pass (18 new)

Made Supabase the single source of truth for authentication in production and added a self-service password-recovery flow on top of it. Previously `/login` verified a local PBKDF2 hash *first* and only then minted a Supabase JWT, so a Supabase-only password reset would have locked users out — the dual credential stores had to stay in sync. Login is now by **email + password**, production authenticates against Supabase alone (**fail-closed**: the local password/cookie path can never run when Supabase is configured), and password reset "just works" because there is no local hash to reconcile. An offline local fallback is retained for dev/tests only.

### What shipped
- **Auth mode gate.** `database/auth.py::supabase_configured()` — single source of truth for the mode (env vars set AND `supabase` importable). `web/auth.py::get_current_user` is fail-closed: Supabase JWT only when configured, local signed cookie only when not.
- **Email login + Supabase-owned credential.** `web/routers/auth_router.py` — `/login` takes `email`+`password` with a generic "Invalid email or password" error (no enumeration); in Supabase mode `/register` stores **no** local `password_hash` (column kept for schema back-compat) and handles the email-confirmation-pending case. Login/reset backfill `supabase_uid` so pre-migration accounts resolve from the JWT `sub`. New `database/user_utils.py` helpers: `authenticate_local_email`, `set_supabase_uid`, `set_local_password`.
- **Password recovery.** New Supabase helpers `supabase_send_password_reset` and `supabase_update_password` (recovery-session `set_session`→`update_user`, least-privilege — no service-role key). New endpoints: `GET /api/auth/capabilities`, `POST /api/auth/forgot-password` (generic 200, 503 in local fallback), `POST /api/auth/reset-password` (min 8-char strength enforced before Supabase is called; invalid/expired token → 400).
- **Frontend.** `LoginPage` switched to email + conditional "Forgot password?" link (gated on `capabilities`); new `ForgotPasswordPage` and `ResetPasswordPage` (reads recovery tokens from the URL fragment, strips them from history, confirm + strength validation); routes wired in `App.tsx`; `api/auth.ts` gains `getAuthCapabilities`/`forgotPassword`/`resetPassword` and email login.
- **Email-confirmation redirect (#62).** `supabase_sign_up` now accepts `email_redirect_to`, and `/register` passes `<APP_BASE_URL>/login` so the sign-up confirmation link lands on the login page instead of the Supabase Site URL / app root. Generalized the router's `_app_url(request, path)` helper (shared with the reset redirect).
- **Tests.** `tests/test_password_reset.py` (18) — capabilities, dev-fallback email login, Supabase-mode login + uid backfill, no-local-hash register, sign-up confirmation `/login` redirect, no-enumeration forgot-password, reset success/invalid-token/weak-password, and local-fallback 503s.
- **Docs.** `web/CLAUDE.md` auth-flow section rewritten; `.env.example` documents the all-Supabase behavior + `APP_BASE_URL` (reset-link redirect origin, must be allowlisted in Supabase → Auth → URL Configuration).

### Deviations from spec
- Scope expanded beyond the original "add password recovery" issue to a full Supabase-only auth migration (agreed with the maintainer), because reset could not be made safe while login depended on a separately-synced local hash.
- Local dev/tests deliberately retain the offline password path; it is unreachable in production (Supabase always configured there), so it is not a production attack surface.
- Reset uses the recovery session (`set_session` + `update_user`) rather than the service-role admin API, keeping the flow least-privilege. `SUPABASE_SERVICE_ROLE_KEY` remains unused by this feature.

---

## Repo hygiene — public-readiness cleanup + service-layer extraction
**Status:** complete | **Tests:** 344 pass (0 new; net −37 from removing the TUI test suite)

Prepared the repository to be made public: removed personal data and the
deprecated Textual TUI, extracted the shared service layer to a top-level
module, and added a proper README. No product behavior changed.

### What shipped
- **Service-layer extraction.** `tui/services.py` → `services.py` (self-contained business logic used by the web app, agents, and CLI); rewrote 45 import sites. The `tui.services` string key in the eval stub map was updated to `services`.
- **TUI retirement.** Deleted `tui/` (app, screens, widgets), `tests/test_tui.py`, the textual-web `tui`/`serve` CLI commands + `cmd_serve`, `launch.bat`, `launch.ps1`, `textual-web.toml`, and `scripts/automation_smoke_check.py`. Dropped `textual`/`plotext` from `requirements*.txt`. Web-deploy structural checks preserved as `tests/test_web_deploy.py`.
- **Personal data + artifact removal.** Removed personal resume/cover-letter/parsed-resume files, `notebooks/`, one-off `debug_*`/`test_*` scripts (kept operational scripts), the empty `agentic_resume.db`, `test.txt`, `knowledge_graph.png`, and tracked `__pycache__/*.pyc`. Made `tests/test_integration.py` self-contained via synthetic `tests/fixtures/` (`sample_resume.md`, `sample_job.txt`). Expanded `.gitignore` for personal materials and local DBs.
- **Docs.** Added `README.md` (overview, architecture, quickstart, deploy). Rewrote `docker-compose.yml` and `INSTALL.md` to serve the web app (uvicorn) instead of the TUI. Deleted the obsolete `STARTUP.md` and `.github/instructions/tui.instructions.md`. Updated `CLAUDE.md`, `agents/CLAUDE.md`, `.github/copilot-instructions.md`, and `supabase/README.md` to drop TUI references.

### Deviations from spec
- Historical `CHANGELOG.md` entries, `docs/prd/`, and `docs/ROADMAP.md` were left untouched — they accurately describe past work (including the then-existing `tui/services.py`) and are treated as historical records.
- The PII purge from git *history* (via `git filter-repo`) is performed as a separate final step outside this commit, since it rewrites all commits.

---

## Issues 54 & 58 — Skills Section Tailoring + Best-of-N Attempt Selection
**Status:** complete | **Tests:** 381 pass (38 new)

Reworked how the Technical Skills section is built and how the tailoring loop selects its final output. Previously the skills section rendered the full skill list under static alphabetical categories (never consulting the job description), and the generate→evaluate loop shipped whatever the last retry produced. Now skills are JD-relevance ranked, capped, and role-aware ordered with a semantic signal and persistent pinned "core" skills, and the loop ships the best-scoring attempt rather than the last. Delivered as #54 Phases 1–4 plus the #58 follow-up.

### What shipped
- **Phase 1 (#55) — JD-relevance ranking, cap, role-aware ordering.** `agents/skill_scorer.py` (**created**) — pure-function scorer (`score_skills`, `select_skills`, `rank_and_select_skills`) blending TF-IDF (with IDF over the JD corpus), JD weight, match confidence, proficiency, and evidence; dynamic cap via drop-off + min/max bounds. `agents/tailor.py` — `_rank_skills()` persists `tailored_content["skills_ranked"]`. `agents/formatter.py` — renders the JD-ranked list in relevance order (falls back to the full DB list). `agents/ats_scorer.py` — skills flattening prefers `skills_ranked`.
- **Phase 2 (#56) — persistent embeddings + semantic component.** `agents/skill_embeddings.py` (**created**) — shared MiniLM cache (`ensure_skill_embeddings`, `load_skill_vectors`, `ensure_job_embedding`), degrades gracefully when the model is unavailable. `database/models.py` + `database/db.py` — `Skill.embedding/embedding_model` and `JobDescription.embedding/embedding_model` columns + backward-compatible ALTER migrations. Reingest hooks recompute embeddings (`agents/parser.py`, `tui/services.py`); `agents/chat.py` invalidates the cached JD embedding on job re-analysis. `scripts/backfill_skill_embeddings.py` (**created**).
- **Phase 3 (#57) — pinned core skills.** `database/models.py` — `UserSkill.is_core` (+ migration). Pinned skills always render and seed a relevance floor. Surfaced end-to-end: `web/routers/profile_router.py` (`POST /api/profile/skills/core`), web frontend ★ pin toggle in the Skills tab, `cli.py` `pin-skill` command, and `tui/services.py` `set_skill_core`.
- **Phase 4 (#54) — tunable weights + offline tuning harness.** `agents/skill_scorer.py` — all weights and cap bounds env-overridable (`SKILL_W_*`, `SKILL_MIN/MAX`, etc.) with unchanged defaults, plus per-call `weights`/`bounds` overrides and a `selection_recall` metric. `eval/skill_selection_eval.py` (**created**) — LLM-free harness over checked-in fixtures comparing weight presets by recall + rendered count.
- **Issue 58 — best-of-N attempt selection.** `agents/tailor.py` — the generate→evaluate loop tracks the highest-scoring attempt by algorithmic composite and ships the argmax (falling back to the last content only when no attempt scored), runs the full `MAX_RETRIES` budget by default, and early-exits only above a high "great" bar. Budget and great-bar thresholds are env-overridable (`TAILOR_MAX_RETRIES`, `TAILOR_GREAT_SKILL_COVERAGE`, `TAILOR_GREAT_KW_COVERAGE`).
- **Tests** — `tests/test_skill_scorer.py` (12), `tests/test_skill_embeddings.py` (8), `tests/test_skill_pinning.py` (7, incl. web `TestClient`), `tests/test_skill_tuning.py` (7), and 4 best-of-N tests in `tests/test_prd04.py`.

### Deviations from spec
- Kept the local MiniLM embedding model rather than adding a provider-swappable embedding config — semantic scoring degrades to lexical + metadata signals when the model is unavailable.
- Final calibration of the skill-scoring weights and the #58 great-bar thresholds is deferred to the #51 ATS efficacy benchmark; this arc ships the tunable mechanism with sensible (un-calibrated) defaults.

---

## Issue 13 — LinkedIn Ingestion via Bright Data
**Status:** complete | **Tests:** 332 pass (10 new)

Replaced the Playwright LinkedIn scraper with Bright Data's Web Scraper API and surfaced LinkedIn ingestion in the web app for the first time. The scrape auto-triggers when a user sets/changes their LinkedIn URL (the "initialize/update knowledge graph" moment) and runs in the background; PDF upload remains as a fallback.

### What shipped
- `ingestion/linkedin.py` — new `ingest_brightdata()` (trigger → poll `/progress` → download `/snapshot`) + `_brightdata_to_text()` flattener; removed `ingest_web` and the Playwright/bs4 scraping path. `ingest_pdf` fallback retained.
- `config.py` — `BRIGHTDATA_API_KEY` (platform-wide) and `BRIGHTDATA_LINKEDIN_DATASET_ID` (default `gd_l1viktl72bvl7bjuj0`).
- `database/models.py` + `database/db.py` — `User.linkedin_ingested_url/linkedin_ingest_status/linkedin_ingest_error/linkedin_ingested_at` columns + backward-compatible ALTER migrations.
- `tui/services.py` — `ingest_linkedin(url, user_id)` records the importing/done/failed lifecycle; never raises.
- `web/routers/ingest_router.py` — `POST /api/ingest/linkedin` and `/linkedin/pdf`.
- `web/routers/profile_router.py` — `PATCH /api/profile` schedules a background ingest when the URL changes; GET exposes ingest status.
- Frontend — LinkedIn tab in `IngestPanel` (URL + PDF fallback) and a live import-status indicator in `ProfilePanel`.
- `cli.py` — `ingest-linkedin` rewired to Bright Data.
- Deps — removed `playwright` from `requirements*.txt`, lockfile, and generator; repointed `test_deps_split.py` heavyweight checks to `sentence-transformers`.

### Deviations from spec
- Issue framed an optional per-user key with a "users without API access" fallback; shipped a platform-wide key (hosted SaaS) with PDF upload as the fallback when the key is unset.

---
## Pre-web era (PRD 01–10, 2026) — archived

Entries for PRD 01, 02, 02.5, 03, 05, and 10, plus the "Chat Routing Overhaul /
TUI Polish" session, were **removed from this changelog**. They described the
Textual TUI (`tui/app.py`, `tui/screens/`, `tui/services.py`) and its test file
`test_smoke_formal.py` — every module named in them has since been deleted, and
the features they shipped were superseded by the web app.

What survived from that era, and where it lives now:

| Then | Now |
|---|---|
| `tui/services.py` — shared business logic | `services.py` (top-level; see "Repo hygiene" above) |
| `ChatMessage` persistence (PRD 10) | `agents/chat.py` + the context-window work in #5/#6/#24–#27 |
| `~/.art/` app data, `config_validator.py` (PRD 05) | unchanged — still the SQLite-fallback data dir |
| `get_active_profile()` / active-profile pointer (PRD 03) | unchanged — `database/user_utils.py`, now a repo invariant |
| Role-based `get_llm()` (PRD 02) | unchanged — `llm.py` |
| Chat-triggered ingest/tailor fast paths (PRD 02.5) | rewritten as the router-first design in `agents/chat.py` |

The full text is recoverable from git history:
`git log --oneline -- CHANGELOG.md`.
