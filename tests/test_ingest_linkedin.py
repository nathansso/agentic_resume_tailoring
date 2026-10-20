"""`ingest_linkedin(url)` and the shared LinkedIn mapping (issue #355).

The gateway is always a fake here: the owner's live quota is two profiles a day, and the
suite never reaches the network. Covered: the model-free mapping (a full record, nested
positions, the sparse public scrape #396 found); the legacy parser going through it; the
tool on all three adapters for a 200, a 401, a 429, a 503 and a timeout; the request it
sends; storage through `upsert_items` (dedup, `manually_edited`, tombstones); the URL rules;
and no token or profile body in any log or output.
"""

import asyncio
import json
import logging

import pytest
from sqlmodel import Session, select

from agents import linkedin_mapping
from conftest import _seed_user_and_skill
from database.models import Achievement, DeletedEntry, Education, Experience, Project
from harness import linkedin, linkedin_gateway
from harness.contract import BY_NAME, invoke

TOKEN = "gho_SECRET-TOKEN-355"
URL = "https://www.linkedin.com/in/jane-dev"
POST = "Thrilled to share our team won 1st place at the HDSI hackathon for a triage model"
ABOUT = "Data science student who builds recommender systems and evaluation tooli"

FULL = {
    "name": "Jane Dev", "position": "ML Engineer", "about": "I build models.",
    "experience": [{"title": "ML Engineer", "company": "Acme", "start_date": "2020",
                    "end_date": "2024", "description": "Built stuff"}],
    "education": [{"title": "UCSD", "degree": "BS", "field": "CS",
                   "start_year": 2019, "end_year": 2023}],
    "projects": [{"title": "Recipe Review Analysis", "start_date": "Nov 2024",
                  "description": "Random Forest pipeline on Food.com reviews."}],
    "honors_and_awards": [{"title": "Dean's List", "publication": "UCSD", "date": "2023"}],
}

# What #396 found for the owner's public profile: no experience or position, a truncated
# about, one education entry with no school; education, courses, activity and bio_links present.
SPARSE = {
    "name": "Sam Student", "position": None, "experience": None, "about": ABOUT,
    "education": [
        {"title": "University of California San Diego", "degree": "Master of Science",
         "field": "Data Science", "start_year": "2025", "end_year": "2027"},
        {"title": "", "degree": "Certificate", "field": "Machine Learning"},
    ],
    "courses": [{"title": "Data Visualization", "subtitle": "DSC 106"}, {"title": ""}],
    "activity": [
        {"title": POST, "link": "https://www.linkedin.com/posts/sam_1",
         "interaction": "Shared by Sam Student"},
        {"title": "x" * 900, "link": "https://www.linkedin.com/posts/sam_2"},
        {"title": "", "link": "https://www.linkedin.com/posts/sam_3"},
    ],
    "bio_links": [{"title": "Portfolio", "link": "https://sam.dev"}, {"title": "No link"}],
}


# ── the shared mapping ───────────────────────────────────────────────────────

def test_a_full_record_maps_to_the_schema_field_names():
    m = linkedin_mapping.map_record(FULL)
    assert m["experiences"] == [{"title": "ML Engineer", "company": "Acme", "start_date": "2020",
                                 "end_date": "2024", "description": "Built stuff"}]
    assert m["education"] == [{"institution": "UCSD", "degree": "BS, CS",
                               "start_date": "2019", "end_date": "2023"}]
    assert m["projects"] == [{"name": "Recipe Review Analysis", "start_date": "Nov 2024",
                              "description": "Random Forest pipeline on Food.com reviews."}]
    assert m["achievements"] == [{"title": "Dean's List", "issuer": "UCSD", "date": "2023"}]
    assert m["skipped"] == [] and m["missing"] == []
    assert (m["name"], m["headline"]) == ("Jane Dev", "ML Engineer")


def test_a_sparse_record_maps_what_exists_and_invents_nothing():
    m = linkedin_mapping.map_record(SPARSE)
    assert m["experiences"] == [] and m["projects"] == [] and m["achievements"] == []
    assert m["education"] == [{"institution": "University of California San Diego",
                               "degree": "Master of Science, Data Science",
                               "start_date": "2025", "end_date": "2027"}]
    assert m["skipped"] == [{"kind": "education", "label": "Certificate, Machine Learning",
                             "reason": "no school"}]
    assert m["missing"] == ["experience", "projects", "honors_and_awards"]
    assert m["headline"] is None
    cand = m["candidates"]
    assert cand["about"] == ABOUT
    assert cand["courses"] == [{"title": "Data Visualization", "code": "DSC 106"}]
    assert cand["bio_links"] == [{"title": "Portfolio", "url": "https://sam.dev"}]
    assert [p["text"] for p in cand["activity"]][0] == POST
    assert len(cand["activity"]) == 2                     # the empty post is dropped
    long = cand["activity"][1]
    assert long["truncated"] is True and len(long["text"]) == linkedin_mapping.POST_MAX_CHARS
    assert "truncated" not in cand["activity"][0]


