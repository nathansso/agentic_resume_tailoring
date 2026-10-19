# Repo-dossier sufficiency spike (#397)

How rich must a per-repo dossier be for a coding agent to write an accurate project description
and resume bullets, at the lowest token cost? This folder holds the eval that answers it for
#398. The results are in `eval/results/repo_dossier_<date>.md` and `.json`.

## What it does

1. **Ladder** (`ladder.py`, offline). From a local clone, the user's git author emails and the
   GitHub metadata, it builds cumulative writer inputs:

   | Rung | Input |
   |---|---|
   | 1 | README |
   | 2 | + metadata (description, topics, languages, dates), dependency manifests, detected imports |
   | 3 | + filtered file tree (no vendored, lock, generated or binary files) |
   | 4 | + authorship: the user's changed files with lines and dates, and their commit messages (`git log --author --numstat`) |
   | 5 | + symbol map: tree-sitter definitions and references, personalized PageRank seeded by the user's files, rendered to 2,048 tokens (`symbols.py`, after Aider's repo map) |
   | 5r | rung 5 with the regex fallback instead of tree-sitter |
   | 6 | + the user's three most-changed source files (12K chars each) |
   | 7 | README, metadata, tree and authorship + the full code (450K-char cap, recorded when hit) |

   Notebooks are rendered as markdown, code and truncated text outputs; data files as their
   shape and first lines. The manifest list and the Python import parser come from the legacy
   `ingestion/github.py`.
2. **Writer** (`llm.py`, `claude-sonnet-5-5`, effort medium, the same for every rung): a 1–2
   sentence description and three bullets, as JSON.
3. **Judge** (`claude-opus-5-5`, effort high): gets the README, metadata, tree, every author's git
   history and the full code (900K-char cap, cached across a repo's rungs), splits each output
   into atomic claims and labels each supported, unsupported or contradicted. Claims that give
   the user credit for another author's work count as contradicted, and are flagged
   `about_user_role`.
4. **Coverage** (`claude-opus-5-5`, no code): for repos with reference facts (the owner's
   approved bullets), which facts each output states (full, partial, none).

Metrics stay separate, per rung: claim accuracy, unsupported rate, contradicted rate, coverage,
writer input tokens. Each is a mean over repos (each repo weighs the same). The decision rule
picks the cheapest rung whose coverage is at least 95% of full code's and whose unsupported rate
is not significantly above full code's (the 95% bootstrap interval of the paired per-repo
difference reaches zero).

## The variants (finishing #397)

The ladder grows its input rung by rung, so its rungs differ in size as well as content. To
compare methods head to head, `variants.py` builds **variants at matched token budgets**: the
same sections (README, metadata with manifests and imports, filtered tree, authorship, symbol
map, and any attribution layers) fitted to one total budget by one rule (`fit`: each section has
a share; one that needs less gives the rest back; one that needs more is cut at a line with a
marker). Tokens are estimated at 3.6 characters each; the writer's real input tokens come from
the API.

| id | symbol map | attribution layered on #397's per-file authorship | extra dependency |
|---|---|---|---|
| `5r-5k`, `5r-10k`, `5r-20k` | regex (#397's pick; `5r-10k` is the baseline) | none | none |
| `ts-10k` | tree-sitter with each grammar wheel's own `queries/tags.scm` (Aider's method) for Python, JS, TS/TSX, Go | none | `tree-sitter` + 4 grammar wheels |
| `ast-10k` | Python's stdlib `ast` (exact spans, full signatures, decorators), regex elsewhere | none | none |
| `ctags-10k` | Universal Ctags JSON (`--fields=+neS`), every language it knows | none | a `ctags` binary (`ART_CTAGS` or PATH) |
| `pyg-10k` | Pygments lexers (`Name.Function`/`Name.Class` as definitions) | none | `pygments` |
| `blame-10k`, `blame-20k` | regex, each definition tagged with its `git blame -w -M -C` ownership and introducing commit (`git log -L`) | + "built by other authors" | none |
| `blame-prs-10k` | regex, ownership-tagged | + "built by other authors" + merged PRs | none (GitHub client) |
| `commits-10k` | regex | per-file commit subjects, Conventional Commits types | none |
| `prs-10k` | regex | + the user's merged PRs (title, number, merge SHA, size, body, files) | none (GitHub client) |
| `own-10k`, `own-20k` | regex, ownership-tagged | all three layers | none |

Every variant cites: the header names the commit, symbol lines carry `L<start>-<end>`, authorship
and ownership lines carry short SHAs, PR lines carry `#<number>` and the merge SHA. Blame is
bounded (40 files, 6,000 lines each, 25 `git log -L` lookups, best-ranked first) and skipped in
single-author repos. The variants' judge evidence (`judge_evidence_v2.md`) is the ladder's plus
the user's merged PRs in full, since PR bodies are material the writer may cite.

