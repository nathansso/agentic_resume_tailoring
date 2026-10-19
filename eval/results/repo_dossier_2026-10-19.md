# Repo-dossier sufficiency (#397): final results, 2026-10-19

**Status: complete.** All **12 configured repos are scored**: 5 of the owner's repos with approved
bullets, 2 repos the owner contributed to, 3 small public repos where "the user" is the creator,
and 2 public many-author repos where the user is a contributor, not the creator
(`jpadilla/pyjwt`, 179 authors, user `mark-adams`; `jd/tenacity`, 123 authors, user `immerrr`).
This finishes the provisional spike of 2026-10-18 (`repo_dossier_2026-10-18.md`, 5 repos).

Code, method and how to rerun: `eval/repo_dossier/README.md`. Machine-readable aggregates:
`repo_dossier_2026-10-19.json`. The research behind the candidates and the recommendation for
#398 and #399: `docs/research/repo-map.md`.

## Recommendation for #398

**Build `blame-20k`**: #397's 5r dossier (README, metadata with manifests and imports, filtered
tree, the user's authorship, a regex symbol map ranked by PageRank seeded with the user's files)
**plus per-symbol ownership**: each mapped definition tagged with who wrote its surviving lines
(`git blame -w -M -C`) and who introduced it (`git log -L`), and a "built by other authors" list.
Budget **20K tokens as a hard cap** (46K characters at 2.3 characters a token); writer input
measured **14.5K mean, 22.8K max**. No new dependency.

It is the only variant that passes #397's decision rule (coverage 95% of full code's, unsupported
claims not above it), and against the 10K baseline it is better on every headline metric at
once:

| vs `5r-10k`, paired over 12 repos | Misattribution | Claim accuracy | Coverage | Unsupported | Supported claims about the user |
|---|---|---|---|---|---|
| `blame-20k` | **−2.8 pts** (95% CI −5.4, −0.5) | **+3.4 pts** (+0.6, +6.3) | **+8.2 pts** (+0.8, +16.1) | −0.6 pts (−2.3, +1.6) | +0.5 (−0.8, +1.9) |

## Metrics per cell

