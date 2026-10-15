"""The preference compliance benchmark and its independent checker (issue #299).

The full run (`python eval/preference_compliance.py --profiles 5 --jobs 6`, about five minutes) is
not part of `run_tests.py`. What is here: the checker alone, that it shares no code with the
enforcement path, and a small run (1 profile, 2 jobs, a handful of the sets) that must show the
metrics the issue asks for and must be able to fail.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "eval" / "compliance_checker.py"


# ── isolation ────────────────────────────────────────────────────────────────

def test_the_compliance_checker_shares_no_code_with_the_enforcement_path():
    """It imports the standard library and nothing else: no `harness`, `agents`, `database`,
    `services` or `eval` module, so it cannot agree with the gates by sharing their mistakes."""
    tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import reaches into the package"
            imported.add(node.module or "")
    assert imported <= {"__future__", "re", "typing"}, imported
    for forbidden in ("harness", "harness.acceptance", "harness.decisions.negative_pins",
                      "harness.rules", "agents", "database", "services", "eval"):
        assert forbidden not in imported


def test_importing_the_checker_loads_none_of_the_enforcement_path():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('checker', r'{CHECKER}')\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "loaded = [m for m in sys.modules if m.split('.')[0] in "
        "('harness', 'agents', 'database', 'services', 'eval', 'sqlmodel')]\n"
        "assert not loaded, loaded\n")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ── the checker on a page ────────────────────────────────────────────────────

PAGE = r"""
\documentclass{article}
\begin{document}

%% ART-SECTION: header
\begin{center}
    \textbf{\Huge Ada \& Co} \\ \vspace{1pt}
\end{center}

%% ART-SECTION: skills
\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Languages \& Libraries}{: Python, C\#, scikit-learn} \\
     \textbf{Tools}{: Docker, K8s} \\
    }}
 \end{itemize}

