---
name: art-tailor
description: Tailor the user's resume to a job posting with ART. Use when the user shares a job posting or link and wants their resume tailored, or asks to re-tailor, cut, or rework a resume for a specific role. Reads their knowledge graph, drafts a plan, waits for their approval, then executes and renders a one-page PDF.
---

# Tailor a resume with ART

ART holds the user's facts (the knowledge graph), their pinned preferences, and every
tailored version. You do the reading and writing. ART checks each change and commits
it. Every fact you put on the page must come from an ART tool result. **Never invent a
metric, date, employer, tool or outcome.** If the user needs something the graph
doesn't hold, ask them, and store it with `upsert_items` first (see the `art-setup`
skill).

The tools named below are the ART MCP server's tools.

## 1. Read the brief

- Call `art_briefing` with the posting's role family if it is obvious (for example
  `data_science`, `ml_engineering`, `software_engineering`).
- **Pins are hard rules.** Quote them to yourself and honour them verbatim.
- Note `job_rules`: yes/no questions about the posting, such as whether it requires
  enrollment after the internship. Answer each from the posting's own words, keeping
  the quote. If the posting doesn't say, ask the user.
- Call `get_profile`. If the name is "Default User" or contact fields are empty, ask
  the user and call `update_profile`.

## 2. Open the job

- Get the posting text. If the user gave a URL, fetch it.
- Extract its requirements as atomic statements. `ingest_schema("requirement")` gives
  the shape. Fill `type` (required, preferred or incidental), `criticality` (1–5),
  `terms`, and `source_section` with the posting's own heading.
- Call `open_job` with `jd_text`, `requirements`,
  `metadata: {title, company, url}` and `rule_answers`.
- Keep the `job_id`. Look at `top_terms`: these are the weighted terms the candidate
  can support.
- If any rule comes back `needs_answer`, ask the user, then call
  `open_job(job_id=…, rule_answers=…)` again.

## 3. Read the candidate

- Call `list_items` to see every experience, project, skill, education entry and
  achievement.
- Call `get_item` for each experience and project you might use. It returns the source
  bullets. Evidence ids are `<item key>#b<n>`, the n-th source bullet, counting from 0.
- A project's record says where it was done: `context` is its role or degree, and
  `context_status` is `personal` for the user's own project. Work is stronger evidence
  than coursework; never present a course or personal project as a job. A role's
  record lists its `projects`; an achievement's `project` is what it was won for.
- Call `get_head(job_id)`. If the job already has a version, build on it; the plan's
  `parent` must be its `node_id`. Respect anything under `editor_edits`: the user made
  those changes by hand.

## 4. Draft the plan and get approval

Draft a plan program for `execute_plan`. Its input schema is published with the tool.

- **nodes**, one per experience or project you touch:
  - `keep`: include the item unchanged.
  - `revise`: rewrite its bullets. Give the full new `bullets` list; each bullet
    `cites` the evidence ids it rests on. Set `strategy` to one of `keyword_weave`,
    `quantify`, `tighten` or `reframe`, and `keywords` to the posting terms you are
    weaving in. Only weave terms the cited evidence supports.
  - `replace`: swap one project for another (`replacement_key`).
  - `delete`: drop the item. Set `because` to `user:<what they asked>` or
    `pref:<preference_id>`.
  - `accept.improves`: the targets a node should raise (`coverage`,
    `relevance_density`).
- **skills**: an ordered list of skill names from the graph, with posting terms first.
- Keep each bullet to two rendered lines and the whole page to one.
- **Leave `finalize` out.** Its defaults are ART's calibrated one-page budget: 60 lines,
  plus the skills cap and floor. Don't invent a smaller budget; cut content instead when
  finalize reports `line_budget`.

**Show the user the plan before you execute it.** Show which items lead, what each
revision says, what is cut and why, and the skills line. Then wait for approval.
Execute directly only if the user said to skip approval.

## 5. Execute

Call `execute_plan({program})`. Read the result:

- `nodes[].status`:
  - `accepted` and `kept` went in.
  - `reverted` failed ART's acceptance rule; `reason` says which gate, guard or target.
  - `refused` never ran: an unknown key, an unresolvable cite, or a crossed hard
    preference. It also covers `tombstoned`, which names or cites an item the user
    deleted (don't bring it back), and `negative_pin`, a bullet that mentions a fact the
    user said must never appear, even when cited.
  - Tell the user about anything reverted or refused. Don't silently resubmit the
    same text.
- `committed: false` with `violations`: nothing was saved. Fix the plan with
  `patch_plan(program_id, edits)`, which takes JSON-pointer edits to the saved program.
  `cut_hints` say which bullets or projects to cut to fit the page. `stale_parent`
  means the user edited in the meantime: read `get_head`, then replace `/parent`.
- `rules_applied`: the job-scoped values ART set, such as a graduation date. Mention
  them.

## 6. Render

- Call `render(job_id)`. It writes `resume.tex` and `resume.pdf` and returns their
  paths, `pages` and `line_budget`.
- If `pages` > 1, cut using `line_budget.cut_hints` through `patch_plan`, then render
  again.
- If `pdf_path` is null, give the user the `.tex` path and the `hint`.
- Tell the user where the PDF is.
- If they want to edit by hand, give them the editor command to run in a terminal,
  with the job id filled in:

  ```
  uvx --from "art-mcp[ui] @ git+https://github.com/nathansso/agentic_resume_tailoring" art ui --job <job_id>
  ```

  Their saves come back to you: through the prompt hook if it's installed, and
  otherwise under `editor_edits` in `get_head`. Read `get_head` before building on
  the job again.

## Afterwards

- To see versions, use `history` and `diff_nodes`. To go back, use `checkout`: show
  the diff and confirm with the user first.
- If the user states a lasting preference ("never list coursework"), tell them it
  will be remembered once ART's memory gate ships. For now, follow it in this session.
