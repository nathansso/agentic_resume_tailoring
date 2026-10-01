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

from harness.program import Program

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


class JobRuleRef(_Model):
    rule_id: str
    item_key: str
    field: str
    question: str
    value_if_yes: str
    value_if_no: Optional[str] = None


class BriefingOutput(_Output):
    role_family: Optional[str] = None
    pins: List[Pin] = Field(default_factory=list,
                            description="Strength-5 preferences. Honour verbatim.")
    preferences: List[Preference] = Field(default_factory=list)
    persona_traits: List[Dict[str, Any]] = Field(default_factory=list)
    job_cards: str = ""
    job_rules: List[JobRuleRef] = Field(
        default_factory=list, description="Yes/no questions to answer for each posting in "
                                          "open_job's rule_answers.")
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
    context: Optional[str] = Field(
        None, description="For a project: key of the role or degree it was done under.")


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


class VariantRef(_Model):
    variant_id: str
    item_key: str
    text: str
    status: Literal["draft", "approved"]
    tags: Dict[str, Any] = Field(default_factory=dict, description="track, job_id.")
    cites: List[str] = Field(default_factory=list)
    line_count: Optional[int] = Field(None, description="Rendered lines, when a LaTeX engine "
                                                        "was available.")
    source_node_id: Optional[str] = None
    score: Optional[float] = Field(None, description="Overlap with the job's weighted terms "
                                                     "(suggest_actions only).")


class ItemOutput(_Output):
    key: Optional[str] = None
    kind: Optional[Kind] = None
    record: Optional[Dict[str, Any]] = None
    variants: Optional[List[VariantRef]] = Field(
        None, description="An experience's or project's approved bullet variants (#229): "
                          "the user's confirmed wording. Start a revision from one "
                          "(bullets[].from_variant) instead of writing from the raw bullets. "
                          "Absent for other kinds.")


# ── project context ──────────────────────────────────────────────────────────

ContextStatus = Literal["unreviewed", "linked", "personal"]


class SuggestContextsInput(_Model):
    include_reviewed: bool = Field(
        False, description="Also list projects whose context is already set.")


class ContextCandidate(_Model):
    context_key: str
    kind: Literal["experience", "education"]
    title: str
    rule: str = Field(description="employer or course_code.")
    reason: str


class ProjectContextSuggestion(_Model):
    project_key: str
    project: str
    context_status: ContextStatus
    context_key: Optional[str] = None
    suggestions: List[ContextCandidate] = Field(
        default_factory=list, description="Empty when no rule fired: ask the user.")


class SuggestContextsOutput(_Output):
    projects: List[ProjectContextSuggestion] = Field(default_factory=list)


class SetContextInput(_Model):
    project: str = Field(description="A project key ('proj:<name>') or name.")
    context: str = Field(description="An 'exp:' or 'edu:' key, 'personal', or "
                                     "'unreviewed' to clear it.")


class SetContextOutput(_Output):
    project_key: Optional[str] = None
    context_status: Optional[ContextStatus] = None
    context_key: Optional[str] = None


class LinkAchievementInput(_Model):
    achievement: str = Field(description="An achievement key ('ach:<title>') or title.")
    project: Optional[str] = Field(None, description="The project it was won for; "
                                                     "omit to untie it.")


class LinkAchievementOutput(_Output):
    achievement_key: Optional[str] = None
    project_key: Optional[str] = None


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

ApplicationStatus = Literal["drafting", "applied", "interview", "closed"]


class ListJobsInput(_Model):
    status: Optional[ApplicationStatus] = Field(
        None, description="Only jobs whose application status is this.")


class JobRef(_Model):
    job_id: str
    title: str
    company: str
    status: Optional[str] = Field(None, description="Pipeline state (created, tailored, ...).")
    application_status: ApplicationStatus = "drafting"
    url: Optional[str] = None
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


# ── host-filled ingestion and jobs (#192) ────────────────────────────────────

SchemaKind = Literal["experience", "education", "project", "skill", "achievement",
                     "requirement", "rule", "variant"]
RecordKind = Literal["experience", "education", "project", "skill", "achievement", "rule",
                     "variant"]


class IngestSchemaInput(_Model):
    kind: SchemaKind


class IngestSchemaOutput(_Output):
    kind: Optional[str] = None
    json_schema: Dict[str, Any] = Field(default_factory=dict,
                                        description="JSON schema of one record's `data`.")
    required: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class UpsertRecord(_Model):
    kind: RecordKind
    data: Dict[str, Any] = Field(description="Fields per ingest_schema(kind).")
    correct: bool = Field(False, description="Overwrite the matched item's given fields "
                                             "instead of only filling blanks. Never "
                                             "changes an item the user edited by hand.")


