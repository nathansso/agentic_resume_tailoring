"""Semantic vs literal coverage on the scripted host's pages (issue #126).

For a handful of benchmark tasks the scripted host (`eval/scripted_host.py`) opens the job, plans it
and commits a page. This asks, of each final page, how its literal `coverage` compares with
`semantic_coverage`, and where the two disagree per requirement:

    python eval/coverage_benchmark.py record     # live Jev, once, needs TYPESAFE_API_KEY
    python eval/coverage_benchmark.py analyze    # offline: replay the recordings, write BENCHMARK.md

The plan runs with Jev off, so the page and the literal figure are exactly what they are without a
key. Only the final page's bullets are then put to the coverage checker, each against every
required and preferred requirement of the posting (about 10 bullets by the posting's requirements:
a few hundred questions in all). `record` answers through ART's engine against a **private** SQLite
store and exports the cache to `eval/coverage_labels/benchmark_recordings.json`; `analyze` replays it
in `replay` mode and refuses to report unless the hit rate is 100%.

The scripted host's requirements are the lines under a posting's requirement headings, and their
`terms` are every keyword in the line, so a requirement has many terms and some are incidental
("degree", "team"). A requirement therefore counts as literally present here when **at least half**
of its terms are on the page, not when any is: with any, almost every requirement would be "present".
A real host supplies a few specific terms per requirement (`ingest_schema("requirement")`), so the
per-requirement disagreement it sees is sharper than this measures. Read the score-level numbers, and
the per-requirement ones as an upper bound on noise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.fit_support_threshold import _pct, _pin_store, _table  # noqa: E402  the shared helpers

OUT_DIR = Path(__file__).resolve().parent / "coverage_labels"
RECORDINGS_PATH = OUT_DIR / "benchmark_recordings.json"
REPORT_PATH = OUT_DIR / "BENCHMARK.md"
DEFAULT_RECORD_DIR = Path(tempfile.gettempdir()) / "art_coverage_benchmark"

# A handful of postings across the benchmark's role families; the first five are the smallest.
TASK_IDS = (
    "9to9_software_solutions_entry_level_software_developer",
    "arondite_ai_engineer",
    "assistrx_data_engineer",
    "accenture_federal_services_azure_data_engineer_in_st_louis_mo",
    "artefact_us_junior_data_engineer_canada_based",
    "abbott_associate_software_engineer",
    "accenture_federal_services_jr_data_scientist",
    "arizent_data_ai_engineer",
)
LITERAL_SHARE = 0.5     # a requirement is literally present when this share of its terms is on the page


def _one_line_each(texts):
    return [1 for _ in texts]


def _bullets(content: Dict) -> List[str]:
    from agents.redundancy import bullet_texts
    return bullet_texts(content)


def final_pages() -> List[Dict[str, Any]]:
    """Run each task through the scripted host with Jev off and return its page, literal score
    and eligible requirements. Seeds the profile into the (private) store once."""
    from eval.profile_fixture import load_profile
    from eval.scripted_host import build_program, open_task_job, seed_profile
    from eval.tailoring_benchmark import DEFAULT_PROFILE, load_tasks
    from harness import executor, tree
    from harness.contract import invoke
    from harness.decisions.coverage import eligible_requirements

    executor.MEASURER = _one_line_each                      # no LaTeX engine needed
    os.environ["ART_JEV_MODE"] = "off"
    uid = seed_profile(load_profile(DEFAULT_PROFILE))
    tasks = {t["id"]: t for t in load_tasks(list(TASK_IDS), limit=0)}
    pages = []
    for tid in TASK_IDS:
        task = tasks[tid]
        job_id = open_task_job(uid, task)
        out = invoke("execute_plan", uid, {"program": build_program(uid, job_id, task["description"])})
        if not out.get("committed"):
            raise RuntimeError(f"{tid}: the plan did not commit: {out.get('violations') or out.get('error')}")
        head = tree.get_head(uid, UUID(job_id))["head"]
        reqs = executor.job_requirements(uid, UUID(job_id))
        pages.append({"task": tid, "title": task["title"], "content": head["content"], "requirements": reqs,
                      "eligible": len(eligible_requirements(reqs)),
                      "literal": out["metrics"]["final"]["targets"]["coverage"]})
    return pages


def measure(page: Dict[str, Any]) -> Dict[str, Any]:
    """Semantic coverage of one page, and its per-requirement comparison with the literal reading."""
    from harness.decisions import coverage

    checker = coverage.make_coverage_checker(page["requirements"])
    row = {"task": page["task"], "title": page["title"], "bullets": len(set(_bullets(page["content"]))),
           "requirements": page["eligible"], "literal": page["literal"], "semantic": None,
           "both": 0, "semantic_only": [], "literal_only": [], "neither": 0, "no_terms": 0, "covered": 0}
    if checker is None:
        return row
    result = checker(page["content"])
    if result["status"] != "checked":
        raise RuntimeError(f"{page['task']}: no Jev answer ({result['reason']})")
    row["semantic"], row["covered"] = result["score"], result["covered"]
    for r in result["requirements"]:
        if not r["terms"]:
            row["no_terms"] += 1
            continue
        seen = coverage.terms_on_page(page["content"], r["terms"])
        literal = len(seen["present"]) / len(r["terms"]) >= LITERAL_SHARE
        entry = {"requirement": r["requirement"], "text": coverage._short(r["text"], 90), "p": r["p"],
                 "terms": f"{len(seen['present'])}/{len(r['terms'])}"}
        if r["covered"] and literal:
            row["both"] += 1
        elif r["covered"]:
            row["semantic_only"].append(entry)
        elif literal:
            row["literal_only"].append(entry)
        else:
            row["neither"] += 1
    return row


def render(rows: Sequence[Dict[str, Any]], meta: Dict[str, Any]) -> str:
    measured = [r for r in rows if r["semantic"] is not None]
    tot = lambda k: sum(r[k] if isinstance(r[k], int) else len(r[k]) for r in measured)   # noqa: E731
    o: List[str] = ["# Semantic vs literal coverage on the scripted host's pages (issue #126)\n"]
    o.append("Generated by `python eval/coverage_benchmark.py analyze` from `benchmark_recordings.json`. "
             f"Recorded {meta['recorded']} with model {', '.join(meta['models'])}, question `{meta['question']}`, "
             f"`TAU_COVER` {meta['tau']}; replayed at {meta['hit_rate']} cache hit rate. "
             f"Profile: the benchmark's default candidate; {len(rows)} tasks (`TASK_IDS`).\n")
    o.append("Each page is the scripted host's committed page with Jev off, so the literal `coverage` is exactly what a run "
             "without a key reports. `semantic` is `semantic_coverage` on the same page: the criticality-weighted share of the "
             "posting's required and preferred requirements that some bullet shows. The two are separate targets and are "
             "never combined; this only compares them.\n")
    o.append("## By task\n")
    o.append(_table(["task", "bullets", "requirements", "literal coverage", "semantic coverage", "covered", "both", "semantic only",
                     "literal only", "neither", "no terms"],
                    [[f"`{r['task'][:48]}`", r["bullets"], r["requirements"], f"{r['literal']:.1f}",
                      "n/a" if r["semantic"] is None else f"{r['semantic']:.1f}", r["covered"], r["both"],
                      len(r["semantic_only"]), len(r["literal_only"]), r["neither"], r["no_terms"]] for r in rows]))
    n_req = sum(r["requirements"] for r in measured)
    disagree = tot("semantic_only") + tot("literal_only")
    compared = tot("both") + tot("semantic_only") + tot("literal_only") + tot("neither")
    o.append("")
    o.append(f"Across the {len(measured)} tasks with a requirement ({n_req} required or preferred requirements, "
             f"{compared} with terms to compare): **{tot('both')} both, {tot('semantic_only')} semantic only, "
             f"{tot('literal_only')} literal only, {tot('neither')} neither.** The two readings disagree on "
             f"{disagree} of {compared} ({_pct(disagree / compared) if compared else 'n/a'}). "
             f"A requirement is literally present here when at least {LITERAL_SHARE:.0%} of its terms are on the page.\n")
    gaps = [(r["literal"], r["semantic"]) for r in measured]
    o.append(f"Score level: literal coverage ranges {min(g[0] for g in gaps):.1f} to {max(g[0] for g in gaps):.1f}, semantic "
             f"{min(g[1] for g in gaps):.1f} to {max(g[1] for g in gaps):.1f}; they differ by more than 10 points on "
             f"{sum(abs(a - b) > 10 for a, b in gaps)} of {len(gaps)} pages. Literal coverage is over every keyword of the "
             "posting, semantic over its requirements, so the scales differ and only the gap and its direction matter.\n")
    for title, key, blurb in (
            ("Semantic only: a bullet shows it, most of its words are not on the page (keyword-weave candidates)", "semantic_only", ""),
            ("Literal only: most of its words are on the page, no bullet shows it (the stuffing signature)", "literal_only", "")):
        o.append(f"## {title}\n")
        body = [[f"`{r['task'][:30]}`", e["requirement"], e["text"], e["terms"], f"{e['p']:.2f}"] for r in measured for e in r[key]]
        o.append(_table(["task", "requirement", "text", "terms on page", "best p"], body) if body else "None.")
        o.append("")
    return "\n".join(o)


def _meta(doc: Dict[str, Any], hit_rate: Optional[float]) -> Dict[str, Any]:
    from harness.decisions.coverage import TAU_COVER, VERSION
    decisions = doc.get("decisions") or []
    return {"recorded": (doc.get("exported_at") or "n/a")[:10],
            "models": sorted({d.get("resolved_model") for d in decisions if d.get("resolved_model")}) or ["n/a"],
            "question": VERSION, "tau": TAU_COVER, "hit_rate": "n/a" if hit_rate is None else f"{hit_rate:.0%}"}


def cmd_record(args) -> int:
    _pin_store(Path(args.data_dir))
    pages = final_pages()
    os.environ["ART_JEV_MODE"] = "auto"
    from harness.decisions import engine, recordings
    from harness.decisions.coverage import POINT
    if engine.get_client() is None:
        print("no TYPESAFE_API_KEY in the environment or .env; nothing was recorded", file=sys.stderr)
        return 2
    engine.reset_stats()
    planned = sum(len(set(_bullets(p["content"]))) * p["eligible"] for p in pages)
    print(f"{len(pages)} pages, {planned} questions", flush=True)
    if planned > args.max_questions:
        print(f"refusing to record: {planned} questions is over --max-questions {args.max_questions}", file=sys.stderr)
        return 2
    for page in pages:
        row = measure(page)
        print(f"{row['task'][:50]}: literal {row['literal']}, semantic {row['semantic']}", flush=True)
    st = engine.stats().get(POINT, {})
    print(json.dumps({"live_answers": st.get("jev", 0), "cache_answers": st.get("cache", 0), "requests": st.get("requests", 0),
                      "input_tokens": round(st.get("input_tokens", 0)), "output_tokens": round(st.get("output_tokens", 0))}))
    doc = recordings.export_recordings()
    Path(args.recordings).write_text(json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(doc['decisions'])} recordings to {args.recordings}")
    return 0


def cmd_analyze(args) -> int:
    doc = json.loads(Path(args.recordings).read_text(encoding="utf-8"))
    _pin_store(Path(tempfile.mkdtemp(prefix="art_coverage_benchmark_analyze_")))
    pages = final_pages()
    os.environ["ART_JEV_MODE"] = "replay"
    os.environ.pop("TYPESAFE_API_KEY", None)
    from harness.decisions import engine, recordings
    from harness.decisions.client import JevReplayMiss
    from harness.decisions.coverage import POINT
    recordings.import_recordings(doc)
    engine.reset_stats()
    try:
        rows = [measure(p) for p in pages]
    except JevReplayMiss as exc:
        print(f"replay missed: {exc}", file=sys.stderr)
        return 2
    st = engine.stats()[POINT]
    if st["hit_rate"] != 1.0 or st["jev"] or st["fallback"]:
        print(f"replay was not a 100% cache hit: {st}", file=sys.stderr)
        return 2
    Path(args.report).write_text(render(rows, _meta(doc, st["hit_rate"])) + "\n", encoding="utf-8")
    print(f"replayed {len(rows)} pages, hit rate {st['hit_rate']:.0%}; wrote {args.report}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("record", cmd_record), ("analyze", cmd_analyze)):
        s = sub.add_parser(name)
        s.add_argument("--recordings", default=str(RECORDINGS_PATH))
        s.set_defaults(fn=fn)
        if name == "record":
            s.add_argument("--data-dir", default=str(DEFAULT_RECORD_DIR))
            s.add_argument("--max-questions", type=int, default=700, help="Refuse to record more live questions than this.")
        else:
            s.add_argument("--report", default=str(REPORT_PATH))
    args = p.parse_args(list(sys.argv[1:] if argv is None else argv))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
