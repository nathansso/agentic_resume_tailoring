"""One-shot script: generate jake_resume_art.tex, .docx (and .pdf if pdflatex available)
and save them to ~/Downloads. Runs against a fresh in-memory SQLite DB - no side-effects.
"""
import os
import shutil
import sys

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import SQLModel, Session, create_engine, select
import agents.formatter as fmt_module
from agents.formatter import ResumeFormatterAgent
from database.models import Skill, User, UserSkill

_JAKE_CONTENT = {
    "experiences": [
        {
            "title": "Undergraduate Research Assistant",
            "company": "Texas A&M University",
            "start_date": "June 2020",
            "end_date": "Present",
            "location": "College Station, TX",
            "bullets": [
                "Developed a REST API using FastAPI and PostgreSQL to store data from learning management systems",
                "Developed a full-stack web application using Flask, React, PostgreSQL and Docker to analyze GitHub data",
                "Explored ways to visualize GitHub collaboration in a classroom setting",
            ],
        },
        {
            "title": "Information Technology Support Specialist",
            "company": "Southwestern University",
            "start_date": "Sep. 2018",
            "end_date": "Present",
            "location": "Georgetown, TX",
            "bullets": [
                "Communicate with managers to set up campus computers used on campus",
                "Assess and troubleshoot computer problems brought by students, faculty and staff",
                "Maintain upkeep of computers, classroom equipment, and 200 printers across campus",
            ],
        },
    ],
    "projects": [
        {
            "name": "Gitlytics",
            "tech_stack": "Python, Flask, React, PostgreSQL, Docker",
            "dates": "June 2020 -- Present",
            "bullets": [
                "Developed a full-stack web application using Flask serving a REST API with React as the frontend",
                "Implemented GitHub OAuth to get data from user's repositories",
                "Visualized GitHub data to show collaboration",
                "Used Celery and Redis for asynchronous tasks",
            ],
        },
        {
            "name": "Simple Paintball",
            "tech_stack": "Spigot API, Java, Maven, TravisCI, Git",
            "dates": "May 2018 -- May 2020",
            "bullets": [
                "Developed a Minecraft server plugin to entertain kids during free time for a previous job",
                "Published plugin to websites gaining 2K+ downloads and an average 4.5/5-star review",
                "Implemented continuous delivery using TravisCI to build the plugin upon new a release",
            ],
        },
    ],
    "skills_emphasized": ["Python", "Java", "React"],
}


def seed_jake(engine):
    with Session(engine) as s:
        user = User(
            name="Jake Ryan",
            email="jake@su.edu",
            phone="123-456-7890",
            location="Georgetown, TX",
            github_username="jake",
            linkedin_url="https://linkedin.com/in/jake",
        )
        s.add(user)
        s.commit()
        s.refresh(user)

        for name, cat in [
            ("Java", "language"),
            ("Python", "language"),
            ("C/C++", "language"),
            ("SQL", "language"),
            ("JavaScript", "language"),
            ("React", "framework"),
            ("Node.js", "framework"),
            ("Flask", "framework"),
            ("FastAPI", "framework"),
            ("Git", "tool"),
            ("Docker", "tool"),
            ("TravisCI", "tool"),
        ]:
            sk = Skill(name=name, category=cat)
            s.add(sk)
            s.commit()
            s.refresh(sk)
            s.add(UserSkill(user_id=user.user_id, skill_id=sk.skill_id, confidence_score=0.9))
        s.commit()
        s.refresh(user)
        return user


def main():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    fmt_module.engine = engine

    user = seed_jake(engine)
    agent = ResumeFormatterAgent(user.user_id)
    downloads = os.path.join(os.path.expanduser("~"), "Downloads")

    # .tex
    tex = agent.format_tex(_JAKE_CONTENT)
    tex_path = os.path.join(downloads, "jake_resume_art.tex")
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)
    print("Saved .tex  : " + tex_path + "  (" + str(os.path.getsize(tex_path)) + " bytes)")

    # .docx
    docx_bytes = agent.format_docx(_JAKE_CONTENT)
    docx_path = os.path.join(downloads, "jake_resume_art.docx")
    with open(docx_path, "wb") as f:
        f.write(docx_bytes)
    print("Saved .docx : " + docx_path + "  (" + str(os.path.getsize(docx_path)) + " bytes)")

    # .pdf (uses tectonic or pdflatex — formatter resolves engine automatically)
    try:
        pdf_bytes = agent.format_pdf(_JAKE_CONTENT)
        pdf_path = os.path.join(downloads, "jake_resume_art.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        print("Saved .pdf  : " + pdf_path + "  (" + str(os.path.getsize(pdf_path)) + " bytes)")
    except RuntimeError as e:
        print("Skipped .pdf -- " + str(e))


if __name__ == "__main__":
    main()