class UpsertInput(_Model):
    records: List[UpsertRecord] = Field(min_length=1, max_length=500)
    source: str = Field("host", description="Where the records came from, e.g. resume.pdf "
                                            "or github:<repo>. Stored as skill evidence.")


class UpsertResult(_Model):
    index: int
    kind: str
    key: Optional[str] = None
    status: Literal["created", "merged", "unchanged", "corrected", "skipped_manual",
                    "skipped_tombstone", "skipped_empty", "filtered", "invalid"]
    message: Optional[str] = None


class UpsertOutput(_Output):
    results: List[UpsertResult] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)


class JobMetadata(_Model):
    title: str = ""
    company: str = ""
    url: Optional[str] = None
    status: Optional[ApplicationStatus] = None
    title_terms: List[str] = []
    role_family: Optional[str] = Field(
        None, description="The posting's role family, from your own reading: one of "
                          "software_engineering, machine_learning, data_science, "
                          "data_engineering, research, product_management, design, "
                          "devops_infrastructure, security, hardware, other. A track "
                          "baseline named after it (save_baseline) is the job's starting "
                          "point. Omitted: ART guesses from the title, else other (#229).")


class RuleAnswer(_Model):
    rule_id: str
    answer: bool
    quote: Optional[str] = Field(None, description="The posting's words the answer rests on.")


class OpenJobInput(_Model):
    jd_text: str = Field("", description="The posting's full text. Required for a new job.")
    requirements: List[Dict[str, Any]] = Field(
        [], description="Atomic requirements per ingest_schema('requirement').")
    metadata: JobMetadata = Field(JobMetadata(), description="title and company are required for a new job.")
    rule_answers: List[RuleAnswer] = Field(
        [], description="Answers to art_briefing's job_rules for this posting.")
    job_id: Optional[str] = Field(None, description="Update this job instead of opening one.")


class TermWeight(_Model):
    term: str
    weight: float


class SkillMatch(_Model):
    skill: str = Field(description="The user's skill the term names.")
    term: str
    requirement: int = Field(description="Ordinal of the requirement that lists the term.")
    type: str


class UnmatchedTerm(_Model):
    term: str
    requirement: int
    type: str


class RuleResult(JobRuleRef):
    status: Literal["answered", "needs_answer"]
    answer: Optional[bool] = None
    quote: Optional[str] = None
    value: Optional[str] = None


class SchemaError(_Model):
    where: str
    message: str


class BaselineRef(_Model):
    track: str
    node_id: str
    applies: bool = Field(description="True when the job has no history yet, so its first "
                                      "plan starts as a copy of this node (#229).")


class OpenJobOutput(_Output):
    job_id: Optional[str] = None
    created: bool = False
    title: Optional[str] = None
    company: Optional[str] = None
    application_status: Optional[ApplicationStatus] = None
    requirements: int = 0
    top_terms: List[TermWeight] = Field(default_factory=list)
    skill_matches: List[SkillMatch] = Field(
        default_factory=list,
        description="Required and preferred requirement terms that name a skill the user has, "
                    "exactly or through the alias map, most important first.")
    unmatched_terms: List[UnmatchedTerm] = Field(
        default_factory=list,
        description="Required and preferred requirement terms that match none of the user's "
                    "skills. ART does not guess: search the KG (kg_search) for evidence "
                    "under another name, or ask the user, and never claim the skill "
                    "without it.")
    rules: List[RuleResult] = Field(
        default_factory=list, description="needs_answer: ask the user, then call open_job "
                                          "again with job_id and rule_answers.")
    schema_errors: List[SchemaError] = Field(default_factory=list)
    role_family: Optional[str] = Field(None, description="The family used to pick a baseline.")
    role_family_source: Optional[Literal["host", "title", "default"]] = Field(
        None, description="host: you supplied it. title: guessed from the job title. "
                          "default: nothing matched, so other.")
    baseline: Optional[BaselineRef] = Field(
        None, description="The track baseline for this job's role family, if one was saved "
                          "(#229). The job's first version starts as a copy of it.")


def _ingest():
    from harness import ingest
    return ingest


# ── render and the profile header (#201) ─────────────────────────────────────

class RenderInput(_Model):
    job_id: str
    node_id: Optional[str] = Field(None, description="A version from history; HEAD if omitted.")
    format: Literal["pdf", "tex"] = "pdf"


