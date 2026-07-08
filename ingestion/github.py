import logging
import json
import requests
import os
import base64
from typing import Dict, List, Any, Optional
from pathlib import Path
from config import GITHUB_TOKEN, BASE_DIR

logger = logging.getLogger(__name__)

# Cache file to track when repos were last scanned
GITHUB_CACHE_FILE = BASE_DIR / ".github_cache.json"

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

# Cap on how many files get their content fetched during import scanning, so
# scan cost is bounded regardless of repo size (issue #74).
MAX_IMPORT_SCAN_FILES = 15


class GitHubRateLimitError(Exception):
    """Raised when GitHub's API rate limit is hit (403 + X-RateLimit-Remaining: 0)."""


class GitHubIngestor:
    REQUEST_TIMEOUT = 15  # seconds per API call

    def __init__(self, username: str, token: str = GITHUB_TOKEN):
        self.username = username
        self.headers = {"Authorization": f"token {token}"} if token else {}
        self.api_url = "https://api.github.com"
        self._cache = self._load_cache()

    def _get(self, url: str) -> requests.Response:
        """Single checkpoint for all outbound GitHub API calls — detects rate limiting."""
        resp = requests.get(url, headers=self.headers, timeout=self.REQUEST_TIMEOUT)
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubRateLimitError(resp.headers.get("X-RateLimit-Reset"))
        return resp

    def _load_cache(self) -> Dict[str, str]:
        """Load the repo scan cache (repo_name -> updated_at timestamp)."""
        if GITHUB_CACHE_FILE.exists():
            try:
                return json.loads(GITHUB_CACHE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupted GitHub cache — starting fresh")
        return {}

    def _save_cache(self):
        """Persist the repo scan cache to disk."""
        GITHUB_CACHE_FILE.write_text(
            json.dumps(self._cache, indent=2), encoding="utf-8"
        )

    def ingest(self, force: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches all repos for the user (including private if token has access),
        along with dependency files and README content for deeper skill extraction.
        Skips repos that haven't been updated since the last scan (use force=True to override).
        """
        logger.info(f"Fetching GitHub repos for {self.username}")

        # Use authenticated endpoint when token is available to include private repos
        if self.headers:
            repos_url = f"{self.api_url}/user/repos?per_page=100&sort=updated&type=all"
        else:
            repos_url = f"{self.api_url}/users/{self.username}/repos?per_page=100&sort=updated"
        
        try:
            response = self._get(repos_url)
            response.raise_for_status()
            repos = response.json()
            logger.info(f"Found {len(repos)} total repos (before filtering)")
            
            project_data = []
            skipped = 0
            for repo in repos:
                if repo.get("fork"): continue

                # When using authenticated endpoint, filter to target user's repos
                owner = repo.get("owner", {}).get("login", "")
                if owner.lower() != self.username.lower():
                    continue

                repo_name = repo["name"]
                updated_at = repo["updated_at"]

                # Skip repos that haven't changed since last scan
                if not force and self._cache.get(repo_name) == updated_at:
                    logger.info(f"Skipping repo (unchanged): {repo_name}")
                    skipped += 1
                    continue

                logger.info(f"Processing repo: {repo_name}")

                # Fetch languages
                lang_url = repo["languages_url"]
                lang_resp = self._get(lang_url)
                languages = lang_resp.json() if lang_resp.status_code == 200 else {}
                lang_list = list(languages.keys())

                # Only deep-scan repos with relevant languages (skip empty / Java-only / etc.)
                scannable_langs = {"Python", "Jupyter Notebook", "R", "TypeScript", "JavaScript"}
                should_deep_scan = bool(set(lang_list) & scannable_langs)

                # One tree call replaces the old per-directory recursive scan (issue #74)
                tree = self._fetch_tree(repo_name) if should_deep_scan else []

                # Fetch README
                readme_text = self._fetch_readme(repo_name) if should_deep_scan else None

                # Fetch dependency/config files (only for scannable repos)
                dependencies = self._fetch_dependency_files(repo_name, tree) if should_deep_scan else {}

                project_info = {
                    "name": repo_name,
                    "description": repo.get("description"),
                    "url": repo["html_url"],
                    "stars": repo["stargazers_count"],
                    "updated_at": updated_at,
                    "languages": lang_list,
                    "readme": readme_text,
                    "dependencies": dependencies,
                }
                project_data.append(project_info)

                # Update cache with this repo's timestamp
                self._cache[repo_name] = updated_at

            # Persist cache after processing all repos
            self._save_cache()

            if skipped:
                logger.info(f"Skipped {skipped} unchanged repos, scanned {len(project_data)} new/updated repos")
                
            return project_data

        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.error(f"GitHub Ingestion failed: {e}")
            return []

    def _fetch_readme(self, repo_name: str) -> Optional[str]:
        """Fetch the README content for a repo (decoded from base64)."""
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/readme"
        try:
            resp = self._get(url)
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("content", "")
                encoding = data.get("encoding", "")
                if encoding == "base64" and content:
                    decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                    # Truncate very long READMEs to avoid blowing up context
                    return decoded[:3000]
                return content[:3000]
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.debug(f"Could not fetch README for {repo_name}: {e}")
        return None

    def _fetch_tree(self, repo_name: str) -> List[Dict[str, Any]]:
        """One-call recursive file listing via the Git Trees API — replaces the
        old per-directory recursive scan (issue #74: unbounded API call growth)."""
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/git/trees/HEAD?recursive=1"
        try:
            resp = self._get(url)
            if resp.status_code == 200:
                return resp.json().get("tree", [])
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.debug(f"Tree fetch failed for {repo_name}: {e}")
        return []

    def _fetch_dependency_files(self, repo_name: str, tree: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
        """Fetch known dependency/config files from the repo root.

        When a tree listing is available, only fetch files confirmed present
        instead of firing a blind request for all DEPENDENCY_FILES (issue #74).
        """
        found = {}
        if tree:
            root_paths = {e["path"] for e in tree if e.get("type") == "blob" and "/" not in e.get("path", "")}
            candidates = [f for f in DEPENDENCY_FILES if f in root_paths]
        else:
            candidates = DEPENDENCY_FILES

        for filename in candidates:
            url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{filename}"
            try:
                resp = self._get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("content", "")
                    encoding = data.get("encoding", "")
                    if encoding == "base64" and content:
                        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                        found[filename] = decoded[:2000]
                    elif content:
                        found[filename] = content[:2000]
            except GitHubRateLimitError:
                raise
            except Exception as e:
                logger.debug(f"Could not fetch {filename} for {repo_name}: {e}")

        # Also scan for imports in Python and notebook files
        imports = self._extract_imports_from_repo(repo_name, tree)
        if imports:
            found["_detected_imports"] = imports

        return found

    def _extract_imports_from_repo(self, repo_name: str, tree: Optional[List[Dict[str, Any]]]) -> str:
        """Scan .py and .ipynb files (root + one level deep) for import statements,
        using a tree listing instead of a per-directory recursive scan. Capped at
        MAX_IMPORT_SCAN_FILES so cost is bounded regardless of repo size (issue #74)."""
        if not tree:
            return ""

        candidates = sorted(
            e["path"] for e in tree
            if e.get("type") == "blob"
            and e.get("path", "").count("/") <= 1
            and (e["path"].endswith(".py") or e["path"].endswith(".ipynb"))
        )

        import_lines = set()
        for fpath in candidates[:MAX_IMPORT_SCAN_FILES]:
            if fpath.endswith(".py"):
                import_lines.update(self._get_python_imports(repo_name, fpath))
            else:
                import_lines.update(self._get_notebook_imports(repo_name, fpath))

        if import_lines:
            return "Detected imports: " + ", ".join(sorted(import_lines))
        return ""

    def _get_python_imports(self, repo_name: str, filename: str) -> List[str]:
        """Extract import statements from a Python file."""
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{filename}"
        try:
            resp = self._get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            content = data.get("content", "")
            if data.get("encoding") == "base64" and content:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
                return self._parse_imports(decoded)
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.debug(f"Could not read {filename}: {e}")
        return []

    def _get_notebook_imports(self, repo_name: str, filename: str) -> List[str]:
        """Extract import statements from a Jupyter notebook's code cells."""
        import json as json_mod
        url = f"{self.api_url}/repos/{self.username}/{repo_name}/contents/{filename}"
        try:
            resp = self._get(url)
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
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.debug(f"Could not read notebook {filename}: {e}")
        return []

    @classmethod
    def fetch_repo(cls, owner: str, repo_name: str, token: str = GITHUB_TOKEN) -> Optional[Dict[str, Any]]:
        """Fetch a single repo by owner/repo. Returns a payload shaped like ingest() items, or None on failure."""
        instance = cls(username=owner, token=token)
        api_url = f"{instance.api_url}/repos/{owner}/{repo_name}"
        try:
            resp = instance._get(api_url)
            if resp.status_code != 200:
                logger.warning(f"Repo {owner}/{repo_name} not found (HTTP {resp.status_code})")
                return None
            repo = resp.json()

            lang_url = repo["languages_url"]
            lang_resp = instance._get(lang_url)
            lang_list = list(lang_resp.json().keys()) if lang_resp.status_code == 200 else []

            scannable_langs = {"Python", "Jupyter Notebook", "R", "TypeScript", "JavaScript"}
            should_deep_scan = bool(set(lang_list) & scannable_langs)

            tree = instance._fetch_tree(repo_name) if should_deep_scan else []
            readme_text = instance._fetch_readme(repo_name) if should_deep_scan else None
            dependencies = instance._fetch_dependency_files(repo_name, tree) if should_deep_scan else {}

            return {
                "name": repo_name,
                "description": repo.get("description"),
                "url": repo["html_url"],
                "stars": repo.get("stargazers_count", 0),
                "updated_at": repo.get("updated_at"),
                "languages": lang_list,
                "readme": readme_text,
                "dependencies": dependencies,
                "owner": owner,
            }
        except GitHubRateLimitError:
            raise
        except Exception as e:
            logger.error(f"fetch_repo failed for {owner}/{repo_name}: {e}")
            return None

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
