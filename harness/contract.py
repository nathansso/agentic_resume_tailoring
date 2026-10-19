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
    job_id: Optional[str] = Field(
        None, description="Also the preferences scoped to this job, and (unless role_family "
                          "is given) its role family's: the scope execute_plan enforces.")


class Pin(_Model):
    text: str
    polarity: Optional[str] = None
    target_key: Optional[str] = None
    target_term: Optional[str] = None
    scope: Optional[str] = None
    scope_value: Optional[str] = None
    negative_pin: bool = Field(
        False, description="A strength-5 suppression: what it names must never reach the page.")


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
    job_id: Optional[str] = None
    pins: List[Pin] = Field(default_factory=list,
                            description="Strength-5 preferences. Honour verbatim.")
    preferences: List[Preference] = Field(default_factory=list)
    rules: Dict[str, Any] = Field(
        default_factory=dict,
        description="The document rules in scope (#298): hard (strength 5; honour verbatim, "
                    "a plan that loosens one is refused), soft (defaults a plan may override, "
                    "reported applied or not_applied), notes (style notes: follow them, nothing "
                    "blocks), overridden (rules that cannot take effect, and the rule that "
                    "outranks each). Each carries rule_id, text, kind, param and value.")
    persona_traits: List[Dict[str, Any]] = Field(default_factory=list)
    job_cards: str = ""
    job_rules: List[JobRuleRef] = Field(
        default_factory=list, description="Yes/no questions to answer for each posting in "
                                          "open_job's rule_answers.")
    counts: Dict[str, int] = Field(default_factory=dict)


class PinsInput(_Model):
    role_family: Optional[str] = Field(
        None, description="Also the pins scoped to this role family (e.g. data_science).")
    job_id: Optional[str] = Field(None, description="Also the pins scoped to this job.")


class PinsOutput(_Output):
    role_family: Optional[str] = None
    job_id: Optional[str] = None
    pins: List[Pin] = Field(default_factory=list,
                            description="Strength-5 preferences, word for word. Honour every one.")
    count: int = 0
    rules: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Strength-5 document rules (#298) in scope, as the executor enforces them: "
                    "limits (pages, bullets per item, skills, a section left off) and a section "
                    "order. A plan that loosens one is refused.")
    pending_bans: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Suppressions of something no knowledge-graph item holds yet (#299), every "
                    "strength, each with its text, scope and `hard`: a strength-5 one is also in "
                    "pins and is enforced as a page-wide term; a softer one is reported only.")


# ── the memory gate (#202) ───────────────────────────────────────────────────

class ObserveInput(_Model):
    text: str = Field(description="The user's message, as they wrote it.")
    session_id: Optional[str] = Field(
        None, description="The host's session id, so a job-scoped preference can use the job "
                          "this session is working on.")
    previous_turn: Optional[str] = Field(
        None, description="The assistant's last reply, when the message depends on it ('never "
                          "list that again', 'keep it like that', 'yes, always'). Only used when "
                          "the message has such a reference; the last 600 characters count. "
                          "Leave it out for a message that reads on its own.")


class ObserveOutput(_Output):
    action: Optional[Literal["drop", "host", "write"]] = Field(
        None, description="drop: not a preference, nothing to do. host: it may be one; confirm "
                          "with the user and call record_preference. write: ART saved it.")
    reason: Optional[str] = Field(None, description="Why: no_cue, uncertain, hard_preference, "
                                                    "no_target, negation_disagrees, auto, ...")
    candidate: bool = Field(False, description="Whether the message carried a preference cue.")
    negated: bool = Field(False, description="Whether the message reads as a wish to leave "
                                             "something out.")
    p: Optional[float] = Field(None, description="Jev's probability that it is a lasting "
                                                 "preference; absent when Jev did not answer.")
    source: Optional[Literal["jev", "cache", "fallback", "prefilter"]] = Field(
        None, description="Who decided: jev, its cache, or the prefilter alone (no key, mode "
                          "off, an API error).")
    guess: Optional[Dict[str, Any]] = Field(
        None, description="The gate's reading: direction, target, strength, scope.")
    preference_id: Optional[str] = Field(None, description="The stored preference, on write.")
    note: Optional[str] = Field(None, description="What to tell the host, as the hook says it.")
    version: Optional[str] = Field(
        None, description="The question version whose answers decided: memory_gate@v1 (the "
                          "message alone) or memory_gate@v2 (read with previous_turn). Absent when "
                          "no Jev answer decided.")
    context: Optional[Literal["none", "used", "missing"]] = Field(
        None, description="none: the message reads on its own. used: its previous turn was read "
                          "with it (v2). missing: it needs the previous turn and none was given "
                          "or readable, so it was read alone.")


