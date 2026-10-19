"""Run the repo-map sufficiency spike (issue #397). See README.md in this folder.

    python -m eval.repo_dossier.run build     # build every rung and variant, print sizes and a cost estimate
    python -m eval.repo_dossier.run run       # writer + judge + coverage (cached; stops at --limit)
    python -m eval.repo_dossier.run spotcheck # draw claims for a hand check
    python -m eval.repo_dossier.run report    # aggregate metrics -> eval/results/repo_dossier_<date>.json
    python -m eval.repo_dossier.run determinism  # rebuild under 3 hash seeds, compare bytes
    python -m eval.repo_dossier.run bench --clones <dir-with-one-big-clone>  # time and memory

Private inputs and outputs (the owner's repos, emails, reference facts, every per-repo output,
claim and label) live in `--work-dir` (default `personal/eval_397/`, gitignored). Only aggregates
and examples from the public repos reach `eval/results/`.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.repo_dossier.ladder import RUNGS, RUNG_NAMES, RepoInput, build_ladder, judge_context  # noqa: E402
from eval.repo_dossier.variants import (BASELINE, VARIANT_NAMES, VARIANTS, build_variant,  # noqa: E402
                                        judge_prs_section, make_context)

CELLS = RUNGS + list(VARIANTS)
CELL_NAMES = {**RUNG_NAMES, **VARIANT_NAMES}

HERE = Path(__file__).resolve().parent
PUBLIC_CONFIG = HERE / "repos.json"
CHARS_PER_TOKEN = 3.6   # for estimates only; reported tokens come from the API's usage


def load_repos(args) -> list[dict]:
    repos = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))["repos"]
    private = Path(args.work_dir) / "repos_private.json"
    if private.exists():
        repos = json.loads(private.read_text(encoding="utf-8"))["repos"] + repos
    if args.only:
        repos = [r for r in repos if r["name"] in args.only.split(",")]
    return repos


def fetch_meta(name: str, cache: Path) -> dict:
    path = cache / (name.replace("/", "__") + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    def gh(endpoint):
        done = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, encoding="utf-8")
        return json.loads(done.stdout) if done.returncode == 0 else {}

    repo = gh(f"repos/{name}")
    meta = {
        "description": repo.get("description"), "topics": repo.get("topics") or [],
        "created_at": repo.get("created_at", ""), "pushed_at": repo.get("pushed_at", ""),
        "homepage": repo.get("homepage"), "stars": repo.get("stargazers_count"),
        "license": (repo.get("license") or {}).get("spdx_id"),
        "languages": gh(f"repos/{name}/languages"),
    }
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def _gh_json(endpoint: str):
    done = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, encoding="utf-8")
    return json.loads(done.stdout) if done.returncode == 0 and done.stdout.strip() else None


def resolve_author_emails(name: str, logins: list[str], clone: Path, cache: Path) -> list[str]:
    """The git author emails behind GitHub logins, so no email is ever written into the config.

    First the commits GitHub links to each login (`repos/{name}/commits?author=<login>`), then any
    git-log author whose name equals the account's display name. The mapping is cached in the
    gitignored work dir only.
    """
    path = cache / (name.replace("/", "__") + "__authors.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["emails"]
    emails: set[str] = set()
    for login in logins:
        for page in range(1, 11):
            commits = _gh_json(f"repos/{name}/commits?author={login}&per_page=100&page={page}") or []
            for c in commits:
                email = ((c.get("commit") or {}).get("author") or {}).get("email")
                if email:
                    emails.add(email)
            if len(commits) < 100:
                break
        display = ((_gh_json(f"users/{login}") or {}).get("name") or "").strip().lower()
        if display:
            log = subprocess.run(["git", "-C", str(clone), "log", "--format=%an|%ae"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
            for line in log.splitlines():
                author, _, email = line.partition("|")
                if author.strip().lower() == display and email:
                    emails.add(email)
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"logins": logins, "emails": sorted(emails)}, indent=1), encoding="utf-8")
    return sorted(emails)


def fetch_prs(name: str, logins: list[str], cache: Path) -> list[dict] | None:
    """The logins' merged PRs in `name` (title, body, size, merge commit, files), through `gh`,
    cached in the work dir. None if no login is configured."""
    if not logins:
        return None
    path = cache / (name.replace("/", "__") + "__prs.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    prs = []
    for login in logins:
        for page in range(1, 11):
            hits = _gh_json(f"search/issues?q=repo:{name}+author:{login}+is:pr+is:merged"
                            f"&per_page=100&page={page}") or {}
            items = hits.get("items", [])
            for it in items:
                n = it["number"]
                pr = _gh_json(f"repos/{name}/pulls/{n}") or {}
                files = []
                for fpage in range(1, 31):
                    batch = _gh_json(f"repos/{name}/pulls/{n}/files?per_page=100&page={fpage}") or []
                    files += [f["filename"] for f in batch]
                    if len(batch) < 100:
                        break
                prs.append({"number": n, "title": it["title"], "body": pr.get("body") or "",
                            "merged_at": pr.get("merged_at"), "additions": pr.get("additions", 0),
                            "deletions": pr.get("deletions", 0),
                            "merge_commit_sha": pr.get("merge_commit_sha"), "files": sorted(files)})
            if len(items) < 100:
                break
    prs.sort(key=lambda p: p["number"])
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prs, indent=1), encoding="utf-8")
    return prs


def repo_input(r: dict, args) -> RepoInput:
    """`user_emails` (the private config, gitignored) or `user_logins` (the committed public
    config), resolved to emails at run time."""
    work = Path(args.work_dir)
    meta = fetch_meta(r["name"], work / "meta")
    clone = Path(args.clones) / r["dir"]
    emails = r.get("user_emails") or resolve_author_emails(r["name"], r.get("user_logins", []),
                                                           clone, work / "meta")
    return RepoInput(clone, r["name"], r["user_label"], emails, meta)


def _estimate(toks: dict[str, float], jt: float, n_facts: int, samples: int) -> tuple[float, float]:
    """Writer and judge+coverage dollars for one repo's new cells (identical dossiers are cached,
    so they count once): writer input plus ~2.5K output; the judge one cache write per evidence
    and the rest cache reads, ~6K output each; coverage is small."""
    distinct = {round(t): t for t in toks.values()}
    n = len(distinct) * samples
    w = sum(distinct.values()) * samples * 2 / 1e6 + n * 2500 * 10 / 1e6
    j = jt * 5 / 1e6 + max(0, n - 1) * jt * 0.2 / 1e6 + n * 6000 * 20 / 1e6
    if n_facts:
        j += n * (1500 * 4 + 1500 * 20) / 1e6
    return w, j


def cmd_build(args) -> None:
    """Build the #397 ladder (kept as stored unless --rebuild, so its finished cells stay
    cached) and every variant, plus the judge's evidence. Prints sizes and a spend estimate for
    the cells `run` has not scored yet."""
    repos = load_repos(args)
    work = Path(args.work_dir)
    out = work / "dossiers"
    results_path = work / "raw_results.json"
    done = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}
    cells = _cells(args)
    total_w = total_j = 0.0
    print(f"{'repo':42} " + " ".join(f"{c:>8}" for c in cells) + "   judge(K tok)")
    for r in repos:
        ri = repo_input(r, args)
        d = out / r["dir"]
        d.mkdir(parents=True, exist_ok=True)
        if args.rebuild or not (d / "rung_1.md").exists():
            ladder = build_ladder(ri)
            evidence, ev_stats = judge_context(ri)
            for rung, text in ladder.rungs.items():
                (d / f"rung_{rung}.md").write_text(text, encoding="utf-8")
            (d / "judge_evidence.md").write_text(evidence, encoding="utf-8")
            (d / "stats.json").write_text(json.dumps({"ladder": ladder.stats, "judge": ev_stats}, indent=1),
                                          encoding="utf-8")
        prs = fetch_prs(r["name"], r.get("user_logins", []), work / "meta")
        ctx = make_context(ri, prs)
        vstats = {"head": ctx.head, "context_s": round(ctx.timings["context_s"], 3), "variants": {},
                  "prs": None if prs is None else len(prs)}
        for vid in VARIANTS:
            text, st = build_variant(ctx, vid)
            (d / f"var_{vid}.md").write_text(text, encoding="utf-8")
            vstats["variants"][vid] = st
        (d / "stats_variants.json").write_text(json.dumps(vstats, indent=1), encoding="utf-8")
        evidence = (d / "judge_evidence.md").read_text(encoding="utf-8")
        extra = judge_prs_section(prs, r["user_label"])
        (d / "judge_evidence_v2.md").write_text(evidence + ("\n\n" + extra if extra else ""),
                                                encoding="utf-8")
        toks = {c: len(_dossier(d, c)) / CHARS_PER_TOKEN for c in cells}
        scored = {x["rung"] for x in done.get(r["name"], {}).get("rows", [])}
        jt = len(evidence) / CHARS_PER_TOKEN
        todo_r = {c: t for c, t in toks.items() if c not in scored and c in RUNGS}
        todo_v = {c: t for c, t in toks.items() if c not in scored and c in VARIANTS}
        print(f"{r['name']:42} " + " ".join(f"{toks[c] / 1000:7.1f}K" for c in cells)
              + f"   {jt / 1000:6.1f}  new cells={len(todo_r) + len(todo_v)}")
        for todo in (todo_r, todo_v):
            if todo:
                w, j = _estimate(todo, jt, len(r.get("facts", [])), args.samples)
                total_w += w
                total_j += j
    print(f"\nestimated new spend: writer ${total_w:.2f}, judge+coverage ${total_j:.2f}, "
          f"total ${total_w + total_j:.2f} (limit ${args.limit:.2f})")


def _cells(args) -> list[str]:
    return [c for c in CELLS if not args.cells or c in args.cells.split(",")]


def _dossier(d: Path, cell: str) -> str:
    name = f"rung_{cell}.md" if cell in RUNGS else f"var_{cell}.md"
    return (d / name).read_text(encoding="utf-8")


def cmd_run(args) -> None:
    from eval.repo_dossier.llm import Client, Ledger
    work = Path(args.work_dir)
    ledger = Ledger(args.limit)
    client = Client(work / "cache", ledger, ROOT / ".env" if not args.env else Path(args.env))
    repos = load_repos(args)
    results_path = work / "raw_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}

    cells = _cells(args)

    def one_repo(r):
        d = work / "dossiers" / r["dir"]
        # the #397 rungs keep the evidence they were judged on; the variants' evidence adds the
        # user's merged PRs, whose bodies are material the writer may cite
        evidence = {"rung": (d / "judge_evidence.md").read_text(encoding="utf-8"),
                    "var": (d / "judge_evidence_v2.md").read_text(encoding="utf-8")}
        kind = {c: "rung" if c in RUNGS else "var" for c in cells}
        jobs = [(c, s) for c in cells for s in range(args.samples)]
        written = {}
        with ThreadPoolExecutor(4) as pool:
            futs = {job: pool.submit(client.write, r["name"], r["user_label"], _dossier(d, job[0]), job[1])
                    for job in jobs}
            for job, f in futs.items():
                written[job] = f.result()
        # per evidence, the first judge call writes the prompt cache; the rest read it
        judged = {}
        for ev in ("rung", "var"):
            group = [j for j in jobs if kind[j[0]] == ev]
            if not group:
                continue
            judged[group[0]] = client.judge(r["name"], r["user_label"], evidence[ev], written[group[0]][0])
            with ThreadPoolExecutor(4) as pool:
                futs = {job: pool.submit(client.judge, r["name"], r["user_label"], evidence[ev],
                                         written[job][0])
                        for job in group[1:]}
                for job, f in futs.items():
                    judged[job] = f.result()
        covered = {}
        if r.get("facts"):
            with ThreadPoolExecutor(4) as pool:
                futs = {job: pool.submit(client.coverage, r["facts"], written[job][0]) for job in jobs}
                for job, f in futs.items():
                    covered[job] = f.result()
        rows = []
        for job in jobs:
            rung, s = job
            out, wu, _ = written[job]
            claims = judged[job][0]["claims"]
            row = {"rung": rung, "sample": s, "output": out, "writer_usage": wu,
                   "claims": claims, "judge_usage": judged[job][1]}
            if job in covered:
                row["coverage"] = covered[job][0]["facts"]
            rows.append(row)
        new = set(jobs)
        keep = [x for x in results.get(r["name"], {}).get("rows", []) if (x["rung"], x["sample"]) not in new]
        return r["name"], {"group": r["group"], "rows": keep + rows, "n_facts": len(r.get("facts", []))}

    for r in repos:
        try:
            name, res = one_repo(r)
        except Exception as exc:  # out of credit, over --limit, an API error: keep what is done
            print(f"{r['name']}: stopped ({type(exc).__name__}: {str(exc)[:200]}); "
                  "finished repos are saved and cached calls are reused on a rerun", flush=True)
            break
        results[name] = res
        results_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"{name}: done; spend so far ${ledger.usd:.2f} over {ledger.calls} new calls", flush=True)
    spend_path = work / "spend.json"
    prior = json.loads(spend_path.read_text(encoding="utf-8")) if spend_path.exists() else {"runs": []}
    prior["runs"].append({"usd": round(ledger.usd, 4), "calls": ledger.calls, "by_model": ledger.by_model})
    spend_path.write_text(json.dumps(prior, indent=1), encoding="utf-8")
    print(f"this run spent ${ledger.usd:.2f}")


# ── metrics ──────────────────────────────────────────────────────────────────

def _rates(claims: list[dict]) -> dict:
    n = len(claims) or 1
    c = {lab: sum(1 for x in claims if x["label"] == lab) for lab in ("supported", "unsupported", "contradicted")}
    role = [x for x in claims if x.get("about_user_role")]
    return {"claims": len(claims), "accuracy": c["supported"] / n, "unsupported": c["unsupported"] / n,
            "contradicted": c["contradicted"] / n,
            # the contradicted share split by kind: a wrong fact about the project, or the user
            # credited with work the history shows someone else did
            "contradicted_fact": sum(1 for x in claims if x["label"] == "contradicted"
                                     and not x.get("about_user_role")) / n,
            "contradicted_role": sum(1 for x in claims if x["label"] == "contradicted"
                                     and x.get("about_user_role")) / n,
            "role_claims": len(role),
            "role_bad": sum(1 for x in role if x["label"] != "supported"),
            # supported claims about the user's own part: what authorship is for
            "role_supported": sum(1 for x in role if x["label"] == "supported")}


def _coverage(facts: list[dict], n_facts: int) -> float:
    score = {"full": 1.0, "partial": 0.5, "none": 0.0}
    return sum(score[f["recovered"]] for f in facts) / max(1, n_facts)


def per_repo_metrics(results: dict) -> dict:
    """repo -> rung -> metrics averaged over samples."""
    out = {}
    for name, res in results.items():
        by_rung = {}
        for rung in CELLS:
            rows = [x for x in res["rows"] if x["rung"] == rung]
            if not rows:
                continue
            ms = [_rates(x["claims"]) for x in rows]
            m = {k: statistics.mean(v[k] for v in ms) for k in ms[0]}
            m["input_tokens"] = statistics.mean(x["writer_usage"]["input_tokens"] for x in rows)
            if res.get("n_facts") and all("coverage" in x for x in rows):
                m["coverage"] = statistics.mean(_coverage(x["coverage"], res["n_facts"]) for x in rows)
            by_rung[rung] = m
        out[name] = {"group": res["group"], "rungs": by_rung}
    return out


def _boot_ci(values: list[float], reps: int = 4000, seed: int = 397) -> tuple[float, float]:
    if len(values) < 2:
        return (values[0], values[0]) if values else (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choice(values) for _ in values) for _ in range(reps))
    return means[int(0.025 * reps)], means[int(0.975 * reps) - 1]


def _paired(per_repo: dict, cell: str, ref: str, key: str) -> tuple[float, tuple[float, float], int] | None:
    """Mean paired per-repo difference cell − ref on `key`, its 95% bootstrap interval, repos."""
    diffs = [v["rungs"][cell][key] - v["rungs"][ref][key] for v in per_repo.values()
             if cell in v["rungs"] and ref in v["rungs"]
             and key in v["rungs"][cell] and key in v["rungs"][ref]]
    if not diffs:
        return None
    return statistics.mean(diffs), _boot_ci(diffs), len(diffs)


def aggregate(per_repo: dict, cells: list[str] | None = None) -> dict:
    agg = {}
    for rung in cells or CELLS:
        rows = [v["rungs"][rung] for v in per_repo.values() if rung in v["rungs"]]
        if not rows:
            continue
        a = {"repos": len(rows)}
        for k in ("accuracy", "unsupported", "contradicted", "contradicted_fact", "contradicted_role",
                  "input_tokens", "claims", "role_supported"):
            a[k] = statistics.mean(r[k] for r in rows)
        # the headline attribution metric: the share of claims that credit the user with work
        # the history shows another author did
        a["misattribution"] = a["contradicted_role"]
        a["input_tokens_max"] = max(r["input_tokens"] for r in rows)
        role_n = sum(r["role_claims"] for r in rows)
        a["role_claims"] = role_n
        a["role_claim_error"] = sum(r["role_bad"] for r in rows) / role_n if role_n else None
        cov = [r["coverage"] for r in rows if "coverage" in r]
        if cov:
            a["coverage"] = statistics.mean(cov)
            a["coverage_repos"] = len(cov)
        # paired differences against full code, over repos
        if (pu := _paired(per_repo, rung, "7", "unsupported")) is not None:
            a["unsupported_vs_full"], a["unsupported_vs_full_ci"], _ = pu
        if (pc := _paired(per_repo, rung, "7", "coverage")) is not None:
            a["coverage_vs_full"], a["coverage_vs_full_ci"], _ = pc
        # and, for the variants, against the baseline variant at the same budget
        if rung in VARIANTS and rung != BASELINE:
            vs = {}
            for k in ("accuracy", "unsupported", "contradicted_role", "coverage", "role_supported",
                      "input_tokens"):
                if (d := _paired(per_repo, rung, BASELINE, k)) is not None:
                    vs[k] = {"mean": d[0], "ci": d[1], "repos": d[2]}
            a["vs_baseline"] = vs
        agg[rung] = a
    full = agg.get("7", {})
    for rung, a in agg.items():
        if "coverage" in a and full.get("coverage"):
            a["coverage_ratio"] = a["coverage"] / full["coverage"]
        a["passes"] = (a.get("coverage_ratio", 0) >= 0.95
                       and a.get("unsupported_vs_full_ci", (1, 1))[0] <= 0)
    return agg


def _family(cell: str) -> str:
    if cell in RUNGS:
        return "ladder"
    return "attribution" if VARIANTS[cell].layers else "structural"


def cmd_spotcheck(args) -> None:
    """Draw claims for a hand check, stratified: for each method family (the #397 ladder, the
    structural variants, the attribution variants), role claims and other claims, and within each
    supported and not supported, `--n` in all. Writes spotcheck_v2.json (the #397 draw of 20 in
    spotcheck.json is kept and counted too). Every repo is eligible: the labels stay in the work
    dir, and only agreement counts reach the results."""
    work = Path(args.work_dir)
    results = json.loads((work / "raw_results.json").read_text(encoding="utf-8"))
    path = work / "spotcheck_v2.json"
    if path.exists() and not args.force:
        print(f"{path} exists; --force to redraw")
        return
    old = json.loads((work / "spotcheck.json").read_text(encoding="utf-8")) if (work / "spotcheck.json").exists() else []
    taken = {(s["repo"], s["rung"], s["sample"], s["i"]) for s in old}
    strata: dict[tuple, list] = {}
    for name, res in sorted(results.items()):
        for row in res["rows"]:
            for i, c in enumerate(row["claims"]):
                if (name, row["rung"], row["sample"], i) in taken:
                    continue
                key = (_family(row["rung"]), bool(c.get("about_user_role")), c["label"] == "supported")
                strata.setdefault(key, []).append({"repo": name, "rung": row["rung"], "sample": row["sample"],
                                                   "i": i, **c})
    rng = random.Random(3970)
    keys = sorted(strata)
    per = max(1, args.n // max(1, len(keys)))
    picks = []
    for k in keys:
        pool = strata[k][:]
        rng.shuffle(pool)
        picks += pool[:per]
    leftovers = [x for k in keys for x in strata[k][per:]]
    rng.shuffle(leftovers)
    picks += leftovers[: max(0, args.n - len(picks))]
    for p in picks:
        p["stratum"] = list(_stratum(p))
        p["my_label"] = None
        p["my_note"] = ""
    path.write_text(json.dumps(picks, indent=1), encoding="utf-8")
    print(f"wrote {len(picks)} claims over {len(keys)} strata to {path}")


def _stratum(c: dict) -> tuple:
    return (_family(c["rung"]), bool(c.get("about_user_role")), c["label"] == "supported")


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def spotcheck_summary(checked: list[dict], results: dict) -> dict:
    """Agreement between the judge and the hand labels, raw (with Wilson intervals) and
    re-weighted to the label mix of all claims (the draw oversamples rare labels)."""
    def agree(s):
        return s["my_label"] == s["label"]

    def agree_sn(s):
        return (s["my_label"] == "supported") == (s["label"] == "supported")

    n = len(checked)
    k_exact = sum(map(agree, checked))
    k_sn = sum(map(agree_sn, checked))
    pop = Counter()
    for res in results.values():
        for row in res["rows"]:
            for c in row["claims"]:
                pop[_stratum({"rung": row["rung"], **c})] += 1
    by = defaultdict(list)
    for s in checked:
        by[_stratum(s)].append(s)
    tot = sum(pop[k] for k in by) or 1
    weighted = {name: sum(pop[k] / tot * statistics.mean(1.0 if f(s) else 0.0 for s in v)
                          for k, v in by.items())
                for name, f in (("exact", agree), ("supported_vs_not", agree_sn))}
    role = [s for s in checked if s.get("about_user_role")]
    out = {
        "checked": n, "agree": k_exact, "rate": k_exact / n if n else None, "rate_ci": _wilson(k_exact, n),
        "agree_supported_vs_not": k_sn, "rate_supported_vs_not": k_sn / n if n else None,
        "rate_supported_vs_not_ci": _wilson(k_sn, n),
        "reweighted_to_population": weighted,
        "role_claims": {"checked": len(role), "agree": sum(map(agree, role)),
                        "ci": _wilson(sum(map(agree, role)), len(role))},
        "judge_labels": {lab: sum(1 for s in checked if s["label"] == lab)
                         for lab in ("supported", "unsupported", "contradicted")},
        "by_family": {f: {"checked": len(v), "agree": sum(map(agree, v))}
                      for f in ("ladder", "structural", "attribution")
                      if (v := [s for s in checked if _family(s["rung"]) == f])},
        # where the judge and the hand label disagree, which way
        "confusion": dict(Counter(f"{s['label']}->{s['my_label']}" for s in checked if not agree(s))),
    }
    return out


def spend_from_cache(cache: Path) -> dict:
    """Every cached response is one billed call, including calls from a run that stopped early."""
    from eval.repo_dossier.llm import JUDGE_MODEL, WRITER_MODEL, Ledger
    ledger = Ledger(float("inf"))
    for f in sorted(cache.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        ledger.add(WRITER_MODEL if rec["tag"] == "write" else JUDGE_MODEL, rec["usage"])
    return {"usd": ledger.usd, "calls": ledger.calls,
            "by_model": {m: {k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()}
                         for m, d in ledger.by_model.items()}}


def _example(results: dict, name: str, rung: str) -> dict | None:
    rows = [x for x in results.get(name, {}).get("rows", []) if x["rung"] == rung and x["sample"] == 0]
    if not rows:
        return None
    row = rows[0]
    bad = [c for c in row["claims"] if c["label"] != "supported"]
    return {"repo": name, "rung": rung, "output": row["output"], "failing_claims": bad}


def cmd_report(args) -> None:
    work = Path(args.work_dir)
    results = json.loads((work / "raw_results.json").read_text(encoding="utf-8"))
    per_repo = per_repo_metrics(results)
    agg = aggregate(per_repo)
    spend = spend_from_cache(work / "cache")
    spot = []
    for f in ("spotcheck.json", "spotcheck_v2.json"):
        if (work / f).exists():
            spot += json.loads((work / f).read_text(encoding="utf-8"))
    checked = [s for s in spot if s.get("my_label")]
    public = {n: v for n, v in per_repo.items() if v["group"] == "public"}
    examples = {rung: [e for n in public if (e := _example(results, n, rung)) and e["failing_claims"]]
                for rung in CELLS}
    stats = {d.name: json.loads((d / "stats.json").read_text(encoding="utf-8"))
             for d in (work / "dossiers").iterdir() if (d / "stats.json").exists()}
    vstats = {d.name: json.loads((d / "stats_variants.json").read_text(encoding="utf-8"))
              for d in (work / "dossiers").iterdir() if (d / "stats_variants.json").exists()}
    scored_dirs = [n.replace("/", "__") for n in results]
    groups = {"owner": "owner", "contributed": "contributed", "public": "public", "multi": "multi"}
    out = {
        "issue": 397, "date": args.date, "writer_model": "claude-sonnet-5-5", "judge_model": "claude-opus-5-5",
        "samples_per_cell": max((x["sample"] for v in results.values() for x in v["rows"]), default=0) + 1,
        "configured_repos": len(load_repos(args)), "scored_repos": len(results),
        "repos": {g: sum(1 for v in per_repo.values() if v["group"] == g) for g in groups}
                 | {"with_reference_facts": sum(1 for v in results.values() if v.get("n_facts"))},
        "cells": {c: CELL_NAMES[c] for c in CELLS},
        "baseline": BASELINE,
        "aggregate": agg,
        "aggregate_by_group": {g: aggregate({n: v for n, v in per_repo.items() if v["group"] == g})
                               for g in groups if any(v["group"] == g for v in per_repo.values())},
        # authors > 10: where attribution is hard
        "aggregate_many_authors": aggregate({n: v for n, v in per_repo.items()
                                             if stats.get(n.replace("/", "__"), {}).get("ladder", {})
                                             .get("authors", 0) > 10}),
        "public_per_repo": public,
        "judge_spotcheck": spotcheck_summary(checked, results) if checked else None,
        "spend_usd": round(spend["usd"], 2),
        "spend_by_model": spend["by_model"],
        # over the scored repos only, and as counts, so no private repo is named
        "symbol_backends": {vid: dict(Counter(vstats[d]["variants"][vid]["symbol_backend"]
                                              for d in scored_dirs if d in vstats))
                            for vid in VARIANTS},
        "variant_est_tokens": {vid: statistics.mean(vstats[d]["variants"][vid]["est_tokens"]
                                                    for d in scored_dirs if d in vstats)
                               for vid in VARIANTS if any(d in vstats for d in scored_dirs)},
        "full_code_truncated": sum(1 for d in scored_dirs if d in stats and stats[d]["ladder"]["full_code"]["truncated"]),
        "judge_evidence_truncated": sum(1 for d in scored_dirs if d in stats and stats[d]["judge"]["truncated"]),
    }
    for f in ("determinism.json", "bench.json"):
        if (work / f).exists():
            out[f.removesuffix(".json")] = json.loads((work / f).read_text(encoding="utf-8"))
    res_dir = ROOT / "eval" / "results"
    res_dir.mkdir(exist_ok=True)
    (res_dir / f"repo_dossier_{args.date}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (work / "examples.json").write_text(json.dumps(examples, indent=1), encoding="utf-8")
    print(json.dumps({"aggregate": {c: {k: a.get(k) for k in ("accuracy", "unsupported", "misattribution",
                                                               "coverage", "input_tokens", "passes")}
                                    for c, a in agg.items()},
                      "spotcheck": out["judge_spotcheck"], "spend": out["spend_usd"]}, indent=1))


# ── determinism and the large-repo bench ─────────────────────────────────────

def _hashes(repos: list[dict], args) -> dict:
    """sha256 of every rung and variant built fresh for each repo (no files written)."""
    import hashlib
    out = {}
    work = Path(args.work_dir)
    for r in repos:
        ri = repo_input(r, args)
        ladder = build_ladder(ri)
        prs = fetch_prs(r["name"], r.get("user_logins", []), work / "meta")
        ctx = make_context(ri, prs)
        h = {f"rung_{k}": hashlib.sha256(v.encode()).hexdigest() for k, v in ladder.rungs.items()}
        for vid in VARIANTS:
            h[vid] = hashlib.sha256(build_variant(ctx, vid)[0].encode()).hexdigest()
        out[r["name"]] = h
    return out


def cmd_hashes(args) -> None:
    print(json.dumps(_hashes(load_repos(args), args), sort_keys=True))


def cmd_determinism(args) -> None:
    """Rebuild every rung and variant in fresh processes under three hash seeds and compare the
    bytes. With --os-label, the hashes are stored under that label, so a run on a second OS
    (e.g. WSL, `--os-label linux`) is compared with the first."""
    import os
    import platform
    work = Path(args.work_dir)
    runs = {}
    for seed in ("0", "1", "2"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        cmd = [sys.executable, "-m", "eval.repo_dossier.run", "hashes", "--work-dir", args.work_dir,
               "--clones", args.clones] + (["--only", args.only] if args.only else [])
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
        if done.returncode:
            raise SystemExit(done.stderr[-2000:])
        runs[seed] = json.loads(done.stdout.strip().splitlines()[-1])
    label = args.os_label or platform.system().lower()
    path = work / "determinism.json"
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"by_os": {}}
    store["by_os"][label] = {"python": platform.python_version(),
                             "git": subprocess.run(["git", "--version"], capture_output=True,
                                                   text=True).stdout.strip(),
                             "hashes": runs["0"],
                             "seeds_identical": all(runs[s] == runs["0"] for s in runs)}
    oses = sorted(store["by_os"])
    if len(oses) > 1:
        a, b = store["by_os"][oses[0]]["hashes"], store["by_os"][oses[1]]["hashes"]
        diff = sorted(f"{repo}:{cell}" for repo in a if repo in b for cell in a[repo]
                      if a[repo][cell] != b[repo].get(cell))
        store["cross_os"] = {"compared": oses[:2], "cells": sum(len(a[r]) for r in a if r in b),
                             "differing": diff}
    # repo names of private repos stay in the work dir; the report keeps counts only
    store["summary"] = {os_: {"seeds_identical": v["seeds_identical"], "cells": sum(len(h) for h in v["hashes"].values()),
                              "python": v["python"], "git": v["git"]} for os_, v in store["by_os"].items()}
    if "cross_os" in store:
        store["summary"]["cross_os_differing"] = len(store["cross_os"]["differing"])
        store["summary"]["cross_os_differing_cells"] = sorted({d.split(":", 1)[1] for d in store["cross_os"]["differing"]})
    path.write_text(json.dumps(store, indent=1), encoding="utf-8")
    print(json.dumps(store["summary"], indent=1))


def _peak_rss_run(cmd: list[str], env: dict) -> tuple[float, float, str]:
    """Wall seconds, peak resident MB of the process tree (sampled every 50 ms), stdout."""
    import threading
    import time

    import psutil
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", env=env, cwd=ROOT)
    peak = [0.0]
    stop = threading.Event()

    def watch():
        try:
            p = psutil.Process(proc.pid)
            while not stop.is_set():
                try:
                    rss = p.memory_info().rss + sum(c.memory_info().rss for c in p.children(recursive=True))
                    peak[0] = max(peak[0], rss / 2**20)
                except psutil.Error:
                    pass
                stop.wait(0.05)
        except psutil.Error:
            pass

    th = threading.Thread(target=watch, daemon=True)
    th.start()
    out, err = proc.communicate()
    stop.set()
    th.join()
    if proc.returncode:
        raise SystemExit(err[-2000:])
    return time.perf_counter() - t0, peak[0], out


def cmd_bench_one(args) -> None:
    """(internal) Build one variant for the one clone in --clones, its user the author ranked
    --bench-rank by commits; print timings as JSON. No email is printed."""
    import time
    clone = Path(args.clones)
    authors = Counter(subprocess.run(["git", "-C", str(clone), "log", "--no-merges", "--format=%ae"],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace").stdout.split())
    ranked = sorted(authors.items(), key=lambda kv: (-kv[1], kv[0]))
    email, commits = ranked[min(args.bench_rank, len(ranked)) - 1]
    ri = RepoInput(clone, clone.name.replace("__", "/"), "the benchmark author", [email], None)
    t0 = time.perf_counter()
    if args.cells in RUNGS:
        text = build_ladder(ri).rungs[args.cells]
        st = {}
    else:
        ctx = make_context(ri, [])
        t1 = time.perf_counter()
        text, st = build_variant(ctx, args.cells)
        st["context_s"] = round(t1 - t0, 2)
    st.update({"total_s": round(time.perf_counter() - t0, 2), "user_commits": commits,
               "files": len(ctx.files) if args.cells not in RUNGS else None,
               "est_tokens": round(len(text) / CHARS_PER_TOKEN)})
    print(json.dumps(st))


def cmd_bench(args) -> None:
    """Time and peak memory of each variant (and rung 5r) on a large clone, each in a fresh
    process. Results go to the work dir's bench.json."""
    import os
    clone = Path(args.clones)
    files = len(subprocess.run(["git", "-C", str(clone), "ls-files"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace").stdout.splitlines())
    commits = int(subprocess.run(["git", "-C", str(clone), "rev-list", "--count", "HEAD"],
                                 capture_output=True, text=True).stdout.strip() or 0)
    out = {"repo": clone.name.replace("__", "/"), "tracked_files": files, "commits": commits,
           "bench_rank": args.bench_rank, "cells": {}}
    for cell in (args.cells.split(",") if args.cells else ["5r"] + list(VARIANTS)):
        if cell == "own-20k" or cell == "5r-20k":
            continue
        cmd = [sys.executable, "-m", "eval.repo_dossier.run", "bench-one", "--clones", str(clone),
               "--cells", cell, "--bench-rank", str(args.bench_rank)]
        wall, peak, stdout = _peak_rss_run(cmd, dict(os.environ))
        st = json.loads(stdout.strip().splitlines()[-1])
        out["cells"][cell] = {"wall_s": round(wall, 1), "peak_mb": round(peak), **st}
        print(cell, out["cells"][cell], flush=True)
    path = Path(args.work_dir) / "bench.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["build", "run", "spotcheck", "report", "determinism", "hashes",
                                       "bench", "bench-one"])
    p.add_argument("--work-dir", default=str(ROOT / "personal" / "eval_397"))
    p.add_argument("--clones", default=str(ROOT / "personal" / "eval_397" / "clones"),
                   help="folder holding one clone per repo, named as each config entry's 'dir'")
    p.add_argument("--only", default="", help="comma-separated owner/repo names")
    p.add_argument("--cells", default="", help="comma-separated rungs/variants (default: all)")
    p.add_argument("--samples", type=int, default=1)
    p.add_argument("--limit", type=float, default=25.0, help="stop before spending past this (USD)")
    p.add_argument("--env", default="", help=".env file holding ANTHROPIC_API_KEY (default: repo .env)")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--n", type=int, default=60, help="spotcheck: claims to draw")
    p.add_argument("--rebuild", action="store_true", help="build: rebuild the stored #397 rungs too")
    p.add_argument("--os-label", default="", help="determinism: label for this OS's hashes")
    p.add_argument("--bench-rank", type=int, default=10, help="bench: the user is the author ranked N by commits")
    p.add_argument("--include-private", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    {"build": cmd_build, "run": cmd_run, "spotcheck": cmd_spotcheck, "report": cmd_report,
     "determinism": cmd_determinism, "hashes": cmd_hashes, "bench": cmd_bench,
     "bench-one": cmd_bench_one}[args.command](args)


if __name__ == "__main__":
    main()
