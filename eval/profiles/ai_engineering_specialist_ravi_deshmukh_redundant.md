# Ravi Deshmukh

ravi.deshmukh@example.com | linkedin.com/in/ravi-deshmukh | github.com/rdeshmukh

## Summary
Computer science undergraduate building retrieval-augmented systems. I spend most of my time on evaluation, because that is where these systems are usually wrong.

## Experience

**Lumen Assistants** — AI Engineering Intern (Jun 2026 – Aug 2026)
- Built a RAG pipeline over 120k support documents with pgvector, lifting answer accuracy from 61% to 79%.
- Cut token spend 40% by adding a prompt cache and trimming 3 redundant context blocks.
- Wrote an evaluation set of 200 graded questions that gated every prompt change.
- Built a retrieval pipeline over 120k support documents with pgvector that lifted answer accuracy to 79%.
- Built the 200-question graded evaluation set that gated every prompt change.
- Built the prompt cache and context trimming that cut token spend 40%.

**Vellore Ridge University AI Studio** — Undergraduate Researcher (Sep 2025 – Present)
- Built LangChain agents that call 6 campus APIs and return grounded answers with citations.
- Ran a 3-model comparison on a 400-question benchmark and published the scoring rubric.
- Shipped a Streamlit interface used by 90 students during a pilot term.

## Projects

**Cite Guard** (github.com/rdeshmukh/cite-guard) — Citation verification for model output
- Flags unsupported claims by checking each sentence against retrieved sources, at 84% agreement with hand labels.
- Serves verification from FastAPI within a 200ms budget per response.
- Checks each generated sentence against its retrieved sources and flags unsupported claims.

**Prompt Ledger** — Prompt versioning tool
- Tracks 500 prompt revisions with diffs and per-version evaluation scores.
- Stores runs in SQLite and exports a side-by-side comparison report.

## Skills
Python, LangChain, OpenAI API, Hugging Face, FastAPI, RAG, pgvector,
prompt engineering, PyTorch, sentence-transformers, Streamlit, SQLite, Docker,
TypeScript, Git

## Education
B.S. Computer Science, Vellore Ridge University (Expected May 2027), GPA: 3.8

## Achievements
**1st Place, Vellore Ridge Hack Week** (2026) — retrieval track, 48 teams
