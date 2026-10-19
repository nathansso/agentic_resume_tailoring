"""Where `render` saves a copy for the user (issue #394): "save it to my Downloads".

`render` always writes under `$ART_DATA_DIR/applications/`. This module turns what
the user said ("downloads", "My Desktop", an absolute path) into a folder on this
machine, names the copy `<Name>_<Company>_<Role>.pdf` in a form every OS accepts,
and never overwrites a file it didn't write, unless asked (` (2)`, ` (3)`, ... instead).
ART's own earlier copy, unchanged since, is replaced in place (`reusable_stem`).

The three well-known names resolve per OS:

- Windows: the Known Folders API (`SHGetKnownFolderPath`), because Downloads is
  often redirected (OneDrive, another drive); `%USERPROFILE%\\<Folder>` only if the
  call fails.
- macOS: `~/Downloads`, `~/Desktop`, `~/Documents`.
- Linux and the rest: the XDG user dirs (`~/.config/user-dirs.dirs`, then
  `xdg-user-dir`), falling back to `~/<Folder>`.

Standard library only, and no network. A stored default keeps the well-known name,
not the path it resolved to, so it follows a later redirect and survives an export
to another machine.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

WELL_KNOWN = ("downloads", "desktop", "documents")
_ALIASES = {"download": "downloads", "document": "documents", "docs": "documents",
            "my documents": "documents"}
_FOLDER = {"downloads": "Downloads", "desktop": "Desktop", "documents": "Documents"}

# KNOWNFOLDERID values (KnownFolders.h).
_KNOWN_FOLDER_IDS = {
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
}
_XDG_KEYS = {"downloads": "DOWNLOAD", "desktop": "DESKTOP", "documents": "DOCUMENTS"}

# Device names Windows reserves, whatever the extension.
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_MAX_STEM_BYTES = 150      # leaves room for " (NN).pdf" under every OS's 255-byte limit


class DestinationError(ValueError):
    """`save_to` (or a stored default) is neither a well-known name nor an absolute path."""


def normalize_name(text: str) -> Optional[str]:
    """`downloads`, `desktop` or `documents` for what the user said, else None.
    Case-insensitive; "my downloads", "the Desktop folder" and "Download" count."""
    t = re.sub(r"\s+", " ", (text or "").strip().strip("\"'").lower()).strip(" .")
    if t in _ALIASES:
        return _ALIASES[t]
    for prefix in ("my ", "the "):
        if t.startswith(prefix):
            t = t[len(prefix):]
    if t.endswith(" folder"):
        t = t[: -len(" folder")]
    t = _ALIASES.get(t, t)
    return t if t in WELL_KNOWN else None


def parse(save_to: str) -> str:
    """The value to use and store for `save_to`: a well-known name, or an absolute path
    (`~` expanded). Raises `DestinationError` for anything else."""
    raw = (save_to or "").strip()
    name = normalize_name(raw)
    if name:
        return name
    if raw:
        path = Path(os.path.expanduser(raw.strip("\"'")))
        if path.is_absolute():
            return str(path)
    raise DestinationError(
        f"save_to must be downloads, desktop, documents or an absolute folder path, "
        f"not {save_to!r}.")


# ── resolving the well-known names ───────────────────────────────────────────

def _shell_known_folder(name: str) -> Optional[str]:
    """Windows: the folder's current path from `SHGetKnownFolderPath`, or None."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                        ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.memmove(ctypes.byref(guid), uuid.UUID(_KNOWN_FOLDER_IDS[name]).bytes_le, 16)
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.POINTER(GUID), wintypes.DWORD,
                                   wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p))
        get_path = proto(("SHGetKnownFolderPath", ctypes.windll.shell32))
        buf = ctypes.c_void_p()
        get_path(ctypes.byref(guid), 0, None, ctypes.byref(buf))   # raises OSError on failure
        try:
            return ctypes.wstring_at(buf.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buf)
    except Exception:                       # not Windows, an old shell32, a failed call
        return None