class RecordPreferenceInput(_Model):
    text: str = Field(description="The preference as a standalone instruction, in the user's "
                                  "own words where you can. Never softened or inferred.")
    polarity: Literal["emphasize", "suppress", "reframe"] = Field(
        description="emphasize: feature or keep it. suppress: leave it out. reframe: keep it "
                    "but present it differently, or a format rule.")
    target: Optional[str] = Field(
        None, description="What it is about: a key from list_items ('skill:python', "
                          "'proj:<name>', 'exp:<title>|<company>', 'edu:...', 'ach:...'), an "
                          "item's name or an alias of a skill ('torch' is PyTorch), "
                          "'section:<name>' (education, experience, projects, skills, "
                          "achievements), or a bare topic ('GPA'). One subject per call. A name "
                          "that fits several items is refused with the candidates. Required for "
                          "emphasize and suppress.")
    strength: int = Field(
        3, ge=1, le=5, description="1 a passing remark to 5 an absolute rule the user stated "
                                   "as non-negotiable ('never'). A 5 becomes a hard gate on "
                                   "every plan, and a 5 suppression a negative pin: use it only "
                                   "for what the user insisted on.")
    scope: Literal["global", "role_family", "job"] = Field(
        "global", description="global: every resume. role_family or job: only that one.")
    scope_value: Optional[str] = Field(
        None, description="The role family (e.g. data_science) or the job id; required unless "
                          "global.")
    quote: Optional[str] = Field(None, description="The user's words this rests on.")


class RecordPreferenceOutput(_Output):
    status: Optional[Literal["stored", "superseded", "already_recorded", "refused"]] = None
    reason: Optional[str] = Field(None, description="Why a preference was refused.")
    preference_id: Optional[str] = None
    text: Optional[str] = None
    polarity: Optional[str] = None
    target_key: Optional[str] = None
    target_term: Optional[str] = None
    target_resolved: bool = Field(False, description="Whether the target is an item in the "
                                                     "knowledge graph or a section.")
    scope_type: Optional[str] = None
    scope_value: Optional[str] = None
    strength: Optional[int] = None
    pinned: bool = Field(False, description="Strength 5: a hard gate, restored word for word "
                                            "after compaction.")
    negative_pin: bool = Field(False, description="A strength-5 suppression the executor enforces: what it names "
                                                  "(a topic, a skill, an experience or a project) never renders.")
    supersedes: Optional[str] = Field(None, description="The earlier preference this replaced.")
    suggestions: List[str] = Field(default_factory=list,
                                   description="Catalog keys the target may have meant.")
    anchor: Optional[Dict[str, Any]] = Field(
        None, description="Where the target attached: status bound (an item, with its key and the "
                          "step that found it: key, label, name or alias), pending (a thing the graph "
                          "does not hold: stored by its term, and linked if it is ingested later), "
                          "document (about the page, not an item), ambiguous or multiple (refused).")
    candidates: List[Dict[str, Any]] = Field(
        default_factory=list, description="When the target named several items: each one's key and "
                                          "label. Ask the user which, then call again with its key.")
    subjects: List[str] = Field(
        default_factory=list, description="When the target named several things ('Kafka or Spark'): "
                                          "each one. Call once per subject.")
    rule: Optional[Dict[str, Any]] = Field(
        None, description="When the target was about the page (a document subject) and the words "
                          "stated one plain limit ('one page', 'no projects section'): the document "
                          "rule it was stored as (#298), instead of a preference. status says "
                          "stored, superseded, already_recorded or refused (a narrower rule "
                          "that would loosen a hard one).")
    route: Optional[Dict[str, Any]] = Field(
        None, description="When the target was about the page but the words do not state one plain "
                          "limit: nothing was stored. Call record_rule with kind (limit, "
                          "section_order or style_note) and params; shapes lists each.")


# ── kg_search / list_items / get_item ────────────────────────────────────────

class SearchInput(_Model):
    query: str = Field(description="Words to look for in the knowledge graph.")
    kinds: Optional[List[Kind]] = Field(None, description="Restrict to these item kinds.")
    limit: int = Field(10, ge=1, le=50)


class ItemPreference(Preference):
    """A preference attached to one item (#297). `scope_type` says where it binds: global,
    or only for a role family or one job."""
    negative_pin: bool = Field(
        False, description="A strength-5 suppression: what it names must never reach the page.")


_PREFS_DOC = ("Active preferences keyed to this item (#297), in every scope; scope_type and "
              "scope_value say where each binds. Honour them when working on the item.")
_RULES_DOC = ("Job rules for this item (#297): yes/no questions whose answer, per posting, sets "
              "one of its fields.")


class SearchHit(_Model):
    key: str
    kind: Kind
    title: str
    score: float
    snippet: str
    context: Optional[str] = Field(
        None, description="For a project: key of the role or degree it was done under.")
    preferences: List[ItemPreference] = Field(default_factory=list, description=_PREFS_DOC)
    job_rules: List[JobRuleRef] = Field(default_factory=list, description=_RULES_DOC)


