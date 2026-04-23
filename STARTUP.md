# ART — Startup Guide

## 1. Prerequisites

- Python 3.11+
- A `.env` file in the project root (copy from `.env.example` if present)

Required env vars:
```
OPENAI_API_KEY=sk-...
GITHUB_USERNAME=your-github-handle   # optional, for GitHub ingestion
```

---

## 2. Install dependencies

```bash
# From the project root
python -m venv .venv
source .venv/Scripts/activate        # Windows/bash
# source .venv/bin/activate           # Mac/Linux

pip install -r requirements.txt

# One-time: install Playwright browser for LinkedIn scraping
playwright install chromium
```

---

## 3. Initialize the database

```bash
python init_project_db.py
```

---

## 4. Launch the TUI

```bash
python -m tui.app
# or
python cli.py tui
```

The window opens full-screen in your terminal.

---

## 5. Layout

```
┌─────────────────────────────────────────────────────┐
│  Header: ART — Agentic Resume Tailoring             │
├────────────┬────────────────────────────────────────┤
│  Jobs      │  [Chat] [Data] [Viz]                   │
│  sidebar   │                                        │
│            │  Chat messages / data tables / charts  │
│            │                                        │
│  + New Job │  [input box]                           │
├────────────┴────────────────────────────────────────┤
│  Footer: F1=Ingest  F2=Data  F3=Tailor  F4=Viz     │
└─────────────────────────────────────────────────────┘
```

**Status bar** (below the header) tells you what to do next.

---

## 6. First-run workflow

### Step 1 — Ingest your resume
Press `F1` or type in the chat box:
```
ingest resume Nathaniel Oliver Resume - 3_27_6.md
```
Supported formats: `.md`, `.docx`, `.pdf`

### Step 2 — (Optional) Ingest GitHub
```
ingest github
```
Uses `GITHUB_USERNAME` from `.env`, or:
```
ingest github <username>
```

### Step 3 — Add a job
Press `Ctrl+N`, fill in title + company, click **Save**.  
Or paste a job description directly into the chat.

### Step 4 — Tailor your resume
Select the job from the sidebar, then press `F3` or type:
```
tailor <path-to-job-description.txt>
```

Results appear in the chat. Tailored files are saved to:
- `tailored_output.json`
- `tailored_resume.md`

---

## 7. Keyboard shortcuts

| Key       | Action                        |
|-----------|-------------------------------|
| `F1`      | Ingestion guide in chat       |
| `F2`      | Switch to Data tab            |
| `F3`      | Tailoring guide in chat       |
| `F4`      | Switch to Viz tab             |
| `Ctrl+N`  | Open/close New Job form       |
| `Ctrl+Q`  | Quit                          |
| `Enter`   | Send chat message             |

---

## 8. Data tab

Press `F2` to browse your ingested profile across four sub-tabs:

| Sub-tab      | Shows                                      |
|--------------|--------------------------------------------|
| Skills       | Skill, source, proficiency, confidence     |
| Experiences  | Title, company, start/end dates            |
| Projects     | Name, URL, description                     |
| Graph        | Knowledge graph — skills linked to exp/projects |

---

## 9. Viz tab

Press `F4` to see terminal charts (powered by `plotext`):

- Skills by source
- Top skills by confidence score
- Most connected nodes in the knowledge graph
- ATS score history over time

---

## 10. CLI (no TUI)

```bash
python cli.py ingest-resume <file>
python cli.py ingest-github [username]
python cli.py ingest-linkedin <url>
python cli.py ingest-linkedin-pdf <pdf>
python cli.py tailor <job_file_or_text>
python cli.py status
```
