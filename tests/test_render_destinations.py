"""`render` saves where the user asks (issue #394): "save it to my Downloads".

The well-known names resolve per OS (faked here: the platform, the Known Folders
call, the XDG config), the copy is named `<Name>_<Company>_<Role>.pdf` legally on
every OS and never overwrites, and with no `save_to` and no stored default the
reply tells the agent to ask (Downloads if the user has no preference).
"""

from pathlib import Path

import pytest
from sqlmodel import Session, select

from database.models import User, UserJobResult
from harness import destinations
from harness.contract import invoke

CONTENT = {"experiences": [{"title": "Data Science Intern", "company": "IDX Exchange",
                            "start_date": "Jun 2024", "end_date": "Sep 2024",
                            "bullets": ["Built a gradient-boosted ensemble on 200K listings"]}],
           "projects": [], "skills_ranked": [{"name": "Python", "category": "Language"}]}


# ── names ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("said, name", [
    ("downloads", "downloads"), ("Downloads", "downloads"), ("my downloads", "downloads"),
    ("  My  Downloads ", "downloads"), ("DESKTOP", "desktop"), ("the Desktop folder", "desktop"),
    ("Documents", "documents"), ("my documents", "documents"), ("download", "downloads"),
    ("Pictures", None), ("", None), ("downloads/resumes", None),
])
def test_well_known_names_are_case_insensitive_and_take_my(said, name):
    assert destinations.normalize_name(said) == name


def test_parse_takes_a_name_or_an_absolute_path_and_refuses_the_rest(tmp_path):
    assert destinations.parse("My Downloads") == "downloads"
    assert destinations.parse(str(tmp_path)) == str(tmp_path)
    for bad in ("resumes", "./out", "Pictures", "", None):
        with pytest.raises(destinations.DestinationError):
            destinations.parse(bad)


# ── per-OS resolution ────────────────────────────────────────────────────────

def test_windows_uses_the_known_folders_api_so_a_redirected_downloads_is_found(monkeypatch,
                                                                               tmp_path):
    asked = []

    def fake_shell(name):
        asked.append(name)
        return r"D:\OneDrive\Downloads" if name == "downloads" else None
    monkeypatch.setattr(destinations, "_shell_known_folder", fake_shell)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert destinations.known_folder("downloads", platform="win32") == Path(r"D:\OneDrive\Downloads")
    # The call failed for Desktop: %USERPROFILE%\Desktop, not a guess from HOME.
    assert destinations.known_folder("desktop", platform="win32") == tmp_path / "Desktop"
    assert asked == ["downloads", "desktop"]