def test_nested_positions_and_bullets_go_through_the_mapping():
    record = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures"
                         / "linkedin_nested_positions.json").read_text())
    exps = linkedin_mapping.map_record(record)["experiences"]
    assert [(e["title"], e["company"]) for e in exps] == [
        ("Research Assistant", "UC San Diego"), ("Financial Assistant", "UC San Diego"),
        ("Data Science Intern", "Acme Analytics")]
    assert len(exps[0]["bullets"]) == 2 and "bullets" not in exps[1]


def test_malformed_sections_do_not_crash_the_mapping():
    m = linkedin_mapping.map_record({"experience": "nope", "education": {"items": ["MIT"]},
                                     "projects": [None, 3, {"title": "  "}],
                                     "honors_and_awards": [{"title": "Award", "date": True}]})
    assert m["education"] == [{"institution": "MIT"}]
    assert m["achievements"] == [{"title": "Award"}]
    assert m["skipped"] == [{"kind": "project", "label": "(no detail)", "reason": "no name"}]
    assert linkedin_mapping.map_record(None)["missing"] == list(linkedin_mapping.SECTIONS)


def test_the_legacy_parser_saves_through_the_shared_mapping(isolated_engine, monkeypatch):
    import agents.parser as parser_module

    assert parser_module.ResumeParserAgent._flatten_linkedin_experiences is \
        linkedin_mapping.flatten_experiences
    seen = []
    real = linkedin_mapping.map_record
    monkeypatch.setattr(linkedin_mapping, "map_record", lambda r: seen.append(r) or real(r))
    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    user = _seed_user_and_skill(isolated_engine)
    agent = parser_module.ResumeParserAgent.__new__(parser_module.ResumeParserAgent)
    agent.user, agent.llm = user, None
    agent._save_linkedin_structured(SPARSE)
    assert seen == [SPARSE]
    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert [(r.institution, r.degree) for r in rows] == [
        ("University of California San Diego", "Master of Science, Data Science")]


# ── a fake gateway ───────────────────────────────────────────────────────────

class FakeGateway:
    def __init__(self, status=200, body=None, headers=None, raises=None):
        self.status, self.body, self.headers, self.raises = status, body, headers or {}, raises
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": json.loads(body), "timeout": timeout})
        if self.raises:
            raise self.raises
        return self.status, self.headers, json.dumps(self.body)


def _error_body(code, message="fixed message"):
    return {"error": {"code": code, "message": message}}


