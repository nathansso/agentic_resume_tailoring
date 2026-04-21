# ART Product PRD Sequence

This folder contains prompt-ready PRDs you can hand to Claude Code in order.

The current repo already has the right foundation:
- A Textual TUI
- A LangGraph tailoring pipeline
- SQLModel with SQLite
- Resume, GitHub, and LinkedIn ingestion paths
- A basic chat agent with direct command routing

What it does not have yet is product structure. The main gaps are workflow design, latency discipline, multi-user boundaries, and desktop-app packaging.

## Recommended Build Order

1. `01-foundation-tui-workflow.md`
2. `02-chat-latency-model-routing.md`
3. `03-onboarding-ingestion-knowledge-graph.md`
4. `04-job-tailoring-chat-revision.md`
5. `05-desktop-productization-data-security.md`

## Key Product Decisions

### 1. Immediate priority
Do not start with a full app rewrite. First stabilize the current TUI, make the workflow explicit, and remove avoidable latency.

### 2. LLM direction
Move away from Ollama as the primary runtime for end users. Use a provider-agnostic cloud model layer.

Recommended starting point:
- Primary fast model: OpenAI for low-latency chat and structured extraction
- Optional second provider: Anthropic for higher-quality long-form resume rewriting

Pragmatic rule:
- Use one fast, cheap model for routing, extraction, and conversational turns
- Use one stronger model only for final tailored resume generation when needed

### 3. SQLite vs Supabase
For a local-first downloadable product, keep SQLite for v1.

Use Supabase only when you need one or more of these:
- Cross-device sync
- Hosted auth
- Remote backup
- Team or recruiter collaboration
- A web companion product

Supabase is not a direct improvement over SQLite for a single-user local desktop TUI. It adds operational complexity and a data-leak surface you do not currently need.

### 4. User data isolation
The current codebase behaves like a single-user prototype in several places. To make this product plausible, you need:
- A first-class user profile entity and active-profile context
- No global `select(User).limit(1)` behavior in application paths
- All reads and writes scoped by `user_id`
- User-specific file storage and artifact paths
- Secrets stored in OS keychain or equivalent, not plain logs or shared config
- If you later add Supabase, row-level security on every user-owned table

## Delivery Guidance For Claude Code

For each PRD:
- Ask Claude Code to implement only that PRD
- Require tests and smoke checks for the changed flows
- Require no unrelated refactors
- Keep feature flags or config switches where migration risk is high

## Success Definition

At the end of this sequence, ART should behave like a coherent local product:
- A user launches a desktop TUI
- Completes onboarding once
- Sees a persistent profile and knowledge graph
- Creates jobs and tailors resumes from a guided workflow
- Chats to refine the result
- Saves tailored outputs locally with clear ownership boundaries