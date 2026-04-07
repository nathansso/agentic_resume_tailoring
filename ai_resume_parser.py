
import logging
from typing import Dict, List, Optional, Any
import json
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AIResumeParser:
    """
    AI-powered resume parser that refines Docling output.
    Uses an LLM (if provided) or advanced heuristics to structure ambiguous content
    and link extracted entity back to source evidence.
    """

    def __init__(self, docling_json: Dict, llm_client=None, model_name: str = "gpt-4o-mini"):
        """
        Args:
            docling_json: The output from resume_ingest_docling.py
            llm_client: An object with a chat.completions.create method (e.g., OpenAI client).
                        If None, falls back to heuristic parsing.
            model_name: Model to use if llm_client is provided.
        """
        self.raw_data = docling_json
        self.llm_client = llm_client
        self.model_name = model_name
        self.source_map = self._build_source_map(docling_json)
        
    def _build_source_map(self, data: Dict) -> Dict[int, str]:
        """
        Flatten the document to map ID -> Text for easy retrieval.
        Useful for verification and context construction.
        """
        mapping = {}
        # We need to traverse the raw sections + uncategorized
        # But wait, the input 'docling_json' has structure.
        # Let's assume we can traverse everything that has an 'id'.
        
        def traverse(obj):
            if isinstance(obj, dict):
                if "id" in obj and "text" in obj:
                    mapping[obj["id"]] = obj["text"]
                for k, v in obj.items():
                    traverse(v)
            elif isinstance(obj, list):
                for item in obj:
                    traverse(item)

        traverse(data)
        return mapping

    def parse(self) -> Dict[str, Any]:
        """
        Main entry point. Returns the cleaned, normalized JSON.
        """
        logger.info("Starting AI parsing...")
        
        # 1. Structure the input for the LLM or Heuristics
        # We process section by section, plus the "uncategorized" dump
        
        refined_skills = self._extract_skills()
        refined_projects = self._extract_projects()
        refined_experience = self._extract_experience()
        refined_education = self._extract_education()
        
        return {
            "skills": refined_skills,
            "projects": refined_projects,
            "experience": refined_experience,
            "education": refined_education,
            "meta": {
                "source": self.raw_data.get("source"),
                "method": "llm" if self.llm_client else "heuristic"
            }
        }

    def _extract_skills(self) -> List[Dict]:
        """
        Extract skills from 'skills' section and implicit mentions in other sections.
        """
        # Gather all text candidates
        candidates = []
        source_ids = []
        
        # 1. Explicit skills section
        for item in self.raw_data.get("skills", []):
            candidates.append(f"Explicit: {item['name']}")
            source_ids.extend(item.get("source", []))
            
        # 2. Uncategorized text (potential skills)
        for item in self.raw_data.get("uncategorized", []):
             if len(item["text"]) < 100: # Short text might be skills
                 candidates.append(f"Uncategorized: {item['text']}")
                 source_ids.append(item["id"])

        if self.llm_client:
            return self._llm_extract_skills(candidates, source_ids)
        else:
            return self._heuristic_extract_skills(self.raw_data.get("skills", []))

    def _call_llm(self, messages: List[Dict], response_format=None) -> Any:
        try:
            # Assume OpenAI-compatible client
            kwargs = {
                "model": self.model_name,
                "messages": messages,
                "temperature": 0.0,
            }
            if response_format:
                 kwargs["response_format"] = response_format

            response = self.llm_client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            
            # Auto-clean markdown json code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].strip()
                
            return json.loads(content)
            return json.loads(content)
        except Exception as e:
            logger.error(f"LLM call failed: {e}", exc_info=True)
            # Start of improved error handling
            # Return empty structure but log heavily
            return []

    def _format_context(self, search_sections: List[str] = None) -> str:
        """
        Format relevant sections items as 'ID: Text' for LLM context.
        """
        lines = []
        
        # New Logic: If 'all_raw_items' exists (from updated ingestor), use it.
        # This guarantees we don't miss text due to heuristic categorization.
        all_raw_items = self.raw_data.get("all_raw_items")
        if all_raw_items:
            # Filter logic could go here if search_sections was strict, 
            # but for LLM extraction, more context is usually better or we can rely on ID ranges?
            # For simplicity, pass the whole document text or a relevant window.
            # But the prompt might be specific (e.g. "Extract projects").
            # Let's just dump everything for now as resumes are short.
            for item in all_raw_items:
                lines.append(f"[{item['id']}] {item['text']}")
        else:
            # Fallback for legacy ingest output
            sections_to_scan = search_sections if search_sections else ["uncategorized", "skills", "experience", "projects", "education"]
            if "uncategorized" not in sections_to_scan:
                sections_to_scan.append("uncategorized")

            for sec in sections_to_scan:
                items = self.raw_data.get(sec, [])
                if isinstance(items, list):
                    for item in items:
                        if "text" in item and "id" in item:
                            lines.append(f"[{item['id']}] {item['text']}")

        # Also iterate through the source map to capture everything?
        # If we used all_raw_items, we are good.
        if not lines:
             # Last resort: source map
             lines = [f"[{k}] {v}" for k, v in sorted(self.source_map.items())]
             
        full_context = "\n".join(lines)
        logger.info(f"Generated Context: {len(full_context)} chars. Preview: {full_context[:200]}...")
        return full_context

    def _extract_skills(self) -> List[Dict]:
        """
        Extract skills from 'skills' section and implicit mentions in other sections.
        """
        if not self.llm_client:
             return self._heuristic_extract_skills(self.raw_data.get("skills", []))
        
        context = self._format_context(["skills", "experience", "projects", "uncategorized"])
        
        system_prompt = """
        You are an expert resume parser. Extract a list of technical skills, tools, and languages from the text.
        Return a JSON object with key "skills", where each item is {"name": "SkillName", "source": [source_id_int]}.
        Only cite source IDs that explicitly mention the skill. inferred skills should cite the context that implies them.
        Detect implicit skills (e.g. "built with Airflow" -> Airflow).
        """
        
        user_prompt = f"""
        Context:
        {context}
        
        Analyze the text above and extract skills.
        """
        
        response = self._call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        
        if isinstance(response, dict) and "skills" in response:
            return response["skills"]
        return response if isinstance(response, list) else []

    def _extract_projects(self) -> List[Dict]:
        if not self.llm_client:
            return self.raw_data.get("projects", [])

        context = self._format_context(["projects", "uncategorized"])
        system_prompt = """
        Extract independent projects. 
        Return JSON object with key "projects":
        [{ "name": "Project Name", "bullets": ["desc"], "source": [ids] }]
        """
        response = self._call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context}\nExtract projects."}
        ])
        return response.get("projects", []) if isinstance(response, dict) else []

    def _extract_education(self) -> List[Dict]:
        if not self.llm_client:
            return self.raw_data.get("education", [])
            
        context = self._format_context(["education", "uncategorized"])
        system_prompt = """
        Extract education history.
        Return JSON object with key "education":
        [{ "institution": "Name", "degrees": ["Degree"], "source": [ids] }]
        """
        response = self._call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context}\nExtract education."}
        ])
        return response.get("education", []) if isinstance(response, dict) else []

    def _extract_experience(self) -> List[Dict]:
        if not self.llm_client:
            return self.raw_data.get("experience", [])
            
        context = self._format_context(["experience", "uncategorized"])
        
        system_prompt = """
        You are an expert resume parser. Structure the work experience from the text.
        Return a JSON object with key "experience", list of objects:
        {
            "role_header": "Role, Company, Dates", 
            "bullets": ["bullet text"], 
            "source": [id_of_header, id_of_bullets...]
        }
        Normalize the header. Merge split lines.
        """
        
        user_prompt = f"""
        Context:
        {context}
        
        Extract work experience sections.
        """
        
        response = self._call_llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ])
        
        if isinstance(response, dict) and "experience" in response:
            return response["experience"]
        return []

