"""The harness import boundary (issue #190): no generative model client under `harness/`.

Root CLAUDE.md: nothing under `harness/` may import `llm`, `langchain_*`,
`langgraph`, `openai` or `anthropic`. Two rules, both checked **statically**
with `ast` so nothing is imported to test it:

1. **Harness code never names a banned module**, not even inside a function —
   a lazy `import llm` in a tool body is still a generative call path.
2. **Importing any harness module never loads one.** Module-level imports are
   followed transitively through the repo (class bodies and conditional blocks
   included, function bodies not). Function-level imports *outside* `harness/`
   are deliberately not followed: `agents/preferences.py` and
   `agents/job_card.py` import `llm` inside their extractor calls (#189), and
   the harness never calls those paths. That is the known limit of this check;
   a harness change that starts calling such a function is caught in review,
   not here.

The modules the executor (#197) reuses from `agents/` are held to rule 2 as
well: `checks` (tailor's checks without the LLM stack), `arbitration` and
`tailor_planner` (which reached `llm` through `jd_profile` until the pure JD
readers moved to `jd_payload`), and `keyword_weights`.
"""

import ast
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNED_EXACT = {"llm", "langgraph", "openai", "anthropic"}
BANNED_PREFIX = ("langchain",)
EXTRA_ENTRIES = ("agents.checks", "agents.arbitration", "agents.tailor_planner",
                 "agents.keyword_weights", "agents.jd_payload")


def _banned(module: str) -> bool:
    top = module.split(".")[0]
    return top in BANNED_EXACT or top.startswith(BANNED_PREFIX)


def _resolve(root: Path, module: str):
    """Repo file for a dotted module name, or None if it is not in the repo."""
    base = root.joinpath(*module.split("."))
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(tree: ast.AST, current: str, is_pkg: bool, *, module_level_only: bool):
    """Dotted names imported by a module (absolute; relative ones resolved)."""
    out = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if module_level_only and isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Import):
                out.extend(a.name for a in child.names)
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    pkg = current.split(".") if is_pkg else current.split(".")[:-1]
                    pkg = pkg[: len(pkg) - (child.level - 1)]
                    base = ".".join(pkg + ([child.module] if child.module else []))
                else:
                    base = child.module or ""
                out.append(base)
                out.extend(f"{base}.{a.name}" for a in child.names if a.name != "*")
            visit(child)

    visit(tree)
    return [m for m in out if m]


def direct_violations(root: Path, package: str = "harness"):
    """Rule 1: every import anywhere in the package's files."""
    found = []
    for path in sorted((root / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = _module_name(root, path)
        for mod in _imports(tree, name, path.name == "__init__.py", module_level_only=False):
            if _banned(mod):
                found.append(f"{path.relative_to(root)} imports {mod}")
    return found


def transitive_violations(root: Path, entries):
    """Rule 2: module-level imports, followed through the repo from each entry."""
    found = []
    for entry in entries:
        seen, queue = {entry}, deque([(entry, [entry])])
        while queue:
            mod, chain = queue.popleft()
            path = _resolve(root, mod)
            if path is None:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for dep in _imports(tree, mod, path.name == "__init__.py", module_level_only=True):
                # `import a.b.c` loads a, a.b and a.b.c.
                parts = dep.split(".")
                for i in range(1, len(parts) + 1):
                    sub = ".".join(parts[:i])
                    if _banned(sub):
                        found.append(" -> ".join(chain + [sub]))
                        break
                    if sub not in seen and _resolve(root, sub) is not None:
                        seen.add(sub)
                        queue.append((sub, chain + [sub]))
    return sorted(set(found))


def _harness_entries(root: Path):
    return [_module_name(root, p) for p in sorted((root / "harness").rglob("*.py"))]


# ── the repo ────────────────────────────────────────────────────────────────

def test_harness_code_names_no_generative_client():
    assert direct_violations(ROOT) == []


def test_importing_harness_or_checks_loads_no_generative_client():
    entries = _harness_entries(ROOT) + list(EXTRA_ENTRIES)
    assert "harness.mcp_server" in entries and "harness.tools" in entries
    assert transitive_violations(ROOT, entries) == []


# ── the checker itself ──────────────────────────────────────────────────────

def _tree(tmp_path, files):
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_lazy_import_inside_a_harness_function_is_caught(tmp_path):
    root = _tree(tmp_path, {
        "harness/__init__.py": "",
        "harness/tools.py": "def tool():\n    import llm\n    return llm\n",
    })
    assert direct_violations(root) == [f"{Path('harness', 'tools.py')} imports llm"]


def test_a_module_level_chain_through_the_repo_is_caught(tmp_path):
    root = _tree(tmp_path, {
        "harness/__init__.py": "",
        "harness/tools.py": "from pkg import helper\n",
        "pkg/__init__.py": "",
        "pkg/helper.py": "from langchain_core.prompts import X\n",
    })
    assert transitive_violations(root, ["harness.tools"]) == [
        "harness.tools -> pkg.helper -> langchain_core"]


def test_function_level_imports_outside_harness_are_not_followed(tmp_path):
    """The documented limit: a lazy LLM import in a dependency is allowed."""
    root = _tree(tmp_path, {
        "harness/__init__.py": "",
        "harness/tools.py": "import pkg.helper\n",
        "pkg/__init__.py": "",
        "pkg/helper.py": "def extract():\n    from llm import get_extractor\n",
    })
    assert transitive_violations(root, ["harness.tools"]) == []
    assert direct_violations(root) == []


def test_relative_imports_are_resolved(tmp_path):
    root = _tree(tmp_path, {
        "harness/__init__.py": "",
        "harness/tools.py": "from . import helpers\n",
        "harness/helpers.py": "import openai\n",
    })
    assert "harness.tools -> harness.helpers -> openai" in transitive_violations(
        root, ["harness.tools"])


def test_tailor_aliases_are_the_checks_functions():
    """#190 moved tailor's pure checks; the old names must be the same objects."""
    from agents import checks
    from agents import tailor
    from agents.tailor import ResumeTailorAgent as R

    for name in ("score_and_budget_experiences", "enforce_bullet_budgets", "exp_key",
                 "proj_key", "label_for_key", "rendered_by_key", "faithfulness_drift",
                 "over_repeated_terms", "expected_sections", "annotate_with_action",
                 "apply_plan_to_inputs", "enforce_plan"):
        assert getattr(R, f"_{name}") is getattr(checks, name), name
    for const in ("FAITHFULNESS_MIN", "MAX_TERM_MENTIONS", "MAX_EXP_BULLETS",
                  "MIN_EXP_BULLETS", "PINNED_SECTIONS", "REORDERABLE_SECTIONS",
                  "_CONTENT_GATED_SECTIONS"):
        assert getattr(tailor, const) is getattr(checks, const), const
