"""The harness tool contract (issue #191): every adapter serves identical results.

Each contract tool is called three ways against the same seeded store — the
contract directly, the MCP server in-process, and the CLI JSON mode in-process —
and must return the same JSON, valid against its output model. Runs on both
suite legs (SQLite, and Postgres when `ART_TEST_DATABASE_URL` is set).
"""

import asyncio
import json

import pytest

from harness.contract import BY_NAME, CONTRACT_VERSION, TOOLS, invoke

mcp = pytest.importorskip("mcp")

# One representative call per tool; keys are filled from the seeded store.
CALLS = {
    "art_briefing": [{}, {"role_family": "data_science"}],
    "kg_search": [{"query": "gradient boosted forecasting"},
                  {"query": "python recommendation", "kinds": ["skill"], "limit": 3}],
    "list_items": [{}, {"kind": "project"}],
    "get_item": [{"key": "exp:data science intern|idx exchange"},
                 {"key": "proj:next item recommender"}],   # not found → suggestions
    "get_profile": [{}],
}


def _via_mcp(name, user_id, args):
    from harness.mcp_server import build_server
    result = asyncio.run(build_server(user_id).call_tool(name, args))
    assert not result.is_error, result
    return result.structured_content


def _via_cli(name, user_id, args, capsys):
    from harness import cli
    capsys.readouterr()
    code = cli.run([name, "--args", json.dumps(args)], user_id=user_id)
    out = capsys.readouterr().out
    assert code == 0, out
    return json.loads(out)


def test_every_contract_tool_has_a_representative_call():
    assert sorted(CALLS) == sorted(t.name for t in TOOLS)


@pytest.mark.parametrize("name,args", [(n, a) for n, calls in CALLS.items() for a in calls])
def test_all_adapters_return_identical_valid_json(kg, capsys, name, args):
    direct = invoke(name, kg, args)
    BY_NAME[name].output_model.model_validate(direct)
    assert _via_mcp(name, kg, args) == direct
    assert _via_cli(name, kg, args, capsys) == direct


def test_list_items_enumerates_without_a_query(kg):
    items = invoke("list_items", kg, {})["items"]
    kinds = {i["kind"] for i in items}
    assert kinds == {"skill", "experience", "project", "education", "achievement"}
    assert [i["key"] for i in invoke("list_items", kg, {"kind": "project"})["items"]] == [
        "proj:next-item recommendation"]


def test_get_profile_returns_header_fields_and_nothing_secret(kg):
    out = invoke("get_profile", kg, {})
    assert out["name"] == "Test User" and out["email"] == "test@example.com"
    assert "password_hash" not in out and "github_access_token" not in out


@pytest.mark.parametrize("name", sorted(CALLS))
def test_no_user_is_the_same_result_on_every_adapter(isolated_engine, capsys, name):
    args = CALLS[name][0]
    direct = invoke(name, None, args)
    assert direct["error"]["code"] == "no_user"
    assert _via_mcp(name, None, args) == direct
    assert _via_cli(name, None, args, capsys) == direct


def test_mcp_publishes_the_contract_schemas():
    from harness.mcp_server import build_server

    for tool in asyncio.run(build_server(None).list_tools()):
        spec = BY_NAME[tool.name]
        want = spec.input_model.model_json_schema()
        assert tool.input_schema.get("properties", {}) == want.get("properties", {})
        assert tool.input_schema.get("required") == want.get("required")
        assert tool.output_schema and "error" in tool.output_schema["properties"]
        assert tool.annotations.read_only_hint is spec.read_only


def test_cli_list_prints_the_whole_contract(capsys):
    from harness import cli

    assert cli.run(["--list"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["contract_version"] == CONTRACT_VERSION
    assert [t["name"] for t in doc["tools"]] == [t.name for t in TOOLS]
    assert all("input_schema" in t and "output_schema" in t for t in doc["tools"])


def test_cli_rejects_unknown_tools_and_bad_arguments(capsys):
    from harness import cli

    assert cli.run(["no_such_tool"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_tool"
    assert cli.run(["kg_search", "--args", '{"limit": 5}']) == 2   # query missing
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_arguments"
    assert cli.run(["kg_search", "--args", "[1]"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_arguments"
