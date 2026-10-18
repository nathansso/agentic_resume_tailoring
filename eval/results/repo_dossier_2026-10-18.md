# Repo-dossier sufficiency (#397): results, 2026-10-18

**Status: partial.** The run stopped when the Anthropic account ran out of credit, after
**5 of the 10 configured repos** were fully scored (4 of the owner's repos with approved
bullets, 1 public repo). The other 5 (1 owner repo, 2 repos the owner contributed to, 2 public
repos) are built and cached; `python -m eval.repo_dossier.run run` finishes them for about
$10 more and reuses every finished call. Numbers below are from the 5 scored repos, 1 sample per
cell. Treat the recommendation as provisional until the rerun.

Code and how to rerun: `eval/repo_dossier/README.md`. Machine-readable aggregates:
`repo_dossier_2026-10-18.json`.

## Recommendation for #398

The decision rule's pick is **rung 5r**: README, metadata with dependency manifests and detected
imports, a filtered file tree, the user's authorship (changed files with line counts and dates,
and their commit messages), and a symbol map seeded by the user's files, built **without
tree-sitter** (line regexes, ranked by personalized PageRank on networkx, which `art-mcp`
already depends on).

- **Token budget: 20K tokens per dossier, hard cap; about 10K is typical.** Writer input was
  9.6K tokens on average and 19.7K at most (the largest repo, a 3-author app of ~300 files).
  Per section: README 12K chars, up to 8 manifests at 2K chars each, a tree of at most 250
  lines, the top 40 changed files and 60 distinct commit messages, and a 2,048-token symbol map.
  Full code averaged 91K tokens and reached 203K: 10 to 20 times the dossier, for 0.04 more
  coverage (0.73 against 0.76).
- **The margin is noise.** Rungs 1 to 6 all sit between 90% and 95% of full code's coverage,
  and every rung's unsupported rate is within 1 point of zero. Rung 3 (94.9%) and rung 4 (92%)
  miss the 95% bar by under one fact per repo. What the data does show is below: inputs past
  the README buy little coverage, and authorship is what lets the writer say what the user did.
- **Authorship stays in regardless of the rule.** In the 3-author repo, the writer made 3 or 4
  claims about the user's own part at rungs 1 to 3 and 18 at rungs 4 and 5, all supported. The
  reference fact naming the user's own component went from partial to fully recovered at
  rung 4 and stayed there. No rung below 4 can attribute work in a team repo.

## Metrics per rung

Means over the 5 scored repos, each repo weighted the same. Coverage is over the 4 repos with
reference facts. "Unsupported vs full" is the mean paired per-repo difference from rung 7, with
a 95% bootstrap interval over repos. Metrics are reported side by side and never combined.

| Rung | Input | Writer input tokens (mean / max) | Claim accuracy | Unsupported | Contradicted (fact / role) | Coverage | Coverage vs full | Unsupported vs full (95% CI) | Passes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | README | 3,450 / 5,343 | 96.7% | 0.0% | 3.3% (1.0% / 2.4%) | 0.69 | 90% | -0.6 pts (-1.8, +0.0) | no |
| 2 | + metadata & deps | 4,514 / 8,615 | 97.7% | 0.0% | 2.3% (0.0% / 2.3%) | 0.69 | 90% | -0.6 pts (-1.8, +0.0) | no |
| 3 | + file tree | 5,684 / 12,670 | 95.6% | 0.0% | 4.4% (1.0% / 3.5%) | 0.73 | 95% (94.9) | -0.6 pts (-1.8, +0.0) | no |
| 4 | + authorship | 7,674 / 16,224 | 95.1% | 1.0% | 3.9% (1.0% / 3.0%) | 0.70 | 92% | +0.4 pts (+0.0, +1.2) | no |
| 5 | + symbol map (tree-sitter) | 9,582 / 19,741 | 98.3% | 0.0% | 1.7% (0.0% / 1.7%) | 0.69 | 90% | -0.6 pts (-1.8, +0.0) | no |
| **5r** | **+ symbol map (regex)** | **9,579 / 19,748** | 93.6% | 0.8% | 5.6% (0.8% / 4.8%) | 0.73 | 95% (95.4) | +0.2 pts (-1.8, +2.4) | **yes** |
| 6 | + sampled files | 18,770 / 34,198 | 95.7% | 0.0% | 4.3% (0.0% / 4.3%) | 0.73 | 95% (95.2) | -0.6 pts (-1.8, +0.0) | yes |
| 7 | full code | 90,823 / 202,909 | 96.4% | 0.6% | 3.0% (0.0% / 3.0%) | 0.76 | 100% | 0 | yes |

Split by group: on the owner's 4 repos, accuracy is 97.6% to 100% at every rung, with one
role error at rung 4 and one at rung 5r. Nearly all the role errors come from the public repo,
where 51 authors wrote the code (below).

**Decision rule.** The cheapest rung whose coverage is at least 95% of full code's (point
estimate) and whose unsupported rate is not significantly above full code's (the bootstrap
interval of the paired difference reaches zero).

## What the ladder shows

- **The README carries most of it, for these repos.** README alone reached 90% of full code's
  coverage with no unsupported claims. The owner writes long, results-first READMEs, and that
  is a threat to validity: a sparse README would leave more to the code. One of the 5 unscored
  repos has no README at all, so the rerun tests this directly.
- **Coverage is capped by the output, not the input.** Full code recovered 0.76 of the reference
  facts. Three bullets and a description cannot state all 8 to 11 reference facts per repo, and
  the writer picks different ones on each run. More input changes which facts are picked more than how many. For #398 this
  favours more records per repo over more input per record.
