"""The landing site's install and usage pages (issue #274) stay true to the docs.

The pages render every command from `site/src/components/docs/commands.json`. These
tests (no network, no npm) hold that file to the sources of truth:

- each command appears verbatim in INSTALL.md, `plugin/README.md` or
  `integrations/codex/README.md`, so an edit there that the site misses fails here;
- the release wheel the site pins is the one the code pins (`harness.hooks`), so a
  version bump in INSTALL.md or `ART_VERSION` that the site misses fails here;
- every `/art:*` command the site names is a command in `plugin/`, and every plugin
  command is documented;
- every tool the site names exists in the contract, and nothing #252 lists as planned
  and not built is named.

`tests/test_site.py` covers the scaffold; this file covers the content.
"""

import json
import re
import shlex
from pathlib import Path

import pytest

from harness.contract import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
SITE_SRC = ROOT / "site" / "src"
DOCS = SITE_SRC / "components" / "docs"
COMMANDS_JSON = DOCS / "commands.json"
PAGES = {"install": SITE_SRC / "pages" / "install.astro", "usage": SITE_SRC / "pages" / "usage.astro"}
PLUGIN_COMMANDS = ROOT / "plugin" / "commands"
SOURCES = ["INSTALL.md", "plugin/README.md", "integrations/codex/README.md"]

# Tools docs/harness.md § 9 and #252 list as planned and not built, and the planned Jev
# points. The site must not teach any of them (a tool that gets built drops out of NOT_BUILT
# below, and out of this check, by joining the contract).
PLANNED_TOOLS = {"check_draft", "record_feedback"}
PLANNED_PHRASES = ["semantic duplicate", "eligibility rule", "action ranking"]

DATA = json.loads(COMMANDS_JSON.read_text(encoding="utf-8"))


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _expand(text: str) -> str:
    """The same {NAME} expansion as commands.ts."""
    for _ in range(4):
        if not re.search(r"\{[A-Z]+\}", text):
            break
        text = re.sub(r"\{([A-Z]+)\}", lambda m: DATA["vars"].get(m.group(1), m.group(0)), text)
    return text


def _lines(block: dict):
    """(text shown on the site, text that must appear in the source) per line."""
    for line in block["lines"]:
        if isinstance(line, str):
            yield _expand(line), _expand(line)
        else:
            yield _expand(line["text"]), line["in_source"]


def _site_files():
    return [p for p in SITE_SRC.rglob("*") if p.is_file() and p.suffix in {".astro", ".ts", ".json", ".css"}]


def _site_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _site_files())


# --- every command appears verbatim in its source ---------------------------------------

def _appears(snippet: str, text: str) -> bool:
    """The command is a whole line of the doc (a code block, optionally with a trailing
    `# comment`), or a whole inline-code span. A longer line that merely starts with it does
    not count, so `/plugin install art@art2` does not pass for `/plugin install art@art`."""
    pattern = rf"(?m)^[ \t]*{re.escape(snippet)}[ \t]*(?:#.*)?$|`{re.escape(snippet)}`"
    return re.search(pattern, text) is not None


def test_every_command_block_line_appears_verbatim_in_its_source():
    assert DATA["blocks"], "no commands"
    for block_id, block in DATA["blocks"].items():
        for source in [block["source"], *block.get("also", [])]:
            assert source in SOURCES, (block_id, source)
            text = _read(source)
            for shown, in_source in _lines(block):
                assert _appears(in_source, text), (
                    f"site block {block_id!r} line {shown!r} is not in {source}: "
                    "the docs changed, or the site did. Update commands.json to match."
                )


def test_inline_mentions_and_slash_commands_appear_verbatim_in_their_source():
    for snippet, source in DATA["inline"].items():
        if snippet == "_comment":
            continue
        assert source in SOURCES, (snippet, source)
        assert snippet in _read(source), f"{snippet!r} is not in {source}"
    for item in DATA["slash"]:
        assert item["source"] in SOURCES, item
        assert _appears(item["command"], _read(item["source"])), item["command"]


def test_the_expansion_of_each_line_matches_the_docs_exactly_once_the_pin_is_filled_in():
    """`uv tool install` is written in INSTALL.md with a placeholder for the URL; the site
    shows the real one. The pair must differ by exactly that substitution."""
    seen = 0
    for block in DATA["blocks"].values():
        for shown, in_source in _lines(block):
            if shown != in_source:
                seen += 1
                assert in_source.replace("<the pinned URL>", DATA["vars"]["URL"]) == shown
    assert seen == 1


# --- the pin ------------------------------------------------------------------------------