class SearchOutput(_Output):
    results: List[SearchHit] = Field(default_factory=list)


class ListItemsInput(_Model):
    kind: Optional[Kind] = Field(None, description="Only this kind; omit for all.")


class ItemRef(_Model):
    key: str
    kind: Kind
    title: str
    preferences: List[ItemPreference] = Field(default_factory=list, description=_PREFS_DOC)
    job_rules: List[JobRuleRef] = Field(default_factory=list, description=_RULES_DOC)


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
    score: Optional[float] = Field(None, description="suggest_actions only: Jev's probability for "
                                                     "this variant, or, from the fallback, its "
                                                     "overlap with the job's weighted terms.")


class ItemOutput(_Output):
    key: Optional[str] = None
    kind: Optional[Kind] = None
    record: Optional[Dict[str, Any]] = None
    variants: Optional[List[VariantRef]] = Field(
        None, description="An experience's or project's approved bullet variants (#229): "
                          "the user's confirmed wording. Start a revision from one "
                          "(bullets[].from_variant) instead of writing from the raw bullets. "
                          "Absent for other kinds.")
    preferences: List[ItemPreference] = Field(default_factory=list, description=_PREFS_DOC)
    job_rules: List[JobRuleRef] = Field(default_factory=list, description=_RULES_DOC)


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


class ItemChange(_Model):
    key: str
    change: Literal["added", "removed", "revised"]
    title: str
    bullets_added: List[str] = Field(default_factory=list)
    bullets_removed: List[str] = Field(default_factory=list)
    reordered: bool = False


class BulletEdit(_Model):
    item: str = Field(description="The item key (exp:... or proj:...), or a section name for "
                                  "bullets outside any item (achievements).")
    title: str = ""
    change: Literal["revised", "added", "removed"]
    bullet: Optional[str] = Field(
        None, description="'<item key>#b<n>': the bullet on the earlier version (revised, "
                          "removed); absent for added.")
    before: Optional[str] = None
    after: Optional[str] = None
    position: Optional[int] = Field(None, description="Index of the bullet in the edited source.")


class ReorderedItem(_Model):
    item: str
    title: str = ""


class UnmappedRegion(_Model):
    region: str = Field(description="The ART-SECTION key (header, education, skills, ...), "
                                    "'preamble' or 'document'.")
    reason: str
    diff: str = Field("", description="Unified diff of the region's lines outside the bullets.")


class TexEdits(_Model):
    bullets: List[BulletEdit] = Field(default_factory=list)
    reordered: List[ReorderedItem] = Field(
        default_factory=list, description="Items whose bullets only moved.")
    section_order: Optional[Dict[str, List[str]]] = Field(
        None, description="{before, after} when sections moved.")
    unmapped: List[UnmappedRegion] = Field(
        default_factory=list, description="Regions that cannot be read as bullets, as a "
                                          "unified diff. A plan cannot carry these over.")
    truncated: int = Field(0, description="Bullet edits left out of a short summary.")


class EditChanges(_Model):
    since_node: str = Field(description="The last version that was not an editor edit.")
    items: List[ItemChange] = Field(default_factory=list)
    skills_added: List[str] = Field(default_factory=list)
    skills_removed: List[str] = Field(default_factory=list)
    tex_changed: bool = False
    layout_changed: bool = False
    tex_edits: Optional[TexEdits] = None


class EditorEdit(NodeSummary):
    changes: Optional[EditChanges] = None


class GetHeadInput(_Model):
    job_id: str
    since_event: Optional[int] = Field(
        None, description="Cursor from a previous call; returns what changed after it.")


class GetHeadOutput(_Output):
    job_id: Optional[str] = None
    head: Optional[Node] = None
    events: List[TreeEventOut] = Field(default_factory=list)
    editor_edits: List[EditorEdit] = Field(
        default_factory=list, description="Edits the user made in the editor since the cursor. "
                                          "The last node of each run of editor edits carries "
                                          "`changes`, what the run changed (#278).")
    cursor: int = 0


class HistoryInput(_Model):
    job_id: str


class HistoryOutput(_Output):
    nodes: List[NodeSummary] = Field(default_factory=list)


class DiffInput(_Model):
    from_node: str
    to_node: str


class DiffOutput(_Output):
    from_node: Optional[str] = None
    to_node: Optional[str] = None
    items: List[ItemChange] = Field(default_factory=list)
    skills_added: List[str] = Field(default_factory=list)
    skills_removed: List[str] = Field(default_factory=list)
    tex_changed: bool = False
    layout_changed: bool = False
    tex_edits: Optional[TexEdits] = Field(
        None, description="When the hand-edited .tex differs: the bullets it revised, added or "
                          "removed (with the '<item key>#b<n>' id on the earlier version), items "
                          "whose bullets only moved, and unmapped regions as a unified diff (#278).")


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