class RenderOutput(_Output):
    job_id: Optional[str] = None
    node_id: Optional[str] = None
    source: Optional[Literal["generated", "edited"]] = Field(
        None, description="edited: the user's own .tex from the editor was rendered.")
    tex_path: Optional[str] = None
    pdf_path: Optional[str] = None
    pages: Optional[int] = None
    line_budget: Dict[str, Any] = Field(default_factory=dict)
    hint: Optional[str] = None


class UpdateProfileInput(_Model):
    fields: Dict[str, Optional[str]] = Field(
        description="Header fields to set, e.g. {\"name\": \"Ada Lovelace\", \"email\": ...}.")


def _render():
    from harness import render
    return render


# ── plan programs (#197) ─────────────────────────────────────────────────────

class ExecuteInput(_Model):
    program: Program
    dry_run: bool = Field(False, description="Evaluate everything, commit nothing.")


class PatchEdit(_Model):
    op: Literal["add", "remove", "replace"]
    path: str = Field(description="JSON pointer into the saved program, e.g. /nodes/0/bullets.")
    value: Any = None


class PatchInput(_Model):
    program_id: str = Field(description="From a previous execute_plan or patch_plan result.")
    edits: List[PatchEdit]
    dry_run: bool = False


class NodeResult(_Model):
    id: str
    op: str
    item_key: str
    status: Literal["accepted", "reverted", "refused", "kept"]
    reason: Optional[str] = None
    improved: List[str] = Field(default_factory=list)
    deltas: Dict[str, Optional[float]] = Field(default_factory=dict)
    review: Optional[List[Dict[str, Any]]] = Field(
        None, description="Bullets Jev could not clearly call supported by their cited "
                          "evidence (item, bullet, label, p), and bullets or item fields it "
                          "thinks may mention a pinned topic in other words (check: "
                          "negative_pin, item, where, bullet, pin, p). Show them to the user.")
    semantic: Optional[Dict[str, Any]] = Field(
        None, description="What this node did (for a reverted node, would have done) to semantic "
                          "requirement coverage (#126), as "
                          "requirement ordinals from open_job: covered, of, gained (now "
                          "evidenced by a bullet), lost, and the page's semantic_only and "
                          "literal_only (see semantic_coverage). Absent when Jev did not answer.")


class Violation(_Model):
    check: str
    detail: Any = None
    hint: Optional[str] = None


class ExecuteOutput(_Output):
    program_id: Optional[str] = None
    parent: Optional[str] = None
    dry_run: bool = False
    committed: bool = False
    node_id: Optional[str] = None
    nodes: List[NodeResult] = Field(default_factory=list)
    skills: Dict[str, List[str]] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(
        default_factory=dict, description="Metric vectors by role (gates, guards, targets, "
                                          "report) for the base and the final version.")
    line_budget: Dict[str, Any] = Field(default_factory=dict)
    violations: List[Violation] = Field(default_factory=list)
    cut_hints: List[Dict[str, Any]] = Field(default_factory=list)
    rules_applied: List[Dict[str, Any]] = Field(
        default_factory=list, description="Job-scoped rule values written into the base.")
    baseline: Optional[Dict[str, Any]] = Field(
        None, description="The track baseline this job's first version was copied from "
                          "(track, node_id, role_family, source). Absent when the job started "
                          "from the whole knowledge graph (#229).")
    support: Optional[Dict[str, Any]] = Field(
        None, description="The cited-bullet support check (#193): how many bullets Jev "
                          "checked and which to review. Absent when nothing was checked "
                          "(no key, mode off, or no changed cited bullet).")
    negative_pins: Optional[Dict[str, Any]] = Field(
        None, description="The negative-pin check (#232): how many (bullet or item field, "
                          "pin) pairs on the final page Jev checked and which to review. Absent when "
                          "nothing was checked (no key, mode off, or no pins).")
    semantic_coverage: Optional[Dict[str, Any]] = Field(
        None, description="Semantic requirement coverage of the final page (#126), a target "
                          "beside the literal coverage: score (0-100, criticality-weighted), "
                          "covered, of, and two lists of requirements where the literal and "
                          "semantic readings disagree. semantic_only: a bullet (or, with by: education, a degree line) shows the "
                          "requirement but `missing` terms are not on the page (keyword-weave "
                          "candidates, if the cited evidence supports the words). literal_only: "
                          "`present` terms are on the page but no bullet shows the requirement "
                          "(the stuffing signature). Absent when Jev did not answer (no key, "
                          "mode off, an API error) or the job has no required or preferred "
                          "requirement.")