def test_the_sites_pinned_wheel_is_the_one_the_code_and_the_docs_pin():
    from harness import ART_VERSION
    from harness.hooks import SOURCE, WHEEL_URL

    assert _expand(DATA["vars"]["URL"]) == WHEEL_URL, (
        "the site pins a different release wheel than harness/hooks.py: bump `vars` in "
        "site/src/components/docs/commands.json with the plugin pin"
    )
    assert _expand(DATA["vars"]["SOURCE"]) == SOURCE
    assert f"/v{ART_VERSION}/art_mcp-{ART_VERSION}-" in WHEEL_URL


def test_every_wheel_url_in_the_docs_and_on_the_site_is_the_same_one():
    """A release bump that updates INSTALL.md but not the site (or the reverse) leaves two
    URLs in play; this names both."""
    pattern = re.compile(r"https://\S+?/releases/download/v[\w.]+/art_mcp-[\w.]+-py3-none-any\.whl")
    pinned = _expand(DATA["vars"]["URL"])
    for path in [*(ROOT / s for s in SOURCES), ROOT / "plugin" / ".mcp.json",
                 *_site_files()]:
        text = path.read_text(encoding="utf-8")
        text = _expand(text) if path == COMMANDS_JSON else text
        for url in pattern.findall(text):
            assert url == pinned, f"{path.relative_to(ROOT)} pins {url}, the site pins {pinned}"
    assert pattern.search(_read("INSTALL.md")), "INSTALL.md no longer carries the pinned wheel"


def test_the_site_does_not_float_on_a_git_source_or_tell_people_to_build():
    text = _site_text()
    assert "git+https" not in text
    assert "npm --prefix" not in text and "npm run" not in text


# --- the commands exist -------------------------------------------------------------------

def test_every_art_subcommand_shown_exists_in_the_cli():
    entry = (ROOT / "harness" / "entry.py").read_text(encoding="utf-8")
    checked = set()
    for block in DATA["blocks"].values():
        for shown, _ in _lines(block):
            if "art-mcp[ui]" not in shown or "--from" not in shown:
                continue
            tokens = shlex.split(shown)
            rest = tokens[tokens.index("art", tokens.index("--from") + 2) + 1:]
            sub = " ".join(rest[:2]) if rest[:1] == ["hooks"] else rest[0]
            assert f"art {sub}" in entry, f"`art {sub}` is not a documented subcommand of harness/entry.py"
            checked.add(sub.split()[0])
    assert {"--list", "mcp", "ui", "hooks"} <= checked
    assert "jev status" in _read("INSTALL.md")
    assert "art jev status" in entry


def _site_slash_names() -> set[str]:
    return set(re.findall(r"/art:([a-z]+)", _site_text()))


def test_every_slash_command_the_site_names_exists_in_the_plugin():
    plugin = {p.stem for p in PLUGIN_COMMANDS.glob("*.md")}
    named = _site_slash_names()
    assert named, "the site names no /art: command"
    assert named <= plugin, f"the site names /art:{sorted(named - plugin)}, which plugin/commands/ lacks"


def test_every_plugin_command_is_documented_on_the_usage_page():
    plugin = {p.stem for p in PLUGIN_COMMANDS.glob("*.md")}
    table = {re.match(r"/art:([a-z]+)", item["command"]).group(1) for item in DATA["slash"]}
    assert table == plugin, f"usage table {sorted(table)} vs plugin/commands {sorted(plugin)}"
    usage = PAGES["usage"].read_text(encoding="utf-8")
    assert "slashCommands" in usage, "the usage page no longer renders the command table"


def test_the_codex_skills_named_exist():
    for name in re.findall(r"\$(art-[a-z]+)", _site_text()):
        assert (ROOT / "plugin" / "skills" / name / "SKILL.md").is_file(), name


# --- tools --------------------------------------------------------------------------------

def _code_tokens(page: Path) -> list[str]:
    return re.findall(r"<code>([^<]+)</code>", page.read_text(encoding="utf-8"))


def test_every_tool_the_pages_name_exists_in_the_contract():
    tokens = {t for page in PAGES.values() for t in _code_tokens(page)}
    snake = {t for t in tokens if re.fullmatch(r"[a-z]+(?:_[a-z]+)+", t)}
    named = snake | (tokens & set(BY_NAME))          # `render` and `history` have no underscore
    assert {"art_briefing", "open_job", "execute_plan", "render", "upsert_items"} <= named
    assert snake <= set(BY_NAME), f"not in the contract: {sorted(snake - set(BY_NAME))}"