%% ART-SECTION: experience
\section{Experience}
  \resumeSubHeadingListStart

    \resumeSubheading
      {Data Engineer}{Jan 2024 -- Present}
      {Acme \& Sons}{}
      \resumeItemListStart
        \resumeItem{Cut costs by 30\% with C\# and Kafka\_Streams.}
        \resumeItem{Built a Kubernetes operator for \textasciitilde{}40 clusters at Google.}
      \resumeItemListEnd

  \resumeSubHeadingListEnd

%% ART-SECTION: projects
\section{Projects}
    \resumeSubHeadingListStart
      \resumeProjectHeading
          {\textbf{Stream\_Board}}{}
          \resumeItemListStart
            \resumeItem{A dashboard in React.}
          \resumeItemListEnd
    \resumeSubHeadingListEnd

\end{document}
"""


def test_the_checker_reads_a_page_the_way_a_reader_would():
    from eval import compliance_checker as ck

    inv = ck.inventory(PAGE)
    assert inv["sections"] == ["skills", "experience", "projects"]
    assert inv["experiences"] == [{
        "title": "Data Engineer", "company": "Acme & Sons",
        "bullets": ["Cut costs by 30% with C# and Kafka_Streams.",
                    "Built a Kubernetes operator for ~40 clusters at Google."]}]
    assert inv["projects"] == [{"name": "Stream_Board", "bullets": ["A dashboard in React."]}]
    assert inv["skills"] == ["Python", "C#", "scikit-learn", "Docker", "K8s"]
    assert "Ada & Co" in inv["text"] and "\\" not in inv["text"] and "{" not in inv["text"]


def test_a_banned_term_is_a_whole_word_or_an_alias_never_a_substring():
    from eval import compliance_checker as ck

    inv = ck.inventory(PAGE)
    assert ck.mentions(inv["text"], "C#") == "c#"
    assert ck.mentions(inv["text"], "kafka_streams") == "kafka_streams"
    assert ck.mentions(inv["text"], "kubernetes") in ("kubernetes", "k8s")
    assert ck.mentions("Ran K8s clusters", "kubernetes") == "k8s"              # an alias, both ways
    assert ck.mentions("Ran Kubernetes clusters", "k8s") == "kubernetes"
    assert ck.mentions(inv["text"], "go") is None                              # "Google"
    assert ck.mentions(inv["text"], "r") is None                               # "React"
    assert ck.mentions("tuned a Go service", "go") == "go"
    assert ck.mentions("Kafka and Kafka Streams", "kafka streams") == "kafka streams"
    assert ck.mentions("PostgreSQL tuning", "postgres") == "postgresql"
    assert ck.mentions("30% faster", "30%") == "30%"                            # an escaped symbol


def test_each_expectation_is_judged_from_the_rendered_page():
    from eval import compliance_checker as ck

    expect = {
        "banned_terms": [{"term": "kubernetes", "source": "no kubernetes"},
                         {"term": "terraform", "source": "no terraform"}],
        "absent_items": [{"kind": "experience", "title": "Data Engineer", "company": "Acme & Sons",
                          "source": "no acme"},
                         {"kind": "project", "name": "Stream_Board", "source": "no board"},
                         {"kind": "project", "name": "Elsewhere", "source": "no elsewhere"}],
        "absent_sections": [{"name": "projects", "source": "no projects"},
                            {"name": "achievements", "source": "no awards"}],
        "section_order": {"order": ["experience", "skills"], "source": "experience first"},
        "max_bullets_per_item": {"value": 1, "source": "one bullet"},
        "max_skills": {"value": 4, "source": "four skills"},
        "required_items": [{"kind": "project", "name": "Missing", "source": "keep missing"}],
    }
    got = ck.check(PAGE, expect)
    by_check = {}
    for v in got["violations"]:
        by_check.setdefault(v["check"], []).append(v["source"])
    assert by_check == {
        "banned_term": ["no kubernetes"],
        "suppressed_item": ["no acme", "no board"],
        "excluded_section": ["no projects"],
        "section_order": ["experience first"],
        "bullets_per_item": ["one bullet"],
        "max_skills": ["four skills"],
        "required_item": ["keep missing"],
    }

    ok = ck.check(PAGE, {"section_order": {"order": ["skills", "experience"], "source": "x"},
                         "max_bullets_per_item": {"value": 2, "source": "x"},
                         "max_skills": {"value": 5, "source": "x"},
                         "absent_sections": [{"name": "achievements", "source": "x"}]})
    assert ok["violations"] == []


def test_the_page_count_is_measured_when_given_and_estimated_and_said_so_when_not():
    from eval import compliance_checker as ck

    expect = {"max_pages": {"value": 1, "source": "one page"}}
    assert ck.check(PAGE, expect, pages=1) == {
        "violations": [], "skipped": [], "inventory": ck.inventory(PAGE)}
    over = ck.check(PAGE, expect, pages=2)
    assert [v["check"] for v in over["violations"]] == ["max_pages"]
    unmeasured = ck.check(PAGE, expect, pages=None)
    assert unmeasured["skipped"] == [ck.SKIP_ENGINE] and unmeasured["violations"] == []
    long_page = PAGE.replace("A dashboard in React.", "word " * 40 * 70)
    assert [v["check"] for v in ck.check(long_page, expect)["violations"]] == ["max_pages_estimated"]


def test_a_page_with_no_markers_is_read_by_its_section_titles():
    from eval import compliance_checker as ck

    tex = (r"\begin{document}\section{Education} x \section{Projects} y "
           r"\section{Technical Skills} z \end{document}")
    assert ck.inventory(tex)["sections"] == ["education", "projects", "skills"]


# ── the benchmark ────────────────────────────────────────────────────────────

SMOKE_SETS = ["suppress_exp", "suppress_skill", "pending_ban", "family_pin", "rule_bullets",
              "supersede_allow"]


@pytest.fixture()
def store(isolated_engine):
    return isolated_engine


def test_a_small_run_holds_every_target_and_is_not_vacuous(store):
    """1 profile x 2 jobs x six of the sets, offline. The numbers the issue asks for: no hard
    violation rendered by the naive host, no leak, over-block within 2%, every supersede followed."""
    from eval import preference_compliance as pc

    report = pc.run_benchmark([pc.PROFILES[0]], 2, only=SMOKE_SETS, compile_pages=False)
    m = report["metrics"]
    nv, ob = m["naive"], m["obedient"]

    assert len({j["role_family"] for j in report["jobs"]}) == 2
    assert nv["traps_armed"] >= 8, "the control pages must violate, or nothing was tested"
    assert nv["hard_violations"] == 0, nv["findings"]
    assert nv["held"] + nv["refused"] + nv["unchanged"] == nv["traps_armed"]
    assert nv["refused"] > 0 and nv["held"] > 0                  # blocked by a gate, and held by one
    assert nv["scope_leak"]["leaks"] == 0 and ob["scope_leak"]["leaks"] == 0
    assert nv["scope_leak"]["checked"] > 0                       # family_pin has an out-of-scope job
    assert ob["hard_violations"] == 0
    assert ob["over_block"]["rate"] is not None and ob["over_block"]["rate"] <= 0.02
    assert ob["over_block"]["pref_refusals"] == 0 and ob["over_block"]["uncommitted_runs"] == 0
    assert m["durability"]["obedient"] == {"checked": 2, "passed": 2, "rate": 1.0}
    assert m["durability"]["naive"]["passed"] == m["durability"]["naive"]["checked"]
    assert report["setup_findings"] == [] and report["skipped_sets"] == []
    assert "estimated" in report["page_check"]

    # nothing in the report is an id, so two runs are the same bytes
    assert all("job_id" not in r and "tex" not in r for r in report["runs"])


def test_the_benchmark_can_fail_a_gate_that_does_not_hold(store, monkeypatch):
    """A negative control: with the executor's hard preferences switched off, the naive host's
    plan commits the suppressed item and the checker, reading the rendered page, reports it."""
    from eval import preference_compliance as pc
    from harness import executor

    monkeypatch.setattr(executor, "_hard_preferences", lambda user_id, job_id: ({}, {}, {}))
    report = pc.run_benchmark([pc.PROFILES[0]], 2, only=["suppress_exp"], compile_pages=False)
    nv = report["metrics"]["naive"]
    assert nv["hard_violations"] > 0 and nv["findings"]
    assert {v["check"] for f in nv["findings"] for v in f["violations"]} == {"suppressed_item"}


def test_two_runs_over_one_store_give_identical_reports(store):
    from eval import preference_compliance as pc

    first = pc.run_benchmark([pc.PROFILES[0]], 2, only=["suppress_exp", "rule_bullets"],
                             compile_pages=False)
    second = pc.run_benchmark([pc.PROFILES[0]], 2, only=["suppress_exp", "rule_bullets"],
                              compile_pages=False)
    assert first == second


def test_the_report_names_what_it_measured(store):
    from eval import preference_compliance as pc

    report = pc.run_benchmark([pc.PROFILES[0]], 2, only=["rule_pages"], compile_pages=False)
    text = pc.render_markdown(report, "2026-10-15")
    assert text.startswith("# Preference compliance, 2026-10-15")
    assert "Naive host: hard violations rendered | 0 of" in text
    assert "estimated from bullet length" in text
