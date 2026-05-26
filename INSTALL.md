# Installing ART

**Requirements:** Python 3.11+, Windows (primary), macOS/Linux (best-effort)

---

## Steps

**1. Clone the repository**
```bash
git clone https://github.com/nathansso/agentic_resume_tailoring.git
cd agentic_resume_tailoring
```

**2. Create and activate a virtual environment**
```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (Command Prompt)
.venv\Scripts\activate.bat

# macOS / Linux
source .venv/bin/activate
```

**3. Install dependencies**

**Core install** *(no browser, no ML model — fastest; suits Docker base images)*:
```bash
pip install -r requirements-core.txt
```

**Full install** *(adds LinkedIn web scraping + semantic skill matching)*:
```bash
pip install -r requirements-full.txt
playwright install chromium
```

**Legacy / all-in-one** *(backwards-compatible, same as before)*:
```bash
pip install -r requirements.txt
playwright install chromium
```

> **What each tier includes:**
> - **core** — all TUI + chat features; LinkedIn PDF import works; no browser or ML model needed
> - **full** — core + `playwright` (LinkedIn web scraping) + `sentence-transformers` (semantic skill matching; downloads ~90 MB model on first use)
> - **requirements.txt** — identical to full; kept for backwards compatibility

**3a. Reproducible install from lockfile** *(optional — recommended for CI or Docker)*

`requirements-lock.txt` pins every transitive dependency at an exact version:
```bash
pip install -r requirements-lock.txt
playwright install chromium
```

To regenerate the lockfile after modifying `requirements.txt`:
```bash
python scripts/generate_lockfile.py
```

**4. Configure your API key**
```bash
cp .env.example .env
```
Open `.env` and set `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` if you prefer OpenAI).

**5. Launch the TUI**

Windows — double-click `launch.bat`, or from PowerShell:
```powershell
.\launch.ps1
```

macOS / Linux:
```bash
python -m tui.app
```

Your data is stored under `~/.art/` (created automatically on first launch).

---

## Troubleshooting

**`[CONFIG ERROR] ANTHROPIC_API_KEY is not set`**
→ Open `.env` and add your Anthropic API key. Get one at https://console.anthropic.com/.

**`ModuleNotFoundError`**
→ Make sure the virtual environment is activated before running.

**`python: command not found` on Windows**
→ Ensure Python 3.11+ is on your PATH, or use `py -3.11` instead of `python`.

**TUI shows blank / garbled text**
→ Use Windows Terminal or a modern terminal emulator. The legacy `cmd.exe` does not support rich text rendering.

**GitHub ingestion returns 403**
→ Set `GITHUB_TOKEN` in `.env` with `repo` and `read:user` scopes.

**`pywin32` install error on Linux**
→ Use `requirements-lock.txt` — it annotates `pywin32` with `; sys_platform == "win32"`
  so pip skips it on Linux automatically. Requires pip ≥ 20.

**`ImportError: playwright is required for LinkedIn web scraping`**
→ You installed the core dependencies only. Run:
  `pip install playwright && playwright install chromium`
  or reinstall with: `pip install -r requirements-full.txt && playwright install chromium`

**`Semantic embedding failed, falling back to exact match` in logs**
→ This is a warning, not an error — skill matching continues using exact-name matching.
  To enable semantic matching, run: `pip install sentence-transformers`
  or reinstall with: `pip install -r requirements-full.txt`
