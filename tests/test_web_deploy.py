"""Structural tests for the web-app deployment surface (Dockerfile + requirements).

No network, no subprocess execution.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"


def test_dockerfile_exposes_8000():
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "EXPOSE 8000" in content, "Dockerfile must EXPOSE 8000"


def test_dockerfile_cmd_uses_uvicorn():
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "uvicorn" in content, "Dockerfile CMD must invoke uvicorn (web.app:app)"


def test_fastapi_in_requirements_core():
    req_core = ROOT / "requirements-core.txt"
    content = req_core.read_text(encoding="utf-8").lower()
    pkgs = {re.split(r"[=<>!\[;]", line.strip())[0].strip().lower().replace("-", "_")
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")}
    assert "fastapi" in pkgs, "fastapi must be listed in requirements-core.txt"
