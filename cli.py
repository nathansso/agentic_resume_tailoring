"""
CLI for Agentic Resume Tailoring (ART)

Usage:
    python cli.py ingest-resume <file>       Ingest and parse your resume
    python cli.py ingest-github [username]   Fetch GitHub repos and extract skills/projects
    python cli.py ingest-linkedin <url>      Scrape LinkedIn profile via browser
    python cli.py ingest-linkedin-pdf <pdf>  Parse LinkedIn PDF export (fallback)
    python cli.py tailor <job_file_or_text>   Analyze job + match + tailor resume
    python cli.py status                      Show your profile summary
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from config import logger as root_logger

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def _check_config() -> None:
    """Ensure app dirs exist and validate config; exit with error messages if critical issues found."""
    from config import ensure_app_dirs
    ensure_app_dirs()
    from config_validator import validate_config
    errors = validate_config()
    if errors:
        for err in errors:
            print(f"[CONFIG ERROR] {err}", file=sys.stderr)
        sys.exit(1)


def cmd_ingest_resume(args):
    """Ingest and parse a resume file into the database."""
    _check_config()
    from database.db import init_db
    init_db()

    resume_path = args.file
    if not Path(resume_path).exists():
        print(f"Error: File not found: {resume_path}")
        sys.exit(1)

    print(f"Ingesting resume: {resume_path}")

    if resume_path.endswith(".md"):
        with open(resume_path, "r", encoding="utf-8") as f:
            text = f.read()
        ingestion_data = {
            "source_file": resume_path,
            "full_text": text,
            "parsed_sections": {},
        }
    else:
        from ingestion.resume import ResumeIngestor
        ingestor = ResumeIngestor()
        ingestion_data = ingestor.ingest(resume_path)

    from agents.parser import ResumeParserAgent
    parser = ResumeParserAgent()
    parser.parse_and_save(ingestion_data)

    print("Resume ingested and parsed successfully.")


def cmd_ingest_github(args):
    """Fetch GitHub repos and parse them into the database."""
    _check_config()
    from database.db import init_db
    from config import GITHUB_USERNAME
    init_db()

    username = args.username or GITHUB_USERNAME
    if not username:
        print("Error: Provide a username or set GITHUB_USERNAME in .env")
        sys.exit(1)

    print(f"Fetching GitHub repos for: {username}")

    from ingestion.github import GitHubIngestor
    ingestor = GitHubIngestor(username=username)
    force = getattr(args, 'force', False)
    repos = ingestor.ingest(force=force)

    if not repos:
        print("No new or updated repos to process.")
        return

    print(f"Found {len(repos)} new/updated repos. Parsing into database...")

    # Build a rich text summary of all repos for the parser to extract skills/projects
    lines = []
    for repo in repos:
        desc = repo.get('description') or 'No description'
        langs = ', '.join(repo.get('languages', []))
        lines.append(f"Project: {repo['name']}")
        lines.append(f"Description: {desc}")
        lines.append(f"Languages: {langs}")
        lines.append(f"URL: {repo.get('url', '')}")

        # Include README content for deeper analysis
        readme = repo.get('readme')
        if readme:
            lines.append(f"README:\n{readme}")

        # Include dependency files for library/framework extraction
        deps = repo.get('dependencies', {})
        for dep_file, dep_content in deps.items():
            lines.append(f"{dep_file}:\n{dep_content}")

        lines.append("")

    combined_text = '\n'.join(lines)

    from agents.parser import ResumeParserAgent
    parser = ResumeParserAgent()
    parser.parse_and_save({
        "source_file": f"github:{username}",
        "full_text": combined_text,
        "parsed_sections": {},
    })

    print(f"GitHub data ingested: {len(repos)} repos parsed.")


def cmd_ingest_linkedin(args):
    """Scrape a LinkedIn profile via Playwright browser automation."""
    _check_config()
    from database.db import init_db
    init_db()

    profile_url = args.url
    print(f"Scraping LinkedIn profile: {profile_url}")

    from ingestion.linkedin import LinkedInIngestor
    ingestor = LinkedInIngestor()

    try:
        data = ingestor.ingest_web(profile_url)
    except RuntimeError as e:
        print(f"\nError: {e}")
        sys.exit(1)

    from agents.parser import ResumeParserAgent
    parser = ResumeParserAgent()
    parser.parse_and_save(data)

    print("LinkedIn profile scraped and parsed successfully.")


def cmd_ingest_linkedin_pdf(args):
    """Parse a LinkedIn PDF export into the database (fallback)."""
    _check_config()
    from database.db import init_db
    init_db()

    pdf_path = args.file
    if not Path(pdf_path).exists():
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    print(f"Ingesting LinkedIn PDF: {pdf_path}")

    from ingestion.linkedin import LinkedInIngestor
    ingestor = LinkedInIngestor()
    data = ingestor.ingest_pdf(pdf_path)

    from agents.parser import ResumeParserAgent
    parser = ResumeParserAgent()
    parser.parse_and_save(data)

    print("LinkedIn PDF ingested and parsed successfully.")


def cmd_tailor(args):
    """Run the full tailoring pipeline: analyze job → match skills → tailor resume."""
    _check_config()
    from database.db import init_db
    init_db()

    from graph.pipeline import build_pipeline

    # Determine job input
    job_text = None
    job_file = None
    source = args.job

    if Path(source).exists():
        job_file = source
        print(f"Using job description file: {job_file}")
    else:
        job_text = source
        print("Using provided text as job description")

    # Determine resume path (for first-time users)
    resume_path = getattr(args, "resume", None) or ""

    pipeline = build_pipeline()
    print("\nRunning tailoring pipeline...\n")

    result = pipeline.invoke({
        "resume_path": resume_path,
        "job_text": job_text or "",
        "job_file": job_file or "",
        "user_id": "",
        "job_id": "",
        "result_id": "",
        "resume_text": "",
        "ats_score": 0.0,
        "matched_skills": {},
        "missing_skills": [],
        "tailored_content": {},
        "formatted_resume": "",
        "status": "",
    })

    # Display results
    print("\n" + "=" * 60)
    print("TAILORING RESULTS")
    print("=" * 60)
    print(f"Status: {result['status']}")
    print(f"ATS Score: {result['ats_score']}%")

    if result["matched_skills"]:
        print(f"\nMatched Skills ({len(result['matched_skills'])}):")
        for skill, info in result["matched_skills"].items():
            match_type = info.get("match_type", "unknown")
            req = "Required" if info.get("required") else "Preferred"
            print(f"  [OK] {skill} [{match_type}, {req}]")

    if result["missing_skills"]:
        print(f"\nMissing Skills ({len(result['missing_skills'])}):")
        for skill in result["missing_skills"]:
            print(f"  [MISS] {skill}")

    if result["tailored_content"] and "error" not in result["tailored_content"]:
        # Save tailored output to file
        output_path = Path("tailored_output.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result["tailored_content"], f, indent=2)
        print(f"\nTailored content saved to: {output_path}")

        # Save formatted markdown resume
        if result.get("formatted_resume"):
            md_path = Path("tailored_resume.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(result["formatted_resume"])
            print(f"Formatted resume saved to: {md_path}")

        # Also print a summary
        tc = result["tailored_content"]
        if "experiences" in tc:
            print(f"\nTailored Experiences ({len(tc['experiences'])}):")
            for exp in tc["experiences"]:
                print(f"  - {exp.get('title', '?')} at {exp.get('company', '?')}")
        if "projects" in tc:
            print(f"\nTailored Projects ({len(tc['projects'])}):")
            for proj in tc["projects"]:
                print(f"  - {proj.get('name', '?')} (style: {proj.get('selected_style', '?')})")
        if "skills_emphasized" in tc:
            print(f"\nSkills Emphasized: {', '.join(tc['skills_emphasized'])}")
    elif result["tailored_content"]:
        print(f"\nTailoring error: {result['tailored_content'].get('error')}")

    print()


def cmd_status(args):
    """Show the current user profile summary."""
    _check_config()
    from database.db import init_db, engine
    from sqlmodel import Session, select
    from database.models import User, UserSkill, Skill, Experience, Project, UserJobResult

    init_db()

    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            print("No user profile found. Run 'ingest-resume' first.")
            return

        skills = session.exec(select(UserSkill).where(UserSkill.user_id == user.user_id)).all()
        experiences = session.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        projects = session.exec(select(Project).where(Project.user_id == user.user_id)).all()
        results = session.exec(select(UserJobResult).where(UserJobResult.user_id == user.user_id)).all()

        print("=" * 50)
        print("USER PROFILE SUMMARY")
        print("=" * 50)
        print(f"Name: {user.name}")
        print(f"Email: {user.email}")
        print(f"Skills: {len(skills)}")
        print(f"Experiences: {len(experiences)}")
        print(f"Projects: {len(projects)}")
        print(f"Job Matches: {len(results)}")

        if skills:
            print("\nSkills:")
            for us in skills:
                skill = session.exec(select(Skill).where(Skill.skill_id == us.skill_id)).first()
                if skill:
                    print(f"  - {skill.name} (proficiency: {us.proficiency}, source: {us.evidence_source})")

        if experiences:
            print("\nExperiences:")
            for e in experiences:
                print(f"  - {e.title} at {e.company} ({e.start_date} – {e.end_date})")

        if projects:
            print("\nProjects:")
            for p in projects:
                print(f"  - {p.name}")

        if results:
            print("\nJob Match History:")
            for r in results:
                print(f"  - Score: {r.ats_score}% | Status: {r.verification_status} | {r.created_at:%Y-%m-%d}")

        print()


def cmd_supabase_setup(args):
    """Apply the Phase 2 schema migration and RLS policies to the Supabase database."""
    import os
    from pathlib import Path
    from database.db import engine

    db_url = os.getenv("DATABASE_URL", "")
    if not db_url.startswith("postgresql"):
        print("Error: DATABASE_URL must point to a PostgreSQL (Supabase) database.")
        print("Set DATABASE_URL=postgresql://... in your .env and try again.")
        sys.exit(1)

    base = Path(__file__).parent / "supabase"
    files = [
        ("Phase 2 schema migration", base / "migrations" / "phase2_auth.sql"),
        ("RLS policies", base / "rls_policies.sql"),
    ]

    for label, path in files:
        if not path.exists():
            print(f"Error: {path} not found.")
            sys.exit(1)
        sql = path.read_text()
        print(f"Applying {label} ({path.name})...")
        try:
            with engine.raw_connection() as raw_conn:
                raw_conn.autocommit = True
                cursor = raw_conn.cursor()
                cursor.execute(sql)
                cursor.close()
            print(f"  OK")
        except Exception as exc:
            print(f"  Error: {exc}")
            sys.exit(1)

    print("\nSupabase Phase 2 setup complete.")
    print("Schema columns added, RLS policies enabled.")
    print("Users can now sign up and log in with username + password.")


def cmd_chat_eval(args):
    """Run the synthetic chat evaluation harness against one or all scenarios."""
    import tempfile
    from pathlib import Path
    from sqlmodel import SQLModel, create_engine, Session
    from database import db as db_module
    from database import user_utils as uu_module
    import agents.chat as chat_module
    import tui.services as services_module
    import knowledge_graph.builder as kg_module
    import tui.app as tui_module_ref

    # Use an isolated in-memory DB so eval runs never touch the user's real data.
    tmp_db = tempfile.mktemp(suffix=".db", prefix="art_eval_")
    eval_engine = create_engine(
        f"sqlite:///{tmp_db}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eval_engine)

    # Patch module-level engines to the isolated DB.
    import database.db as _db_mod
    import database.user_utils as _uu_mod
    _db_mod.engine = eval_engine
    chat_module.engine = eval_engine
    services_module.engine = eval_engine
    kg_module.engine = eval_engine
    _uu_mod.engine = eval_engine

    # Point active profile file to a temp location.
    import uuid as _uuid_mod
    profile_tmp = Path(tmp_db).parent / f"active_profile_{_uuid_mod.uuid4().hex}"
    _uu_mod.ACTIVE_PROFILE_FILE = profile_tmp

    from verification.chat_eval.scenario_loader import load_scenario, load_all_scenarios, seed_scenario_db
    from verification.chat_eval.synthetic_user import SyntheticUserAgent
    from verification.chat_eval.runner import EvalRunner

    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else None

    # Resolve scenarios to run.
    if getattr(args, "scenario", None):
        try:
            scenarios = [load_scenario(args.scenario)]
        except FileNotFoundError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    else:
        scenarios = load_all_scenarios()

    if not scenarios:
        print("No scenarios found.")
        sys.exit(0)

    mode = getattr(args, "mode", "canonical")
    n_variants = max(1, getattr(args, "variants", 1))
    stub = not getattr(args, "live", False)

    print(f"Running {len(scenarios)} scenario(s) | mode={mode} variants={n_variants} stub={stub}")

    all_results = []
    with EvalRunner(stub=stub, output_dir=output_dir or Path.home() / ".art" / "evals") as runner:
        for scenario in scenarios:
            # Re-seed the DB for each scenario.
            seed_scenario_db(scenario)

            agent_obj = SyntheticUserAgent(scenario, mode=mode)
            variants = agent_obj.generate_variants(n=n_variants)

            for i, turns in enumerate(variants):
                vid = f"v{i+1}" if len(variants) > 1 else ""
                label = f"{scenario['scenario_id']}{vid}"
                print(f"  {label} ({len(turns)} turn(s)) ...", end=" ", flush=True)
                result = runner.run_scenario(scenario, turns)
                result["variant_id"] = vid
                result["scenario_id"] = label
                all_results.append(result)
                status = "PASS" if result["score"]["passed"] else "FAIL"
                print(status)

        run_dir = runner.write_artifacts(all_results)

    passed = sum(1 for r in all_results if r["score"]["passed"])
    print(f"\n{passed}/{len(all_results)} passed")
    print(f"artifacts: {run_dir}")
    print(f"report:    {run_dir / 'report.md'}")

    # Cleanup temp DB.
    try:
        Path(tmp_db).unlink(missing_ok=True)
        profile_tmp.unlink(missing_ok=True)
    except Exception:
        pass

    if passed < len(all_results):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="art",
        description="Agentic Resume Tailoring (ART) — Tailor your resume to job descriptions using local AI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ingest-resume
    p_ingest = subparsers.add_parser("ingest-resume", help="Ingest and parse your resume")
    p_ingest.add_argument("file", help="Path to resume file (PDF, DOCX, or MD)")

    # ingest-github
    p_github = subparsers.add_parser("ingest-github", help="Fetch GitHub repos and extract skills/projects")
    p_github.add_argument("username", nargs="?", default=None, help="GitHub username (defaults to GITHUB_USERNAME in .env)")
    p_github.add_argument("--force", action="store_true", help="Re-scan all repos even if unchanged since last scan")

    # ingest-linkedin (web scraping)
    p_linkedin = subparsers.add_parser("ingest-linkedin", help="Scrape your LinkedIn profile via browser")
    p_linkedin.add_argument("url", help="LinkedIn profile URL or username (e.g. https://linkedin.com/in/username)")

    # ingest-linkedin-pdf (fallback)
    p_linkedin_pdf = subparsers.add_parser("ingest-linkedin-pdf", help="Parse a LinkedIn PDF export (fallback)")
    p_linkedin_pdf.add_argument("file", help="Path to LinkedIn PDF export")

    # tailor
    p_tailor = subparsers.add_parser("tailor", help="Analyze a job and tailor your resume")
    p_tailor.add_argument("job", help="Path to job description file, or paste the text directly")
    p_tailor.add_argument("--resume", help="Path to resume (only needed on first run)", default="")

    # status
    subparsers.add_parser("status", help="Show your profile summary")

    # tui
    subparsers.add_parser("tui", help="Launch interactive TUI")

    # supabase-setup
    subparsers.add_parser(
        "supabase-setup",
        help="Apply Phase 2 schema migration + RLS policies to Supabase (requires DATABASE_URL)",
    )

    # chat-eval
    p_eval = subparsers.add_parser("chat-eval", help="Run synthetic chat evaluation harness")
    p_eval.add_argument("--scenario", default=None, help="Run a single scenario by ID")
    p_eval.add_argument("--variants", type=int, default=1, help="Number of paraphrase variants per scenario")
    p_eval.add_argument("--mode", choices=["canonical", "synthetic", "mixed"], default="canonical",
                        help="Turn generation mode")
    p_eval.add_argument("--stubbed", dest="stubbed", action="store_true", default=True,
                        help="Stub heavy side effects (default)")
    p_eval.add_argument("--live", dest="live", action="store_true", default=False,
                        help="Run with real services (no stubs)")
    p_eval.add_argument("--output-dir", dest="output_dir", default=None,
                        help="Override artifact output directory")

    args = parser.parse_args()

    if args.command == "ingest-resume":
        cmd_ingest_resume(args)
    elif args.command == "ingest-github":
        cmd_ingest_github(args)
    elif args.command == "ingest-linkedin":
        cmd_ingest_linkedin(args)
    elif args.command == "ingest-linkedin-pdf":
        cmd_ingest_linkedin_pdf(args)
    elif args.command == "tailor":
        cmd_tailor(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "tui":
        from tui.app import main as tui_main
        tui_main()
    elif args.command == "supabase-setup":
        cmd_supabase_setup(args)
    elif args.command == "chat-eval":
        cmd_chat_eval(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
