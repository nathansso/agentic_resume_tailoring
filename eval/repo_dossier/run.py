"""Run the repo-map sufficiency spike (issue #397). See README.md in this folder.

    python -m eval.repo_dossier.run build     # clone-free: build every rung, print sizes and a cost estimate
    python -m eval.repo_dossier.run run       # writer + judge + coverage (cached; stops at --limit)
    python -m eval.repo_dossier.run spotcheck # draw claims for a hand check
    python -m eval.repo_dossier.run report    # aggregate metrics -> eval/results/repo_dossier_<date>.{md,json}

Private inputs and outputs (the owner's repos, emails, reference facts, every per-repo output,
claim and label) live in `--work-dir` (default `personal/eval_397/`, gitignored). Only aggregates
and examples from the public repos reach `eval/results/`.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.repo_dossier.ladder import RUNGS, RUNG_NAMES, RepoInput, build_ladder, judge_context  # noqa: E402

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


def repo_input(r: dict, args) -> RepoInput:
    """`user_emails` (the private config, gitignored) or `user_logins` (the committed public
    config), resolved to emails at run time."""
    work = Path(args.work_dir)
    meta = fetch_meta(r["name"], work / "meta")
    clone = Path(args.clones) / r["dir"]
    emails = r.get("user_emails") or resolve_author_emails(r["name"], r.get("user_logins", []),
                                                           clone, work / "meta")
    return RepoInput(clone, r["name"], r["user_label"], emails, meta)


def cmd_build(args) -> None:
    repos = load_repos(args)
    out = Path(args.work_dir) / "dossiers"
    total_w = total_j = 0.0
    print(f"{'repo':42} " + " ".join(f"{r:>7}" for r in RUNGS) + "   judge(K tok)")
    for r in repos:
        ri = repo_input(r, args)
        ladder = build_ladder(ri)
        evidence, ev_stats = judge_context(ri)
        d = out / r["dir"]
        d.mkdir(parents=True, exist_ok=True)
        for rung, text in ladder.rungs.items():
            (d / f"rung_{rung}.md").write_text(text, encoding="utf-8")
        (d / "judge_evidence.md").write_text(evidence, encoding="utf-8")
        (d / "stats.json").write_text(json.dumps({"ladder": ladder.stats, "judge": ev_stats}, indent=1),
                                      encoding="utf-8")
        toks = {k: len(v) / CHARS_PER_TOKEN for k, v in ladder.rungs.items()}
        jt = len(evidence) / CHARS_PER_TOKEN
        print(f"{r['name']:42} " + " ".join(f"{toks[k] / 1000:6.1f}K" for k in RUNGS)
              + f"   {jt / 1000:6.1f}  trunc={ladder.stats['full_code']['truncated']}"
              f"/{ev_stats['truncated']} sym={ladder.stats['symbol_backend']}")
        # writer: input + ~2.5K output (thinking incl.) per call; judge: one cache write, the
        # rest cache reads, ~5K output each; coverage is small
        n = len(RUNGS) * args.samples
        total_w += sum(toks.values()) * args.samples * 2 / 1e6 + n * 2500 * 10 / 1e6
        total_j += jt * 5 / 1e6 + (n - 1) * jt * 0.2 / 1e6 + n * 6000 * 20 / 1e6
        if r.get("facts"):
            total_j += n * (1500 * 4 + 1500 * 20) / 1e6
    print(f"\nestimated spend: writer ${total_w:.2f}, judge+coverage ${total_j:.2f}, "
          f"total ${total_w + total_j:.2f} (limit ${args.limit:.2f})")


def cmd_run(args) -> None:
    from eval.repo_dossier.llm import Client, Ledger
    work = Path(args.work_dir)
    ledger = Ledger(args.limit)
    client = Client(work / "cache", ledger, ROOT / ".env" if not args.env else Path(args.env))
    repos = load_repos(args)
    results_path = work / "raw_results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if results_path.exists() else {}

    def one_repo(r):
        d = work / "dossiers" / r["dir"]
        evidence = (d / "judge_evidence.md").read_text(encoding="utf-8")
        jobs = [(rung, s) for rung in RUNGS for s in range(args.samples)]
        written = {}
        with ThreadPoolExecutor(4) as pool:
            futs = {job: pool.submit(client.write, r["name"], r["user_label"],
                                     (d / f"rung_{job[0]}.md").read_text(encoding="utf-8"), job[1])
                    for job in jobs}
            for job, f in futs.items():
                written[job] = f.result()
        # the first judge call writes the evidence cache; the rest read it
        judged = {}
        first = jobs[0]
        judged[first] = client.judge(r["name"], r["user_label"], evidence, written[first][0])
        with ThreadPoolExecutor(4) as pool:
            futs = {job: pool.submit(client.judge, r["name"], r["user_label"], evidence, written[job][0])
                    for job in jobs[1:]}
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
        return r["name"], {"group": r["group"], "rows": rows, "n_facts": len(r.get("facts", []))}

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
            "role_bad": sum(1 for x in role if x["label"] != "supported")}


def _coverage(facts: list[dict], n_facts: int) -> float:
    score = {"full": 1.0, "partial": 0.5, "none": 0.0}
    return sum(score[f["recovered"]] for f in facts) / max(1, n_facts)


def per_repo_metrics(results: dict) -> dict:
    """repo -> rung -> metrics averaged over samples."""
    out = {}
    for name, res in results.items():
        by_rung = {}
        for rung in RUNGS:
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


def aggregate(per_repo: dict) -> dict:
    agg = {}
    for rung in RUNGS:
        rows = [v["rungs"][rung] for v in per_repo.values() if rung in v["rungs"]]
        if not rows:
            continue
        a = {"repos": len(rows)}
        for k in ("accuracy", "unsupported", "contradicted", "contradicted_fact", "contradicted_role",
                  "input_tokens", "claims"):
            a[k] = statistics.mean(r[k] for r in rows)
        a["input_tokens_max"] = max(r["input_tokens"] for r in rows)
        role_n = sum(r["role_claims"] for r in rows)
        a["role_claim_error"] = sum(r["role_bad"] for r in rows) / role_n if role_n else None
        cov = [r["coverage"] for r in rows if "coverage" in r]
        if cov:
            a["coverage"] = statistics.mean(cov)
            a["coverage_repos"] = len(cov)
        # paired differences against full code, over repos
        full = {n: v["rungs"]["7"] for n, v in per_repo.items() if "7" in v["rungs"] and rung in v["rungs"]}
        du = [per_repo[n]["rungs"][rung]["unsupported"] - full[n]["unsupported"] for n in full]
        a["unsupported_vs_full"] = statistics.mean(du)
        a["unsupported_vs_full_ci"] = _boot_ci(du)
        dc = [per_repo[n]["rungs"][rung]["coverage"] - full[n]["coverage"] for n in full
              if "coverage" in full[n] and "coverage" in per_repo[n]["rungs"][rung]]
        if dc:
            a["coverage_vs_full"] = statistics.mean(dc)
            a["coverage_vs_full_ci"] = _boot_ci(dc)
        agg[rung] = a
    full = agg.get("7", {})
    for rung, a in agg.items():
        if "coverage" in a and full.get("coverage"):
            a["coverage_ratio"] = a["coverage"] / full["coverage"]
        a["passes"] = (a.get("coverage_ratio", 0) >= 0.95
                       and a["unsupported_vs_full_ci"][0] <= 0)
    return agg


def cmd_spotcheck(args) -> None:
    """Draw 20 claims for a hand check: up to 6 unsupported, up to 6 contradicted, the rest
    supported. Public repos only unless --include-private."""
    work = Path(args.work_dir)
    results = json.loads((work / "raw_results.json").read_text(encoding="utf-8"))
    pool = []
    for name, res in results.items():
        if res["group"] != "public" and not args.include_private:
            continue
        for row in res["rows"]:
            for i, c in enumerate(row["claims"]):
                pool.append({"repo": name, "rung": row["rung"], "sample": row["sample"], "i": i, **c})
    rng = random.Random(397)
    picks = []
    for lab in ("unsupported", "contradicted"):  # oversample the rarer labels
        cands = [p for p in pool if p["label"] == lab]
        rng.shuffle(cands)
        picks += cands[:6]
    cands = [p for p in pool if p["label"] == "supported"]
    rng.shuffle(cands)
    picks += cands[: 20 - len(picks)]
    path = work / "spotcheck.json"
    if path.exists() and not args.force:
        print(f"{path} exists; --force to redraw")
        return
    for p in picks:
        p["my_label"] = None
        p["my_note"] = ""
    path.write_text(json.dumps(picks, indent=1), encoding="utf-8")
    print(f"wrote {len(picks)} claims to {path}")


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
    spot = json.loads((work / "spotcheck.json").read_text(encoding="utf-8")) if (work / "spotcheck.json").exists() else []
    checked = [s for s in spot if s.get("my_label")]
    agree = sum(1 for s in checked if s["my_label"] == s["label"])
    public = {n: v for n, v in per_repo.items() if v["group"] == "public"}
    examples = {rung: [e for n in public if (e := _example(results, n, rung)) and e["failing_claims"]]
                for rung in RUNGS}
    stats = {d.name: json.loads((d / "stats.json").read_text(encoding="utf-8"))
             for d in (work / "dossiers").iterdir()}
    scored_dirs = [n.replace("/", "__") for n in results]
    out = {
        "issue": 397, "date": args.date, "writer_model": "claude-sonnet-5-5", "judge_model": "claude-opus-5-5",
        "samples_per_cell": max((x["sample"] for v in results.values() for x in v["rows"]), default=0) + 1,
        "configured_repos": len(load_repos(args)), "scored_repos": len(results),
        "repos": {"owner_and_contributed": sum(1 for v in per_repo.values() if v["group"] != "public"),
                  "public": len(public),
                  "with_reference_facts": sum(1 for v in results.values() if v.get("n_facts"))},
        "rungs": {r: RUNG_NAMES[r] for r in RUNGS},
        "aggregate": agg,
        "aggregate_by_group": {
            "owner_and_contributed": aggregate({n: v for n, v in per_repo.items() if v["group"] != "public"}),
            "public": aggregate(public),
        },
        "public_per_repo": public,
        "judge_spotcheck": {
            "checked": len(checked), "agree": agree,
            "rate": agree / len(checked) if checked else None,
            # agreement on supported vs not, the distinction the metrics rest on
            "agree_supported_vs_not": sum(1 for s in checked
                                          if (s["my_label"] == "supported") == (s["label"] == "supported")),
            "judge_labels": {lab: sum(1 for s in checked if s["label"] == lab)
                             for lab in ("supported", "unsupported", "contradicted")},
            "drawn_from": sorted({s["repo"] if s["repo"] in public else "(owner/contributed)" for s in checked}),
        },
        "spend_usd": round(spend["usd"], 2),
        "spend_by_model": spend["by_model"],
        # over the scored repos only, and as counts, so no private repo is named
        "symbol_backends": dict(Counter(stats[d]["ladder"]["symbol_backend"] for d in scored_dirs)),
        "full_code_truncated": sum(1 for d in scored_dirs if stats[d]["ladder"]["full_code"]["truncated"]),
        "judge_evidence_truncated": sum(1 for d in scored_dirs if stats[d]["judge"]["truncated"]),
    }
    res_dir = ROOT / "eval" / "results"
    res_dir.mkdir(exist_ok=True)
    (res_dir / f"repo_dossier_{args.date}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (work / "examples.json").write_text(json.dumps(examples, indent=1), encoding="utf-8")
    print(json.dumps({"aggregate": agg, "spotcheck": out["judge_spotcheck"], "spend": out["spend_usd"]}, indent=1))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["build", "run", "spotcheck", "report"])
    p.add_argument("--work-dir", default=str(ROOT / "personal" / "eval_397"))
    p.add_argument("--clones", default=str(ROOT / "personal" / "eval_397" / "clones"),
                   help="folder holding one clone per repo, named as each config entry's 'dir'")
    p.add_argument("--only", default="", help="comma-separated owner/repo names")
    p.add_argument("--samples", type=int, default=1)
    p.add_argument("--limit", type=float, default=25.0, help="stop before spending past this (USD)")
    p.add_argument("--env", default="", help=".env file holding ANTHROPIC_API_KEY (default: repo .env)")
    p.add_argument("--date", default=date.today().isoformat())
    p.add_argument("--include-private", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    {"build": cmd_build, "run": cmd_run, "spotcheck": cmd_spotcheck, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    main()
