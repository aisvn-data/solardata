"""The release notes, and the version they belong to, from one place.

Run with no arguments it prints the notes for the version in ``package.json``:

    python scripts/release_notes.py

That is the whole contract, and it exists because the three facts a release needs
-- the version, the notes, and whether they agree with each other -- used to be
three separate things to remember.  ``release.yml`` had an inline ``awk`` over
``CHANGELOG.md`` that would cheerfully produce notes for a version that had never
been built, from a tag that did not match ``package.json``, and the workflow would
publish it.

**``package.json`` is the canonical version.**  It is the one file the frontend
build already reads, so it cannot be the one that was forgotten.  ``etl/__init__.py``
and ``pyproject.toml`` are checked against it, never read from it.

The notes themselves are the ``CHANGELOG.md`` section for that version, taken
verbatim.  This script does not compose, summarise or reformat them: a release note
that a script assembled is a release note that drifts from the changelog, and the
changelog is what a reader reads next.

Exit status is the check.  ``0`` means the three agree and notes were found;
``2`` means they do not, and the message says which.  ``release.yml`` treats
non-zero as a failure, so a version bump that forgot the changelog cannot ship.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG = REPO / "CHANGELOG.md"
PACKAGE = REPO / "package.json"
ETL_INIT = REPO / "etl" / "__init__.py"
PYPROJECT = REPO / "pyproject.toml"


class ReleaseProblem(Exception):
    """Something a human has to fix before a release can be published."""


def canonical_version() -> str:
    """The version in ``package.json``, which is the only place it is decided."""
    if not PACKAGE.exists():
        raise ReleaseProblem(f"{PACKAGE} is missing; there is no canonical version")
    try:
        version = json.loads(PACKAGE.read_text(encoding="utf-8")).get("version")
    except json.JSONDecodeError as err:
        raise ReleaseProblem(f"{PACKAGE} is not valid JSON: {err}") from err
    if not version:
        raise ReleaseProblem(f"{PACKAGE} has no 'version' field")
    return str(version)


def _declared_version(path: Path, pattern: str, label: str) -> str | None:
    if not path.exists():
        return None
    found = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else None


def check_versions_agree(version: str) -> list[str]:
    """Every place that repeats the version, compared against the canonical one.

    Returns the list of disagreements rather than raising, so the caller can print
    all of them at once.  A release that fails one check at a time is a release
    that fails three times.
    """
    problems: list[str] = []
    for path, pattern, label in (
        (ETL_INIT, r'^__version__ = "([^"]+)"', "etl/__init__.py"),
        (PYPROJECT, r'^version = "([^"]+)"', "pyproject.toml"),
    ):
        found = _declared_version(path, pattern, label)
        if found is None:
            problems.append(f"{label} declares no version, so it cannot be checked")
        elif found != version:
            problems.append(f"{label} says {found}, package.json says {version}")
    return problems


def _section(version: str) -> str | None:
    """The ``## [version]`` section of the changelog, up to the next heading.

    Everything between the version's own heading and the next ``## `` is the
    note, so the body can be as long as it needs to be and a new top-level
    section still terminates it correctly.
    """
    if not CHANGELOG.exists():
        return None
    text = CHANGELOG.read_text(encoding="utf-8")
    start = re.search(rf"(?m)^## \[{re.escape(version)}\][^\n]*\n", text)
    if start is None:
        return None
    rest = text[start.end() :]
    end = re.search(r"(?m)^## ", rest)
    body = rest[: end.start()] if end else rest
    return body.strip("\n")


def release_notes(version: str | None = None) -> tuple[str, list[str]]:
    """The notes for ``version``, and every reason they might be wrong.

    Returns ``(notes, problems)``.  An empty ``problems`` means the release can be
    published; a non-empty one means it cannot, and each entry names the file that
    disagrees rather than describing a class of mistake.
    """
    version = version or canonical_version()
    problems = check_versions_agree(version)
    body = _section(version)
    if body is None:
        found = re.findall(r"(?m)^## \[([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"))
        have = ", ".join(found) if found else "none"
        problems.append(f"CHANGELOG.md has no '## [{version}]' section (it has: {have})")
        body = ""
    elif not body.strip():
        problems.append(f"the '## [{version}]' section in CHANGELOG.md is empty")
    return body, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="report problems only, print nothing, and exit 2 on any disagreement",
    )
    parser.add_argument(
        "--version",
        help="the version to describe, instead of the one in package.json",
    )
    args = parser.parse_args(argv)

    version = args.version or canonical_version()
    notes, problems = release_notes(version)

    if args.check:
        for problem in problems:
            print(f"release problem: {problem}", file=sys.stderr)
        if not problems:
            print(f"{version}: package.json, etl and pyproject agree, and the changelog has notes")
        return 2 if problems else 0

    if problems:
        for problem in problems:
            print(f"release problem: {problem}", file=sys.stderr)
        print(
            "\nA release cannot be published from disagreeing sources. Fix the files above; "
            "do not loosen this check.",
            file=sys.stderr,
        )
        return 2

    print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