class RelinkedPreference(_Model):
    preference_id: str = Field(description="The new, keyed preference.")
    supersedes_id: Optional[str] = Field(None, description="The pending one it replaced.")
    text: str
    polarity: Optional[str] = None
    key: str = Field(description="The item the preference now attaches to.")


class UpsertOutput(_Output):
    results: List[UpsertResult] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    relinked: List[RelinkedPreference] = Field(
        default_factory=list,
        description="Preferences that named something the graph lacked and now attach to an item "
                    "this call created (#297). The old, pending row is superseded, not deleted.")


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
                          "devops_infrastructure, security, hardware, other. A saved track "
                          "named exactly like it (save_baseline) is the job's starting point, "
                          "without asking Jev (#199). Otherwise Jev chooses among your saved "
                          "tracks from the posting, and with no Jev the track named after the "
                          "family. Omitted: ART guesses from the title, else other (#229).")


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
    source: Optional[Literal["host", "jev", "cache", "fallback"]] = Field(
        None, description="Who chose this track (#199). host: you gave a role_family named "
                          "exactly like a saved track. jev or cache: Jev chose it from the "
                          "saved tracks by reading the posting (cache: an earlier answer, "
                          "replayed). fallback: Jev did not answer (no key, switched off, an "
                          "error), so the track named after the role family was used.")
    p: Optional[float] = Field(
        None, description="Jev's probability for the chosen track (source jev or cache), else "
                          "None. Not a calibrated confidence.")


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
        None, description="The track baseline this job starts from, if one was saved (#229): "
                          "chosen by Jev among the saved tracks, or named after the role "
                          "family when Jev did not answer (see source). The job's first "
                          "version starts as a copy of it.")


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
    waived: Optional[Dict[str, float]] = Field(
        None, description="Guard regressions (guard -> how far it moved) that did not veto an "
                          "accepted requested delete (#279) or a carried-over editor edit (#306). "
                          "For a carried edit a target that fell (relevance_density, coverage) "
                          "is listed too, when no target improved. Guards never veto a delete "
                          "the user or a preference asked for, nor the user's own words; hard "
                          "gates still do. Absent when none. Tell the user what it cost.")
    carried_edit: Optional[List[Dict[str, Any]]] = Field(
        None, description="Present when the executor verified this revise as the user's own "
                          "editor edit, carried over verbatim (#306): the tex edits it carries "
                          "(change, bullet = the '<item key>#b<n>' id diff_nodes.tex_edits gives "
                          "for a revised bullet, null for an added one, after). The executor "
                          "decides this from the parent's .tex; nothing in the node says so. "
                          "If a hard gate refused it, reason says which gate and claim, and what "
                          "to record with upsert_items before retrying.")
    review: Optional[List[Dict[str, Any]]] = Field(
        None, description="Bullets Jev could not clearly call supported by their cited "
                          "evidence (item, bullet, label, p), bullets the deterministic fallback "
                          "(no Jev answer, #351) thinks stretch their cited evidence (item, bullet, "
                          "label, score, source: fallback, signals: what it read), and bullets or item "
                          "fields Jev "
                          "thinks may mention a pinned topic in other words (check: "
                          "negative_pin, item, where, bullet, pin, p). Show them to the user.")
    semantic: Optional[Dict[str, Any]] = Field(
        None, description="What this node did (for a reverted node, would have done) to semantic "
                          "requirement coverage (#126), as "
                          "requirement ordinals from open_job: covered, of, gained (now "
                          "evidenced by a bullet), lost, and the page's semantic_only and "
                          "literal_only (see semantic_coverage). Absent when Jev did not answer.")


class PlanSummaryCut(_Model):
    bullet: str = Field(description="The cut bullet's id, '<item key>#b<n>'.")
    text: Optional[str] = Field(None, description="Its text on the parent version; null when "
                                                  "the item has no such bullet.")


class PlanSummaryNode(_Model):
    id: str
    op: str
    item_key: str
    title: str = Field(description="The item as the user reads it: role, company, or the "
                                   "project's name.")
    change: str = Field(description="The change in plain words: kept as is, revised with N "
                                    "bullets, replaced X with Y, dropped, cut N bullets #b...")
    because: Optional[str] = Field(None, description="The node's reason as sent.")
    reason: str = Field(description="The reason in plain words; a pref: reason is quoted from "
                                    "the preference itself.")
    status: Optional[str] = Field(None, description="What the dry run made of it (nodes[].status).")
    not_applied: Optional[str] = Field(
        None, description="For a reverted or refused node: why ART would not apply it.")
    replacement_key: Optional[str] = None
    replacement_title: Optional[str] = None
    bullets: Optional[List[str]] = Field(None, description="revise / replace: the new bullets.")
    cut: Optional[List[PlanSummaryCut]] = Field(None, description="delete_bullet: what is cut.")


