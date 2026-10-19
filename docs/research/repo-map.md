# How ART should map a repo: research for #397, #398 and #399

Researched and measured 2026-10-19. This finishes the #397 spike (provisional in PR #405) and
fixes what #398 builds. The eval behind it is `eval/repo_dossier/` (README there); the numbers are
in `eval/results/repo_dossier_2026-10-19.{md,json}`.

## Summary

- **Method for #398: #397's 5r dossier plus per-symbol ownership.** That is README, metadata
  with manifests and imports, filtered tree, the user's authorship, and a regex symbol map
  ranked by PageRank seeded with the user's files. Each mapped definition is tagged with who
  wrote its surviving lines (`git blame -w -M -C`) and who introduced it (`git log -L`), and a
  "built by other authors" list closes the dossier. The variant is `blame-20k`.
- **Budget: a 20K-token hard cap** (46K characters), with 14.5K typical (measured 14.5K mean,
  22.8K max). **Dependencies: none new.** Every fact cites a path and line span, a commit SHA or
  a PR number.
- **What the numbers say.** Paired over 12 repos against #397's 5r at 10K, `blame-20k` cuts
  misattribution by 2.8 points (5.8% → 3.0%, 95% CI −5.4 to −0.5), raises claim accuracy by 3.4
  (+0.6 to +6.3) and coverage of the owner's approved facts by 8.2 (+0.8 to +16.1). It is the
  only variant within 95% of full code's coverage at no rise in unsupported claims, which is
  #397's decision rule. In the many-author repos misattribution falls from 12.3% to 6.5%; full
  code, at 6.7× the tokens, sits at 12.2%.
- **What doesn't earn its place.** No symbol backend beats the line regexes: tree-sitter with
  Aider's queries, stdlib `ast`, Universal Ctags and Pygments are all within noise.
  `tree-sitter-language-pack` 1.x would also add a hidden 22 MB download. Commit subjects by
  file add nothing measurable overall. Merged PRs lower unsupported claims (−1.5 pts) but not
  misattribution, so they are the right unit for #399, not a fix for attribution.



## 1. The problem

`artie ingest` builds one **dossier** per GitHub repo, with no generative model (root
`CLAUDE.md`, "Architecture invariants"). The user's own coding agent reads it and writes the
project's description and resume bullets into the graph. So the dossier has to be:

- **compact**, a few thousand tokens, not the code;
- **accurate about the user's own part**: a resume needs what *they* did;
- **cited**: every fact names a path, line span, commit SHA or PR number, so a bullet can pass
  the evidence gate;
- **deterministic**: the same repo at the same commit gives the same bytes everywhere (#405 found
  and fixed hash-seed order once);
- **light and offline**: one wheel under `uvx --python 3.12` on Windows, macOS and Linux; after
  the clone, no network except through the named clients in `harness/` (Jev, the GitHub client,
  the Tectonic downloader), enforced by `tests/test_harness_boundary.py`;
- **bounded** on repos with tens of thousands of files.

#397 showed that inputs past the README buy little coverage, that unsupported claims are rare
at every rung (0–1%), and that the errors that remain are **role errors**: in a 51-author repo
9–24% of claims credited the user with other authors' features at every rung, full code included.
Per-file authorship ("the user changed `records.py` most") is not per-feature authorship. That
open problem is the centre of this follow-up.

## 2. Candidates

Each is judged against ART's constraints: **viable**, **viable with adaptation** (what changes),
or **out** (which constraint it breaks). Sizes are measured (section 4); everything else cites a
source in section 8.

### 2.1 Comparison

