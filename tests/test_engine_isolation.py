"""`isolated_engine` reaches every module that binds the engine at import (#175).

`from database.db import engine` at module level copies the object, so a test
that patched only `database.db.engine` left those modules reading another
database: the developer's real store, or whichever test first imported them.
Queries came back empty and assertions passed vacuously. The fixture now
patches a declared list, and this test keeps that list complete.
"""

import ast
from pathlib import Path

import conftest

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", "tests", "personal", "tmp", ".git", "eval", "scripts"}


def _module_level_binders():
    """Dotted names of repo modules with a module-level
    `from database.db import ... engine ...`."""
    found = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts) or rel.parts[0].startswith("."):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:                       # module level only
            if (isinstance(node, ast.ImportFrom) and node.module == "database.db"
                    and any(a.name == "engine" for a in node.names)):
                found.add(".".join(rel.with_suffix("").parts))
    return found


def test_every_module_that_binds_the_engine_is_patched():
    binders = _module_level_binders()
    assert "services" in binders and "agents.formatter" in binders   # the scan works
    assert binders == set(conftest.ENGINE_BINDERS), (
        "add new binders to conftest.ENGINE_BINDERS (or read database.db.engine at "
        f"call time): missing {sorted(binders - set(conftest.ENGINE_BINDERS))}, "
        f"stale {sorted(set(conftest.ENGINE_BINDERS) - binders)}")


def test_isolated_engine_reaches_every_binder(isolated_engine):
    import importlib

    for name in conftest.ENGINE_BINDERS:
        assert importlib.import_module(name).engine is isolated_engine, name


def test_a_binder_reads_the_seeded_store(isolated_engine):
    """The #171 symptom: a module queried an empty (or someone else's) store."""
    from sqlmodel import Session

    import agents.formatter as fmt
    from conftest import _seed_user_and_skill
    from database.models import User

    user = _seed_user_and_skill(isolated_engine)
    with Session(fmt.engine) as s:
        assert s.get(User, user.user_id) is not None
