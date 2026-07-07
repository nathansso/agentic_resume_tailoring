"""Fallback tests for malformed LLM extraction output (parser hardening).

The extraction chains in agents/parser.py ask for a JSON list of objects, but
models sometimes return a wrapper object ({"skills": [...]}) or a list of bare
strings. Untreated, those shapes crash the save path with
"'str' object has no attribute 'get'" (seen in production LinkedIn ingestion).
"""
from agents.parser import ResumeParserAgent
from agents.skill_postprocessor import postprocess_skills

_coerce = ResumeParserAgent._coerce_records


def test_coerce_passthrough_list_of_dicts():
    data = [{"name": "Python", "proficiency": 4}]
    assert _coerce(data, str_key="name") == data


def test_coerce_unwraps_wrapper_object():
    data = {"skills": [{"name": "Python"}, {"name": "SQL"}]}
    assert _coerce(data, str_key="name") == [{"name": "Python"}, {"name": "SQL"}]


def test_coerce_single_record_dict_becomes_one_item_list():
    data = {"title": "ML Engineer", "company": "Acme"}
    assert _coerce(data, str_key="title") == [data]


def test_coerce_maps_bare_strings_with_str_key():
    assert _coerce(["Python", "  SQL "], str_key="name") == [
        {"name": "Python"}, {"name": "SQL"}
    ]


def test_coerce_drops_bare_strings_without_str_key():
    assert _coerce(["Python", {"title": "Engineer"}]) == [{"title": "Engineer"}]


def test_coerce_drops_non_dict_non_str_items():
    assert _coerce([42, None, {"name": "SQL"}], str_key="name") == [{"name": "SQL"}]


def test_coerce_garbage_returns_empty():
    assert _coerce("not json at all", str_key="name") == []
    assert _coerce(None, str_key="name") == []
    assert _coerce(42, str_key="name") == []


def test_coerce_wrapped_list_of_strings():
    data = {"skills": ["Python", "PyTorch"]}
    assert _coerce(data, str_key="name") == [{"name": "Python"}, {"name": "PyTorch"}]


# ── postprocess_skills guards (the line that crashed in production) ────────────

def test_postprocess_tolerates_bare_string_skills():
    result = postprocess_skills(["Python", {"name": "SQL", "proficiency": 3}])
    names = {s["name"] for s in result}
    assert names == {"Python", "SQL"}


def test_postprocess_skips_non_dict_non_str_items():
    result = postprocess_skills([None, 42, {"name": "pandas"}])
    assert [s["name"] for s in result] == ["pandas"]


def test_postprocess_tolerates_non_string_name():
    # e.g. the model emitted {"name": {"value": "Python"}} — skip, don't crash
    result = postprocess_skills([{"name": {"value": "Python"}}, {"name": "SQL"}])
    assert [s["name"] for s in result] == ["SQL"]