- **Unsupported claims are rare at every rung.** Told to state only what the material shows, the
  writer added nothing the judge couldn't find, rung 1 included (0 to 1% per rung). The errors
  that remain are role errors.
- **Role errors in a many-author repo are not fixed by more input.** On the public repo
  (304 non-merge commits by 51 authors, 66% of them the user's), 9% to 24% of claims credit the user with
  someone else's feature at every rung, full code included. Authorship gives files and line
  counts, not who wrote each feature, so a writer that sees "user changed records.py most"
  credits them with everything in it. #398's dossier should attribute at the feature level
  where it can: commit subjects per file, or `git blame` shares for the top symbols.

## Does the symbol map earn its dependency weight?

**No, not on this evidence.** Rung 5 (tree-sitter) and rung 5r (regex) differ only in how
definitions are found, and their metrics differ only by noise (coverage 0.69 against 0.73,
accuracy 98% against 94%, in opposite directions). Neither is distinguishable from rung 4,
which is about 1,900 tokens smaller. If #398 keeps a symbol map, build it on the regex
fallback in `eval/repo_dossier/symbols.py`: no new dependency.

What tree-sitter would cost:

- Core plus Python, JavaScript and TypeScript grammars: about 0.55 MB of Windows wheels and
  4 MB installed (`tree-sitter` 0.25.2, `tree-sitter-python` 0.25.0, `tree-sitter-javascript`
  0.25.0, `tree-sitter-typescript` 0.23.2). These are already pinned in
  `requirements-lock.txt` (the legacy full install), but they are not `art-mcp` dependencies.
- `tree-sitter-language-pack`: 14 to 17 MB wheels in 0.x. The 1.x wheels are 2.3 MB for 372
  languages, so they cannot bundle every grammar; check how 1.x obtains them before adopting
  it, since `harness/` allows only named network clients.
- Aider's `grep-ast` depends on the language pack.

## What fails at each rung

One failing claim per rung, all from the public repo (`kennethreitz/records`, "the user" being
its creator). Every one is a role error: the feature exists, but the history shows another
author wrote it.

| Rung | Claim | What the history shows |
|---|---|---|
| 1 | "Built transaction support" | `def transaction` came from Brian Hammond (67deca5) |
| 2 | "Designed the `first()` accessor" | Chad Whitacre added `.one`, then renamed it `.first` (e7da94b, 7588c1c) |
| 3 | "Set up tox" | Jan Vlcinsky added `tox.ini` (84ed3f1); the tree showed the file, not its author |
| 4 | "Built transaction support" | Still credited with authorship in view: authorship is per file, and the user changed `records.py` most |
| 5 | "Added transactions" | As above |
| 5r | "Designed the `scalar()` method" | Added by Joakim Uddholm (d1b76ca) |
| 6 | "Wrote the Connection class" | Added by Greg Troszak (89ef187) |
| 7 | "Designed `one()` on RecordCollection" | Chad Whitacre added it (e7da94b); Joakim Uddholm reworked it (de6f80e) |

Each was checked against the clone with `git log -S`. One more judge error turned up while
choosing these: at rung 5r it labelled "moved the engine layer to SQLAlchemy 2.0" contradicted,
but the user's own commit 9e0d8bc changed 94 lines of `records.py` for that upgrade.

## Judge agreement

20 judge labels hand-checked against the clones (`git log -S`, file reads): **16 of 20 agree
(80%) on the exact label, 18 of 20 (90%) on supported against not supported.** The draw
oversampled the rarer labels (11 supported, 3 unsupported, 6 contradicted), from the public repo
and the owner's repos, since only one public repo finished. All 11 supported labels were right.
The 4 disagreements:

- Two "unsupported" labels should have been "contradicted": another author's work, which the
  judge's own evidence showed.
- "Wrote `bulk_query`" was labelled contradicted. The user did add `bulk_query` and
  `bulk_query_file` (dd581ff); another author had added the underlying bulk support earlier.
- A per-file commit count (33) was labelled unsupported. It is true. The judge's history lists
  commits and per-file line counts, but not per-file commit counts.

So the judge leans strict on role claims, which slightly overstates the role-error rates above.

## Method

- **Repos.** 10 configured: 5 of the owner's repos with approved bullets and a GitHub repo, 2
  repos the owner contributed to but doesn't own (found with `gh search commits`), and 3 small
  public repos (`kennethreitz/records`, `sindresorhus/p-queue`, `rakyll/hey`) where "the user"
  is the main author. Scored so far: 4 owner, 1 public.
- **Ladder.** `eval/repo_dossier/ladder.py`. Rung 7 is capped at 450K chars; one scored repo hit
  the cap (the 3-author app) and is recorded in the JSON.
- **Writer.** `claude-sonnet-5-5`, effort medium, structured JSON output, the same prompt at
  every rung.
- **Judge.** `claude-opus-5-5`, effort high, given the README, metadata, tree, every author's
  history and the full code (900K-char cap, prompt-cached across a repo's rungs). It splits
  each output into atomic claims and labels each one supported, unsupported or contradicted.
  Crediting the user with another author's work counts as contradicted and is flagged as a role
  claim.
- **Coverage.** A separate `claude-opus-5-5` call that sees no code: for each reference fact, is
  it stated fully, partly or not at all (scored 1, 0.5, 0). The reference facts come from the
  owner's approved bullets, keeping only facts the repo itself can show, with the bullets'
  recorded accuracy corrections applied. They stay in the gitignored work dir.
- **Spend.** $11.55 in all (Opus judge and coverage $9.24, Sonnet writer $2.32), 121 calls,
  including a partly judged sixth repo. That is under the $25 limit; the stop was the account's
  credit.