class PlanSummarySkills(_Model):
    line: List[str] = Field(default_factory=list, description="The skills line, in order.")
    source: Literal["plan", "current", "ranked"] = Field(
        description="plan: the program's skills; current: the parent's, unchanged; ranked: ART's "
                    "ranking for this posting (a job's first version).")
    unknown: List[str] = Field(default_factory=list,
                               description="Skills the plan named that the graph does not hold.")
    suppressed: List[str] = Field(default_factory=list,
                                  description="Skills a hard preference left off.")


class PlanSummary(_Model):
    title: str
    nodes: List[PlanSummaryNode] = Field(default_factory=list,
                                         description="One entry per node, in page order.")
    skills: PlanSummarySkills
    text: str = Field(description="The same as lines. Show it to the user verbatim and wait for "
                                  "their approval before you commit.")


class Violation(_Model):
    check: str
    detail: Any = None
    hint: Optional[str] = None
    rule_id: Optional[str] = Field(
        None, description="For a rules violation: the document rule it is about (#298).")


class ExecuteOutput(_Output):
    active_constraints: Optional[Dict[str, Any]] = Field(
        None, description="Dry runs only (#299), first in the result: what the user has stated "
                          "that binds this job, as suggest_actions returns it. Read it before "
                          "you commit; a plan that breaks a hard pin or loosens a hard rule is "
                          "refused. Absent on a commit.")
    plan_summary: Optional[PlanSummary] = Field(
        None, description="Dry runs only (#392), after active_constraints: the plan in plain words "
                          "for the user's approval. One entry per node (the item, the change, the "
                          "reason from its because, and what the dry run made of it), then the "
                          "skills line; `text` is the same as lines to show the user verbatim. "
                          "Absent on a commit.")
    program_id: Optional[str] = None
    parent: Optional[str] = None
    dry_run: bool = False
    committed: bool = False
    node_id: Optional[str] = None
    reason: Optional[str] = Field(
        None, description="Why nothing was committed when no violation says so: no_change means "
                          "the plan left the page identical to its parent (every node reverted, "
                          "refused or kept, no skills or job-rule change), so no version was made. "
                          "Absent otherwise; see nodes for the per-node reasons.")
    discarded_editor_edits: Optional[TexEdits] = Field(
        None, description="Present when the commit went ahead with discard_editor_edits: the "
                          "parent's hand edits to its .tex that this version does not hold "
                          "(#278). Tell the user what was dropped.")
    nodes: List[NodeResult] = Field(default_factory=list)
    skills: Dict[str, List[str]] = Field(default_factory=dict)
    metrics: Dict[str, Any] = Field(
        default_factory=dict, description="Metric vectors by role (gates, guards, targets, "
                                          "report) for the base and the final version.")
    line_budget: Dict[str, Any] = Field(default_factory=dict)
    violations: List[Violation] = Field(default_factory=list)
    cut_hints: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Cuts that would fit the page, in the order to apply them. Each names the plan "
                    "op that carries it out (#289): a cut_bullet is op delete_bullet (item, "
                    "bullet = the id to put in cut_bullets), a drop_item is op delete. Send them "
                    "as those ops with a `because` (e.g. user:fit the one-page budget), one node "
                    "per item with all of its cut bullets in cut_bullets; a drop_item replaces "
                    "any cut_bullet of the same item. Not a revise: a revise faces every guard "
                    "and MTLD reverts it.")
    rules_applied: List[Dict[str, Any]] = Field(
        default_factory=list, description="Job-scoped rule values written into the base.")
    rules: Optional[Dict[str, Any]] = Field(
        None, description="The document rules in effect on this plan (#298): applied (each with "
                          "rule_id, text, param, value), overridden (cannot take effect, and the "
                          "rule that outranks it), style_notes (words to follow; nothing blocks) "
                          "and excluded_sections. Absent when the user has none.")
    soft_preferences: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Strength 1-4 preferences and soft document rules, each applied or "
                    "not_applied on this page, with a reason when not (#298). Reported, never "
                    "blocking: tell the user which they asked for and the page does not show.")
    baseline: Optional[Dict[str, Any]] = Field(
        None, description="The track baseline this job's first version was copied from "
                          "(track, node_id, role_family, family_source, source, p; source is host, "
                          "jev, cache or fallback, #199). Absent when the job started "
                          "from the whole knowledge graph (#229).")
    support: Optional[Dict[str, Any]] = Field(
        None, description="The cited-bullet support check (#193): how many bullets Jev "
                          "checked and which to review; fallback_checked is how many the "
                          "deterministic fallback read in Jev's place (no key, mode off, an API "
                          "error, #351). Absent when nothing was checked by Jev and the fallback has "
                          "nothing to review (no changed cited bullet, or nothing it flags).")
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
    best_score: float = Field(description="Jev: the best variant's own probability, even "
                                          "when no_match won. Fallback: the best overlap among the "
                                          "item's approved variants, even when below the floor.")
    approved_variants: int = 0
    actions: List[ActionChoice] = Field(default_factory=list,
                                        description="Valid ops, uniform propensities.")
    source: Literal["fallback", "jev", "cache"] = Field(
        "fallback", description="Who chose the variant (#199). jev: a Jev choice over the "
                                "item's approved variants. cache: the same, replayed from an "
                                "earlier answer. fallback: Jev did not answer (no key, switched "
                                "off, an error) or the item has no variant; the word-overlap "
                                "pick.")
    propensity: Optional[Dict[str, float]] = Field(
        None, description="Jev's whole distribution over this item's choices: each approved "
                          "variant id, and no_match. Present only when source is jev or cache. "
                          "The likeliest variant is picked when the probability on all the "
                          "variants together (1 - no_match) reaches the fitted threshold, so two "
                          "close phrasings can each sit under 0.5 and still be picked. Not a "
                          "calibrated confidence.")


