#!/usr/bin/env python3
"""Block private data from entering the repository.

MiHomes manages real homes, so it is easy to paste a real address, a staff member's phone number
or a job candidate's notes into a commit. This check runs before every commit (pre-commit hook)
and on every pull request (CI), and fails when the change adds:

1. **A private path or file type**: anything under the private knowledge folders, databases,
   dumps, `.env` files, keys, and office documents (`.pdf`, `.docx`, `.xlsx`, ...).
2. **A real-looking email address**: anything outside the reserved test domains
   (`example.com`, `*.test`, ...) and MiHomes' own sending domain.
3. **A real-looking North American phone number**: anything outside the fictional 555-01xx range.
4. **A known private term**: a name, street address or host name on the denylist. The denylist
   holds only SHA-256 hashes (`.privacy/denylist.sha256`), so the check itself publishes nothing.

Only **added** lines and **added** files are checked, so existing text never blocks an unrelated
change. A line that must contain a flagged value (for example a test of email parsing) can carry
the marker `privacy-check: allow`.

Usage:
    python scripts/privacy_check.py --staged            # pre-commit: what is staged
    python scripts/privacy_check.py --diff origin/main  # CI: what this branch adds
    python scripts/privacy_check.py --all               # the whole tree (before a release)
    python scripts/privacy_check.py --hash "Some Name"  # print the hash to add to the denylist

Stdlib only, so it runs anywhere without installing the project.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENYLIST = ROOT / ".privacy" / "denylist.sha256"
ALLOW_MARKER = "privacy-check: allow"

# Paths that must never be committed. `fnmatch` patterns against the repo-relative path.
BLOCKED_PATHS = [
    "knowledge/staff/*",
    "knowledge/properties/*",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.dump",
    "*.bak",
    "*.sql.gz",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.pdf",
    "*.doc",
    "*.docx",
    "*.xls",
    "*.xlsx",
    "*.csv",
    "whatsapp-auth/*",
]
# Exceptions to BLOCKED_PATHS.
ALLOWED_PATHS = [
    "knowledge/properties/property-template.md",
    ".env.example",
    "tests/fixtures/*",
    "tests/data/*",
]

# Email domains reserved for documentation and testing (RFC 2606 / RFC 6761), plus MiHomes' own.
ALLOWED_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "mihomes.ai", "send.mihomes.ai",
    "users.noreply.github.com", "localhost",
}
ALLOWED_EMAIL_SUFFIXES = (".test", ".example", ".invalid", ".localhost", ".example.com")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# North American numbers: 404-555-0123, (404) 555 0123, +1 404.555.0123.
PHONE_RE = re.compile(r"(?<![\w.])(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-](\d{3})[ .-](\d{4})(?![\w])")
WORD_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")
MAX_NGRAM = 5


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.rule}: {self.detail}"


def term_hash(term: str) -> str:
    """The denylist hash of a term: lowercase words joined by single spaces."""
    normalized = " ".join(WORD_RE.findall(term.lower()))
    return hashlib.sha256(normalized.encode()).hexdigest()


def load_denylist(path: Path = DENYLIST) -> set[str]:
    if not path.exists():
        return set()
    hashes = set()
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            hashes.add(line.lower())
    return hashes


def path_blocked(path: str) -> bool:
    if any(fnmatch.fnmatch(path, pat) for pat in ALLOWED_PATHS):
        return False
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in BLOCKED_PATHS)


def email_allowed(domain: str) -> bool:
    domain = domain.lower()
    return domain in ALLOWED_EMAIL_DOMAINS or domain.endswith(ALLOWED_EMAIL_SUFFIXES)


def phone_allowed(exchange: str, number: str) -> bool:
    # 555-0100 through 555-0199 is reserved for fiction.
    return exchange == "555" and number.startswith("01")


def check_line(path: str, lineno: int, text: str, denylist: set[str]) -> list[Finding]:
    if ALLOW_MARKER in text:
        return []
    found: list[Finding] = []
    for m in EMAIL_RE.finditer(text):
        if not email_allowed(m.group(1)):
            found.append(Finding(path, lineno, "email", f"{m.group(0)} (use an example.com address)"))
    for m in PHONE_RE.finditer(text):
        if not phone_allowed(m.group(2), m.group(3)):
            found.append(Finding(path, lineno, "phone", f"{m.group(0).strip()} (use 555-01xx)"))
    if denylist:
        words = WORD_RE.findall(text.lower())
        for size in range(1, MAX_NGRAM + 1):
            for i in range(len(words) - size + 1):
                if term_hash(" ".join(words[i:i + size])) in denylist:
                    found.append(Finding(path, lineno, "private term",
                                         f"word {i + 1}-{i + size} on this line is on the denylist"))
    return found


def check_file_content(path: str, lines: list[tuple[int, str]], denylist: set[str]) -> list[Finding]:
    found: list[Finding] = []
    if path.startswith(".privacy/") or path == "scripts/privacy_check.py":
        return found  # the check's own data and code
    for lineno, text in lines:
        found.extend(check_line(path, lineno, text, denylist))
    return found


# ── Collecting what changed ────────────────────────────────────────────────────

def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True, errors="replace").stdout


HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_lines(diff_args: list[str]) -> tuple[list[str], dict[str, list[tuple[int, str]]]]:
    """(added file paths, {path: [(line number, added text), ...]}) for a git diff."""
    added_files = [p for p in _git("diff", "--name-only", "--diff-filter=A", *diff_args).splitlines() if p]
    changed: dict[str, list[tuple[int, str]]] = {}
    path, lineno = None, 0
    for line in _git("diff", "--unified=0", "--no-color", "--no-ext-diff", *diff_args).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = HUNK_RE.match(line)
            lineno = int(m.group(1)) if m else 0
        elif path and line.startswith("+"):
            changed.setdefault(path, []).append((lineno, line[1:]))
            lineno += 1
    return added_files, changed


def whole_tree() -> tuple[list[str], dict[str, list[tuple[int, str]]]]:
    files = [p for p in _git("ls-files").splitlines() if p]
    content: dict[str, list[tuple[int, str]]] = {}
    for p in files:
        try:
            text = (ROOT / p).read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; the path rule still applies
        content[p] = list(enumerate(text.splitlines(), start=1))
    return files, content


def run(files: list[str], content: dict[str, list[tuple[int, str]]], denylist: set[str]) -> list[Finding]:
    found = [Finding(p, 0, "private path", "this file type or folder must not be committed")
             for p in files if path_blocked(p)]
    for p, lines in content.items():
        found.extend(check_file_content(p, lines, denylist))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check staged changes (pre-commit)")
    mode.add_argument("--diff", metavar="BASE", help="check what HEAD adds compared with BASE")
    mode.add_argument("--all", action="store_true", help="check every tracked file")
    mode.add_argument("--hash", metavar="TERM", help="print the denylist hash of TERM")
    args = ap.parse_args(argv)

    if args.hash:
        print(term_hash(args.hash))
        return 0

    denylist = load_denylist()
    if args.all:
        files, content = whole_tree()
    elif args.staged:
        files, content = added_lines(["--cached"])
    else:
        files, content = added_lines([f"{args.diff}...HEAD"])

    findings = run(files, content, denylist)
    if not findings:
        print(f"privacy check: OK ({len(content)} file(s) checked, {len(denylist)} denylist entries)")
        return 0
    print("privacy check: FAILED. This change adds data that looks private:\n", file=sys.stderr)
    for f in findings:
        print(f"  {f}", file=sys.stderr)
    print(
        "\nRemove it, or replace it with invented data (example.com emails, 555-01xx numbers,"
        "\nmade-up names and addresses). If a line genuinely needs the value, add the comment"
        f"\n`{ALLOW_MARKER}` to that line and explain why in the pull request.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