def _executor():
    from harness import executor
    return executor


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


# ── bullet library and track baselines (#229) ────────────────────────────────

class PromoteInput(_Model):
    node_id: str = Field(description="A committed version of one of your jobs (history).")
    bullet: str = Field(description="The bullet's text exactly as it reads on that version.")
    item_key: Optional[str] = Field(None, description="Needed only when the same text sits under "
                                                      "more than one item.")
    track: Optional[str] = Field(None, description="Tag it with this track; default: the job's "
                                                   "role family.")


class PromoteOutput(_Output):
    variant: Optional[VariantRef] = None
    created: bool = Field(False, description="False when that bullet was already a variant of "
                                             "the item; the existing one is returned.")


class ApproveInput(_Model):
    variant_id: str = Field(description="A draft from promote_bullet. Ask the user first: "
                                        "nothing else approves a variant.")


class ApproveOutput(_Output):
    variant: Optional[VariantRef] = None
    changed: bool = False


class SaveBaselineInput(_Model):
    node_id: str = Field(description="The version to pin (history).")
    track: str = Field(description="A track name, normalized to lowercase with underscores. "
                                   "A job starts from the baseline whose track equals its role "
                                   "family (e.g. data_science).")


class SaveBaselineOutput(_Output):
    track: Optional[str] = None
    node_id: Optional[str] = None
    job_id: Optional[str] = None
    replaced: Optional[str] = Field(None, description="The node this track pointed at before.")


class SuggestActionsInput(_Model):
    job_id: str
    node_id: Optional[str] = Field(None, description="A version of the job; HEAD if omitted.")


class ActionChoice(_Model):
    op: Literal["keep", "revise", "replace", "delete"]
    propensity: float


class ItemSuggestion(_Model):
    item_key: str
    title: str
    match: Literal["variant", "no_match"]
    variant: Optional[VariantRef] = Field(
        None, description="The approved variant that best fits the job. Start the revision "
                          "from it (bullets[].from_variant). None on no_match: write from "
                          "the raw facts.")
    best_score: float = Field(description="The best overlap among the item's approved "
                                          "variants, even when below the floor.")
    approved_variants: int = 0
    actions: List[ActionChoice] = Field(default_factory=list,
                                        description="Valid ops, uniform propensities.")
    source: Literal["fallback", "jev"] = "fallback"


class SuggestActionsOutput(_Output):
    job_id: Optional[str] = None
    node_id: Optional[str] = Field(None, description="The version judged; None for a job with "
                                                     "no history (its starting version).")
    floor: Optional[float] = None
    items: List[ItemSuggestion] = Field(default_factory=list)