class SuggestActionsOutput(_Output):
    active_constraints: Optional[Dict[str, Any]] = Field(
        None, description="Read this before choosing what any item says (#299): everything the user "
                          "has stated that binds this job, with its scope. hard_pins (a plan that "
                          "breaks one is refused), pending_bans, item_preferences, and rules (hard, "
                          "soft, style_notes), plus `text`, the same as a few lines. The scope "
                          "execute_plan enforces. Also first in an execute_plan dry run.")
    job_id: Optional[str] = None
    node_id: Optional[str] = Field(None, description="The version judged; None for a job with "
                                                     "no history (its starting version).")
    floor: Optional[float] = Field(None, description="The word-overlap fallback's floor; Jev's "
                                                     "choice has its own threshold.")
    items: List[ItemSuggestion] = Field(default_factory=list)


# ── document rules (#298) ────────────────────────────────────────────────────

class RecordRuleInput(_Model):
    text: str = Field(description="The rule in the user's own words, verbatim. Never softened or "
                                  "inferred.")
    kind: Literal["limit", "section_order", "style_note"] = Field(
        description="limit: pages, bullets_per_item, max_skills, max_bullet_lines, or a "
                    "<section> set to 0 to leave that section off. section_order: the sections "
                    "first, in order. style_note: tone or wording, pushed to you and never "
                    "blocks.")
    params: Optional[Dict[str, Any]] = Field(
        None, description="limit: {\"pages\": 1}, {\"bullets_per_item\": 3}, {\"max_skills\": 12}, "
                          "{\"max_bullet_lines\": 1} or {\"projects\": 0} (education, experience, "
                          "projects, skills or achievements). section_order: {\"order\": "
                          "[\"skills\", \"experience\"]}. style_note: none. A limit with no params "
                          "is read from the words only when they state exactly one plain thing "
                          "(\"one page\", \"no projects section\"); otherwise it is refused.")
    strength: int = Field(
        3, ge=1, le=5, description="5 is hard: a plan that loosens it is refused, and a narrower "
                                   "scope may only tighten it. 1-4 is a default a plan may "
                                   "override, reported. Use 5 only for what the user insisted on.")
    scope: Literal["global", "role_family", "job"] = Field(
        "global", description="global: every resume. role_family or job: only that one. A "
                              "narrower rule that would loosen a broader hard rule is refused.")
    scope_value: Optional[str] = Field(
        None, description="The role family (e.g. data_science) or the job id; required unless "
                          "global.")
    quote: Optional[str] = Field(None, description="The user's words this rests on.")


class RecordRuleOutput(_Output):
    status: Optional[Literal["stored", "superseded", "already_recorded", "refused"]] = None
    reason: Optional[str] = Field(None, description="Why a rule was refused.")
    rule: Optional[Dict[str, Any]] = Field(
        None, description="The rule as stored: rule_id, kind, params, text, scope, strength, "
                          "hard, and enforcement (how it is applied).")
    supersedes: Optional[str] = Field(None, description="The earlier rule's words it replaced.")
    blocked_by: Optional[Dict[str, Any]] = Field(
        None, description="The broader hard rule a refused rule would have loosened.")
    shape: Optional[str] = Field(None, description="The params shape the kind takes, on a refusal.")


class RetractRuleInput(_Model):
    rule_id: str = Field(description="From art_briefing's rules, or record_rule's result.")


class RetractRuleOutput(_Output):
    status: Optional[Literal["retracted", "already_inactive", "not_found"]] = None
    reason: Optional[str] = None
    rule: Optional[Dict[str, Any]] = None


# ── list_github_repos (#353) ─────────────────────────────────────────────────

