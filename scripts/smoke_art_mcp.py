"""Smoke-test an installed `art-mcp` over stdio, as a host would (issue #194).

Run it with the Python of the environment the package is installed in:

    python scripts/smoke_art_mcp.py

It starts the `art-mcp` script next to that Python against a throwaway
`ART_DATA_DIR`, lists the tools, upserts one experience and searches for it.
It exits non-zero on any failure, and when torch, sentence-transformers or an
LLM client is importable (the default install must not carry them). CI runs it
in a fresh venv holding only the built wheel.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HEAVY = ("torch", "sentence_transformers", "docling", "transformers", "langchain",
         "langchain_core", "openai", "anthropic")


def _script(name: str) -> str:
    bindir = Path(sys.executable).parent
    for cand in (bindir / name, bindir / f"{name}.exe"):
        if cand.exists():
            return str(cand)
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"smoke: no {name} script next to {sys.executable}")
    return found


async def _run(data_dir: str) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {**os.environ, "ART_DATA_DIR": data_dir}
    env.pop("DATABASE_URL", None)
    params = StdioServerParameters(command=_script("art-mcp"), args=[], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            assert {"art_briefing", "kg_search", "upsert_items", "open_job"} <= tools, tools

            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.is_error, result
                out = result.structured_content
                assert out.get("error") is None, out
                return out

            up = await call("upsert_items", {"records": [{"kind": "experience", "data": {
                "title": "Data Analyst", "company": "Acme",
                "bullets": ["Forecasted weekly demand with gradient-boosted models"]}}]})
            assert up["results"][0]["status"] == "created", up
            hits = (await call("kg_search", {"query": "forecasts"}))["results"]
            assert [h["key"] for h in hits] == ["exp:data analyst|acme"], hits
            items = (await call("list_items", {}))["items"]
            job = await call("open_job", {
                "jd_text": "Data Analyst\n\nRequirements\n- Forecasting with Python and SQL",
                "requirements": [{"text": "The candidate can forecast demand.",
                                  "terms": ["forecasting"]}],
                "metadata": {"title": "Data Analyst", "company": "Beta"}})
            run = await call("execute_plan", {"dry_run": True, "program": {
                "job_id": job["job_id"], "nodes": [
                    {"id": "k", "op": "keep", "item_key": "exp:data analyst|acme"}]}})
            assert run["nodes"][0]["status"] == "kept", run
            # The plugin's last steps (#201): the header, a committed version, a render.
            prof = await call("update_profile", {"fields": {"name": "Smoke Test"}})
            assert prof["name"] == "Smoke Test", prof
            done = await call("execute_plan", {"program": {
                "job_id": job["job_id"], "nodes": [
                    {"id": "k", "op": "keep", "item_key": "exp:data analyst|acme"}]}})
            assert done["committed"], done
            page = await call("render", {"job_id": job["job_id"], "format": "tex"})
            assert Path(page["tex_path"]).is_file(), page
            assert "Smoke Test" in Path(page["tex_path"]).read_text(encoding="utf-8")
            print(json.dumps({"tools": len(tools), "items": [i["key"] for i in items],
                              "search": hits[0]["key"], "job": job["requirements"]}))


def main() -> int:
    present = [m for m in HEAVY if importlib.util.find_spec(m) is not None]
    if present:
        print(f"smoke: the default install carries {present}", file=sys.stderr)
        return 1
    data_dir = tempfile.mkdtemp(prefix="art-smoke-")
    try:
        asyncio.run(_run(data_dir))
        assert (Path(data_dir) / "art.db").is_file(), "no store was created"
        assert (Path(data_dir) / "active_profile_id").is_file(), "no profile was bound"
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    print("smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
