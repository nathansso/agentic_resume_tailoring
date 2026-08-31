# ART — agent guidelines

This file exists for tools that read `AGENTS.md` and do not fall back to
`CLAUDE.md` (Codex, via `.codex/`). It is a pointer, not a policy document.

**Read [`CLAUDE.md`](CLAUDE.md) in the repository root.** It is the single
policy surface: project overview, always-on rules, guidance hierarchy, work
tracking, commit format, project-board procedure, architecture invariants, and
testing.

Folder-local `CLAUDE.md` files (`web/`, `agents/`) supplement the root file with
implementation-specific constraints; read the one covering the files you are
touching.

Do not restate policy here. A second copy drifts — this file was a 230-line
duplicate that had already fallen behind root `CLAUDE.md` when #182 collapsed
it. `tests/test_repo_guidance.py` guards against the regrowth.
