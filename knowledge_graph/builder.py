import networkx as nx
import logging
from uuid import UUID
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional

from database.db import engine
from database.models import User, Skill, UserSkill, Project, Experience, Education, Achievement

logger = logging.getLogger(__name__)

class SkillGraphBuilder:
    """Builds the in-memory knowledge graph for ONE user.

    Every query is scoped by user_id (issue #73): the graph previously selected
    all users' rows, contaminating skill matching and graph views across users.

    Nodes: Skill, Project, Experience, Education, Achievement. Edges:
    Project -USES-> Skill and Experience -DEMONSTRATES-> Skill (derived from
    text); Project -PART_OF-> Experience | Education and Achievement
    -AWARDED_FOR-> Project (stored links the user confirmed, see
    agents/project_context.py). With no stored links, the Skill/Project/
    Experience subgraph is exactly what it was before those edges existed.
    """

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self.graph = nx.DiGraph()

    def build_graph(self):
        """
        Constructs the Knowledge Graph from this user's rows in the Database.
        """
        logger.info(f"Building Knowledge Graph for user {self.user_id}...")
        try:
            with Session(engine) as session:
                skills = self._user_skills(session)
                projects = self._user_projects(session)
                experiences = self._user_experiences(session)
                education = list(session.exec(
                    select(Education).where(Education.user_id == self.user_id)).all())
                achievements = list(session.exec(
                    select(Achievement).where(Achievement.user_id == self.user_id)).all())
                self._add_skills(skills)
                self._add_projects(projects)
                self._add_experiences(experiences)
                self._add_education(education)
                self._add_achievements(achievements)
                self._connect_entities(skills, projects, experiences)
                self._connect_contexts(projects, experiences, education, achievements)

            logger.info(f"Graph built with {self.graph.number_of_nodes()} nodes and {self.graph.number_of_edges()} edges.")
            return self.graph
        except Exception as e:
            logger.error(f"Failed to build graph: {e}")
            raise

    def _user_skills(self, session) -> List[Skill]:
        return list(session.exec(
            select(Skill)
            .join(UserSkill, UserSkill.skill_id == Skill.skill_id)
            .where(UserSkill.user_id == self.user_id)
            .distinct()
        ).all())

    def _user_projects(self, session) -> List[Project]:
        return list(session.exec(
            select(Project).where(Project.user_id == self.user_id)
        ).all())

    def _user_experiences(self, session) -> List[Experience]:
        return list(session.exec(
            select(Experience).where(Experience.user_id == self.user_id)
        ).all())

    def _add_skills(self, skills: List[Skill]):
        for s in skills:
            # Basic validation
            if not s.name or len(s.name) > 50 or "\n" in s.name:
                continue
            self.graph.add_node(f"Skill:{s.name}", type="Skill", id=str(s.skill_id), name=s.name, category=s.category)

    def _add_projects(self, projects: List[Project]):
        for p in projects:
            self.graph.add_node(f"Project:{p.name}", type="Project", id=str(p.project_id), name=p.name)

    def _add_experiences(self, experiences: List[Experience]):
        for e in experiences:
            node_id = f"Experience:{e.company} - {e.title}"
            self.graph.add_node(node_id, type="Experience", id=str(e.experience_id), name=e.title, company=e.company)

    @staticmethod
    def _experience_node(e) -> str:
        return f"Experience:{e.company} - {e.title}"

    @staticmethod
    def _education_node(e) -> str:
        return f"Education:{e.institution} - {e.degree or ''}".rstrip(" -")

    def _add_education(self, education: List[Education]):
        for e in education:
            self.graph.add_node(self._education_node(e), type="Education", id=str(e.education_id),
                                name=e.degree or e.institution, institution=e.institution)

    def _add_achievements(self, achievements: List[Achievement]):
        for a in achievements:
            self.graph.add_node(f"Achievement:{a.title}", type="Achievement",
                                id=str(a.achievement_id), name=a.title, issuer=a.issuer)

    def _connect_contexts(self, projects, experiences, education, achievements):
        """Stored links only: where each project was done, and what it won.
        A link to a row that no longer exists is skipped, never guessed."""
        exp_nodes = {e.experience_id: self._experience_node(e) for e in experiences}
        edu_nodes = {e.education_id: self._education_node(e) for e in education}
        proj_nodes = {p.project_id: f"Project:{p.name}" for p in projects}
        for p in projects:
            target = exp_nodes.get(p.experience_id) or edu_nodes.get(p.education_id)
            if target:
                self.graph.add_edge(f"Project:{p.name}", target, relation="PART_OF")
        for a in achievements:
            if a.project_id in proj_nodes:
                self.graph.add_edge(f"Achievement:{a.title}", proj_nodes[a.project_id],
                                    relation="AWARDED_FOR")

    def _connect_entities(self, skills: List[Skill], projects: List[Project], experiences: List[Experience]):
        # 1. Connect Skills to Projects/Experiences

        # Helper for matching
        def normalize(t): return t.lower().strip() if t else ""

        valid_skills = {normalize(s.name): s for s in skills if s.name and len(s.name) < 50}

        # Link Projects -> Skills
        for p in projects:
            p_text = normalize(p.description) + " " + normalize(p.name)
            for s_norm, s_obj in valid_skills.items():
                # Match if skill name is in text (word boundary aware would be better, but simple subset is ok for MVP)
                # Avoid matching short words like "C" or "Go" too aggressively without boundaries?
                # For now, simplistic check.
                if len(s_norm) < 3 and f" {s_norm} " not in p_text:
                    continue

                if s_norm in p_text:
                    self.graph.add_edge(f"Project:{p.name}", f"Skill:{s_obj.name}", relation="USES")

        # Link Experience -> Skills
        for e in experiences:
            # Join bullets with space
            bullets_text = " ".join([str(b) for b in e.bullets]) if e.bullets else ""
            e_text = normalize(e.description) + " " + normalize(e.title) + " " + normalize(bullets_text)

            for s_norm, s_obj in valid_skills.items():
                if len(s_norm) < 3 and f" {s_norm} " not in e_text:
                     continue

                if s_norm in e_text:
                    self.graph.add_edge(f"Experience:{e.company} - {e.title}", f"Skill:{s_obj.name}", relation="DEMONSTRATES")

    def get_skills_for_project(self, project_name: str) -> List[str]:
        node = f"Project:{project_name}"
        if node not in self.graph:
            return []
        # Skill successors only: a project's PART_OF context is a successor too.
        return [self.graph.nodes[n]['name'] for n in self.graph.successors(node)
                if self.graph.nodes[n].get('type') == 'Skill']

    def get_project_context(self, project_name: str) -> Optional[Dict[str, Optional[str]]]:
        """The role or degree a project was done under, or None."""
        node = f"Project:{project_name}"
        if node not in self.graph:
            return None
        for n in self.graph.successors(node):
            data = self.graph.nodes[n]
            if data.get("type") == "Experience":
                return {"type": "Experience", "title": data.get("name"),
                        "company": data.get("company")}
            if data.get("type") == "Education":
                return {"type": "Education", "degree": data.get("name"),
                        "institution": data.get("institution")}
        return None

    def get_awards_for_project(self, project_name: str) -> List[str]:
        """Titles of achievements won for this project."""
        node = f"Project:{project_name}"
        if node not in self.graph:
            return []
        return [self.graph.nodes[n]["name"] for n in self.graph.predecessors(node)
                if self.graph.nodes[n].get("type") == "Achievement"]

    def get_projects_using_skill(self, skill_name: str) -> List[str]:
        # Predecessors of the skill node
        target = f"Skill:{skill_name}"
        if target not in self.graph:
            return []

        sources = []
        for n in self.graph.predecessors(target):
            if self.graph.nodes[n]['type'] == 'Project':
                sources.append(self.graph.nodes[n]['name'])
        return sources

    def get_experiences_using_skill(self, skill_name: str) -> List[Dict[str, Optional[str]]]:
        """Experiences that DEMONSTRATE this skill, as {title, company} dicts.

        The Experience->Skill edge is set in _connect_entities from the role's
        title/description/bullets, so a hit means the skill is evidenced by that
        experience's logged work. Returns title *and* company (not just the node
        name) because titles collide across employers, and the planner keys an
        experience by both. Mirrors get_projects_using_skill for the experience
        side (issue #138)."""
        target = f"Skill:{skill_name}"
        if target not in self.graph:
            return []
        out: List[Dict[str, Optional[str]]] = []
        for n in self.graph.predecessors(target):
            node = self.graph.nodes[n]
            if node.get("type") == "Experience":
                out.append({"title": node.get("name"), "company": node.get("company")})
        return out

    def evidence_for_skills(self, skill_names: List[str]) -> Dict[str, Dict[str, List]]:
        """Graph evidence tying each given (JD) skill to this user's work.

        For every skill, the projects that USE it and the experiences that
        DEMONSTRATE it — including ties the JD-keyword pre-selection in the
        tailoring pipeline misses, such as a required skill a project evidences
        whose name the JD's own prose never repeats (issue #138). build_graph()
        must have run first; skills with no evidence edge are omitted, so an
        empty/sparse graph yields {}.

        This is the single evidence-traversal surface shared by the matcher and
        the tailoring planner — neither reimplements graph traversal on top of
        the raw networkx graph.
        """
        evidence: Dict[str, Dict[str, List]] = {}
        # dict.fromkeys de-dupes while preserving caller order.
        for name in dict.fromkeys(n for n in (skill_names or []) if n):
            projects = self.get_projects_using_skill(name)
            experiences = self.get_experiences_using_skill(name)
            if projects or experiences:
                evidence[name] = {"projects": projects, "experiences": experiences}
                # A role also evidences a skill through the projects done in it
                # (Experience <-PART_OF- Project -USES-> Skill). Kept apart from
                # `experiences`: it is weaker than the role's own text naming the
                # skill. Present only when non-empty, so a graph with no stored
                # links returns exactly the pre-context evidence.
                direct = {(e["title"], e["company"]) for e in experiences}
                via = []
                for pname in projects:
                    ctx = self.get_project_context(pname)
                    if ctx and ctx["type"] == "Experience" \
                            and (ctx["title"], ctx["company"]) not in direct:
                        via.append({"title": ctx["title"], "company": ctx["company"],
                                    "project": pname})
                if via:
                    evidence[name]["experiences_via_projects"] = via
        return evidence