Means over the 12 repos, each repo weighted the same; the variants with three samples
(`5r-5k`, `5r-10k`, `5r-20k`, `blame-10k`, `blame-20k`, `blame-prs-10k`, `commits-10k`,
`prs-10k`, `own-10k`) average them first. Coverage is over the 6 repos with reference facts.
**Misattribution** is the share of claims that credit the user with work the history shows
another author did (contradicted and about the user's role). Writer input tokens are the API's
count, less Claude Code's own 1.4K-token prompt on `claude -p` rows. Metrics are reported side
by side and never combined.

| Cell | Input | Writer tokens (mean / max) | Accuracy | Unsupported | Misattribution | Supported claims about the user | Coverage (vs full) | Passes #397's rule |
|---|---|---|---|---|---|---|---|---|
| 1 | README | 3,215 / 5,343 | 84.3% | 3.7% | 8.8% | 4.4 | 0.56 (74%) | no |
| 2 | + metadata & deps | 4,247 / 8,615 | 92.5% | 2.2% | 5.3% | 5.9 | 0.57 (76%) | no |
| 3 | + file tree | 5,529 / 12,670 | 89.9% | 2.6% | 6.6% | 5.2 | 0.67 (88%) | no |
| 4 | + authorship | 7,577 / 16,224 | 91.0% | 3.2% | 5.2% | 11.9 | 0.65 (86%) | no |
| 5 | + symbol map (tree-sitter) | 9,677 / 19,741 | 93.4% | 1.8% | 4.4% | 11.9 | 0.64 (84%) | no |
| 5r | + symbol map (regex) | 9,605 / 19,748 | 90.5% | 1.1% | 7.9% | 11.6 | 0.68 (89%) | no |
| 6 | + sampled files | 19,175 / 34,198 | 90.9% | 1.5% | 7.6% | 10.7 | 0.71 (94%) | no |
| 7 | full code | 96,638 / 203,305 | 92.2% | 2.4% | 5.4% | 11.6 | 0.76 (100%) | yes |
| `5r-5k` | 5r at 5K | 4,851 / 5,885 | 90.2% | 3.8% | 5.3% | 12.1 | 0.65 (86%) | no |
| `5r-10k` | 5r at 10K (baseline) | 8,126 / 11,637 | 90.1% | 3.3% | 5.8% | 12.5 | 0.64 (84%) | no |
| `5r-20k` | 5r at 20K | 13,613 / 21,972 | 90.4% | 3.1% | 5.4% | 13.4 | 0.71 (93%) | no |
| `ts-10k` | tree-sitter tags (Aider's method) | 8,591 / 11,624 | 90.7% | 3.1% | 5.9% | 13.2 | 0.66 (87%) | no |
| `ast-10k` | Python `ast` + regex | 8,172 / 11,619 | 90.2% | 3.1% | 6.1% | 12.4 | 0.63 (83%) | no |
| `ctags-10k` | Universal Ctags | 8,234 / 11,633 | 90.7% | 4.0% | 5.3% | 13.0 | 0.66 (87%) | no |
| `pyg-10k` | Pygments | 7,945 / 11,600 | 90.0% | 2.5% | 6.8% | 12.2 | 0.69 (91%) | no |
| `blame-10k` | 5r + blame ownership | 8,783 / 11,900 | 92.3% | 4.4% | **2.7%** | 12.8 | 0.67 (88%) | no |
| **`blame-20k`** | **5r + blame ownership at 20K** | **14,485 / 22,797** | **93.5%** | 2.8% | **3.0%** | 13.0 | **0.72 (95%)** | **yes** |
| `blame-prs-10k` | 5r + blame + merged PRs | 8,669 / 11,640 | 92.5% | 3.0% | 3.9% | 12.3 | 0.66 (87%) | no |
| `commits-10k` | 5r + commit subjects by file | 8,031 / 11,218 | 91.0% | 3.1% | 4.9% | 11.9 | 0.63 (83%) | no |
| `prs-10k` | 5r + merged PRs | 8,079 / 11,466 | 92.7% | **1.8%** | 5.4% | 13.6 | 0.62 (81%) | no |
| `own-10k` | 5r + all three layers | 8,628 / 11,125 | 91.7% | 4.3% | 3.3% | 11.1 | 0.63 (83%) | no |
| `own-20k` | all three layers at 20K (1 sample) | 14,339 / 22,327 | 90.0% | 5.0% | 4.7% | 11.8 | 0.70 (92%) | no |

### Paired against the baseline (`5r-10k`), 95% bootstrap intervals over the 12 repos

| Cell | Misattribution | Unsupported | Accuracy | Coverage |
|---|---|---|---|---|
| `5r-5k` | −0.5 (−2.7, +1.0) | +0.5 (−1.0, +2.6) | +0.1 (−3.1, +3.1) | +1.0 (−2.3, +4.5) |
| `5r-20k` | −0.4 (−1.4, +0.4) | −0.2 (−0.8, +0.5) | +0.3 (−0.8, +1.8) | +6.6 (−0.6, +15.2) |
| `ts-10k` | +0.1 (−1.6, +1.8) | −0.3 (−0.9, +0.3) | +0.6 (−1.4, +3.0) | +1.8 (−1.0, +5.5) |
| `ast-10k` | +0.3 (−1.3, +1.7) | −0.2 (−2.0, +1.9) | +0.1 (−2.2, +2.3) | −1.2 (−4.3, +1.0) |
| `ctags-10k` | −0.4 (−2.4, +1.2) | +0.7 (−0.4, +1.8) | +0.6 (−1.3, +2.7) | +2.1 (−1.3, +5.8) |
| `pyg-10k` | +1.1 (−1.1, +3.4) | −0.8 (−2.9, +1.3) | −0.1 (−2.5, +2.6) | **+4.8 (+1.1, +9.1)** |
| `blame-10k` | **−3.0 (−5.8, −0.7)** | +1.1 (−0.4, +3.0) | +2.2 (−1.0, +5.6) | +2.6 (−1.2, +6.5) |
| `blame-20k` | **−2.8 (−5.4, −0.5)** | −0.6 (−2.3, +1.6) | **+3.4 (+0.6, +6.3)** | **+8.2 (+0.8, +16.1)** |
| `blame-prs-10k` | −1.9 (−4.7, +0.8) | −0.3 (−1.9, +1.2) | +2.3 (−0.3, +5.6) | +1.7 (−3.6, +7.1) |
| `commits-10k` | −0.8 (−2.5, +1.0) | −0.2 (−1.3, +0.9) | +0.9 (−1.6, +3.1) | −1.0 (−2.6, +0.4) |
| `prs-10k` | −0.4 (−2.2, +1.2) | **−1.5 (−3.0, −0.1)** | **+2.5 (+0.6, +4.9)** | **−2.3 (−4.8, −0.3)** |
| `own-10k` | −2.5 (−6.0, +0.2) | +1.0 (−1.0, +3.2) | +1.6 (−1.5, +4.2) | −0.7 (−6.5, +4.2) |
| `own-20k` | −1.1 (−4.1, +1.6) | +1.7 (−0.8, +4.4) | −0.1 (−2.9, +2.3) | +5.9 (+2.3, +9.5) |

## Misattribution, where it is hard

On the 5 repos with more than 10 authors (the 3 public creator repos and the 2 contributor repos):

| Cell | Misattribution | vs `5r-10k` |
|---|---|---|
| 7 (full code) | 12.2% | — |
| `5r-10k` | 12.3% | — |
| `5r-20k` | 11.2% | −1.0 (−3.2, +0.5) |
| `commits-10k` | 9.3% | −3.0 (−5.2, −0.7) |
| `prs-10k` | 10.5% | −1.8 (−4.7, +0.7) |
| `blame-10k` | 6.2% | −6.1 (−10.5, −1.1) |
| **`blame-20k`** | **6.5%** | **−5.8 (−9.9, −0.7)** |
| `blame-prs-10k` | 7.8% | −4.5 (−9.2, +0.5) |
| `own-10k` | 5.7% | −6.6 (−12.6, −1.7) |

Full code does not help (12.2%): seeing every file does not say who wrote it. On the 3 public
creator repos, blame halves misattribution (19.5% → 9.3% at 10K, −10.3 points, CI −11.6 to −9.1).
On the 2 contributor repos misattribution was already low with plain 5r (1.4%), since their
authorship sections are short and specific. **What blame fixed** (`kennethreitz/records`, the
user being its creator): at `5r-10k`, 9 role errors in 84 claims ("transactions support",
"context-manager support", "`first()`, `one()`, `scalar()`"); at `blame-20k`, 1 in 77. The
dossier now says, for example:

```
records.py:
  L212-231: def first(self, default=None, as_dict=False, as_ordereddict=False):  [mostly Chad Whitacre 100%, e7da94b; introduced by Chad Whitacre e7da94b]
  L336-347: def transaction(self):  [mostly Greg Troszak 100%, 89ef187]
  L252-255: def scalar(self, default=None):  [mostly Joakim Uddholm 100%, d1b76ca]
```

**What blame does not fix.** On `rakyll/hey`, half the remaining role errors are one claim
repeated: "authored 91 of 247 commits". That number is the dossier's, and it is too low. The
user committed under seven names and emails (143 commits), and resolving the GitHub login finds
only the emails GitHub links to it. #398 must resolve identity more widely (every email on the
account, `.mailmap`, the account's display names) before counting. The rest are features another
author introduced that the user later extended (`-z` by mdakin, HTTP/2 by xuqingfeng); the
ownership tags carry the information, and the writer still credits the user sometimes.

## Group splits (aggregates only)

| Group | `5r-10k` misattribution | `blame-20k` misattribution | `5r-10k` accuracy | `blame-20k` accuracy | `blame-20k` coverage vs full |
|---|---|---|---|---|---|
| Owner's repos (5, 1–4 authors) | 0.5% | 0.3% | 98.0% | 99.5% | 103% |
| Contributed-to repos (2; 2 and 4 authors) | 2.7% | 1.0% | 87.2% | 85.2% | 63% |
| Public creator repos (3) | 19.5% | 9.8% | 75.6% | 86.3% | — |
| Public contributor repos (2) | 1.4% | 1.6% | 95.0% | 97.6% | — |

The contributed-to repos are the weak spot: unsupported claims run 7–15% at every cell, and
their coverage is low at every budget below full code. They are two small repos (one has no
README, the other no code), so the numbers are noisy; #399's PR-only dossier is
untested here (section "Deviations" in the CHANGELOG).

## Symbol backends: none beats the regex map

`ts-10k` (Aider's method with each grammar's own `tags.scm`), `ast-10k`, `ctags-10k` and
`pyg-10k` are within noise of the regex map on misattribution, accuracy and unsupported claims.
`pyg-10k`'s coverage gain (+4.8, CI +1.1 to +9.1, one sample) meets the pre-set rule, but it
comes from Pygments finding *fewer* definitions (its map is smaller, so the budget goes to the
README and authorship; on Kubernetes it found 4 definitions in 26K Go files), so it is not
adopted; the same budget effect shows directly in `5r-20k` and `blame-20k`. The regex map stays:
no dependency, and no backend earns one.

## Budget

Coverage rises with budget (5K 0.65, 10K 0.64, 20K 0.71, full code 0.76); misattribution and
unsupported claims don't move with it. With blame tags, 20K reaches 95% of full code's coverage
at 15% of its tokens. Coverage stays capped by the output, as #397 found: three bullets and a
description cannot state 8 to 11 reference facts.

## Judge agreement

80 judge labels checked by hand (the 20 from 2026-10-18 and 60 new, drawn blind, stratified over
the ladder, the structural variants and the attribution variants, role claims and others,
supported and not): **63 agree exactly (79%, Wilson 95% CI 69–86%) and 65 on supported vs not
(81%, 71–88%)**. Re-weighted to the label mix of all claims (the draw oversamples the rarer
labels), agreement is **96%** on both. On role claims, 35 of 45 agree (78%, 64–87%). Every one
of the 17 disagreements has the judge stricter than the hand label (contradicted → supported 10,
unsupported → supported 5, unsupported → contradicted 2), most often a feature another author
introduced that the user later reworked substantially. So **the absolute misattribution rates
above are overstated**; the comparisons are paired under the same judge. Agreement by family:
ladder 33/40, structural 16/20, attribution 14/20.

## Determinism

Every one of the 14 variants is **byte-identical across 3 hash seeds on Windows (Python 3.11.9,
git 2.52) and on Linux (WSL Ubuntu, Python 3.12.12, git 2.53, native LF clones) for all 12 repos**
(168 dossiers). The only cells that differ across the two OSes are #397's rungs 5, 5r and 6
(30 of 264 cells), which keep networkx's PageRank: it needs SciPy, `art-mcp` doesn't ship
it, and without it the ranking silently fell back to uniform scores. Four defects found and fixed
on the way are in `docs/research/repo-map.md` § 5.

## Build time and memory on a large repo

`kubernetes/kubernetes` (31,441 tracked files, 25,977 after filtering; the user is the
10th-ranked author, 919 commits), each in a fresh process: the bounded `5r-10k` builds in **34 s
at 532 MB peak**, against 147 s and 7.5 GB before bounding (and 171 s, 7.1 GB for #397's rung 5r).
`blame-10k` takes 111 s at 875 MB (40 files blamed with deep history, 25 `git log -L`). Details:
`repo_dossier_2026-10-19.json` → `bench`.

## Dependency sizes

The recommended method adds nothing to `art-mcp`. For the alternatives (wheels / installed,
Windows · Linux · macOS arm64): tree-sitter + 4 grammars 0.6/4.3 · 1.2/6.6 · 0.6/4.6 MB; Pygments
1.2/5.3 MB everywhere; tree-sitter-language-pack 0.13 17/181 · 21/196 · 33/365 MB;
tree-sitter-language-pack 1.22 2.3 MB plus a 22–26 MB parser download on first use (Linux
wheels need glibc 2.34); Universal Ctags is not on PyPI (1.7–3.2 MB archives, GPL-2.0). Full
table: `docs/research/repo-map.md` § 4.

## Method

- **Repos.** 12, all scored: 5 owner repos with approved bullets, 2 contributed-to repos, 3
  public creator repos, 2 public contributor repos. Public repos are pinned by commit in
  `eval/repo_dossier/repos.json`.
- **Cells.** #397's ladder (rungs 1–7 and 5r) and 14 variants at matched budgets
  (`eval/repo_dossier/variants.py`). One sample per cell, three for the nine attribution and
  budget cells listed above.
- **Writer** `claude-sonnet-5-5` (effort medium) and **judge** `claude-opus-5-5` (effort high),
  prompts unchanged from #397. The variants' judge evidence adds the user's merged PRs.
- **Where the calls ran.** The 2026-10-18 cells (5 repos' ladder rungs) used the Anthropic API;
  everything new ran through `claude -p` on the owner's Claude Code subscription
  (`--backend cli`, the default now), with tools, MCP, settings and session persistence off and
  no project CLAUDE.md in reach. Requests keep the API shape, so the API cells stay cached.
- **Statistics.** Per-repo means, paired differences against `5r-10k` (and full code for #397's
  rule), 4,000-sample bootstrap over repos.
- **Spend.** **$0 new.** 1,086 calls on the subscription (writer and judge), $106 at list price
  and not billed. The API total stays at #397's $11.55.
