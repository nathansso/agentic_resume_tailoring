"""
CLI for Agentic Resume Tailoring (ART)

Usage:
    python cli.py ingest-resume <file>       Ingest and parse your resume
    python cli.py ingest-github <username>   Fetch GitHub repos and extract skills/projects
    python cli.py ingest-linkedin <pdf>      Parse LinkedIn PDF export
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


def cmd_ingest_resume(args):
    """Ingest and parse a resume file into the database."""
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
    repos = ingestor.ingest()

    if not repos:
        print("No repos found (check username or GITHUB_TOKEN in .env).")
        return

    print(f"Found {len(repos)} repos. Parsing into database...")

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
    """Parse a LinkedIn PDF export into the database."""
    from database.db import init_db
    init_db()

    pdf_path = args.file
    if not Path(pdf_path).exists():
        print(f"Error: File not found: {pdf_path}")
        sys.exit(1)

    print(f"Ingesting LinkedIn profile: {pdf_path}")

    from ingestion.linkedin import LinkedInIngestor
    ingestor = LinkedInIngestor()
    data = ingestor.ingest(pdf_path)

    from agents.parser import ResumeParserAgent
    parser = ResumeParserAgent()
    parser.parse_and_save(data)

    print("LinkedIn profile ingested and parsed successfully.")


def cmd_tailor(args):
    """Run the full tailoring pipeline: analyze job → match skills → tailor resume."""
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
            print(f"  ✓ {skill} [{match_type}, {req}]")

    if result["missing_skills"]:
        print(f"\nMissing Skills ({len(result['missing_skills'])}):")
        for skill in result["missing_skills"]:
            print(f"  ✗ {skill}")

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

    # ingest-linkedin
    p_linkedin = subparsers.add_parser("ingest-linkedin", help="Parse a LinkedIn PDF export")
    p_linkedin.add_argument("file", help="Path to LinkedIn PDF export")

    # tailor
    p_tailor = subparsers.add_parser("tailor", help="Analyze a job and tailor your resume")
    p_tailor.add_argument("job", help="Path to job description file, or paste the text directly")
    p_tailor.add_argument("--resume", help="Path to resume (only needed on first run)", default="")

    # status
    subparsers.add_parser("status", help="Show your profile summary")

    args = parser.parse_args()

    if args.command == "ingest-resume":
        cmd_ingest_resume(args)
    elif args.command == "ingest-github":
        cmd_ingest_github(args)
    elif args.command == "ingest-linkedin":
        cmd_ingest_linkedin(args)
    elif args.command == "tailor":
        cmd_tailor(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
