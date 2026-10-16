# Jev fallbacks, scored on the labelled sets (issue #350)

Jev stays optional (epic #349, decided 2026-10-15): with no `TYPESAFE_API_KEY`, `ART_JEV_MODE=off`, or an API error, every decision point runs on its fallback (`docs/harness.md` § 4). This file is the baseline for that harness. It scores each of the six shipped points' **fallback** on the same labelled set that point's Jev threshold was fitted on, with the same error breakdown as that point's Jev `REPORT.md` (the harmful error first, then misses and counts), and sets Jev's own figures beside it.

- **How it is measured.** `python eval/fallback_benchmark.py write` runs each fallback through the production functions the harness calls (`make_support_checker`, `acceptance.negative_pin_violations`, `make_coverage_checker`, `memory_gate.route`, `library.best_variant`, `library.pick_track`) with `ART_JEV_MODE=off`. No recording is replayed, no key is read and nothing touches the network. `tests/test_fallback_benchmark.py` pins the numbers and fails when any table below is stale.
- **Where Jev's figures come from.** They are read out of each `REPORT.md` by the script, not retyped: `support_labels`, `negative_pin_labels`, `coverage_labels`, `memory_gate_labels` (and its `context/`), `library_labels`. A refit changes them, and the test then fails until this file is regenerated.
- **Nothing is invented.** A fallback that is *absent* (coverage's semantic target) is reported as that: no requirement is credited. The support fallback was *unchecked* when #350 took this baseline (0 of 54 should-block pairs blocked, 19 with #123 beside it) and is the deterministic check since #351; its row reports what that check does, beside #123, on the same pairs. No number stands in for a check that does not exist.
- **The labels are the fits' labels.** Synthetic profiles, user-confirmed for support, negative pins, coverage and the memory gate; planner-reviewed and pending the user's spot-check for the library sets. Read the numbers as a comparison of two methods on the same cases, not as an accuracy to expect.

## Summary

<!-- fallback-benchmark:begin summary -->
| point (labelled set) | harmful error: fallback | harmful error: Jev | main figure: fallback | main figure: Jev | proposed bar | verdict |
|---|---|---|---|---|---|---|
| support (89 pairs) | 6/54 should-block pairs kept silently by the check and #123 (1 surfaced for review); 0/35 supported blocked by the check, 1 by #123 | 0/54 kept silently (3 surfaced for review); 0/35 supported blocked | blocked 36/54; with #123 47/54 | blocked 51/54 (94%) | 35/54 should-block pairs blocked with #123 counted (contradicts 10/14, adds_unsupported 25/40) | met: #351 |
| negative pin (74 pairs) | 30/41 pinned mentions render | 0/41 render (3 surfaced for review) | blocked 11/41 (27%) | blocked 38/41 | hold 11/41 (the direct mentions) | leave |
| coverage (131 pairs) | none possible | 0 false covers | credited 0/58 (target absent) | credited 58/58 | hold: the target stays absent without Jev | good enough |
| memory gate (111 messages) | 0 wrong writes (it writes nothing) | 0 wrong of 15 writes | 57/67 preferences kept; 14 non-preferences sent to the host | 57/67 kept; 0 non-preferences sent | hold 57/67 preferences kept | good enough |
| variant choice (48 cases) | 0 poor-fit picks, 1 other wrong | 0 poor-fit, 1 other wrong | 41/48 (85%) correct | 47/48 (98%) | 33/48 correct, and above the constant 'always no_match' (15/48) | done: #352 |
| track baseline (36 cases) | 0 wrong tracks | 0 wrong | 36/36 (100%) correct | 35/36 (97%) | 31/36 correct, and above the constant 'always none' (14/36) | done: #352 |
<!-- fallback-benchmark:end summary -->

## Proposed targets

The bar a fix has to meet, per point. Two rules, applied the same way everywhere:

1. **Harmful errors first, never worse than Jev's.** A fallback that blocks an honest edit, picks a poor-fit variant or a wrong track is worse than one that abstains. The hard bar is Jev's own harmful-error count on the same set, which is 0 for every point that has one. A review tier, if a fix adds one, is capped at the fits' 10% of the supported pairs (`REVIEW_NOISE_LIMIT`).
2. **Then close at least half the distance** from what runs today to Jev's figure on the point's main figure, rounded up (`half_gap` in the script). A fixed rule, so the bar follows the measurement and is not tuned to a fix. Where a constant answer ("always `no_match`") already scores something, the fallback also has to beat it.

Any threshold a fix introduces is fitted the way `docs/architecture.md` § 8.8 says: harmful errors first, on these labels, never on a model's reported confidence, and recorded in `harness/policy.json` with the fallback's own `model` name.

<!-- fallback-benchmark:begin targets -->
| point | verdict | hard bar (harmful errors) | bar for the main figure | why |
|---|---|---|---|---|
| support | met: #351 | 0/35 supported pairs blocked by the new check (#123's own 1 is reported, not counted against it); any review tier surfaces at most 3 of the 35 supported pairs (the fits' 10% cap) | 35/54 should-block pairs blocked with #123 counted (contradicts 10/14, adds_unsupported 25/40) | Half the way from the 19 that #123 blocks alone to Jev's 51; a check that blocks an honest edit is worse than none, so the hard bar is Jev's 0. Measured with the #351 check: 47/54 blocked with #123 counted (contradicts 13/14, adds_unsupported 34/40), 0/35 supported blocked and 0/35 surfaced for review by the new check. |
| negative pin | leave | 0/33 not-a-mention pairs blocked (hold) | hold 11/41 (the direct mentions) | The term match is exact and never wrongly blocks; the 30 misses are paraphrases and products that no word list reads, and the host is handed every pin verbatim. |
| coverage | good enough | none possible: an absent target blocks and writes nothing | hold: the target stays absent without Jev | `semantic_coverage` is a target, never a gate; the literal `coverage` and `relevance_density` still steer. |
| memory gate | good enough | 0 automatic writes without Jev (hold) | hold 57/67 preferences kept | Nothing is written, so no write can be wrong; the 10 misses are the prefilter's, which Jev sits behind and shares. |
| variant choice | done: #352 | 0/48 poor-fit picks, and at most 1 other wrong pick | 33/48 correct, and above the constant 'always no_match' (15/48) | Half the way from 18 (#229's word overlap, before #352) to Jev's 47; that fallback was only 3 cases better than a constant. |
| track baseline | done: #352 | 0/36 wrong tracks | 31/36 correct, and above the constant 'always none' (14/36) | Half the way from 27 (#229's title lookup, before #352) to Jev's 35; the lookup read the title alone and was right on none of the jobs whose title and duties disagree. |
<!-- fallback-benchmark:end targets -->

### Already good enough: spend no work here

- **Memory gate.** The fallback writes nothing, so it cannot write a wrong preference; the cost is that the host confirms every candidate Jev would have written itself. Its misses are the prefilter's, which Jev sits behind and shares.
- **Requirement coverage.** The semantic target is absent without Jev, and nothing can go wrong because of it: it is a target, not a gate (`docs/harness.md` § 5), so the planner steers on the literal `coverage` and `relevance_density` as it did before #126.
- **Negative pins (leave as is).** Recall is low (the term match reads literal mentions only) but it is exact, never refuses an honest edit, and the misses are paraphrases and products that a word list cannot read. The host is handed every pin verbatim (`art_briefing`, `art_pins`, and again after compaction), so it is the first line of defence and the gate is the backstop. An alias table in the style of `SkillMatcher` (#254) would add some company-to-product hits and is not worth scheduling here.

### Needs work

- **Support (#351) is done; the bar is met.** It was the one that mattered: nothing semantic stopped a reworded claim, and the only deterministic gate beside it (#123) reads new numbers and names, so role inflation, invented scope and contradictions that introduce no new token passed. `harness/decisions/support_fallback.py` now reads those (small explicit word lists, fitted by `eval/fit_support_fallback.py`, recorded in `harness/policy.json` as `fallback-support-v1`). The numbers are in the support section below and in `eval/support_labels/FALLBACK_REPORT.md`. The lists were written from the failure classes #350 named and run on this same set, so they are a floor, not a held-out accuracy; Jev still reads what no list does.

### Done: variant choice and track baseline (#352)

- **Variant choice** went from 18/48 with 7 poor-fit picks (#229's word overlap, 3 cases better than a constant) to 41/48 with 0 poor-fit picks and 1 other wrong pick. The fallback reads *kinds of work* instead of words (`harness/concepts.py`): a bullet is scored by how much of the job's top requirements and title asks for what the bullet shows, so "trained a scikit-learn churn classifier" fits "build predictive models", and a bullet about grading assignments does not fit a software posting however many of its nouns it repeats. A job with no requirements has little to read and gets `no_match`.
- **Track baseline** went from 27/36 with 8 wrong tracks (#229's title lookup, right on none of the 7 title/duties mismatches) to 36/36 with 0 wrong. Each saved track is scored by its role family against the job's duties (`harness.library.pick_track`), and a pick must clear a fitted score and lead every other family, including kinds of work no track covers (support, sales, writing), by a fitted margin; otherwise the job starts from none. A job with no requirements still gets #229's title lookup, as the title is then all there is.
- **How far to trust it.** The vocabulary was written with these labelled sets in view, so the 41/48 and 36/36 above are fits. `library_labels/FALLBACK_FIT.md` sets leave-one-out and 22-case and 17-case holdouts beside them. Both holdouts were written by the same author before the vocabulary, but the vocabulary grew afterwards, so they show its reach into fresh wording and are not an independent test. The variant floor sits in a thin gap (worst poor-fit score 0.083, lowest right pick 0.104). Jev still leads on variant choice (47/48) and on any bullet whose point is not in the vocabulary.

## Support: cited-bullet check (#193, #237)

<!-- fallback-benchmark:begin support -->
89 pairs: 54 should block (contradicts 14, adds_unsupported 40), 35 supported. With Jev off every cited bullet's finding is the deterministic check's (`harness/decisions/support_fallback.py`, #351, model `fallback-support-v1`, `source`: fallback, `reason`: mode=off); `violations` and `reviews` are its block and review. The #123 numbers-and-names gate runs beside it in production, so the middle column counts a pair blocked by either. Jev figures: `support_labels/REPORT.md`, "in support.py" row.

|  | fallback: the #351 check | fallback: the #351 check + #123 | Jev (tau_block 0.85, tau_review 0.35) |
|---|---|---|---|
| should-block pairs blocked | 36/54 (67%) | 47/54 (87%) | 51/54 (94%) |
|   contradicts | 11/14 | 13/14 | 14/14 |
|   adds_unsupported | 25/40 | 34/40 | 37/40 |
| should-block pairs surfaced for review | 1 | 1 | 3 |
| **should-block pairs kept silently (the harmful error)** | **17** | **6** | **0** |
| supported pairs blocked (false blocks) | 0/35 | 1/35 | 0/35 |
| supported pairs surfaced for review | 0 | 0 | 1 |

**What stands beside it.** The #123 numbers-and-names gate (`agents.checks.consistency_check`) runs beside the check, in production and here. On these pairs it alone blocks 19/54 (contradicts 6/14, adds_unsupported 13/40), the baseline #350 measured, and 1/35 supported pair (`bd_seaborn_python`), which Jev does not block. The pair carries only its cited text as evidence, where a real item adds its other source bullets and header, so both checks' figures are upper bounds. Lexical drift (`faithfulness_drift`) is an item-level mean over the whole item's bullets that fires on wholesale rewrites; it has no per-bullet reading, so it is not scored here.

| category | should block | #123 blocks | #351 check blocks | #351 check reviews (not blocked) | either blocks | Jev blocks (tau_block 0.85) |
|---|---|---|---|---|---|---|
| `boundary` | 6 | 0 | 3 | 1 | 3 | 5 |
| `contradiction` | 13 | 6 | 10 | 0 | 12 | 13 |
| `invented_scope_outcome` | 13 | 2 | 9 | 0 | 10 | 13 |
| `role_inflation` | 11 | 0 | 11 | 0 | 11 | 9 |
| `ungrounded_tool` | 11 | 11 | 3 | 0 | 11 | 11 |

The residual the check worked on, the should-block pairs #123 does not block: 35 (`boundary` 6, `contradiction` 7, `invented_scope_outcome` 11, `role_inflation` 11). The check blocks 28 of them and surfaces 1 more for review; 6 should-block pairs pass both unflagged (`rw_dependency_free`, `scope_no_internet`, `scope_plagiarism_feature`, `scope_hourly_refresh`, `contra_replay_overwrites`, `bd_original_not_evidence`).
<!-- fallback-benchmark:end support -->

## Negative pin (#232)

<!-- fallback-benchmark:begin negative_pin -->
74 (text, pin) pairs: 41 mentions, 33 not. The fallback is the whole-word term match (`acceptance.negative_pin_violations`), which always runs; with Jev off the pin checker's findings are all `unchecked`. Jev figures: `negative_pin_labels/REPORT.md`, "tau_block grid, whole set" and "Review band".

|  | fallback (term match) | Jev (tau_block 0.85, tau_review 0.15) |
|---|---|---|
| mentions blocked | 11/41 (27%) | 38/41 |
| mentions surfaced for review | 0 | 3 |
| **mentions that render (the harmful error)** | **30** | **0** |
| not-a-mention pairs blocked (false blocks) | 0/33 | 0/33 |

| category (blocked / pairs in it) | fallback | Jev |
|---|---|---|
| `direct` | 11/11 | 11/11 |
| `paraphrase` | 0/14 | 14/14 |
| `indirect` | 0/10 | 7/10 |
| `near_miss` | 0/16 | 0/16 |
| `unrelated` | 0/11 | 0/11 |
| `negation` (6 mentions) | 0/12 | 6/12 |
<!-- fallback-benchmark:end negative_pin -->

## Requirement coverage (#126)

<!-- fallback-benchmark:begin coverage -->
131 (bullet or education entry, requirement) pairs: 58 covered, 73 not. With Jev off `make_coverage_checker` reports `unchecked` for every pair, so `semantic_coverage` is left out of the metric vector (0 of 131 pairs get an answer). Jev figures: `coverage_labels/REPORT.md`, "By category" and the pooled line.

|  | fallback (target absent) | Jev (tau_cover 0.65) |
|---|---|---|
| covered pairs credited | 0/58 (0%) | 58/58 |
| not-covered pairs called covered (false covers) | 0/73 (cannot occur) | 0/73 |

| category | pairs | covered | fallback credits | Jev calls covered |
|---|---|---|---|---|
| `literal` | 13 | 13 | 0 | 13/13 |
| `semantic` | 16 | 16 | 0 | 16/16 |
| `partial` | 13 | 0 | 0 | 0/13 |
| `aspiration` | 11 | 0 | 0 | 0/11 |
| `near_miss` | 15 | 0 | 0 | 0/15 |
| `unrelated` | 11 | 0 | 0 | 0/11 |
| `boundary` | 18 | 13 | 0 | 13/18 |
| `soft_skill` | 19 | 10 | 0 | 10/19 |
| `education` | 15 | 6 | 0 | 6/15 |

The literal `coverage` target stays: by construction of the labelled set the `literal` pairs share the requirement's own words with the bullet (the literal target sees them) and the `semantic` pairs share none (it cannot). The pairs carry no posting `terms`, so the literal target is not scored here and no number stands in for it.
<!-- fallback-benchmark:end coverage -->

## Memory gate (#202, #244)

<!-- fallback-benchmark:begin memory_gate -->
111 messages (67 standing preferences, 44 not) and 40 context messages. With Jev off the prefilter alone decides: a non-candidate is dropped, every candidate goes to the host with the gate's cue words, and nothing is written (routes: jev_unavailable 71). Jev figures: `memory_gate_labels/REPORT.md`, "Gate precision and recall" and "Automatic writes".

|  | fallback (prefilter) | Jev (gate at TAU_LO/TAU_HI) |
|---|---|---|
| **wrong automatic writes (the harmful error)** | **0** (it writes nothing) | **0** of 15 writes |
| automatic writes | 0 | 15 |
| standing preferences kept (host or store) | 57/67 (85%) | 57/67 (85%) |
| standing preferences dropped (never reach the host) | 10 | 10 |
| non-preferences sent to the host | 14 | 0 |
| messages the host has to confirm | 71 | 42 (57 kept - 15 written) |

The 10 dropped preferences are the prefilter's, which Jev sits behind: `im_excel_junior`, `im_abtest_card`, `im_ballot_weekend`, `im_halcyon_proud`, `im_looker_padding`, `im_edu_top`, `im_r_dead`, `im_forecast_first`, `fr_past_tense`, `fr_acronyms`. Jev does not recover them. On the 40 context messages (a reference to the previous assistant turn, which the fallback never reads) the prefilter keeps 33/33 (100%) preferences, drops none below a threshold, and sends 3 of 7 non-preferences to the host (36 messages in all); Jev (`context/REPORT.md`) keeps 31/33 preferences asked alone (`memory_gate@v1`) and 33/33 with the turn (`@v2`) and hands the host 32 and 34, with 0 automatic writes either way.
<!-- fallback-benchmark:end memory_gate -->

## Variant choice (#199)

<!-- fallback-benchmark:begin variant_choice -->
48 cases. The fallback is the concept-fit pick of #352 (`harness.library.best_variant` over `harness/concepts.py`, floor `library.variant_match_floor`), run exactly as `fit_library_thresholds.py` runs it; #229's word overlap (floor 0.10) ran before. The harmful error is a poor-fit pick (the label names the variants that do not fit). Jev figures: `library_labels/REPORT.md`, "Variant choice". Its vocabulary was written with these labels in view: `library_labels/FALLBACK_FIT.md` sets leave-one-out and an author-written holdout beside this fit.

|  | correct | picks made | right picks | poor-fit picks | other wrong picks | lost |
|---|---|---|---|---|---|---|
| **the Jev-free fallback (#352)** | 41/48 (85%) | 27 | 26 | 0 | 1 | 6 |
| before #352 (#229 word overlap) | 18/48 (38%) | 18 | 7 | 7 | 4 | 19 |
| Jev at the threshold 0.50 | 47/48 (98%) | 33 | 32 | 0 | 1 | 0 |
| constant: always no_match | 15/48 (31%) | 0 | 0 | 0 | 0 | 33 |

The fallback row above equals the one in `library_labels/REPORT.md` (41/48 (85%), 27 picks, 26 right, 0 poor-fit, 6 lost); the test pins it.

| category | cases | fallback correct | fallback poor-fit | Jev correct | Jev poor-fit |
|---|---|---|---|---|---|
| `clear_best` | 8 | 6/8 | 0 | 8/8 | 0 |
| `close_call` | 8 | 6/8 | 0 | 8/8 | 0 |
| `no_match` | 8 | 8/8 | 0 | 8/8 | 0 |
| `synonym` | 8 | 7/8 | 0 | 7/8 | 0 |
| `keyword_trap` | 8 | 6/8 | 0 | 8/8 | 0 |
| `single` | 8 | 8/8 | 0 | 8/8 | 0 |
<!-- fallback-benchmark:end variant_choice -->

## Track baseline (#199)

<!-- fallback-benchmark:begin track_baseline -->
36 cases. The fallback is the duties-based pick of #352 (`harness.library.pick_track`): the saved track whose role family best fits the job's top requirements and title, when it clears the fitted floor and leads every other family by the fitted margin, else none; #229's title lookup (`agents.job_card.role_family_from_title`) ran before and still runs for a job with no requirements. A host-stated `role_family` wins before either is asked and is not scored here. The harmful error is a wrong track. Jev figures: `library_labels/REPORT.md`, "Track baseline". The vocabulary was written with these labels in view: `library_labels/FALLBACK_FIT.md` sets leave-one-out and an author-written holdout beside this fit.

|  | correct | picks made | right picks | wrong picks | lost |
|---|---|---|---|---|---|
| **the Jev-free fallback (#352)** | 36/36 (100%) | 22 | 22 | 0 | 0 |
| before #352 (#229 title lookup) | 27/36 (75%) | 23 | 15 | 8 | 1 |
| Jev at the threshold 0.50 | 35/36 (97%) | 21 | 21 | 0 | 1 |
| constant: always none | 14/36 (39%) | 0 | 0 | 0 | 22 |

The fallback row above equals the one in `library_labels/REPORT.md` (36/36 (100%), 22 picks, 22 right, 0 wrong, 0 lost); the test pins it.

| category | cases | fallback correct | fallback wrong | Jev correct | Jev wrong |
|---|---|---|---|---|---|
| `clear_match` | 8 | 8/8 | 0 | 8/8 | 0 |
| `hybrid_title` | 7 | 7/7 | 0 | 6/7 | 0 |
| `title_mismatch` | 7 | 7/7 | 0 | 7/7 | 0 |
| `no_track` | 7 | 7/7 | 0 | 7/7 | 0 |
| `near_miss` | 7 | 7/7 | 0 | 7/7 | 0 |
<!-- fallback-benchmark:end track_baseline -->

## Limits of this measurement

- The support pairs carry only their cited text as evidence, so the #123 figures are upper bounds on its catches and on its false blocks (a real item adds its other source bullets and header).
- The variant pick runs with the job's title and requirements and uniform keyword weights (used only to break ties), as `fit_library_thresholds.py` runs it. The track pick is given track names equal to role families, its best case; a saved track named otherwise reads as the family of the title of the job it came from. Both read requirements: a real job whose host extracted none gets `no_match` (variants) or #229's title lookup (tracks).
- A host-stated `role_family` that names a saved track wins before either baseline path is asked; it is the host's call, not the fallback's, and is not scored.
- Coverage and the memory gate's `unresolved_reference` path are not scored beyond what is stated: the coverage pairs carry no posting `terms` for the literal target, and the fallback never reads the previous assistant turn.
- The #351 support check's word lists were written from the failure classes #350 named and then run on this same set, so its row is the set they were authored on, not a held-out accuracy. No supported pair fires any signal, so the labels cannot place its block cutoff between 0.5 and 1.0 (a rule does: a lone soft signal never blocks). `tests/test_support_fallback.py` adds 35 bullets written after the lists, honest and dishonest, as a regression floor; they are not a measurement either.
- Labels are synthetic and the sets are small (36 to 131 cases per point, 6 to 19 per category), so a per-category figure moves by a whole case when one label is corrected.

## Regenerating

```bash
python eval/fallback_benchmark.py write    # rewrite the tables above from the committed labels and REPORT.md files
python eval/fallback_benchmark.py check    # exit 1 if they are stale
python eval/fallback_benchmark.py json     # the measured numbers
python eval/fit_support_fallback.py analyze   # refit the support fallback's cutoffs (#351); rewrites support_labels/FALLBACK_REPORT.md
```