@pytest.fixture()
def user(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_GITHUB_TOKEN", TOKEN)
    return _seed_user_and_skill(isolated_engine).user_id


def _gateway(monkeypatch, fake):
    monkeypatch.setattr(linkedin_gateway, "_requests_transport", fake)
    return fake


def _via_mcp(uid, args):
    pytest.importorskip("mcp")
    from harness.mcp_server import build_server
    result = asyncio.run(build_server(uid).call_tool("ingest_linkedin", args))
    assert not result.is_error, result
    return result.structured_content


def _via_cli(uid, args, capsys):
    from harness import cli
    capsys.readouterr()
    assert cli.run(["ingest_linkedin", "--args", json.dumps(args)], user_id=uid) == 0
    return json.loads(capsys.readouterr().out)


def _adapters(uid, args, capsys):
    direct = invoke("ingest_linkedin", uid, args)
    BY_NAME["ingest_linkedin"].output_model.model_validate(direct)
    return direct, _via_mcp(uid, args), _via_cli(uid, args, capsys)


# ── the request ──────────────────────────────────────────────────────────────

def test_the_request_goes_to_the_production_gateway_with_the_github_token(user, monkeypatch):
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    invoke("ingest_linkedin", user, {"url": "linkedin.com/in/Jane-Dev/"})
    (call,) = fake.calls
    assert call["method"] == "POST"
    assert call["url"] == "https://artie-gateway.vercel.app/v1/linkedin"
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert call["body"] == {"url": URL}                     # normalised before it is sent
    assert call["timeout"] > 280                            # the gateway polls up to 280 s


def test_the_gateway_url_can_be_overridden(user, monkeypatch):
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    monkeypatch.setenv("ART_LINKEDIN_GATEWAY_URL", "http://localhost:3000/")
    invoke("ingest_linkedin", user, {"url": URL})
    assert fake.calls[0]["url"] == "http://localhost:3000/v1/linkedin"


def test_a_requests_timeout_becomes_a_timeout_error(monkeypatch):
    import requests

    seen = {}

    def slow(method, url, headers=None, data=None, timeout=None):
        seen["timeout"] = timeout
        raise requests.Timeout("read timed out")
    monkeypatch.setattr(requests, "request", slow)
    with pytest.raises(linkedin_gateway.GatewayError) as err:
        linkedin_gateway.fetch_profile(URL, TOKEN)
    assert err.value.code == "timeout" and seen["timeout"] == linkedin_gateway.TIMEOUT_SECONDS


# ── the 200 path ─────────────────────────────────────────────────────────────

def test_a_profile_is_stored_through_upsert_items_on_every_adapter(user, isolated_engine,
                                                                   monkeypatch, capsys):
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    direct, mcp_out, cli_out = _adapters(user, {"url": URL}, capsys)
    assert direct["error"] is None
    assert direct["added"] == {"experience": 1, "education": 1, "project": 1, "achievement": 1}
    assert direct["source"] == f"linkedin:{URL}"
    assert {r["status"] for r in direct["results"]} == {"created"}
    # The second and third calls find everything stored: dedup, not duplicates.
    assert mcp_out == cli_out and mcp_out["added"] == {}
    assert {r["status"] for r in mcp_out["results"]} == {"unchanged"}
    assert len(fake.calls) == 3
    with Session(isolated_engine) as s:
        assert len(s.exec(select(Experience).where(Experience.user_id == user)).all()) == 1
        assert len(s.exec(select(Project).where(Project.user_id == user)).all()) == 1


def test_a_sparse_profile_stores_what_exists_and_reports_the_rest(user, isolated_engine,
                                                                  monkeypatch):
    _gateway(monkeypatch, FakeGateway(body=SPARSE))
    out = invoke("ingest_linkedin", user, {"url": URL})
    assert out["added"] == {"education": 1}
    assert out["skipped"] == [{"kind": "education", "label": "Certificate, Machine Learning",
                               "reason": "no school"}]
    assert "experience" in out["missing"]
    assert "data export" in out["note"] and "candidates are not stored" in out["note"]
    assert out["candidates"]["activity"][0]["text"] == POST
    with Session(isolated_engine) as s:   # a post about a win is not stored as an award
        assert s.exec(select(Achievement).where(Achievement.user_id == user)).all() == []


def test_a_role_without_a_company_is_skipped_not_given_one(user, isolated_engine, monkeypatch):
    _gateway(monkeypatch, FakeGateway(body={"experience": [{"title": "Founder"}]}))
    out = invoke("ingest_linkedin", user, {"url": URL})
    assert out["skipped"] == [{"kind": "experience", "label": "Founder", "reason": "no company"}]
    assert out["results"] == [] and "Nothing in the profile could be stored." in out["note"]
    with Session(isolated_engine) as s:
        assert s.exec(select(Experience).where(Experience.user_id == user)).all() == []


def test_hand_edited_and_deleted_items_are_left_alone(user, isolated_engine, monkeypatch):
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user, title="ML Engineer", company="Acme",
                         description="My own words", manually_edited=True))
        s.add(DeletedEntry(user_id=user, entity_type="project",
                           key_a="Recipe Review Analysis", key_b=None))
        s.commit()
    _gateway(monkeypatch, FakeGateway(body=FULL))
    out = invoke("ingest_linkedin", user, {"url": URL})
    status = {r["kind"]: r["status"] for r in out["results"]}
    assert status["experience"] == "skipped_manual"
    assert status["project"] == "skipped_tombstone"
    with Session(isolated_engine) as s:
        exp = s.exec(select(Experience).where(Experience.user_id == user)).one()
        assert exp.description == "My own words" and exp.start_date is None
        assert s.exec(select(Project).where(Project.user_id == user)).all() == []


def test_the_saved_profile_url_is_used_when_none_is_given(user, monkeypatch):
    invoke("update_profile", user, {"fields": {"linkedin_url": URL}})
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    assert invoke("ingest_linkedin", user, {})["url"] == URL
    assert fake.calls[0]["body"] == {"url": URL}