| Candidate | How it works | Captures | Deterministic | Install (measured, py3.12) | Licence | Large repo | Citations | Author-weightable | Health (2026-10-19) | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| **Aider repo map** (`aider-chat`) | tree-sitter tags (`*-tags.scm`), file graph, personalised PageRank, binary search to a token budget | definitions + references, signature lines | mostly: unsorted sets, an mtime cache and a timing-dependent refresh mode | the app pins `tree-sitter-language-pack==0.13.0` (17–33 MB wheels) | Apache-2.0; queries mostly MIT, some unrecorded | no published timings; open freeze issues | path only, no line numbers printed | yes, the personalisation vector | 49.6K stars; last release v0.86.0 (2025-08), PyPI 0.86.2 (2026-02) | **method: viable with adaptation** (it is the `ts-10k` variant: own renderer with line spans, sorted sets, grammar wheels); **package: out** (weight, network via its pack) |
| **Grammar wheels + their `tags.scm`** (`tree-sitter` + `tree-sitter-<lang>`) | Aider's method on the official per-language wheels, which ship `queries/tags.scm` | as above, exact node spans | yes (sorted here; see the fix in section 5) | 4 languages: 0.6–1.2 MB wheels, 4.3–6.6 MB installed | MIT | fast (C parsers) | path + line span | yes | tree-sitter 0.26.0 (2026-06) | **viable with adaptation**: one pinned wheel per language; no `tags.scm` for Kotlin, Bash, Haskell, Julia, Zig and others |
| **tree-sitter-language-pack 0.x** | 300+ grammars compiled into the wheel | as above | yes | 17.0 MB (Win) / 20.6 MB (Linux) / 32.8 MB (macOS) wheels; **181–365 MB installed** | MIT | — | — | — | 0.13.0 (2025-11) was the last bundled release | **out**: install weight |
| **tree-sitter-language-pack 1.x** | 2.2–2.5 MB native wheel; on first use **downloads a 22–26 MB parser bundle** from GitHub Releases (sha256 in a manifest) into `%LOCALAPPDATA%`/`~/.cache` | as above, plus a `process()` API | yes once cached | 2.3 MB wheel + 22.0 MB download (Win, measured); Linux wheels need **glibc 2.34** (`manylinux_2_34`) | MIT, permissive grammars only | — | — | — | 1.22.1 (2026-10-12), 478 stars | **out as is**: a hidden network client inside native code breaks the allow-list; would need its own named, pinned client like `toolchain.py`, and drops older Linux (Ubuntu 20.04, Debian 11, RHEL 8) |
| **grep-ast** | Aider's tree context renderer | — | — | depends on the pack **unpinned** (resolves to 1.x: 2.4 MB + the download) | Apache-2.0 | — | — | — | 0.9.0 (2025-05) | **out**: inherits 1.x's download |
| **Universal Ctags** | regex and hand-written parsers, JSON output (`--fields=+neS`) | definitions with kind, scope, line and end line; references only for includes/imports | sorted output; JSON format "can be changed in the future" | **not on PyPI**; binaries: 3.2 MB zip / 8.8 MB unpacked (Win), 1.8 MB tar.xz / 7.2 MB (Linux), 1.7 MB (macOS); stable releases are source-only, binaries are prereleases/nightlies | **GPL-2.0** | fast | path + line span | no (filter files yourself) | 7.3K stars, active | **viable with adaptation only as an optional enhancer** found on PATH; never a requirement (no wheel, GPL redistribution, nightly-only binaries) — the reason Aider left it |
| **LSP / Serena** | language servers over MCP | precise references, types | no (warm server state) | `serena-agent` 1.1 MB wheel + **language servers downloaded at run time** (Node for TS) | app GPL-3.0-or-later, SolidLSP MIT | needs a warmed project | path + range | no | 30.4K stars, v2.0.0 (2026-10) | **out**: run-time downloads, determinism, weight |
| **SCIP / LSIF indexers** | per-language indexers emit a protobuf index | occurrences, definitions, references, relationships | not documented (type-checks the project) | Node + per-language indexers (`scip-python` 20 MB unpacked; `scip-java` 86 MB) | Apache-2.0 / MIT | needs resolvable dependencies | path + range | no | `lsif-go` archived | **out**: Node, per-language toolchains, environment-dependent; borrow its stable symbol IDs |
| **Python stdlib `ast`** (+ regex for the rest) | `ast.parse`, exact `lineno`/`end_lineno`, decorators, full signatures | Python definitions, calls, imports | yes (pin the interpreter; `uvx --python 3.12` does) | **0** | PSF | fast | path + exact span | yes | stdlib | **viable** (the `ast-10k` variant) |
| **Regex symbol map** (#397's 5r) | one line regex per language, word references | definitions (Python, JS/TS, Go), approximate spans | yes | **0** | ART's own | fast | path + span (approximate end) | yes | — | **viable** (the `5r-*` variants) |
| **Pygments lexers** | `Name.Function`/`Name.Class` tokens as definitions, `Name` as references (Aider's reference fallback) | ~600 languages, definitions only by token type | yes | 1.2 MB pure wheel, 5.3 MB installed (all platforms) | BSD-2-Clause | slower (pure Python lexing) | path + line | yes | 2.21.0 (2026-08) | **viable with adaptation** (the `pyg-10k` variant): misses definitions some lexers don't mark |
| **Repomix** `--compress` | tree-sitter (WASM) keeps signatures, drops bodies; sorts files by git change count | signatures, types, classes | not documented | npm, **Node ≥ 22**, 24 MB of WASM grammars | MIT | — | optional line numbers | no | 28.9K stars | **out** (Node); **ideas adopted**: signature-only compression, sort by changes, default ignores |
| **Gitingest** | README-first tree + file dump, size caps (10 MiB/file, 10,000 files) | everything, uncompressed | fixed sort | pure wheel, but `tiktoken` downloads a vocabulary on first use | MIT | caps, 60 s timeout | paths | no | 16K stars | **out** (network via tiktoken, no compression); ideas: ignore list, README first, caps |
| **code2prompt**, **files-to-prompt** | glob-selected dumps with templates / line numbers | everything | — | Rust binary / tiny pure wheel | MIT / Apache-2.0 | — | `-n` line numbers | no | active | **out**: no selection or compression beyond globs |
| **RepoGraph** (ICLR 2025) | line-level def/ref graph via tree-sitter, k-hop ego graphs | invoke/contain edges | — | Aider's stack (`tree-sitter-languages`, abandoned) | Apache-2.0 | slow build (README) | lines | no | last push 2025-04 | **out at dossier scale**: its own ablation shows 2-hop text (~10.5K tokens) *below* baseline |
| **Codebase-Memory** (`codebase-memory-mcp`) | tree-sitter → SQLite graph, 14 MCP tools | functions, calls, imports, routes | — | static C binary, ~50 MB per platform | MIT | Django ~6 s, Linux ~3 min | nodes with paths | no | 46.9K stars | **out**: an interactive query server; its graph agent scored *lower* than a file-reading agent on answer quality (0.83 vs 0.92) |
| **DeepWiki / deepwiki-open, RepoAgent, CodeWiki** | LLM-written wikis, bottom-up summarisation | prose | no | generative models | MIT / Apache-2.0 | minutes to hours | DeepWiki links sources | no | active | **out** (generative models in `harness/`); **ceiling only**: LLM-judged rubric scores in the mid-60s to low-80s, weakest on systems code, no grounding guarantee |

### 2.2 Attribution methods (the open problem)

| Method | What it says | Cost | Determinism | Verdict |
|---|---|---|---|---|
| Per-file numstat (`git log --author --numstat`, #397's rung 4) | files and lines the user *touched* | one `git log` | yes | keep as the base; it is what caused #397's role errors |
| **`git blame -w -M -C` per file, aggregated over each definition's span** | who wrote the *surviving* lines of each function or class | one blame per file; bounded to the files holding top-ranked symbols | yes (pinned commit) | **measured** (`blame-10k`, `own-*`) |
| **Introducing commit** (`git log -L <span>:<path>`, oldest entry) | who *first* wrote a span (first authorship: the strongest single signal of authorship in developer surveys) | one `git log -L` per symbol; bounded to the 25 best-ranked | yes | **measured** (in the ownership tag) |
| `-C -C` / `-C -C -C` | copies from any file in the creating commit / any commit | reported as minutes per file on big histories | yes | skipped: cost |
| Commit subjects by file + Conventional Commits | names *what* each of the user's commits did, per file | one `git log --name-only` | yes | **measured** (`commits-10k`); subjects name features but don't decide credit (adoption is uneven; 7–20% of commits are tangled) |
| Merged PRs (GitHub API: title, body, files, merge SHA) | the user's own account of each change, merged by a maintainer | 1 search + 2 calls per PR, through the GitHub client | yes once fetched | **measured** (`prs-10k`); the natural unit for #399's contributed-to repos |
| Churn-weighted ownership (code-maat main-dev, DOA) | who touched a file most | cheap | yes | fallback only: file level, the failure mode |
| Hercules, git-of-theseus, cregit, git-author | incremental line tracking, token-level blame | hours on big repos / research code | — | out: cost or maturity |

No study validates any of these at the function or feature level; the published comparisons
(Fritz et al.; Avelino et al. 2018; Cury et al. 2022) are file- or package-level, and find
first authorship the strongest signal, with commits, blame and DOA close together. So the
eval measures it directly.

## 3. The head-to-head eval

`eval/repo_dossier/` (see its README) now builds **14 variants at matched budgets** next to
#397's ladder, on **12 repos** (5 of the owner's with approved bullets, 2 the owner contributed
to, 3 public repos where "the user" is the creator, 2 public 100+-author repos where the user is
a contributor), with #397's writer (`claude-sonnet-5-5`), judge (`claude-opus-5-5`), cache and
paired-bootstrap statistics. The decision rule was fixed in the README before the run. Full
tables: `eval/results/repo_dossier_2026-10-19.md`.

| Variant (vs `5r-10k`, paired, 95% CI) | Misattribution (pts) | Accuracy (pts) | Unsupported (pts) | Coverage (pts) | Writer tokens (mean) |
|---|---|---|---|---|---|
| `5r-10k` baseline: 5.8% / 90.1% / 3.3% / 0.64 | — | — | — | — | 8,126 |
| `5r-20k` | −0.4 (−1.4, +0.4) | +0.3 (−0.8, +1.8) | −0.2 (−0.8, +0.5) | +6.6 (−0.6, +15.2) | 13,613 |
| `ts-10k` (Aider's method) | +0.1 (−1.6, +1.8) | +0.6 (−1.4, +3.0) | −0.3 (−0.9, +0.3) | +1.8 (−1.0, +5.5) | 8,591 |
| `ast-10k` | +0.3 (−1.3, +1.7) | +0.1 (−2.2, +2.3) | −0.2 (−2.0, +1.9) | −1.2 (−4.3, +1.0) | 8,172 |
| `ctags-10k` | −0.4 (−2.4, +1.2) | +0.6 (−1.3, +2.7) | +0.7 (−0.4, +1.8) | +2.1 (−1.3, +5.8) | 8,234 |
| `pyg-10k` | +1.1 (−1.1, +3.4) | −0.1 (−2.5, +2.6) | −0.8 (−2.9, +1.3) | +4.8 (+1.1, +9.1) | 7,945 |
| `commits-10k` | −0.8 (−2.5, +1.0) | +0.9 (−1.6, +3.1) | −0.2 (−1.3, +0.9) | −1.0 (−2.6, +0.4) | 8,031 |
| `prs-10k` | −0.4 (−2.2, +1.2) | +2.5 (+0.6, +4.9) | −1.5 (−3.0, −0.1) | −2.3 (−4.8, −0.3) | 8,079 |
| `blame-10k` | −3.0 (−5.8, −0.7) | +2.2 (−1.0, +5.6) | +1.1 (−0.4, +3.0) | +2.6 (−1.2, +6.5) | 8,783 |
| **`blame-20k`** | **−2.8 (−5.4, −0.5)** | **+3.4 (+0.6, +6.3)** | −0.6 (−2.3, +1.6) | **+8.2 (+0.8, +16.1)** | **14,485** |
| `blame-prs-10k` | −1.9 (−4.7, +0.8) | +2.3 (−0.3, +5.6) | −0.3 (−1.9, +1.2) | +1.7 (−3.6, +7.1) | 8,669 |
| `own-10k` (blame + subjects + PRs) | −2.5 (−6.0, +0.2) | +1.6 (−1.5, +4.2) | +1.0 (−1.0, +3.2) | −0.7 (−6.5, +4.2) | 8,628 |

**Reading it against the rule.**
1. *Symbol backend.* None beats regex on accuracy or misattribution. Pygments' coverage gain
   meets the letter of the rule, but its cause is a smaller map: it finds fewer definitions,
   only 4 in Kubernetes' 26K Go files, so the budget goes to the README and authorship. The
   same coverage gain appears from simply raising the budget. Regex stays.
2. *Attribution.* Blame ownership is the one layer that cuts misattribution significantly,
   without a significant rise in unsupported claims or a fall in supported claims about the
   user. Commit subjects don't. PRs make claims more grounded (fewer unsupported, higher
   accuracy) but not better attributed, and cost coverage. Combining all three (`own-*`) is not
   better than blame alone.
3. *Budget.* Only `blame-20k` reaches 95% of full code's coverage (0.72 against 0.76). 5r at
   20K reaches 93%. Misattribution and unsupported claims don't change with budget.

**Judge reliability.** 80 labels were hand-checked: 60 new ones drawn blind and stratified over
method families and role claims, plus #397's 20. Exact agreement was 79% (Wilson 95% CI 69–86%)
and supported-vs-not 81% (71–88%); re-weighted to the real label mix, both are 96%. All 17
disagreements are the judge being stricter, typically on a feature another author introduced
and the user reworked. Absolute misattribution is therefore overstated, while the paired
comparisons share the bias.

**Attribution findings.**
- Per-file authorship can't separate features in a file several people wrote. Full code doesn't
  either: 12.2% misattribution in the many-author repos.
- Blame over a definition's span does separate them, and the ownership tag gives the writer a
  name and a SHA to cite.
- The remaining errors fall in two classes:
  - **Identity.** `rakyll/hey`'s user committed under seven names and emails. Resolving the
    GitHub login found 91 of their 143 commits, so the dossier's own count was wrong. #398 must
    resolve identities from every email on the account, `.mailmap` and display names before
    counting.
  - **Extended, not introduced.** The user reworked features others introduced. The tags show
    both shares, and the writer still over-credits sometimes. That is a prompt-side job for the
    agent skill: credit "extended X", not "built X".

## 4. Install weight (measured)

Measured 2026-10-19 for CPython 3.12 with `pip download --only-binary=:all:` (wheels) and
`uv pip install --target --python-platform` (installed), per platform: Windows x86-64, Linux
x86-64 (manylinux), macOS arm64. Each row is what the method adds to `art-mcp`'s closure, which
already has networkx 3.7 and numpy but **not SciPy** (`uv pip compile pyproject.toml`).

| Adds | Windows wheels / installed | Linux wheels / installed | macOS arm64 wheels / installed | Run-time download | Notes |
|---|---|---|---|---|---|
| Regex map, stdlib `ast`, all git-based attribution | 0 | 0 | 0 | none | |
| `tree-sitter` 0.25.2 + python, javascript, typescript, go grammars | 0.59 MB / 4.3 MB | 1.24 MB / 6.6 MB | 0.63 MB / 4.6 MB | none | each further language ~50–340 KB; `tags.scm` ships in the wheel |
| `pygments` 2.21.0 | 1.2 MB / 5.3 MB | 1.2 MB / 5.3 MB | 1.2 MB / 5.3 MB | none | pure Python |
| `tree-sitter-language-pack` 0.13.0 (+ tree-sitter 0.26 and 3 grammars it pulls) | 17.0 MB / 181 MB | 20.6 MB / 196 MB | 32.8 MB / 365 MB | none | |
| `tree-sitter-language-pack` 1.22.1 (+ tree-sitter 0.26) | 2.3 MB / 6.0 MB | 2.5 MB wheel / 8.1 MB; **glibc ≥ 2.34 only** | 2.3 MB / 5.5 MB | **22.0 MB** bundle on first use (Win; 22.5–26.2 MB elsewhere) into the user cache | outside the network allow-list |
| `grep-ast` 0.9.0 (+ pathspec + the pack, unpinned) | 2.4 MB / 6.4 MB | 2.3 MB / 8.5 MB | 2.4 MB / 5.9 MB | as the pack 1.x | |
| Universal Ctags (not on PyPI) | 3.2 MB zip / 8.8 MB | 1.8 MB tar.xz / 7.2 MB | 1.7 MB tar.xz | it *is* a download, or a user install | GPL-2.0; binaries are prereleases (Windows) and nightlies (Linux, macOS) |
| SciPy (what networkx's `pagerank` needs) | — | — | — | — | not measured: avoided with a 30-line power iteration (section 5) |

Measuring this ran tree-sitter-language-pack 1.22.1 once, which left its 22 MB parser bundle in
`%LOCALAPPDATA%\tree-sitter-language-pack\` on the measuring machine.

## 5. Determinism, scale and the bugs found on the way

**Determinism: every variant is byte-identical across 3 hash seeds × Windows (Python 3.11.9,
git 2.52) and Linux (WSL Ubuntu, Python 3.12.12, git 2.53, native LF clones): 168 of 168
variant dossiers.** The check (`run.py determinism`) rebuilds every rung and variant in fresh
processes. Getting there found four real defects, each of which #398 would have shipped:

1. **networkx's `pagerank` needs SciPy, and `art-mcp` doesn't ship it.** Without SciPy the
   call raises and the map silently fell back to uniform ranks, a different map with no error.
   The variants use their own power iteration (`symbols.pagerank`, tested against networkx to
   1e-6) and round scores to 10 significant digits before ordering. #397's own rungs 5, 5r and 6
   still use networkx, which is why they are the only cells that differ across OSes (30 of 264).
2. **Line endings.** A Windows clone (`core.autocrlf`) has CRLF where Linux has LF, so README
   text, tree sizes and language shares differed. Files are now read with CRLF normalised, and
   sizes come from `git ls-tree -l` (committed size), not the disk.
3. **Symlinks.** A Windows checkout without `core.symlinks` writes a symlink as a one-line file
   holding its target. `jd/tenacity`'s `README.rst` is a symlink, so on Windows its README read
   as the text `doc/source/index.rst`. Symlinks are now resolved through git.
4. **Capture order.** #397's tree-sitter map took the first definition of a repeated name (a
   file with several `__init__`) in tree-sitter's capture order, which is not stable across
   processes. Sorted by position.

**Scale.** `kubernetes/kubernetes` at 31,441 tracked files (25,977 after filtering), 919 commits
by the benchmark user (the 10th-ranked author by commits), each variant in a fresh process
(`run.py bench`):

| Build | Wall time | Peak memory (process tree) |
|---|---|---|
| #397's rung 5r (unbounded map) | 171 s | 7.1 GB |
| `5r-10k`, first version (unbounded) | 147 s | 7.5 GB |
| `5r-10k`, bounded | **34 s** | **532 MB** |
| `ts-10k` / `ast-10k` / `ctags-10k` / `pyg-10k`, bounded | 41 / 34 / 42 / 47 s | 530 / 530 / 844 / 526 MB |
| `commits-10k` / `prs-10k` | 36 / 36 s | 583 / 531 MB |
| `blame-10k` / `own-10k` (40 files blamed, 25 `git log -L`) | 111 / 110 s | 875 / 872 MB |

About 27 s of every build is reading the clone (listing, authorship, decoding 26K files). The
bound (`symbols.select_sources`, `MAX_DEFINERS`): parse at most 3,000 source files (the user's
changed files first, then their directories' other files, then the rest by path), keep
references as per-file counts, and drop names defined in more than 20 files (`String`, `New`,
`init`: millions of edges, almost no signal). It changes none of the eval dossiers. Blame
cost is history depth, not file count: 40 files of Kubernetes history take ~75 s, so #398 should
blame only the files behind the top-ranked symbols and in parallel.


## 6. Recommendation for #398

**Method.** Deterministic and model-free, from a full clone at a pinned commit, through `git`
and the filesystem only. The GitHub client supplies metadata and, for #399, PRs.

**Dossier sections**, in this order, fitted to the budget by fixed shares. A section that
needs less hands its share on; one that needs more is cut at a line with a marker
(`variants.fit`):

| Section | Share | Content | Cites |
|---|---|---|---|
| Header | — | repo name and the commit it was built at | commit SHA |
| README | 30% | the root README, following git symlinks | path |
| Metadata | 12% | description, topics, languages (committed byte shares), dates, up to 12 dependency manifests, detected imports | paths |
| File tree | 10% | filtered tree with committed sizes, collapsing deep directories past the line cap | paths |
| Authorship | 23% | the user's commits and share, their changed files with +/− and commit counts, their distinct commit messages | file paths, short SHAs |
| Symbol map with ownership | 25% | definitions ranked by personalised PageRank seeded with the user's files; each line `L<start>-<end>: <signature>  [you 80%, <sha>; introduced by you <sha>]` or `[mostly <author> 90%, <sha>; introduced by <author> <sha>]` | path, line span, SHAs |
| Built by other authors | 6% | top-ranked, non-test definitions whose surviving lines are under 20% the user's and that the user did not introduce: author, share, SHA | path, line span, SHA |
| Merged PRs (#399; optional elsewhere) | 14% | number, merge date, size, merge SHA, title, first 240 characters of the body, files | PR number, merge SHA |

**Token budget.** A hard cap of 20K tokens, applied as 46K characters (2.3 characters a token,
measured on #397's dossiers, range 2.2–2.7). Typical writer input was 14.5K; the maximum was
22.8K, where the estimate ran low. If the cap must hold in real tokens, set 2.1 characters a
token.

**Dependencies.** None new. networkx (already a dependency) holds the graph. **PageRank must
not call `networkx.pagerank`**: it needs SciPy, which `art-mcp` doesn't ship, and it falls
back silently. Use the 30-line power iteration in `eval/repo_dossier/symbols.py`
(`pagerank`) and round scores before ordering.

**How citations work.** Every line names its source: a file path; a line span (`L12-40`) for
definitions; a short SHA for commits, ownership and introducing commits; a PR number and merge
SHA for PRs; and the build commit in the header. The agent's bullets cite those through the
evidence gate, and a re-run at a new commit is incremental: rebuild only when HEAD moved.

**Implementation notes, each one a measured failure.**
- Read text with CRLF normalised, take sizes from `git ls-tree -l`, and resolve symlinks
  through git. Otherwise a Windows clone gives different bytes: README text, tree sizes, and
  `jd/tenacity`'s symlinked README read as its target path.
- Sort everything that feeds the ranking. Tree-sitter capture order and set iteration both
  varied across processes.
- Bound the map on large repos: at most 3,000 source files (the user's first), per-file
  reference counts, and no names with more than 20 definers. Kubernetes went from 7.5 GB and
  147 s to 532 MB and 34 s.
- Bound blame: only the files behind the top-ranked definitions (40 here), files under 6,000
  lines, 25 `git log -L` lookups, and run them in parallel. Kubernetes' deep history costs about
  75 s for 40 files. Never use `--since` (it invents boundary owners). Skip blame in
  single-author repos.
- **Identity before counting.** Resolve the user's commits from every email on the GitHub
  account, the repo's `.mailmap` and the account's display names, not only the emails GitHub
  links to the login. #397's resolver found 91 of a user's 143 commits.
- Leave out tests when listing "built by others": they crowd out features.
- **Prompt side (#401's skill text, not the dossier).** When a definition is tagged
  `mostly <other>` with `you N%`, write "extended" or "reworked", not "built".

## 7. Contributed-to repos (#399)

The same dossier, restricted to the user's part:

- **Selection.** The symbol map is seeded only by the files the user's merged PRs and commits
  touched. Blame and introducing commits are computed as in owned repos, and "built by others"
  stays: in someone else's repo it is the most useful section.
- **PRs section, required.** The user's merged PRs come from GraphQL `contributionsCollection`
  and the merged-PR search (`is:pr author:<user> is:merged`, 1,000 results a query, 30 searches
  a minute). For each PR: title, number, merge SHA, size, body excerpt and files. PRs lowered
  unsupported claims by 1.5 points and raised accuracy by 2.5 here. Squash merges carry
  `Co-authored-by` trailers since 2019, so credit the trailers too.
- **No whole-repo README claims about the user.** The README describes the project, not the
  user's part. Keep it, since the writer needs context, but the PR and ownership sections carry
  all credit.
- **Evidence from this eval is thin.** Two owner-contributed repos (2 and 4 authors) were scored
  as full-repo dossiers, and unsupported claims ran 7–15% on them at every cell. The PR-only
  dossier #399 specifies was not built or scored here. Score it on these two repos and the two
  public contributor repos when #399 lands.

## 8. Open questions for the owner

1. **The judge is strict on extended work.** 10 of the 17 hand-check disagreements were features
   another author introduced and the user substantially reworked. Should a resume claim "built
   X" for such work count as wrong (the judge's view) or right (the hand checks')? The answer
   sets the skill's wording rule in section 6.
2. **The Pygments coverage gain** meets the letter of the pre-set rule, but its cause is a
   smaller symbol map. This report does not adopt it. Confirm.
3. **Universal Ctags as an optional enhancer** found on PATH: worth supporting in #398, given
   it measured no better than regex? This report says no.
4. **Budget in real tokens.** Is a 20K cap that ran to 22.8K once acceptable, or should the
   character rate be 2.1 so the cap holds strictly?

## 9. Sources

Structural maps and packers
- Aider repo map: <https://aider.chat/2023/10/22/repomap.html>, source
  <https://github.com/Aider-AI/aider/blob/main/aider/repomap.py>, queries and credits
  <https://github.com/Aider-AI/aider/blob/main/aider/queries/tree-sitter-languages/README.md>,
  <https://github.com/Aider-AI/aider/tree/main/aider/queries/tree-sitter-language-pack>; package
  pins <https://pypi.org/pypi/aider-chat/json>; open freeze report
  <https://github.com/Aider-AI/aider/issues/3297>
- grep-ast: <https://pypi.org/pypi/grep-ast/json>, <https://github.com/Aider-AI/grep-ast>
- tree-sitter-language-pack: <https://pypi.org/pypi/tree-sitter-language-pack/json>,
  <https://github.com/xberg-io/tree-sitter-language-pack>, download model
  <https://docs.tree-sitter-language-pack.xberg.io/concepts/download-model/>, release assets
  <https://github.com/xberg-io/tree-sitter-language-pack/releases/tag/v1.22.1>
- tree-sitter-languages (abandoned): <https://pypi.org/pypi/tree-sitter-languages/json>,
  <https://github.com/grantjenks/py-tree-sitter-languages/issues>
- Grammar wheels: <https://pypi.org/pypi/tree-sitter/json>, <https://pypi.org/pypi/tree-sitter-python/json>
  (and `tree-sitter-<lang>`)
- Universal Ctags: <https://docs.ctags.io/en/latest/man/ctags.1.html>,
  <https://docs.ctags.io/en/latest/man/ctags-json-output.5.html>,
  <https://github.com/universal-ctags/ctags>, <https://github.com/universal-ctags/ctags-win32/releases>,
  <https://github.com/universal-ctags/ctags-nightly-build>, <https://formulae.brew.sh/formula/universal-ctags>,
  GPL FAQ <https://www.gnu.org/licenses/gpl-faq.html>
- Serena / LSP: <https://github.com/oraios/serena>,
  <https://raw.githubusercontent.com/oraios/serena/main/LICENSE>,
  <https://raw.githubusercontent.com/oraios/serena/main/docs/01-about/020_programming-languages.md>,
  <https://pypi.org/pypi/serena-agent/json>, <https://github.com/microsoft/multilspy>
- SCIP: <https://github.com/sourcegraph/scip>, <https://sourcegraph.com/blog/announcing-scip>,
  <https://github.com/sourcegraph/scip-python>, <https://registry.npmjs.org/@sourcegraph/scip-typescript>
- Python `ast`: <https://docs.python.org/3/library/ast.html>; griffe <https://pypi.org/pypi/griffelib/json>
- Pygments: <https://pypi.org/pypi/pygments/json>
- Repomix: <https://repomix.com/guide/code-compress>, <https://repomix.com/guide/configuration>,
  <https://registry.npmjs.org/repomix>; Gitingest <https://github.com/coderamp-labs/gitingest>,
  <https://github.com/coderamp-labs/gitingest/blob/main/src/gitingest/config.py>; code2prompt
  <https://github.com/mufeedvh/code2prompt>; files-to-prompt <https://github.com/simonw/files-to-prompt>

Graphs and ceilings
- RepoGraph: <https://arxiv.org/html/2410.14684v2>, <https://github.com/ozyyshr/RepoGraph>
- Codebase-Memory: <https://arxiv.org/html/2603.27277v1>, <https://github.com/DeusData/codebase-memory-mcp>
- CodexGraph <https://arxiv.org/html/2408.03910v1>; code-graph-rag <https://github.com/vitali87/code-graph-rag>;
  AST- vs LLM-built graphs <https://arxiv.org/abs/2601.08773v1>; Repository Intelligence Graph
  <https://arxiv.org/abs/2601.10112>
- DeepWiki <https://docs.devin.ai/work-with-devin/deepwiki>; deepwiki-open
  <https://github.com/AsyncFuncAI/deepwiki-open>; RepoAgent <https://arxiv.org/html/2402.16667>;
  CodeWiki <https://arxiv.org/html/2510.24428>, <https://github.com/FSoft-AI4Code/CodeWiki>;
  DocAgent <https://arxiv.org/html/2504.08725>; AOCI <https://arxiv.org/abs/2605.02421>

Attribution
- git: blame <https://git-scm.com/docs/git-blame>, log <https://git-scm.com/docs/git-log>,
  gitattributes (funcname drivers) <https://git-scm.com/docs/gitattributes>, userdiff drivers
  <https://raw.githubusercontent.com/git/git/master/userdiff.c>, mailmap <https://git-scm.com/docs/gitmailmap>
- Blame cost reports: <https://groups.google.com/a/chromium.org/g/chromium-dev/c/oYe69KzyG_U/m/PAXryur3BQAJ>,
  <https://gitlab.com/gitlab-org/gitlab/-/issues/586593>,
  <https://public-inbox.org/git/58E1F239020000A100025732@gwsmtp1.uni-regensburg.de/> (via search summary)
- Meng et al., accurate authorship: <https://ftp1.cs.wisc.edu/par-distr-sys/papers/Meng13Authorship.pdf>
- Fritz et al., degree of knowledge: <https://www.cs.ubc.ca/sites/default/files/tr/2009/TR-2009-13_0.pdf>
- Avelino et al., truck factor (DOA vs blame): <https://arxiv.org/pdf/1604.06766>; and
  IEEE Software 2018: <https://homepages.dcc.ufmg.br/~mtov/pub/2018-ieeesw.pdf>
- Cury et al. ESEM 2022: <https://arxiv.org/pdf/2208.07501>
- Bird et al. 2011: <https://www.microsoft.com/en-us/research/?p=161465>; replication
  <https://www.microsoft.com/en-us/research/publication/code-ownership-and-software-quality-a-replication-study/>
- Girba et al. 2005: <https://scg.unibe.ch/assets/archive/papers/Girb05cOwnershipMap.pdf>
- Tools: git-fame <https://github.com/casperdcl/git-fame>, git-who <https://github.com/sinclairtarget/git-who>,
  Hercules <https://github.com/src-d/hercules>, code-maat <https://github.com/adamtornhill/code-maat>,
  cregit <https://github.com/cregit/cregit>
- Conventional Commits <https://www.conventionalcommits.org/en/v1.0.0/>; adoption
  <https://arxiv.org/pdf/2408.14431>; tangled changes
  <https://www.st.cs.uni-saarland.de/publications/details/herzig-msr-2013>
- GitHub: search <https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28>, pulls
  <https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28>, squash attribution
  <https://github.blog/changelog/2019-12-19-improved-attribution-when-squashing-commits/>,
  co-authors <https://docs.github.com/en/pull-requests/committing-changes-to-your-project/creating-and-editing-commits/creating-a-commit-with-multiple-authors>
