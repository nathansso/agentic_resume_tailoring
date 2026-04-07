import logging
import requests
import os
import base64
from typing import Dict, List, Any, Optional
from config import GITHUB_TOKEN

logger = logging.getLogger(__name__)

# Files to fetch from repos for deeper skill extraction
DEPENDENCY_FILES = [
    "requirements.txt",
    "setup.py",
    "pyproject.toml",
    "Pipfile",
    "environment.yml",
    "package.json",
    "Cargo.toml",
    "go.mod",
]


class GitHubIngestor:
    def __init__(self, username: str, token: str = GITHUB_TOKEN):
        self.username = username
        self.headers = {"Authorization": f"token {token}"} if token else {}
        self.api_url = "https://api.github.com"

    def ingest(self) -> List[Dict[str, Any]]:
        """
        Fetches all repos for the user (including private if token has access),
        along with dependency files and README content for deeper skill extraction.
        """
        logger.info(f"Fetching GitHub repos for {self.username}")

        # Use authenticated endpoint when token is available to include private repos
        if self.headers:
            repos_url = f"{self.api_url}/user/repos?per_page=100&sort=updated&type=all"
        else:
            repos_url = f"{self.api_url}/users/{self.username}/repos?per_page=100&sort=updated"
        
        try:
            response = requests.get(repos_url, headers=self.headers)
            response.raise_for_status()
            repos = response.json()
            
            project_data = []
            for repo in repos:
                if repo.get("fork"): continue

                # When using authenticated endpoint, filter to target user's repos
                owner = repo.get("owner", {}).get("login", "")
                if owner.lower() != self.username.lower():
                    continue

                repo_name = repo["name"]

                # Fetch languages
                lang_url = repo["languages_url"]
                lang_resp = requests.get(lang_url, headers=self.headers)
                languages = lang_resp.json() if lang_resp.status_code == 200 else {}
                lang_list = list(languages.keys())

                # Only deep-scan repos with relevant languages (skip empty / Java-only / etc.)
                scannable_langs = {"Python", "Jupyter Notebook", "R", "TypeScript", "JavaScript"}
                should_deep_scan = bool(set(lang_list) & scannable_langs)

                # Fetch README
                readme_text = self._fetch_readme(repo_name) if should_deep_scan else None

                # Fetch dependency/config files (only for scannable repos)
                dependencies = self._fetch_dependency_files(repo_name) if should_deep_scan else {}

                project_info = {
                    "name": repo_name,
                    "description": repo.get("description"),
                    "url": repo["html_url"],
                    "stars": repo["stargazers_count"],
                    "updated_at": repo["updated_at"],
                    "languages": lang_list,
                    "readme": readme_text,
                    "dependencies": dependencies,
                }
                project_data.append(project_info)
                
            return project_data

        except Exception as e:
            logger.error(f"GitHub Ingestion failed: {e}")
            return []

    def _fetch_readme(self, repo_name: str) -> Optional[str]:
        """Fetch the README content for a repo (decoded from base64)."""
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/readme"
        try:
            resp = requests.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", "")
                encoding = data.get("encoding", "")
                if encoding == "base64" and content:
                    decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                    # Truncate very long READMEs to avoid blowing up context
                    return decoded[:3000]
                return content[:3000]
        except Exception as e:
            logger.debug(f"Could not fetch README for {repo_name}: {e}")
        return None

    def _fetch_dependency_files(self, repo_name: str) -> Dict[str, str]:
        """Fetch known dependency/config files from the repo root."""
        found = {}
        for filename in DEPENDENCY_FILES:
            url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{filename}"
            try:
                resp = requests.get(url, headers=self.headers)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("content", "")
                    encoding = data.get("encoding", "")
                    if encoding == "base64" and content:
                        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                        found[filename] = decoded[:2000]
                    elif content:
                        found[filename] = content[:2000]
            except Exception as e:
                logger.debug(f"Could not fetch {filename} for {repo_name}: {e}")

        # Also scan for imports in Python and notebook files
        imports = self._extract_imports_from_repo(repo_name)
        if imports:
            found["_detected_imports"] = imports

        return found

    def _extract_imports_from_repo(self, repo_name: str) -> str:
        """Scan .py and .ipynb files (root + one level deep) for import statements."""
        import_lines = set()
        self._scan_directory_for_imports(repo_name, "", import_lines, depth=0, max_depth=1)
        if import_lines:
            return "Detected imports: " + ", ".join(sorted(import_lines))
        return ""

    def _scan_directory_for_imports(self, repo_name: str, path: str,
                                     import_lines: set, depth: int, max_depth: int):
        """Recursively scan a directory for Python/notebook files."""
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{path}"
        try:
            resp = requests.get(url, headers=self.headers)
            if resp.status_code != 200:
                return

            items = resp.json()
            if not isinstance(items, list):
                return

            for item in items:
                fname = item.get("name", "")
                ftype = item.get("type", "")
                fpath = item.get("path", "")

                if ftype == "file":
                    if fname.endswith(".py"):
                        imports = self._get_python_imports(repo_name, fpath)
                        import_lines.update(imports)
                    elif fname.endswith(".ipynb"):
                        imports = self._get_notebook_imports(repo_name, fpath)
                        import_lines.update(imports)
                elif ftype == "dir" and depth < max_depth:
                    self._scan_directory_for_imports(
                        repo_name, fpath, import_lines, depth + 1, max_depth
                    )
        except Exception as e:
            logger.debug(f"Directory scan failed for {repo_name}/{path}: {e}")

    def _get_python_imports(self, repo_name: str, filename: str) -> List[str]:
        """Extract import statements from a Python file."""
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{filename}"
        try:
            resp = requests.get(url, headers=self.headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            content = data.get("content", "")
            if data.get("encoding") == "base64" and content:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                return self._parse_imports(decoded)
        except Exception as e:
            logger.debug(f"Could not read {filename}: {e}")
        return []

    def _get_notebook_imports(self, repo_name: str, filename: str) -> List[str]:
        """Extract import statements from a Jupyter notebook's code cells."""
        import json as json_mod
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{filename}"
        try:
            resp = requests.get(url, headers=self.headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            content = data.get("content", "")
            if data.get("encoding") == "base64" and content:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                nb = json_mod.loads(decoded)
                all_imports = []
                for cell in nb.get("cells", []):
                    if cell.get("cell_type") == "code":
                        source = "".join(cell.get("source", []))
                        all_imports.extend(self._parse_imports(source))
                return all_imports
        except Exception as e:
            logger.debug(f"Could not read notebook {filename}: {e}")
        return []

    @staticmethod
    def _parse_imports(code: str) -> List[str]:
        """Extract top-level package names from Python import statements."""
        import re
        packages = set()
        for line in code.split("\n"):
            line = line.strip()
            # import foo, import foo.bar, import foo as f
            m = re.match(r'^import\s+([\w.]+)', line)
            if m:
                packages.add(m.group(1).split(".")[0])
            # from foo import bar, from foo.bar import baz
            m = re.match(r'^from\s+([\w.]+)\s+import', line)
            if m:
                packages.add(m.group(1).split(".")[0])
        # Filter out stdlib and common non-skill imports
        stdlib = {
            "os", "sys", "re", "json", "math", "random", "datetime",
            "collections", "itertools", "functools", "pathlib", "typing",
            "abc", "io", "time", "copy", "warnings", "logging", "unittest",
            "string", "textwrap", "csv", "hashlib", "struct", "operator",
            "contextlib", "tempfile", "glob", "shutil", "pickle",
            "subprocess", "threading", "multiprocessing", "socket",
            "http", "urllib", "email", "html", "xml", "pdb", "traceback",
            "inspect", "dis", "gc", "weakref", "enum", "dataclasses",
            "statistics", "decimal", "fractions", "numbers",
            "zipfile", "getpass", "argparse", "uuid", "sqlite3",
            "base64", "codecs", "configparser", "pprint", "heapq",
            "queue", "signal", "ctypes", "platform", "site",
        }
        return [p for p in packages if p and p not in stdlib]
