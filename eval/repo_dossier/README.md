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

## Rerunning

```bash
pip install -r eval/repo_dossier/requirements.txt     # eval-only; not in pyproject.toml

# 1. Clone the repos named in repos.json (and the private config, if any) into one folder,
#    each as <owner>__<repo>. Long Windows paths need a short folder and core.longpaths.
gh repo clone kennethreitz/records C:/r397/kennethreitz__records -- -c core.longpaths=true

# 2. Build every rung and print sizes plus a spend estimate (no API calls).
python -m eval.repo_dossier.run build --clones C:/r397

# 3. Writer, judge and coverage. Responses are cached under the work dir, so a rerun of a
#    finished cell costs nothing; --limit stops before new spend passes it (USD).
python -m eval.repo_dossier.run run --clones C:/r397 --limit 25

# 4. Draw ~20 claims for a hand check; fill in my_label / my_note in spotcheck.json.
python -m eval.repo_dossier.run spotcheck

# 5. Aggregate into eval/results/repo_dossier_<date>.json.
python -m eval.repo_dossier.run report
```

`ANTHROPIC_API_KEY` comes from the environment or the repo's `.env` (`--env` to point
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
