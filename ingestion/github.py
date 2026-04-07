import logging
import requests
import os
from typing import Dict, List, Any
from config import GITHUB_TOKEN

logger = logging.getLogger(__name__)

class GitHubIngestor:
    def __init__(self, username: str, token: str = GITHUB_TOKEN):
        self.username = username
        self.headers = {"Authorization": f"token {token}"} if token else {}
        self.api_url = "https://api.github.com"

    def ingest(self) -> List[Dict[str, Any]]:
        """
        Fetches all public repos for the user.
        """
        logger.info(f"Fetching GitHub repos for {self.username}")
        repos_url = f"{self.api_url}/users/{self.username}/repos?per_page=100&sort=updated"
        
        try:
            response = requests.get(repos_url, headers=self.headers)
            response.raise_for_status()
            repos = response.json()
            
            project_data = []
            for repo in repos:
                if repo.get("fork"): continue # Skip forks? Maybe.

                # Fetch languages
                lang_url = repo["languages_url"]
                lang_resp = requests.get(lang_url, headers=self.headers)
                languages = lang_resp.json() if lang_resp.status_code == 200 else {}

                project_info = {
                    "name": repo["name"],
                    "description": repo["description"],
                    "url": repo["html_url"],
                    "stars": repo["stargazers_count"],
                    "updated_at": repo["updated_at"],
                    "languages": list(languages.keys())
                }
                project_data.append(project_info)
                
            return project_data

        except Exception as e:
            logger.error(f"GitHub Ingestion failed: {e}")
            return []
