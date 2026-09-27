"""The changelog is a machine-readable record, and something reads it.

`.github/workflows/release.yml` extracts a `## [<version>]` section out of
`CHANGELOG.md` to write the GitHub Release notes, so the format is a contract
rather than a convention. Three things can break it silently, and all three have
happened:

* **A duplicated version heading.** `5f4ecc6` added a second `## [0.7.1]`
  section, because `db13d57` had already used that version. The extractor takes
  the *first* match and stops at the next `## [`, so one of the two sections
  became unreachable -- and the release notes for that version would have
  described half of it.
* **A tag with no section.** `v0.7.2` was released with a hand-written title and
  no `## [0.7.2]` anywhere. Nothing failed; the release simply had no changelog.
* **A tag whose section is the wrong one.** The tag is `v0.7.1` and the heading
  is `## [0.7.1]`, so matching on the tag verbatim finds nothing.

These tests are about the *file*, not the workflow: they are what would have
caught the duplication, and they run in `ci.yml` where a release does not.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

#: `## [0.8.0] - 2026-09-27`, `## [Unreleased]`, `## [Open questions]`. The
#: trailing `[^#]*` swallows the date and stops the pattern eating a section that
#: has no date, and the capture excludes the closing bracket so a prefix like
#: `0.7.1` cannot match `0.7.10`.
SECTION = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)


def _sections() -> list[str]:
    return SECTION.findall(CHANGELOG.read_text(encoding="utf-8"))


class TestChangelogIsMachineReadable(unittest.TestCase):
    def test_a_version_appears_once(self):
        # The regression. Two sections under one version heading means the
        # release notes for that version silently describe only the first.
        seen: dict[str, int] = {}
        for name in _sections():
            seen[name] = seen.get(name, 0) + 1
        duplicated = {name: count for name, count in seen.items() if count > 1}
        self.assertEqual(
            duplicated,
            {},
            f"CHANGELOG.md has duplicate sections for {duplicated}; the release notes "
            "extract the first one only",
        )

    def test_every_tag_has_a_section(self):
        # A release with no changelog is a release nobody can read. This is what
        # v0.7.2 was: tagged, published, and absent from the file.
        #
        # Tags that are not `X.Y.Z` are exempt and reported instead. `v0.2` is one
        # and will stay one -- the changelog calls that release 0.2.0, and
        # rewriting a published tag to agree with a file written after it is worse
        # than recording the mismatch. `docs/roadmap.md` lists it.
        sections = _sections()
        for line in _git_tags():
            version = line.removeprefix("v")
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                continue
            self.assertIn(
                version,
                sections,
                f"tag {line} has no CHANGELOG.md section, so its release notes "
                "would fall back to a generated commit list",
            )

    def test_the_version_is_a_release_version_and_not_something_else(self):
        # `Unreleased` and the top-level `## Findings`/`## Open questions` sections
        # are document structure, not releases. Anything else that is not a version
        # is a heading somebody added without thinking about the extractor.
        for name in _sections():
            self.assertTrue(
                name == "Unreleased" or re.fullmatch(r"\d+\.\d+\.\d+", name),
                f"CHANGELOG.md section [{name}] is neither a version nor Unreleased",
            )

    def test_sections_are_in_descending_version_order(self):
        # Keep a Changelog convention, and it is the only thing that tells a
        # reader which section is the newest without checking the tag list.
        versions = [n for n in _sections() if re.fullmatch(r"\d+\.\d+\.\d+", n)]
        keys = [tuple(int(p) for p in v.split(".")) for v in versions]
        self.assertEqual(keys, sorted(keys, reverse=True), f"out of order: {versions}")

    def test_the_extractor_in_the_release_workflow_finds_the_newest_section(self):
        # The awk program from release.yml, run here so the workflow's parsing and
        # this file cannot drift. It is the same text, not a reimplementation:
        # a second implementation is a second thing to be wrong.
        program = _workflow_awk()
        if program is None:
            self.skipTest("release.yml no longer contains the changelog extractor")
        for name in _sections():
            extracted = _run_awk(program, name)
            self.assertNotEqual(
                extracted.strip(),
                "",
                f"the release workflow's extractor finds nothing for [{name}]",
            )
        # And a version that is not there yields nothing, which is what makes the
        # workflow's fallback fire rather than publishing an empty release.
        self.assertEqual(_run_awk(program, "0.0.0").strip(), "")


def _git_tags() -> list[str]:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "tag", "-l", "v*"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError:  # pragma: no cover - depends on the environment
        return []
    if out.returncode != 0:
        return []
    return [line for line in out.stdout.split() if line.startswith("v")]


def _workflow_awk() -> str | None:
    """The `awk -v want=...` program as written in release.yml."""
    import re as _re

    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    match = _re.search(r"awk -v want=\"\$version\" '(.*?)'", text, _re.DOTALL)
    return match.group(1) if match else None


def _run_awk(program: str, want: str) -> str:
    import shutil
    import subprocess

    # `awk` ships with the ubuntu runner, not with Windows. The extractor is only
    # ever run by release.yml, so its absence here is a skip rather than a
    # failure -- CI runs it for real.
    if shutil.which("awk") is None:
        raise unittest.SkipTest("awk is not available on this platform")
    result = subprocess.run(
        ["awk", "-v", f"want={want}", program, str(CHANGELOG)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
