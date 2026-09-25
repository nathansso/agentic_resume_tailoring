"""The harness tool contract (issue #191): one definition, every adapter.

Each tool is declared once as a `ToolSpec` — name, description, pydantic input
and output models, read/write, and the function that does the work. The MCP
server (`harness/mcp_server.py`) and the CLI JSON mode (`harness/cli.py`) both
serve `TOOLS` through `invoke`, so a tool cannot behave differently depending
on which host drives it; `tests/test_harness_contract.py` holds them to that.

Errors are part of each output, not exceptions: every output model carries an
optional `error` (`no_user`, `not_found`, …). A host reads one shape whether
the call worked or not, and the MCP output schema stays valid either way.

Model-free by rule: see `tests/test_harness_boundary.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONTRACT_VERSION = "1"

Kind = Literal["skill", "experience", "project", "education", "achievement"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolError(_Model):
    code: str = Field(description="Machine-readable error code, e.g. no_user, not_found.")
    message: str
    suggestions: List[str] = Field(default_factory=list,
                                   description="Keys the caller may have meant.")


class _Output(_Model):
    error: Optional[ToolError] = None


# ── art_briefing ─────────────────────────────────────────────────────────────

class BriefingInput(_Model):
    role_family: Optional[str] = Field(
        None, description="Scope preferences and job cards to this role family "
                          "(e.g. data_science).")


class Pin(_Model):
    text: str
    polarity: Optional[str] = None
    target_key: Optional[str] = None
    scope: Optional[str] = None


class Preference(_Model):
    preference_id: Optional[str] = None
    text: str
    polarity: Optional[str] = None
    target_key: Optional[str] = None
    target_term: Optional[str] = None
    scope_type: Optional[str] = None
    scope_value: Optional[str] = None
    strength: Optional[int] = None


class BriefingOutput(_Output):
    role_family: Optional[str] = None
    pins: List[Pin] = Field(default_factory=list,
                            description="Strength-5 preferences. Honour verbatim.")
    preferences: List[Preference] = Field(default_factory=list)
    persona_traits: List[Dict[str, Any]] = Field(default_factory=list)
    job_cards: str = ""
    counts: Dict[str, int] = Field(default_factory=dict)


# ── kg_search / list_items / get_item ────────────────────────────────────────

class SearchInput(_Model):
    query: str = Field(description="Words to look for in the knowledge graph.")
    kinds: Optional[List[Kind]] = Field(None, description="Restrict to these item kinds.")
    limit: int = Field(10, ge=1, le=50)


class SearchHit(_Model):
    key: str
    kind: Kind
    title: str
    score: float
    snippet: str


class SearchOutput(_Output):
    results: List[SearchHit] = Field(default_factory=list)


class ListItemsInput(_Model):
    kind: Optional[Kind] = Field(None, description="Only this kind; omit for all.")


class ItemRef(_Model):
    key: str
    kind: Kind
    title: str


class ListItemsOutput(_Output):
    items: List[ItemRef] = Field(default_factory=list)


class ItemInput(_Model):
    key: str = Field(description="A key from kg_search or list_items, "
                                 "e.g. 'exp:<title>|<company>', 'proj:<name>'.")


class ItemOutput(_Output):
    key: Optional[str] = None
    kind: Optional[Kind] = None
    record: Optional[Dict[str, Any]] = None


# ── get_profile ──────────────────────────────────────────────────────────────

class ProfileInput(_Model):
    pass


class ProfileOutput(_Output):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_username: Optional[str] = None
    portfolio_url: Optional[str] = None


# ── tailoring tree (#196) ────────────────────────────────────────────────────

class ListJobsInput(_Model):
    pass


class JobRef(_Model):
    job_id: str
    title: str
    company: str
    status: Optional[str] = None
    head: Optional[str] = Field(None, description="Current node id, if the job has history.")
    created_at: Optional[str] = None


class ListJobsOutput(_Output):
    jobs: List[JobRef] = Field(default_factory=list)


class NodeSummary(_Model):
    node_id: str
    parent_id: Optional[str] = None
    job_id: str
    seq: int
    source: str
    note: Optional[str] = None
    provenance: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class Node(NodeSummary):
    content: Dict[str, Any] = Field(default_factory=dict)
    score_breakdown: Dict[str, Any] = Field(default_factory=dict)
    program: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    edited_tex: Optional[str] = None
    layout_overrides: Optional[Dict[str, Any]] = None
    result_id: Optional[str] = None


class TreeEventOut(_Model):
    event_id: int
    job_id: str
    node_id: str
    kind: str
    created_at: str


class GetHeadInput(_Model):
    job_id: str
    since_event: Optional[int] = Field(
        None, description="Cursor from a previous call; returns what changed after it.")


class GetHeadOutput(_Output):
    job_id: Optional[str] = None
    head: Optional[Node] = None
    events: List[TreeEventOut] = Field(default_factory=list)
    editor_edits: List[NodeSummary] = Field(
        default_factory=list, description="Edits the user made in the editor since the cursor.")
    cursor: int = 0


class HistoryInput(_Model):
    job_id: str


class HistoryOutput(_Output):
    nodes: List[NodeSummary] = Field(default_factory=list)


class DiffInput(_Model):
    from_node: str
    to_node: str


class ItemChange(_Model):
    key: str
    change: Literal["added", "removed", "revised"]
    title: str
    bullets_added: List[str] = Field(default_factory=list)
    bullets_removed: List[str] = Field(default_factory=list)
    reordered: bool = False


class DiffOutput(_Output):
    from_node: Optional[str] = None
    to_node: Optional[str] = None
    items: List[ItemChange] = Field(default_factory=list)
    skills_added: List[str] = Field(default_factory=list)
    skills_removed: List[str] = Field(default_factory=list)
    tex_changed: bool = False
    layout_changed: bool = False


class CheckoutInput(_Model):
    job_id: str
    node_id: str


class CheckoutOutput(_Output):
    head: Optional[Node] = None


def _tree():
    from harness import tree
    return tree


def _tree_call(fn):
    """Run a tree function; map its NotFound / bad ids to the contract's error shape."""
    def call(*args, **kwargs):
        tree = _tree()
        try:
            return fn(tree, *args, **kwargs)
        except (tree.NotFound, ValueError) as exc:
            return {"error": {"code": "not_found", "message": str(exc)}}
    return call