# ── failures ─────────────────────────────────────────────────────────────────

FAILURES = [
    ("unauthorized", FakeGateway(401, _error_body("unauthorized")), "artie connect --reauth"),
    ("quota_exceeded", FakeGateway(429, _error_body("quota_exceeded"), {"retry-after": "7200"}),
     "in about 2 hours"),
    ("rate_limited", FakeGateway(429, _error_body("rate_limited"), {"retry-after": "30"}),
     "30 seconds"),
    ("spend_cap_reached", FakeGateway(503, _error_body("spend_cap_reached")),
     "rest of the month"),
    ("unavailable", FakeGateway(503, _error_body("unavailable")), "down right now"),
    ("timeout", FakeGateway(raises=linkedin_gateway.GatewayError("timeout")), "timed out"),
    ("upstream_timeout", FakeGateway(504, _error_body("upstream_timeout")), "timed out"),
    ("upstream_error", FakeGateway(502, _error_body("upstream_error")), "data provider"),
    ("unreachable", FakeGateway(raises=linkedin_gateway.GatewayError("unreachable")),
     "Could not reach"),
    ("gateway_error", FakeGateway(500, "not json"), "failed"),
]


@pytest.mark.parametrize("code,fake,says", FAILURES, ids=[f[0] for f in FAILURES])
def test_a_failure_says_so_plainly_and_points_to_the_export(user, isolated_engine, monkeypatch,
                                                            capsys, code, fake, says):
    _gateway(monkeypatch, fake)
    direct, mcp_out, cli_out = _adapters(user, {"url": URL}, capsys)
    assert direct == mcp_out == cli_out
    assert direct["error"]["code"] == code
    assert says in direct["error"]["message"]
    assert "LinkedIn data export" in direct["error"]["message"]
    assert TOKEN not in json.dumps(direct)
    with Session(isolated_engine) as s:
        assert s.exec(select(Education).where(Education.user_id == user)).all() == []


def test_signed_out_it_names_the_sign_in_and_calls_no_gateway(isolated_engine, monkeypatch,
                                                              capsys):
    uid = _seed_user_and_skill(isolated_engine).user_id
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    direct, mcp_out, cli_out = _adapters(uid, {"url": URL}, capsys)
    assert direct == mcp_out == cli_out
    assert direct["error"]["code"] == "not_logged_in"
    assert "artie connect" in direct["error"]["message"]
    assert "LinkedIn data export" in direct["error"]["message"]
    assert fake.calls == []


@pytest.mark.parametrize("args,code", [({}, "no_url"),
                                       ({"url": "https://example.com/in/jane"}, "invalid_url"),
                                       ({"url": "linkedin.com/company/acme"}, "invalid_url")])
def test_no_url_or_a_bad_one_is_refused_before_any_request(user, monkeypatch, args, code):
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    assert invoke("ingest_linkedin", user, args)["error"]["code"] == code
    assert fake.calls == []


def test_a_read_only_store_refuses_before_any_request(user, monkeypatch):
    fake = _gateway(monkeypatch, FakeGateway(body=FULL))
    out = invoke("ingest_linkedin", user, {"url": URL}, allow_writes=False)
    assert out["error"]["code"] == "read_only" and fake.calls == []


# ── privacy ──────────────────────────────────────────────────────────────────

def test_no_token_or_profile_body_reaches_a_log(user, monkeypatch, caplog, capsys):
    _gateway(monkeypatch, FakeGateway(body=SPARSE))
    with caplog.at_level(logging.DEBUG):
        out = invoke("ingest_linkedin", user, {"url": URL})
        _gateway(monkeypatch, FakeGateway(401, _error_body("unauthorized")))
        invoke("ingest_linkedin", user, {"url": URL})
    logged = "\n".join(r.getMessage() for r in caplog.records) + "".join(capsys.readouterr())
    for secret in (TOKEN, POST, ABOUT, "Data Visualization", "sam.dev", json.dumps(SPARSE)):
        assert secret not in logged
    # Above debug, nothing about the profile at all.
    loud = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert "University of California San Diego" not in loud and URL not in loud
    assert TOKEN not in json.dumps(out)


def test_the_tool_is_a_network_write_that_needs_a_profile():
    spec = BY_NAME["ingest_linkedin"]
    assert spec.network and not spec.read_only and spec.needs_user
    assert "data export" in spec.description and "own profile" in spec.description
    assert linkedin.EXPORT_HINT.startswith("The LinkedIn data export")