**Decision rule for the variants, fixed before the run.** Every comparison is the paired
per-repo difference from `5r-10k` with a 95% bootstrap interval over repos.
1. *Symbol backend.* A backend replaces regex only if accuracy or coverage rises with an
   interval above zero and misattribution does not rise; otherwise regex stays (no dependency).
2. *Attribution.* A layer is adopted if misattribution (the share of claims that credit the
   user with another author's work, the headline metric) falls without unsupported claims
   rising significantly or supported claims about the user's own part falling. With no layer
   significant, the point estimates and the multi-author repos decide, and the report says so.
3. *Budget.* The smallest budget whose coverage is at least 95% of full code's and whose
   unsupported rate is not significantly above it, as in #397.

Metrics stay separate and are never combined: claim accuracy, unsupported, contradicted (fact /
role), misattribution, supported claims about the user, coverage, writer input tokens; plus
build time and peak memory on a large repo (`bench`), determinism (`determinism`) and dependency
sizes (in the results).

## Rerunning

```bash
pip install -r eval/repo_dossier/requirements.txt     # eval-only; not in pyproject.toml

# 1. Clone the repos named in repos.json (and the private config, if any) into one folder,
#    each as <owner>__<repo>. Long Windows paths need a short folder and core.longpaths.
gh repo clone kennethreitz/records C:/r397/kennethreitz__records -- -c core.longpaths=true

#    Check out each public repo's pinned "commit" (repos.json) so the dossiers match.
git -C C:/r397/kennethreitz__records checkout -q ea4273695cee6da42edf1cb294d1f2a4505470fc

# 2. Build every rung and variant and print sizes plus a spend estimate for unscored cells
#    (no API calls; it fetches GitHub metadata, author emails and merged PRs once, through gh,
#    into the work dir). Stored rungs are kept, so their cached calls stay valid; --rebuild
#    rebuilds them too. For the ctags variant, point ART_CTAGS at a Universal Ctags binary.
python -m eval.repo_dossier.run build --clones C:/r397
python -m eval.repo_dossier.run build --clones C:/r397 --cells 5r-10k,own-10k   # a subset

# 3. Writer, judge and coverage. Responses are cached under the work dir, so a rerun of a
#    finished cell costs nothing. By default the calls run through `claude -p` on the signed-in
#    Claude Code subscription (no API spend; tools, MCP and settings off, run from a temp folder
#    outside any repo). --samples 3 adds samples to the cells named in --cells.
python -m eval.repo_dossier.run run --clones C:/r397
python -m eval.repo_dossier.run run --clones C:/r397 --samples 3 --cells 5r-10k,blame-20k
#    --backend api uses the Anthropic API instead (billed); --limit stops before new spend
#    passes it (USD). #397's first run used it, and its cached calls are reused either way.
python -m eval.repo_dossier.run run --clones C:/r397 --backend api --limit 25

# 4. Draw 60 claims for a hand check, stratified over method families (ladder, structural,
#    attribution), role and other claims, supported and not; fill in my_label / my_note in
#    spotcheck_v2.json. The report pools it with #397's 20 and gives Wilson intervals.
python -m eval.repo_dossier.run spotcheck --n 60

# 5. Determinism: rebuild every rung and variant in fresh processes under 3 hash seeds. Run it
#    again on a second OS against the same clones (e.g. WSL, --os-label linux) to compare bytes.
python -m eval.repo_dossier.run determinism --clones C:/r397 --os-label windows

# 6. Time and peak memory per variant on one large clone (its user is the author ranked
#    --bench-rank by commits; no email is printed).
python -m eval.repo_dossier.run bench --clones C:/r397big/kubernetes__kubernetes

# 7. Aggregate into eval/results/repo_dossier_<date>.json.
python -m eval.repo_dossier.run report
```

With `--backend api`, `ANTHROPIC_API_KEY` comes from the environment or the repo's `.env` (`--env` to point
elsewhere). It is never printed or logged.

**Privacy.** No email address is committed. `repos.json` names each public repo's user by
GitHub login; the runner resolves a login to its git author emails at run time (the commits
GitHub links to the login, then git-log authors matching the account's display name) and caches
that mapping in the work dir only. The work dir defaults to `personal/eval_397/` (gitignored):
the owner's repos, reference facts and every per-repo output, claim and label stay there, along
with the private config `repos_private.json` (same shape as `repos.json`, plus `facts`; it may
list `user_emails` directly). Only aggregates and the public repos' examples go in
`eval/results/`.

The offline test is `tests/test_repo_dossier.py`: it builds the ladder from a two-author fixture
repo with no network and no API.