# ── registry ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type
    output_model: type
    fn: Callable[..., Dict[str, Any]]
    read_only: bool = True


def _tools():
    from harness import tools
    return tools


NO_USER = ToolError(code="no_user", message=(
    "No ART user is bound. Pass --user-id (or set ART_MCP_USER_ID) when starting ART."))
READ_ONLY = ToolError(code="read_only", message=(
    "This ART process is read-only (a remote database without --allow-writes)."))

TOOLS: List[ToolSpec] = [
    ToolSpec(
        "art_briefing",
        "Pinned preferences (verbatim), scoped preferences, persona traits and prior-job "
        "cards for this candidate. Call before planning any resume.",
        BriefingInput, BriefingOutput,
        lambda uid, role_family=None: _tools().art_briefing(uid, role_family)),
    ToolSpec(
        "kg_search",
        "Search the candidate's knowledge graph by words. Returns stable keys for get_item.",
        SearchInput, SearchOutput,
        lambda uid, query, kinds=None, limit=10:
            {"results": _tools().kg_search(uid, query, kinds, limit)}),
    ToolSpec(
        "list_items",
        "Every item in the knowledge graph (or one kind), as keys and titles. Use it to "
        "see everything without guessing search words.",
        ListItemsInput, ListItemsOutput,
        lambda uid, kind=None: {"items": _tools().list_items(uid, kind)}),
    ToolSpec(
        "get_item",
        "Full record for one key. Unknown keys return an error with suggestions.",
        ItemInput, ItemOutput,
        lambda uid, key: _tools().get_item(uid, key)),
    ToolSpec(
        "get_profile",
        "The candidate's name and contact details for the resume header.",
        ProfileInput, ProfileOutput,
        lambda uid: _tools().get_profile(uid)),
    ToolSpec(
        "list_jobs",
        "The candidate's jobs with their current tailoring node (HEAD).",
        ListJobsInput, ListJobsOutput,
        lambda uid: {"jobs": _tree().list_jobs(uid)}),
    ToolSpec(
        "get_head",
        "A job's current resume version, plus what changed after a cursor — including "
        "edits the user made in the editor. Call before building on a version.",
        GetHeadInput, GetHeadOutput,
        _tree_call(lambda t, uid, job_id, since_event=None: t.get_head(uid, job_id, since_event))),
    ToolSpec(
        "history",
        "Every version of a job's resume, oldest first.",
        HistoryInput, HistoryOutput,
        _tree_call(lambda t, uid, job_id: {"nodes": t.history(uid, job_id)})),
    ToolSpec(
        "diff_nodes",
        "What changed between two versions, by item and bullet.",
        DiffInput, DiffOutput,
        _tree_call(lambda t, uid, from_node, to_node: {
            **{k: v for k, v in t.diff_nodes(uid, from_node, to_node).items()
               if k not in ("from", "to")},
            "from_node": from_node, "to_node": to_node})),
    ToolSpec(
        "checkout",
        "Make an earlier version current again (revert or branch). Writes.",
        CheckoutInput, CheckoutOutput,
        _tree_call(lambda t, uid, job_id, node_id: {"head": t.checkout(uid, job_id, node_id)}),
        read_only=False),
]
BY_NAME: Dict[str, ToolSpec] = {t.name: t for t in TOOLS}


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so datetimes/UUIDs/sets become plain values."""
    return json.loads(json.dumps(value, default=str))


def invoke(name: str, user_id: Optional[UUID], args: Optional[Dict[str, Any]] = None,
           *, allow_writes: bool = True) -> Dict[str, Any]:
    """Validate `args`, run the tool, validate the output. Returns plain JSON data.

    Raises `KeyError` for an unknown tool and `ValidationError` for bad
    arguments — adapters map those to their own error surface.
    """
    spec = BY_NAME[name]
    parsed = spec.input_model.model_validate(args or {})
    if user_id is None:
        out = spec.output_model(error=NO_USER)
    elif not spec.read_only and not allow_writes:
        out = spec.output_model(error=READ_ONLY)
    else:
        out = spec.output_model.model_validate(
            _jsonable(spec.fn(user_id, **parsed.model_dump())))
    return out.model_dump(mode="json")


def describe() -> List[Dict[str, Any]]:
    """The whole contract as data: what `harness.cli --list` prints."""
    return [{
        "name": t.name, "description": t.description, "read_only": t.read_only,
        "input_schema": t.input_model.model_json_schema(),
        "output_schema": t.output_model.model_json_schema(),
    } for t in TOOLS]


__all__ = ["CONTRACT_VERSION", "TOOLS", "BY_NAME", "ToolSpec", "ToolError", "invoke",
           "describe", "ValidationError"]
