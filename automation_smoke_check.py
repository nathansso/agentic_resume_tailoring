import asyncio
import subprocess
import sys
import time
from pathlib import Path

from sqlmodel import Session, select
from textual.widgets import Input, Static

from agents.chat import ChatAgent
from database.db import engine
from database.models import JobDescription
from tui.app import ArtApp

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


def _run_cmd(args: list[str]) -> tuple[int, float, str, str]:
    start = time.perf_counter()
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    duration = time.perf_counter() - start
    return proc.returncode, duration, proc.stdout, proc.stderr


def check_chat_command_routing() -> bool:
    print("\n[1/3] Chat semantic command routing test")
    agent = ChatAgent()

    prompt = "could you please shwo me all my skils"
    start = time.perf_counter()
    response = agent.chat(prompt)
    duration = time.perf_counter() - start

    ok = response.startswith("Your skills (") and "| source:" in response
    print(f"  prompt: {prompt}")
    print(f"  duration: {duration:.2f}s")
    print(f"  result: {'PASS' if ok else 'FAIL'}")
    if not ok:
        print("  response preview:")
        print("  " + response[:300].replace("\n", "\\n"))

    # This should be fast because it bypasses LLM for command-like intents.
    fast_enough = duration < 2.0
    print(f"  speed check (<2.0s): {'PASS' if fast_enough else 'WARN'}")
    return ok


async def check_tui_new_job_flow() -> bool:
    print("\n[2/3] TUI new-job flow test")
    app = ArtApp()

    with Session(engine) as session:
        count_before = len(session.exec(select(JobDescription)).all())

    async with app.run_test() as pilot:
        await pilot.pause()

        app.action_new_job()
        await pilot.pause()

        # Save with missing fields should show validation message.
        app._save_new_job()
        await pilot.pause()

        with Session(engine) as session:
            count_after_invalid = len(session.exec(select(JobDescription)).all())
        blocked_invalid_save = count_after_invalid == count_before

        # Now provide valid values and save.
        app.query_one("#job-title-input", Input).value = "Smoke Test Role"
        app.query_one("#job-company-input", Input).value = "SmokeCo"
        app._save_new_job()
        await pilot.pause()

        title_cleared = app.query_one("#job-title-input", Input).value == ""
        company_cleared = app.query_one("#job-company-input", Input).value == ""
        form_hidden = not app.query_one("#job-input-area").has_class("visible")

    # Confirm persisted.
    with Session(engine) as session:
        exists = session.exec(
            select(JobDescription).where(
                JobDescription.title == "Smoke Test Role",
                JobDescription.company == "SmokeCo",
            )
        ).first() is not None
        count_after_valid = len(session.exec(select(JobDescription)).all())

    inserted_once = count_after_valid == count_before + 1

    ok = blocked_invalid_save and exists and inserted_once and title_cleared and company_cleared and form_hidden
    print(f"  invalid save blocked: {'PASS' if blocked_invalid_save else 'FAIL'}")
    print(f"  valid save inserted one row: {'PASS' if inserted_once else 'FAIL'}")
    print(f"  db persisted: {'PASS' if exists else 'FAIL'}")
    print(f"  inputs cleared: {'PASS' if (title_cleared and company_cleared) else 'FAIL'}")
    print(f"  form hidden after save: {'PASS' if form_hidden else 'FAIL'}")
    print(f"  result: {'PASS' if ok else 'FAIL'}")
    return ok


def check_full_cli_pipeline() -> bool:
    print("\n[3/3] Full CLI ingestion + tailoring pipeline")

    steps = [
        [PYTHON, "cli.py", "ingest-resume", "Nathaniel Oliver Resume - 3_27_6.md"],
        [PYTHON, "cli.py", "ingest-github"],
        [PYTHON, "cli.py", "tailor", "test.txt"],
    ]

    all_ok = True
    for idx, cmd in enumerate(steps, start=1):
        code, duration, out, err = _run_cmd(cmd)
        ok = code == 0
        all_ok = all_ok and ok
        print(f"  step {idx}: {' '.join(cmd[1:])}")
        print(f"    exit: {code} | duration: {duration:.2f}s | {'PASS' if ok else 'FAIL'}")
        if not ok:
            preview = (err or out)[-800:]
            print("    error preview:")
            for line in preview.splitlines()[-8:]:
                print(f"      {line}")

    out_json = ROOT / "tailored_output.json"
    out_md = ROOT / "tailored_resume.md"
    has_outputs = out_json.exists() and out_json.stat().st_size > 0 and out_md.exists() and out_md.stat().st_size > 0
    print(f"  output files present/non-empty: {'PASS' if has_outputs else 'FAIL'}")

    return all_ok and has_outputs


def main() -> int:
    ok1 = check_chat_command_routing()
    ok2 = asyncio.run(check_tui_new_job_flow())
    ok3 = check_full_cli_pipeline()

    print("\n=== SUMMARY ===")
    print(f"chat command routing: {'PASS' if ok1 else 'FAIL'}")
    print(f"tui new-job flow: {'PASS' if ok2 else 'FAIL'}")
    print(f"full cli pipeline: {'PASS' if ok3 else 'FAIL'}")

    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
