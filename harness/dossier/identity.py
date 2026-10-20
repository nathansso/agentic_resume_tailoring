"""Who the user is in a repository's history (#398).

#397's resolver matched only the emails GitHub links to the login, and found 91 of one user's
143 commits. Here an author is the user when any of these match, before anything is counted:

- **an email the user is known by**: every email on the GitHub account (when the token may
  read them), the account's public email, its two `users.noreply.github.com` forms, the
  emails GitHub links to the login on this repo's commits, and the machine's own
  `git config user.email`;
- **a name the user goes by**: the account's display name and login, and the machine's
  `git config user.name`, compared case-insensitively;
- **the repository's `.mailmap`** at the built commit: an author whose mapped name or email
  matches is the user, and so is every raw identity mapped onto the same person.

The result is the set of raw and mapped emails and names that count as the user, which every
later step (authorship, blame, `git log -L`) matches against. Emails are never written into a
dossier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from harness.dossier.gitread import Snapshot


def _norm_email(email: str) -> str:
    return (email or "").strip().strip("<>").lower()


def _norm_name(name: str) -> str:
    return " ".join((name or "").split()).casefold()


@dataclass(frozen=True)
class Seeds:
    """What is known about the user before a repository is read."""
    emails: FrozenSet[str] = frozenset()
    names: FrozenSet[str] = frozenset()
    login: Optional[str] = None

    @classmethod
    def make(cls, emails: Iterable[str] = (), names: Iterable[str] = (),
             login: Optional[str] = None, user_id: Optional[int] = None) -> "Seeds":
        es = {_norm_email(e) for e in emails if e and "@" in e}
        ns = {_norm_name(n) for n in names if n and n.strip()}
        if login:
            ns.add(_norm_name(login))
            es.add(f"{login.lower()}@users.noreply.github.com")
            if user_id:
                es.add(f"{user_id}+{login.lower()}@users.noreply.github.com")
        return cls(frozenset(es), frozenset(ns), login)

    def merged(self, other: "Seeds") -> "Seeds":
        return Seeds(self.emails | other.emails, self.names | other.names,
                     self.login or other.login)


@dataclass(frozen=True)
class Identity:
    """The emails and names that are the user in one repository."""
    emails: FrozenSet[str]
    names: FrozenSet[str]
    login: Optional[str] = None

    def matches(self, name: str, email: str) -> bool:
        return _norm_email(email) in self.emails or _norm_name(name) in self.names

    def __bool__(self) -> bool:
        return bool(self.emails or self.names)


def _authors(snap: Snapshot) -> List[Tuple[str, str, str, str]]:
    """Every distinct (raw name, raw email, mapped name, mapped email) in the history."""
    raw = snap.git("-c", f"mailmap.blob={snap.commit}:.mailmap", "log", "--format=%an%x1f%ae%x1f%aN%x1f%aE",
                   snap.commit)
    seen: Set[Tuple[str, str, str, str]] = set()
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            seen.add((parts[0], _norm_email(parts[1]), parts[2], _norm_email(parts[3])))
    return sorted(seen)


def resolve(snap: Snapshot, seeds: Seeds) -> Identity:
    """Widen `seeds` through the repository's history and `.mailmap`."""
    emails, names = set(seeds.emails), set(seeds.names)
    if not emails and not names:
        return Identity(frozenset(), frozenset(), seeds.login)
    authors = _authors(snap)
    # Group raw identities by the person .mailmap maps them to; a person is the user when any
    # of their raw or mapped emails or names is.
    people: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = {}
    for a in authors:
        people.setdefault((_norm_name(a[2]), a[3]), []).append(a)
    for group in people.values():
        hit = any(e in emails for a in group for e in (a[1], a[3])) or \
            any(_norm_name(n) in names for a in group for n in (a[0], a[2]))
        if hit:
            for a in group:
                emails.update({a[1], a[3]})
                names.update({_norm_name(a[0]), _norm_name(a[2])})
    emails.discard("")
    names.discard("")
    return Identity(frozenset(emails), frozenset(names), seeds.login)


def local_git_identity() -> Seeds:
    """The machine's own `git config user.email` and `user.name` (global or system)."""
    import subprocess
    found = []
    for key in ("user.email", "user.name"):
        try:
            done = subprocess.run(["git", "config", "--get", key], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=10)
            found.append(done.stdout.strip() if done.returncode == 0 else "")
        except (OSError, subprocess.SubprocessError):
            found.append("")
    return Seeds.make([found[0]], [found[1]])


__all__ = ["Seeds", "Identity", "resolve", "local_git_identity"]