class GithubReposInput(_Model):
    page: int = Field(1, ge=1, description="Page of the user's repos, most recently pushed first.")
    per_page: int = Field(20, ge=1, le=50, description="Repos per page (at most 50).")
    include_private: bool = Field(
        True, description="Include private repos. They appear only when the user signed in with "
                          "`artie github login --private`; otherwise this is ignored.")
    include_readme: bool = Field(True, description="Fetch each README excerpt (a few KB).")


class GithubRepo(_Model):
    name: Optional[str] = None
    full_name: str = ""
    description: Optional[str] = None
    languages: List[str] = Field(default_factory=list, description="Biggest first.")
    topics: List[str] = Field(default_factory=list)
    stargazers: int = 0
    pushed_at: Optional[str] = None
    fork: bool = False
    archived: bool = False
    private: bool = False
    html_url: Optional[str] = None
    clone_url: Optional[str] = None
    clone_command: Optional[str] = Field(
        None, description="The clone command for this repo. Run it only after the user agrees.")
    evidence_source: Optional[str] = Field(
        None, description="`github:<repo>`: pass as upsert_items' `source` for records read "
                          "from this repo.")
    readme_excerpt: Optional[str] = Field(None, description="The README's first few KB.")
    readme_truncated: bool = False


class GithubReposOutput(_Output):
    login: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    private_included: bool = False
    page: int = 1
    per_page: int = 20
    next_page: Optional[int] = Field(None, description="Pass as `page` for more; null on the last.")
    repos: List[GithubRepo] = Field(default_factory=list)
    note: Optional[str] = None


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
    needs_user: bool = True      # False: acts on the machine, not on a profile (#353)
    network: bool = False        # reaches a service off this machine (MCP's open-world hint)


def _tools():
    from harness import tools
    return tools


def _github():
    from harness import github
    return github


def _memory():
    from harness import memory
    return memory


_OBSERVE_FIELDS = ("action", "reason", "candidate", "negated", "p", "source", "guess",
                   "preference_id", "note", "version", "context")