def _xdg_user_dir(name: str, home: Path) -> Optional[Path]:
    """Linux: the XDG user dir for `name`, from `user-dirs.dirs` then `xdg-user-dir`.
    A dir set to `$HOME` itself means "disabled" in XDG, so it does not count."""
    key = _XDG_KEYS[name]
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    # The file holds "$HOME/<dir>" or an absolute "/<dir>" (the XDG format), nothing else.
    match = re.search(rf'^\s*XDG_{key}_DIR\s*=\s*"?([^"\n]*)"?\s*$', text, re.M)
    if match:
        value = match.group(1).strip()
        for var in ("${HOME}", "$HOME"):
            if value == var or value.startswith(var + "/"):
                value = str(home / value[len(var):].lstrip("/")) if value != var else str(home)
                break
        else:
            value = value if value.startswith("/") else ""
        if value and Path(value) != home:
            return Path(value)
    exe = shutil.which("xdg-user-dir")
    if exe:
        try:
            out = subprocess.run([exe, key], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out.startswith("/") and Path(out) != home:
            return Path(out)
    return None


def known_folder(name: str, platform: Optional[str] = None,
                 home: Optional[Path] = None) -> Path:
    """The path of `downloads`, `desktop` or `documents` on this OS (or `platform`)."""
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    if platform.startswith("win"):
        found = _shell_known_folder(name)
        if found:
            return Path(found)
        return Path(os.environ.get("USERPROFILE") or home) / _FOLDER[name]
    if platform == "darwin":
        return home / _FOLDER[name]
    return _xdg_user_dir(name, home) or home / _FOLDER[name]


def resolve(value: str) -> Path:
    """The folder a parsed `save_to` (or stored default) names."""
    value = parse(value)
    return known_folder(value) if value in WELL_KNOWN else Path(value)


# ── naming and copying ───────────────────────────────────────────────────────

def _clean(part: Optional[str]) -> str:
    part = unicodedata.normalize("NFC", part or "")
    part = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " ", part)     # illegal on Windows (and / on all)
    part = re.sub(r"\s+", "_", part.strip())
    return re.sub(r"_+", "_", part).strip("._- ")


def output_stem(name: Optional[str], company: Optional[str], role: Optional[str]) -> str:
    """`<Name>_<Company>_<Role>`, legal as a file name on Windows, macOS and Linux."""
    stem = "_".join(p for p in (_clean(name), _clean(company), _clean(role)) if p) or "resume"
    while len(stem.encode("utf-8")) > _MAX_STEM_BYTES:
        stem = stem[:-1]
    stem = stem.rstrip("._- ") or "resume"
    if stem.split(".")[0].upper() in _RESERVED:
        stem += "_"
    return stem


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reusable_stem(directory: Path, base: str, exts: Iterable[str],
                  record: Dict[str, Dict[str, str]]) -> Optional[str]:
    """The stem of ART's own earlier copy in `directory`, when a re-render may replace it.

    `record` is `{extension: {"path", "sha256"}}`: what ART last wrote there for this job.
    The stem qualifies only if it is `base` or `base (N)`, at least one of its files is still
    there with the recorded hash, and every other target is free or likewise ART's unchanged
    copy. A file the user edited, or one ART never wrote, is never replaced: then None, and
    the caller numbers a new copy."""
    exts = list(exts)
    pattern = re.compile(re.escape(base) + r"( \(\d+\))?")
    stems = []
    for ext, rec in record.items():
        name = Path(rec.get("path") or "").name
        if name.endswith(ext) and pattern.fullmatch(name[: -len(ext)]):
            stems.append(name[: -len(ext)])
    for stem in dict.fromkeys(stems):
        own = 0
        for ext in exts:
            target = directory / f"{stem}{ext}"
            if not target.exists():
                continue
            rec = record.get(ext) or {}
            try:
                same = (Path(rec.get("path") or "") == target
                        and file_sha256(target) == rec.get("sha256"))
            except OSError:
                same = False
            if not same:
                break
            own += 1
        else:
            if own:
                return stem
    return None


def copy_files(sources: Dict[str, Path], directory: Path, stem: str,
               overwrite: bool = False, in_place: Optional[str] = None) -> List[Path]:
    """Copy each `{extension: source}` into `directory` as `<stem><extension>`, all under
    one stem. Without `overwrite`, the first of `stem`, `stem (2)`, `stem (3)`, ... that
    is free for every extension; files are created exclusively, so a file that appears
    meanwhile is never clobbered. `in_place` is a stem `reusable_stem` cleared: its
    files are written over."""
    directory.mkdir(parents=True, exist_ok=True)
    if in_place is not None:
        written = []
        for ext, src in sources.items():
            target = directory / f"{in_place}{ext}"
            shutil.copyfile(src, target)
            written.append(target)
        return written
    n = 1
    while True:
        candidate = stem if n == 1 else f"{stem} ({n})"
        targets = {ext: directory / f"{candidate}{ext}" for ext in sources}
        if overwrite or not any(t.exists() for t in targets.values()):
            written: List[Path] = []
            try:
                for ext, src in sources.items():
                    with open(src, "rb") as fin, open(targets[ext], "wb" if overwrite else "xb") as fout:
                        shutil.copyfileobj(fin, fout)
                    written.append(targets[ext])
                return written
            except FileExistsError:
                for path in written:
                    path.unlink(missing_ok=True)
        n += 1
