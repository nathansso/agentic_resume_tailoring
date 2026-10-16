---
description: Open a tailored resume in ART's local LaTeX editor
argument-hint: [job id, or company/role]
---

I want to edit a tailored resume by hand in ART's local editor.

1. Find the job: $ARGUMENTS. If that's empty or ambiguous, call the ART tool `list_jobs`
   and pick the job I most recently worked on, or ask me.
2. The editor is a local server I run myself. Give me this command to run in a
   terminal, with the job id filled in, and don't run it yourself:

   ```
   uvx --from "art-mcp[ui] @ https://github.com/nathansso/agentic_resume_tailoring/releases/download/v0.1.1/art_mcp-0.1.1-py3-none-any.whl" art ui --job <job_id>
   ```

   It opens http://127.0.0.1:8765/. That wheel has the editor built in, so nothing needs
   building and no checkout is needed. It shares the download the ART server already made,
   so it normally starts at once. Offline, add `UV_OFFLINE=1` to the environment.
3. Tell me that anything I save or drag there comes back to you on my next message,
   and that you'll build on my edits rather than overwrite them: ART won't let a new
   version drop an edit I made to the `.tex` unless I agree to discarding it.