def _library():
    from harness import library
    return library


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
        "Full record for one key; an experience or project also lists its approved bullet "
        "variants. Unknown keys return an error with suggestions.",
        ItemInput, ItemOutput,
        lambda uid, key: _tools().get_item(uid, key)),
    ToolSpec(
        "suggest_project_contexts",
        "Where each unreviewed project was probably done (a role or a degree), from "
        "employer names in repo names and course codes. Confirm each with the user, then "
        "call set_project_context. A project with no suggestion: ask.",
        SuggestContextsInput, SuggestContextsOutput,
        lambda uid, include_reviewed=False:
            _tools().suggest_project_contexts(uid, include_reviewed)),
    ToolSpec(
        "set_project_context",
        "Record where a project was done: an exp: or edu: key, or 'personal'. Only what "
        "the user confirmed. Writes.",
        SetContextInput, SetContextOutput,
        lambda uid, project, context: _tools().set_project_context(uid, project, context),
        read_only=False),
    ToolSpec(
        "link_achievement",
        "Tie an award (e.g. a hackathon placing) to the project it was won for. Only what "
        "the user confirmed. Writes.",
        LinkAchievementInput, LinkAchievementOutput,
        lambda uid, achievement, project=None:
            _tools().link_achievement(uid, achievement, project),
        read_only=False),
    ToolSpec(
        "get_profile",
        "The candidate's name and contact details for the resume header.",
        ProfileInput, ProfileOutput,
        lambda uid: _tools().get_profile(uid)),
    ToolSpec(
        "ingest_schema",
        "The JSON schema of one record you fill for upsert_items (a KG item or a job-scoped "
        "rule) or open_job (a requirement).",
        IngestSchemaInput, IngestSchemaOutput,
        lambda uid, kind: _ingest().ingest_schema(kind)),
    ToolSpec(
        "upsert_items",
        "Store knowledge-graph records you extracted from the user's resume, repos or "
        "LinkedIn export, job-scoped rules, or the user's curated bullet library (kind "
        "variant: approved wording for an experience or project). ART validates, "
        "deduplicates and merges; it never changes an item the user edited by hand. Writes.",
        UpsertInput, UpsertOutput,
        lambda uid, records, source="host": _ingest().upsert_items(uid, records, source),
        read_only=False),
    ToolSpec(
        "open_job",
        "Open a job from a posting: its text, the requirements you extracted, and answers "
        "to the user's job-scoped rules (from art_briefing), and its role_family if you can "
        "tell. Returns the job id, weighted terms, any rule still needing an answer, and the "
        "track baseline the job starts from, if one was saved. Re-opening updates the job. "
        "Writes.",
        OpenJobInput, OpenJobOutput,
        lambda uid, jd_text="", requirements=(), metadata=None, rule_answers=(), job_id=None:
            _ingest().open_job(uid, jd_text, requirements, metadata or {}, rule_answers, job_id),
        read_only=False),
    ToolSpec(
        "list_jobs",
        "The candidate's jobs with their application status and current tailoring node "
        "(HEAD), optionally filtered by status.",
        ListJobsInput, ListJobsOutput,
        lambda uid, status=None: {"jobs": _tree().list_jobs(uid, status)}),
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
    ToolSpec(
        "suggest_actions",
        "For each experience and project on a version (HEAD by default): the approved "
        "bullet variant that best fits the job, or no_match, and the valid actions with "
        "uniform propensities. Start a revision from the variant (bullets[].from_variant); "
        "write from raw facts only on no_match.",
        SuggestActionsInput, SuggestActionsOutput,
        lambda uid, job_id, node_id=None: _library().suggest_actions(uid, job_id, node_id)),
    ToolSpec(
        "promote_bullet",
        "Make a draft bullet variant from a bullet on a committed version of one of your "
        "jobs. A draft is never used until the user confirms it with approve_variant. Writes.",
        PromoteInput, PromoteOutput,
        lambda uid, node_id, bullet, item_key=None, track=None:
            _library().promote_bullet(uid, node_id, bullet, item_key, track),
        read_only=False),
    ToolSpec(
        "approve_variant",
        "Approve a draft bullet variant, only after the user said yes. Approved variants "
        "are offered by get_item and suggest_actions. Writes.",
        ApproveInput, ApproveOutput,
        lambda uid, variant_id: _library().approve_variant(uid, variant_id),
        read_only=False),
    ToolSpec(
        "save_baseline",
        "Pin a version as the baseline for a track (replacing that track's earlier one). A "
        "new job whose role family equals the track starts as a copy of it. Writes.",
        SaveBaselineInput, SaveBaselineOutput,
        lambda uid, node_id, track: _library().save_baseline(uid, node_id, track),
        read_only=False),
    ToolSpec(
        "execute_plan",
        "Run a whole tailoring plan as one program: arbitration, each node under the "
        "per-metric acceptance rule, then finalize (preferences, sections, skills cap, line "
        "budget). Commits one version unless a check fails or dry_run is set. Writes.",
        ExecuteInput, ExecuteOutput,
        lambda uid, program, dry_run=False:
            _executor().execute_plan(uid, program, dry_run=dry_run),
        read_only=False),
    ToolSpec(
        "patch_plan",
        "Amend a saved program by JSON pointer and run it again (e.g. apply a cut hint, or "
        "replace /parent to rebase on a new HEAD). Writes.",
        PatchInput, ExecuteOutput,
        lambda uid, program_id, edits, dry_run=False:
            _executor().patch_plan(uid, program_id, edits, dry_run=dry_run),
        read_only=False),
    ToolSpec(
        "render",
        "Write a job's version (HEAD by default) as .tex and PDF under the ART data "
        "directory. Returns the paths, the page count and the line budget with cut hints. "
        "The user's own .tex edits win. Writes files.",
        RenderInput, RenderOutput,
        lambda uid, job_id, node_id=None, format="pdf":
            _render().render(uid, job_id, node_id, format),
        read_only=False),
    ToolSpec(
        "update_profile",
        "Set the resume header: name, email, phone, location, linkedin_url, "
        "github_username, portfolio_url. Only what the user gave you. Writes.",
        UpdateProfileInput, ProfileOutput,
        lambda uid, fields: _tools().update_profile(uid, fields),
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