def test_the_known_folder_ids_are_the_documented_guids():
    assert destinations._KNOWN_FOLDER_IDS == {
        "downloads": "374DE290-123F-4565-9164-39C4925E467B",
        "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
        "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    }


def test_the_shell_call_answers_none_off_windows_rather_than_raising(monkeypatch):
    import sys
    if sys.platform.startswith("win"):
        assert Path(destinations._shell_known_folder("downloads")).is_absolute()
    else:
        assert destinations._shell_known_folder("downloads") is None


def test_macos_uses_the_home_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(destinations, "_shell_known_folder",
                        lambda name: pytest.fail("no Known Folders call off Windows"))
    for name, folder in (("downloads", "Downloads"), ("desktop", "Desktop"),
                         ("documents", "Documents")):
        assert destinations.known_folder(name, platform="darwin", home=tmp_path) == tmp_path / folder


def _no_xdg_command(monkeypatch):
    monkeypatch.setattr(destinations.shutil, "which", lambda exe: None)


def test_linux_reads_the_xdg_user_dirs(monkeypatch, tmp_path):
    _no_xdg_command(monkeypatch)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    (tmp_path / ".config").mkdir()
    (tmp_path / ".config" / "user-dirs.dirs").write_text(
        '# written by xdg-user-dirs-update\n'
        'XDG_DOWNLOAD_DIR="$HOME/Telechargements"\n'
        'XDG_DESKTOP_DIR="$HOME"\n'                           # disabled in XDG terms
        'XDG_DOCUMENTS_DIR="/srv/docs"\n', encoding="utf-8")
    assert destinations.known_folder("downloads", "linux", tmp_path) == tmp_path / "Telechargements"
    assert destinations.known_folder("desktop", "linux", tmp_path) == tmp_path / "Desktop"
    assert destinations.known_folder("documents", "linux", tmp_path) == Path("/srv/docs")


def test_linux_honours_xdg_config_home(monkeypatch, tmp_path):
    _no_xdg_command(monkeypatch)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="${HOME}/dl"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    assert destinations.known_folder("downloads", "linux", tmp_path) == tmp_path / "dl"


def test_linux_asks_xdg_user_dir_when_the_file_is_silent(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(destinations.shutil, "which", lambda exe: "/usr/bin/xdg-user-dir")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="/data/Downloads\n", stderr="")
    monkeypatch.setattr(destinations.subprocess, "run", fake_run)
    assert destinations.known_folder("downloads", "linux", tmp_path) == Path("/data/Downloads")
    assert calls == [["/usr/bin/xdg-user-dir", "DOWNLOAD"]]


def test_linux_falls_back_to_home_downloads(monkeypatch, tmp_path):
    _no_xdg_command(monkeypatch)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert destinations.known_folder("downloads", "linux", tmp_path) == tmp_path / "Downloads"


# ── names on disk ────────────────────────────────────────────────────────────

def test_the_file_name_is_legal_on_every_os():
    stem = destinations.output_stem("Ada Lovelace", 'AT&T: "Labs"', "Sr. Data Scientist / ML\t")
    assert stem == "Ada_Lovelace_AT&T_Labs_Sr._Data_Scientist_ML"
    assert not set(stem) & set('<>:"/\\|?*')
    assert destinations.output_stem(None, "", None) == "resume"
    assert destinations.output_stem("CON", None, None) == "CON_"          # a Windows device name
    assert destinations.output_stem("José Núñez", "Café", "Ingeniero") == "José_Núñez_Café_Ingeniero"
    long = destinations.output_stem("A" * 300, "B", "C")
    assert len(long.encode("utf-8")) <= 150 and not long.endswith((".", " ", "_"))


def test_trailing_dots_and_spaces_are_stripped():
    assert destinations.output_stem("Ada.", "Acme Inc.", "Engineer. ") == "Ada_Acme_Inc_Engineer"


def test_copies_never_overwrite_unless_asked(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "resume.pdf").write_bytes(b"%PDF new")
    (src / "resume.tex").write_text("new tex")
    out = tmp_path / "out"
    out.mkdir()
    (out / "Ada_Acme_Engineer.pdf").write_bytes(b"%PDF old")

    first = destinations.copy_files({".pdf": src / "resume.pdf"}, out, "Ada_Acme_Engineer")
    assert first == [out / "Ada_Acme_Engineer (2).pdf"]
    assert (out / "Ada_Acme_Engineer.pdf").read_bytes() == b"%PDF old"
    second = destinations.copy_files({".pdf": src / "resume.pdf"}, out, "Ada_Acme_Engineer")
    assert second == [out / "Ada_Acme_Engineer (3).pdf"]
    # The PDF and the .tex share one free stem: (4) is free for both.
    (out / "Ada_Acme_Engineer (4).tex").write_text("taken")
    both = destinations.copy_files({".pdf": src / "resume.pdf", ".tex": src / "resume.tex"},
                                   out, "Ada_Acme_Engineer")
    assert both == [out / "Ada_Acme_Engineer (5).pdf", out / "Ada_Acme_Engineer (5).tex"]
    replaced = destinations.copy_files({".pdf": src / "resume.pdf"}, out, "Ada_Acme_Engineer",
                                       overwrite=True)
    assert replaced == [out / "Ada_Acme_Engineer.pdf"]
    assert (out / "Ada_Acme_Engineer.pdf").read_bytes() == b"%PDF new"


def test_a_missing_folder_is_created(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    out = tmp_path / "new" / "deeper"
    assert destinations.copy_files({".pdf": tmp_path / "a.pdf"}, out, "x") == [out / "x.pdf"]


# ── through the render tool ──────────────────────────────────────────────────

@pytest.fixture()
def job(kg, isolated_engine):
    from harness import tree

    with Session(isolated_engine) as s:
        job_id = s.exec(select(UserJobResult).where(UserJobResult.user_id == kg)).first().job_id
    tree.commit_node(kg, job_id, content=CONTENT, source="host")
    return kg, str(job_id)


@pytest.fixture()
def fake_pdf(monkeypatch):
    """A render that "compiles" without LaTeX, and well-known folders under tmp."""
    import agents.formatter as fmt
    import harness.render_cache as rc
    import harness.toolchain as tc

    monkeypatch.setattr(tc, "latex_engine", lambda: ("tectonic", None))
    monkeypatch.setattr(rc, "page_budget", lambda content: {"over_by": 0})
    monkeypatch.setattr(fmt, "_compile_tex_to_pdf", lambda tex: b"%PDF-1.5 fake")
    monkeypatch.setattr(fmt, "_pdf_page_count", lambda pdf: 1)


@pytest.fixture()
def folders(monkeypatch, tmp_path):
    """Every well-known name resolves to its own folder under tmp_path: no test writes
    into the real Downloads."""
    root = tmp_path / "home"
    monkeypatch.setattr(destinations, "known_folder",
                        lambda name, platform=None, home=None: root / name.title())
    return root


def test_no_save_to_and_no_default_tells_the_agent_to_ask(job, fake_pdf, folders, tmp_path):
    uid, job_id = job
    out = invoke("render", uid, {"job_id": job_id})
    assert out["error"] is None and out["pdf_path"].startswith(str(tmp_path))
    assert out["destination"] == "ask_user" and out["saved_paths"] == []
    note = out["destination_note"]
    assert "Ask the user" in note and "remember=true" in note and 'save_to="downloads"' in note
    assert not folders.exists()                    # nothing copied anywhere


def test_save_to_downloads_copies_the_pdf_with_the_users_name(job, fake_pdf, folders):
    uid, job_id = job
    out = invoke("render", uid, {"job_id": job_id, "save_to": "my Downloads"})
    assert out["error"] is None and out["destination"] == "save_to"
    assert out["save_dir"] == str(folders / "Downloads")
    assert out["saved_paths"] == [str(folders / "Downloads" / "Test_User_Rippling_Data_Scientist.pdf")]
    assert Path(out["saved_paths"][0]).read_bytes() == b"%PDF-1.5 fake"
    assert out["remembered"] is False
    assert invoke("get_profile", uid, {})["default_output_dir"] is None   # not remembered

    again = invoke("render", uid, {"job_id": job_id, "save_to": "downloads"})
    assert again["saved_paths"] == [
        str(folders / "Downloads" / "Test_User_Rippling_Data_Scientist (2).pdf")]
    replaced = invoke("render", uid, {"job_id": job_id, "save_to": "downloads", "overwrite": True})
    assert replaced["saved_paths"] == [str(folders / "Downloads" / "Test_User_Rippling_Data_Scientist.pdf")]


def test_remember_stores_the_default_and_later_renders_use_it(job, fake_pdf, folders):
    uid, job_id = job
    out = invoke("render", uid, {"job_id": job_id, "save_to": "Desktop", "remember": True,
                                 "save_tex": True})
    assert out["remembered"] is True
    assert [Path(p).name for p in out["saved_paths"]] == [
        "Test_User_Rippling_Data_Scientist.pdf", "Test_User_Rippling_Data_Scientist.tex"]
    assert invoke("get_profile", uid, {})["default_output_dir"] == "desktop"

    later = invoke("render", uid, {"job_id": job_id})
    assert later["destination"] == "default" and later["destination_note"] is None
    assert later["saved_paths"] == [str(folders / "Desktop" / "Test_User_Rippling_Data_Scientist (2).pdf")]
    # An explicit save_to still wins over the default, without replacing it.
    once = invoke("render", uid, {"job_id": job_id, "save_to": "documents"})
    assert once["destination"] == "save_to" and once["save_dir"] == str(folders / "Documents")
    assert invoke("get_profile", uid, {})["default_output_dir"] == "desktop"


def test_update_profile_sets_validates_and_clears_the_default(job, fake_pdf, folders, tmp_path):
    uid, job_id = job
    assert invoke("update_profile", uid, {"fields": {"default_output_dir": "My Documents"}})[
        "default_output_dir"] == "documents"
    target = tmp_path / "Resumes"
    assert invoke("update_profile", uid, {"fields": {"default_output_dir": str(target)}})[
        "default_output_dir"] == str(target)
    out = invoke("render", uid, {"job_id": job_id})
    assert out["destination"] == "default"
    assert out["saved_paths"] == [str(target / "Test_User_Rippling_Data_Scientist.pdf")]

    bad = invoke("update_profile", uid, {"fields": {"default_output_dir": "resumes"}})
    assert bad["error"]["code"] == "invalid_arguments" and "downloads" in bad["error"]["suggestions"]
    assert invoke("get_profile", uid, {})["default_output_dir"] == str(target)
    assert invoke("update_profile", uid, {"fields": {"default_output_dir": ""}})[
        "default_output_dir"] is None
    assert invoke("render", uid, {"job_id": job_id})["destination"] == "ask_user"


def test_bad_save_to_and_remember_alone_are_refused_before_anything_is_written(job, fake_pdf,
                                                                              folders, tmp_path):
    uid, job_id = job
    bad = invoke("render", uid, {"job_id": job_id, "save_to": "somewhere"})
    assert bad["error"]["code"] == "invalid_arguments"
    alone = invoke("render", uid, {"job_id": job_id, "remember": True})
    assert alone["error"]["code"] == "invalid_arguments"
    assert not (tmp_path / "applications").exists()


def test_format_tex_copies_the_tex_and_no_pdf_copies_nothing(job, folders, monkeypatch):
    import agents.formatter as fmt
    import harness.render_cache as rc

    monkeypatch.setattr(fmt, "_find_latex_engine", lambda: None)
    monkeypatch.setattr(rc, "_find_latex_engine", lambda: None)
    uid, job_id = job
    tex = invoke("render", uid, {"job_id": job_id, "format": "tex", "save_to": "downloads"})
    assert [Path(p).name for p in tex["saved_paths"]] == ["Test_User_Rippling_Data_Scientist.tex"]
    no_pdf = invoke("render", uid, {"job_id": job_id, "save_to": "downloads"})
    assert no_pdf["pdf_path"] is None and no_pdf["saved_paths"] == []
    assert "save_tex" in no_pdf["destination_note"] and "tectonic" in no_pdf["hint"]


def test_a_failed_copy_keeps_the_render(job, fake_pdf, monkeypatch, tmp_path):
    uid, job_id = job

    def refuse(*a, **k):
        raise PermissionError("read-only folder")
    monkeypatch.setattr(destinations, "copy_files", refuse)
    out = invoke("render", uid, {"job_id": job_id, "save_to": str(tmp_path / "locked")})
    assert out["error"] is None and out["pdf_path"] and out["saved_paths"] == []
    assert "read-only folder" in out["destination_note"]


def test_a_hand_edited_default_that_no_longer_parses_says_to_ask(job, fake_pdf, folders,
                                                                isolated_engine):
    uid, job_id = job
    with Session(isolated_engine) as s:
        user = s.get(User, uid)
        user.default_output_dir = "relative/dir"
        s.add(user)
        s.commit()
    out = invoke("render", uid, {"job_id": job_id})
    assert out["error"] is None and out["destination"] == "default"
    assert out["saved_paths"] == [] and "Ask the user" in out["destination_note"]


# ── the schema ───────────────────────────────────────────────────────────────

def test_an_old_database_without_the_column_migrates_and_its_user_loads(tmp_path, monkeypatch):
    """A store written before #394 has no `user.default_output_dir`: the ALTER adds it
    (NULL, so the agent asks once) and the old row loads through the model."""
    import sqlalchemy as sa
    import database.db as db_module

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            'CREATE TABLE "user" (user_id CHAR(32) PRIMARY KEY, name VARCHAR NOT NULL, '
            "email VARCHAR NOT NULL UNIQUE, created_at TIMESTAMP, updated_at TIMESTAMP)"))
        conn.execute(sa.text(
            "INSERT INTO \"user\" (user_id, name, email, created_at, updated_at) VALUES "
            "('0123456789abcdef0123456789abcdef', 'Old User', 'old@example.com', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
    monkeypatch.setattr(db_module, "engine", engine)

    db_module._migrate_db()
    db_module._migrate_db()                                   # idempotent

    cols = {c["name"] for c in sa.inspect(engine).get_columns("user")}
    assert "default_output_dir" in cols
    with engine.connect() as conn:
        row = conn.execute(sa.text('SELECT name, default_output_dir FROM "user"')).one()
    assert tuple(row) == ("Old User", None)


def test_the_column_is_on_the_model_with_no_default(isolated_engine):
    import sqlalchemy as sa

    cols = {c["name"]: c for c in sa.inspect(isolated_engine).get_columns("user")}
    assert cols["default_output_dir"]["nullable"]
    assert User(name="x", email="x@example.com").default_output_dir is None