def test_nothing_planned_and_not_built_is_named():
    """#252: `check_draft`, `record_feedback` and the unbuilt Jev points. The planned list is
    read from docs/harness.md as well, so a newly planned tool is caught without an edit here."""
    harness = _read("docs/harness.md")
    planned = set(PLANNED_TOOLS)
    for row in harness.splitlines():
        if "Planned, not built" in row or "planned and not built" in row.lower():
            planned |= set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", row))
    planned -= set(BY_NAME)                       # a tool that has since been built is fine
    assert PLANNED_TOOLS <= planned, "docs/harness.md no longer lists these as planned (#252)"
    text = _site_text().lower()
    for name in sorted(planned):
        assert name not in text, f"the site names {name}, which is planned and not built (#252)"
    for phrase in PLANNED_PHRASES:
        assert phrase not in text, f"the site describes {phrase!r}, a planned Jev point (#252)"


# --- structure ----------------------------------------------------------------------------

def _command_blocks(page: Path):
    return re.findall(r"<CommandBlock\b(.*?)/>", page.read_text(encoding="utf-8"), re.S)


def test_every_command_block_renders_from_the_data_file_and_none_is_dead():
    used = set()
    for name, page in PAGES.items():
        blocks = _command_blocks(page)
        assert blocks, name
        for attrs in blocks:
            ref = re.search(r'lines=\{block\("([\w-]+)"\)\}', attrs)
            if ref:
                assert ref.group(1) in DATA["blocks"], (name, ref.group(1))
                used.add(ref.group(1))
            else:
                # The one literal block is a sentence to say, not a command to run.
                assert 'label="Say this to your agent"' in attrs, (name, attrs)
    dead = set(DATA["blocks"]) - used
    assert not dead, f"commands.json blocks no page shows: {sorted(dead)}"


def test_the_table_of_contents_matches_the_sections_and_links_resolve():
    ids_by_page = {}
    for name, page in PAGES.items():
        text = page.read_text(encoding="utf-8")
        sections = dict(re.findall(r'(\w+): \{ id: "([\w-]+)"', text))
        used = re.findall(r"<DocSection section=\{S\.(\w+)\}", text)
        assert sections and set(used) == set(sections), (name, sections, used)
        assert len(used) == len(set(used)), f"{name}: a section is rendered twice"
        assert "toc={toc}" in text and "Object.values(S)" in text, name
        ids_by_page[name] = set(sections.values())
    for name, page in PAGES.items():
        text = page.read_text(encoding="utf-8")
        for target in re.findall(r'href="#([\w-]+)"', text):
            assert target in ids_by_page[name], f"{name}: #{target} does not exist"
        for other, anchor in re.findall(r'href="/(install|usage)#([\w-]+)"', text):
            assert anchor in ids_by_page[other], f"{name}: /{other}#{anchor} does not exist"


def test_the_pages_use_the_docs_layout_over_the_base_layout():
    layout = (SITE_SRC / "layouts" / "Docs.astro").read_text(encoding="utf-8")
    assert 'import Base from "./Base.astro"' in layout
    for page in PAGES.values():
        assert "<Docs" in page.read_text(encoding="utf-8")


def test_long_lines_scroll_inside_the_block_and_never_widen_the_page():
    block = (DOCS / "CommandBlock.astro").read_text(encoding="utf-8")
    pre = re.search(r"<pre\b(.*?)>", block, re.S).group(1)
    assert "overflow-x-auto" in pre
    assert "overflow-hidden" in block and "min-w-0" in block and "max-w-full" in block
    layout = (SITE_SRC / "layouts" / "Docs.astro").read_text(encoding="utf-8")
    assert "minmax(0,1fr)" in layout            # a grid track may shrink below its content
    assert "min-w-0" in layout
    assert "white-space: pre" in layout         # code in a block never wraps
    assert "overflow-wrap: anywhere" in layout  # inline code does, so it cannot widen the page


def test_each_command_block_has_a_copy_button_with_a_fallback():
    block = (DOCS / "CommandBlock.astro").read_text(encoding="utf-8")
    assert "data-copy" in block and "navigator.clipboard" in block
    assert "isSecureContext" in block, "no check that the clipboard API is allowed"
    assert 'execCommand("copy")' in block, "no fallback where the clipboard API is unavailable"
    assert 'aria-live="polite"' in block, "the result of a copy is not announced"


def test_the_docs_pieces_follow_the_themes_tokens_not_fixed_colors():
    """Light and dark come from the `.dark` tokens in global.css; a hard-coded color would
    stay light in dark mode."""
    for path in [DOCS / "CommandBlock.astro", DOCS / "Callout.astro", DOCS / "DocSection.astro",
                 SITE_SRC / "layouts" / "Docs.astro", *PAGES.values()]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"&#\d+;|href=\"#[\w-]+\"|\(#\w+", "", text)), path
        assert not re.search(r"\b(?:text|bg|border)-(?:white|black)\b", text), path