def _observe_out(decision: Dict[str, Any]) -> Dict[str, Any]:
    return {k: decision.get(k) for k in _OBSERVE_FIELDS}


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
        lambda uid, role_family=None, job_id=None: _tools().art_briefing(uid, role_family, job_id)),
    ToolSpec(
        "art_pins",
        "The candidate's pins word for word: strength-5 preferences, negative pins included. "
        "Call it to restore them after compaction or when the briefing is out of reach. Same "
        "set and scoping as art_briefing's pins; pass job_id to include a job's own.",
        PinsInput, PinsOutput,
        lambda uid, role_family=None, job_id=None: _tools().art_pins(uid, role_family, job_id)),
    ToolSpec(
        "observe",
        "Run ART's memory gate on one user message. Returns drop (nothing to remember), host "
        "(it may be a standing preference: confirm with the user, then call "
        "record_preference) or write (ART saved a clear, low-stakes preference itself; tell "
        "the user). ART never saves a strength-5 preference or a negative pin without your "
        "confirmation. Claude Code's hook already does this on every prompt; call it yourself "
        "on a host with no hook. For a message that depends on your last reply ('never list "
        "that again'), pass it as previous_turn. Writes.",
        ObserveInput, ObserveOutput,
        lambda uid, text, session_id=None, previous_turn=None: _observe_out(
            _memory().observe(uid, text, session_id, previous_turn=previous_turn)),
        read_only=False),
    ToolSpec(
        "record_preference",
        "Store one standing preference the user stated or confirmed, or refuse it with a "
        "reason. Resolves the target against their knowledge graph (a key, a label, a name or "
        "a skill's alias, never a part of one) and refuses a name that fits several items, "
        "returning the candidates. Keeps a replaced preference, superseded. Strength 5 makes it a hard gate "
        "restored word for word after compaction, and a 5 suppression a negative pin: only for "
        "what the user insisted on. A target about the page (a section, length, order, tone) is "
        "not an item: 'one page' or 'no projects section' is stored as a document rule, and "
        "anything else is sent back to record_rule with nothing stored. Only what the user "
        "said. Writes.",
        RecordPreferenceInput, RecordPreferenceOutput,
        lambda uid, text, polarity, target=None, strength=3, scope="global", scope_value=None,
               quote=None:
            _memory().record_preference(uid, text, polarity, target, strength, scope,
                                        scope_value, quote),
        read_only=False),
    ToolSpec(
        "record_rule",
        "Store one document-level rule the user stated or confirmed: about the page as a whole, "
        "not an item on it. kind limit (pages, bullets per item, skills, bullet lines, or a "
        "section left off), section_order, or style_note (tone and wording: shown to you, never "
        "blocks). A hard rule (strength 5) refuses any plan that loosens it at finalize, and a "
        "narrower scope may only tighten it. Soft rules (1-4) are defaults a plan may override, "
        "reported. Keeps a replaced rule, superseded. Only what the user said. Writes.",
        RecordRuleInput, RecordRuleOutput,
        lambda uid, text, kind, params=None, strength=3, scope="global", scope_value=None,
               quote=None:
            _tools().record_rule(uid, text, kind, params, strength, scope, scope_value, quote),
        read_only=False),
    ToolSpec(
        "retract_rule",
        "Withdraw a document rule the user took back. It stops applying and stays on record. "
        "Writes.",
        RetractRuleInput, RetractRuleOutput,
        lambda uid, rule_id: _tools().retract_rule(uid, rule_id),
        read_only=False),
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
        "track baseline the job starts from, if one was saved: a role_family that names a "
        "saved track exactly wins, otherwise Jev chooses among the saved tracks, and without "
        "Jev the track named after the role family is used (baseline.source says which). "
        "Re-opening updates the job. Writes.",
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
        "edits the user made in the editor. An editor edit to the .tex says which bullets it "
        "revised, added or removed (editor_edits[].changes.tex_edits, kept short; diff_nodes has "
        "all of it). Call before building on a version.",
        GetHeadInput, GetHeadOutput,
        _tree_call(lambda t, uid, job_id, since_event=None: t.get_head(uid, job_id, since_event))),
    ToolSpec(
        "history",
        "Every version of a job's resume, oldest first.",
        HistoryInput, HistoryOutput,
        _tree_call(lambda t, uid, job_id: {"nodes": t.history(uid, job_id)})),
    ToolSpec(
        "diff_nodes",
        "What changed between two versions, by item and bullet. When the user's hand-edited "
        ".tex differs, tex_edits reads it back: per item the bullets it revised, added or "
        "removed (with the '<item key>#b<n>' id, the text before and after), items whose "
        "bullets only moved, and any region it cannot map to a bullet (heading, header, "
        "skills) as a unified diff marked unmapped.",
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
        "uniform propensities. Jev picks among the item's approved variants (source jev, "
        "or cache when replayed) and propensity holds its whole distribution; with no key "
        "or on an error a word-overlap rule picks (source fallback). Start a revision from "
        "the variant (bullets[].from_variant); write from raw facts only on no_match.",
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
        "budget). Commits one version unless a check fails, dry_run is set, or the result would "
        "be identical to its parent (committed false, reason no_change). Every node needs a "
        "short because (job:<what in the posting calls for it>, user:<what they asked> or "
        "pref:<preference_id>); a dry run returns plan_summary, the plan in plain words with "
        "those reasons: show its text to the user and wait for approval before you commit. To "
        "remove whole bullets use op delete_bullet with cut_bullets (ids '<item key>#b<n>' on "
        "the parent version); to drop an item use op delete. A delete or delete_bullet whose "
        "because is user: or pref: faces the hard gates only: guard regressions are accepted "
        "and reported under waived. An ordinary revise never gets that. When the parent carries "
        "hand edits to its .tex that the plan does not hold, nothing commits: the violation "
        "discards_editor_edits lists the edited bullets. Carry them over as nodes (a revise "
        "with the user's text, a delete_bullet for a removed bullet) or, after asking the "
        "user, set discard_editor_edits true. A revise whose new bullets are exactly the "
        "user's edited text, whitespace aside, and which changes nothing else on the item "
        "(the executor checks it against the parent's .tex; you cannot label it) is a "
        "carried-over edit: guards and the target rule do not veto it, regressions are "
        "reported under waived and the node under carried_edit. Hard gates still apply; if "
        "one refuses it, reason names the gate and the claim: record the supporting fact with "
        "upsert_items (or ask the user), cite it, and retry rather than rewording the user's "
        "text. Any other wording is a normal revise. Writes.",
        ExecuteInput, ExecuteOutput,
        lambda uid, program, dry_run=False:
            _executor().execute_plan(uid, program, dry_run=dry_run),
        read_only=False),
    ToolSpec(
        "patch_plan",
        "Amend a saved program by JSON pointer and run it again (e.g. add the delete_bullet or "
        "delete node a cut hint names, or replace /parent to rebase on a new HEAD). Writes.",
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
    ToolSpec(
        "list_github_repos",
        "The signed-in user's own GitHub repos, most recently pushed first, so you can pick "
        "which to read: name, description, languages, topics, stars, last push, fork and "
        "archived flags, URLs, a clone command and a README excerpt. The user must have signed "
        "in once in their own terminal (`artie github login`, or its uvx form when `artie` is not "
        "on PATH); without it you get not_logged_in, whose message gives both. Public repos only unless the token has the repo "
        "scope. Page with next_page. Clone a repo only after the user agrees; store what you "
        "read with upsert_items, source github:<repo> (evidence_source).",
        GithubReposInput, GithubReposOutput,
        lambda uid, page=1, per_page=20, include_private=True, include_readme=True:
            _github().list_github_repos(page, per_page, include_private, include_readme),
        needs_user=False, network=True),
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
    if user_id is None and spec.needs_user:
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
