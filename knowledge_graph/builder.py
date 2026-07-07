import networkx as nx
import logging
from uuid import UUID
from sqlmodel import Session, select
from typing import List, Dict, Any

from database.db import engine
from database.models import User, Skill, UserSkill, Project, Experience

logger = logging.getLogger(__name__)

class SkillGraphBuilder:
    """Builds the in-memory knowledge graph for ONE user.

    Every query is scoped by user_id (issue #73): the graph previously selected
    all users' rows, contaminating skill matching and graph views across users.
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
                self._add_skills(skills)
                self._add_projects(projects)
                self._add_experiences(experiences)
                self._connect_entities(skills, projects, experiences)

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
        return [self.graph.nodes[n]['name'] for n in self.graph.successors(node)]

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
