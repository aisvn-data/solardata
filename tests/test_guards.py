"""The build-time guards: the audit, the baseline, and the CLI.

0.8 had 147 tests and none of them could notice that a band was applied to the
wrong unit, because a band being applied to the wrong unit is a claim about the
archive and only a run over the archive can make it. So the checks that can are
here and in ``etl.audit``; what is left in the unit tests is the shape of the
guard, and the guard itself runs in the build.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from etl import __version__, audit, verify
from etl.cli import build_parser, main
from etl.readers import xlsx

from tests.support import TempArchiveCase
from tests.test_pipeline import AISVN_HEADER, PHUMY2_HEADER, aisvn_fixture, phumy2_fixture

REPO = Path(__file__).resolve().parent.parent


class TestAuditChecks(TempArchiveCase):
    def test_the_catalog_check_reports_every_layout(self) -> None:
        check = audit.check_catalog(self.raw)
        self.assertTrue(check.ok, check.failures)
        self.assertIn("no data/raw", check.notes[0], "an empty archive skips the scan")

    def test_a_missing_layout_is_a_failure_not_a_fallback(self) -> None:
        # The archive scan is the expensive part of the build and the only place
        # the catalog is checked against reality, so it is worth one test with a
        # real file in it.
        self.write("aisvn", aisvn_fixture(2), AISVN_HEADER)
        xlsx.clear_read_cache()
        check = audit.check_catalog(self.raw)
        self.assertTrue(check.ok, check.failures)
        self.assertTrue(
            any("1 (station, width) pairs" in note for note in check.notes),
            check.notes,
        )
        self.assertTrue(any("the catalog declares 9" in note for note in check.notes), check.notes)

    def test_an_unregistered_folder_is_a_note_not_a_failure(self) -> None:
        # A folder nobody has decided about is a question, and the note is where
        # it is asked.
        self.write("not_a_station", aisvn_fixture(2), AISVN_HEADER)
        xlsx.clear_read_cache()
        check = audit.check_catalog(self.raw)
        self.assertTrue(check.ok, check.failures)
        self.assertTrue(
            any("not in the station registry" in note for note in check.notes), check.notes
        )

    def test_a_constant_exclusion_that_starts_moving_is_caught(self) -> None:
        # `etl.audit` re-checks on every build, so an exclusion cannot go stale: a
        # channel excluded as constant that begins to vary is a real change in the
        # hardware, and the site is now hiding a measurement.
        from etl import build_aggregate, build_db

        solar_rows = [
            ["May 21, 2020 at 09:52AM", 2197, 2206, 553, 216, 3532, 7, 600, 1],
            ["May 21, 2020 at 09:54AM", 2198, 2207, 554, 217, 3532, 8, 601, 2],
        ]
        solar_header = [
            "time",
            "solar",
            "battery",
            "load_1",
            "load_2",
            "LiPo",
            "wind",
            "dump",
            "boot",
        ]
        self.write("AISVN_Solar", solar_rows, solar_header)
        build_db.ingest(self.settings, verbose=False)
        conn = self.connect()
        self.addCleanup(conn.close)
        build_aggregate.build(conn, verbose=False)

        # wind_v and dump_adc both vary, so a `constant` exclusion is now stale.
        for column in ("wind_v", "dump_adc"):
            row = conn.execute(
                "SELECT min, max FROM channel_stats WHERE station_id = 'aisvn-solar'"
                " AND channel = ?",
                (column,),
            ).fetchone()
            self.assertNotEqual(row["min"], row["max"], column)
        check = audit.check_exclusions(conn)
        self.assertFalse(check.ok, "a channel that now varies must fail the check")
        self.assertEqual(
            len([f for f in check.failures if "excluded as constant" in f]),
            2,
            check.failures,
        )

    def test_the_audit_reports_a_not_measurement_exclusion_without_judging_it(self) -> None:
        # Nothing asserts phumy2's power pin is or is not a measurement -- nobody
        # knows -- so the audit prints the range it recorded and moves on.
        #
        # 0.9.0 used `aisvn.wind_v` as the example. It cannot be any more: the
        # collector has since confirmed that channel as a power measurement in
        # watts, so aisvn has no `not_measurement` exclusion left. The pin nobody
        # can vouch for is phumy2's, which is why the fixture is phumy2's.
        self.build({"phumy2": phumy2_fixture(3)}, {"phumy2": PHUMY2_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        from etl import build_aggregate

        build_aggregate.build(conn, verbose=False)
        check = audit.check_exclusions(conn)
        self.assertTrue(check.ok, check.failures)
        self.assertTrue(
            any("phumy2.power_w excluded as not a measurement" in n for n in check.notes),
            check.notes,
        )


class TestReleaseNotes(unittest.TestCase):
    """The version and its notes come from one place, or a release does not happen.

    `release.yml` had an inline `awk` over `CHANGELOG.md` that would produce notes
    for a version that had never been built, from a tag that did not match
    `package.json`, and publish it. `scripts/release_notes.py` exists so that
    failure is a non-zero exit rather than a release.
    """

    @staticmethod
    def _module():
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "release_notes", REPO / "scripts" / "release_notes.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_repository_is_publishable_right_now(self) -> None:
        notes, problems = self._module().release_notes()
        self.assertEqual(problems, [], "a release cannot be cut from this state")
        self.assertTrue(notes.strip(), "and the notes are not empty")

    def test_a_version_with_no_changelog_section_is_a_problem(self) -> None:
        # The failure this whole script exists for. Named explicitly, so the error
        # names the version that has no section rather than "notes not found".
        _notes, problems = self._module().release_notes("0.0.1")
        self.assertTrue(problems)
        self.assertTrue(
            any("CHANGELOG.md has no '## [0.0.1]' section" in p for p in problems),
            problems,
        )

    def test_a_version_the_code_does_not_declare_is_a_problem(self) -> None:
        problems = self._module().check_versions_agree("9.9.9")
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("etl/__init__.py" in p for p in problems), problems)
        self.assertTrue(any("pyproject.toml" in p for p in problems), problems)

    def test_the_script_fails_loudly_and_names_the_file(self) -> None:
        # Exit 2, not 0 and not a traceback: `release.yml` treats non-zero as a
        # failure, and a script that returns 0 with an empty body is the bug.
        code = self._module().main(["--check", "--version", "0.0.1"])
        self.assertEqual(code, 2)

    def test_release_yml_uses_the_script_rather_than_its_own_awk(self) -> None:
        # Otherwise there are two implementations of "which section belongs to this
        # version", and the one in the workflow is the one that ships.
        workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/release_notes.py", workflow, "the workflow does not call the script")
        self.assertNotIn(
            "CHANGELOG.md >",
            workflow,
            "the workflow still extracts the section itself",
        )


class TestPipelineDocument(TempArchiveCase):
    """`docs/pipeline.md` quotes bands, units and ranges. They must be true.

    The document is generated from the catalog and the built database, which is
    what keeps it honest at the moment it is written and does nothing afterwards.
    A committed document full of numbers that nobody checks is the failure mode
    this project is built to avoid: plausible-looking and wrong, with no error
    anywhere, and a reader has no way to tell which parts moved.

    So it is checked against the catalog here, and against a *built* database for
    the ranges. The band and unit assertions need no archive and run in the unit
    suite; the stored ranges need a build, so they are checked against a fixture
    archive rather than `data/raw`, for the reason the rest of the suite does not
    read the archive.
    """

    #: Backtick, spelled out: the assertions below are about the document's shape,
    #: and writing the character literally invites an editor to eat it.
    TICK = "\u0060"

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = (REPO / "docs" / "pipeline.md").read_text(encoding="utf-8")

    def _section(self, station) -> str:
        from etl.catalog import table_name

        heading = (
            f"### {self.TICK}{station.station_id}{self.TICK}"
            f" -> {self.TICK}{table_name(station.station_id)}{self.TICK}"
        )
        start = self.doc.find(heading)
        self.assertNotEqual(start, -1, f"docs/pipeline.md has no section for {station.station_id}")
        section = self.doc[start:]
        nxt = section.find("\n### ", 3)
        return section[:nxt] if nxt > 0 else section

    def _row(self, section: str, name: str) -> list[str] | None:
        """One table row, split into its cells, or None.

        Split rather than substring-match, and that is the whole point. Comparing
        with `in` looks like it works and does not: a band of `0 .. 5.1` is a
        substring of the stored range `0 .. 5.148` in the same row, so a document
        claiming the wrong band passes the check. Mutation testing the guard is
        what found that, and it is why this parses.
        """
        for line in section.splitlines():
            if not line.startswith("| ") or self.TICK not in line:
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] == f"{self.TICK}{name}{self.TICK}":
                return cells
        return None

    def test_every_station_has_a_section(self) -> None:
        from etl.catalog import STATIONS, table_name

        for station in STATIONS:
            self.assertIn(f"`{station.station_id}` -> `{table_name(station.station_id)}`", self.doc)

    def test_every_channel_is_listed_with_its_real_band_and_unit(self) -> None:
        from etl.catalog import STATIONS

        missing: list[str] = []
        wrong: list[str] = []
        for station in STATIONS:
            section = self._section(station)
            for channel in station.channels:
                cells = self._row(section, channel.name)
                if cells is None:
                    missing.append(f"{station.station_id}.{channel.name}")
                    continue
                # channel | kind | raw -> published | scale | band | stored | notes
                self.assertEqual(len(cells), 7, f"{station.station_id}.{channel.name} row shape")
                units, scale, band = cells[2], cells[3], cells[4]

                want_units = f"{channel.raw_unit or '-'} -> {channel.unit or '-'}"
                if units != want_units:
                    wrong.append(
                        f"{station.station_id}.{channel.name}: units {units!r} != {want_units!r}"
                    )
                if scale != f"{channel.scale:g}":
                    wrong.append(f"{station.station_id}.{channel.name}: scale {scale!r}")

                if not channel.band:
                    want_band = "none"
                elif channel.band[0] is None:
                    want_band = f"<= {channel.band[1]:g}"
                elif channel.band[1] is None:
                    want_band = f">= {channel.band[0]:g}"
                else:
                    want_band = f"{channel.band[0]:g} .. {channel.band[1]:g}"
                if band != want_band:
                    wrong.append(
                        f"{station.station_id}.{channel.name}: band {band!r} != {want_band!r}"
                    )
        self.assertEqual(missing, [], "channels missing from docs/pipeline.md")
        self.assertEqual(wrong, [], "docs/pipeline.md disagrees with etl/catalog.py")

    def test_the_stages_it_describes_are_the_stages_that_run(self) -> None:
        # The document's stage list is a claim about the CLI. If a stage is added
        # and not documented, the next reader is missing a step.
        from etl.cli import HELP

        for stage in ("ingest", "aggregate", "export", "report", "audit", "verify", "all", "fresh"):
            self.assertIn(stage, HELP, f"{stage} is not a stage any more")
            self.assertIn(f"python -m etl {stage}", self.doc, f"docs/pipeline.md omits {stage}")


class TestBaseline(TempArchiveCase):
    def test_a_fresh_build_measures_every_field(self) -> None:
        self.build({"aisvn": aisvn_fixture(4)}, {"aisvn": AISVN_HEADER})
        conn = self.connect()
        self.addCleanup(conn.close)
        from etl import build_aggregate

        build_aggregate.build(conn, verbose=False)
        counts = verify.measure(conn)
        self.assertEqual(set(counts), set(verify.BASELINE_FIELDS))
        self.assertEqual(counts["readings"], 4)
        self.assertEqual(counts["files"], 1)
        self.assertEqual(counts["stations"], 8)
        self.assertEqual(counts["hourly_buckets"], 1)
        self.assertEqual(counts["daily_buckets"], 1)

    def test_drift_is_reported_field_by_field(self) -> None:
        expected = dict.fromkeys(verify.BASELINE_FIELDS, 0)
        expected["readings"] = 999
        expected["notes"] = 12
        result = verify.BaselineResult(
            expected=expected, actual=dict.fromkeys(verify.BASELINE_FIELDS, 0)
        )
        self.assertFalse(result.ok)
        self.assertEqual(
            set(result.drift),
            {"readings", "notes"},
            "only the fields that actually moved are reported",
        )
        self.assertIn("BASELINE DRIFT", verify.render(result))
        self.assertIn("--update-baseline --reason", verify.render(result))
        self.assertIn("Do not loosen", verify.render(result))

    def test_a_matching_baseline_says_so(self) -> None:
        counts = dict.fromkeys(verify.BASELINE_FIELDS, 3)
        result = verify.BaselineResult(expected=dict(counts), actual=dict(counts))
        self.assertTrue(result.ok)
        self.assertEqual(verify.render(result), "baseline matches: 3 readings")

    def test_the_committed_baseline_is_the_current_build(self) -> None:
        # The recorded reason travels with the file, so a diff explains itself in
        # the pull request without anyone re-running anything.
        payload = verify.load(REPO / "data" / "baseline.json")
        self.assertIn("counts", payload)
        self.assertIn("recorded", payload)
        self.assertTrue(payload["recorded"]["reason"].strip(), "a reason is required")
        self.assertEqual(payload["recorded"]["tool_version"], __version__)
        self.assertEqual(len(payload["recorded"]["stations"]), 8)

    def test_readings_is_a_sum_over_the_station_tables(self) -> None:
        # There is no longer one readings table to COUNT(*), so the headline is a
        # sum -- and it is still one number and still the headline.
        self.assertIn("SUM(n_readings)", verify.BASELINE_FIELDS["readings"])

    def test_undeclared_layouts_is_pinned_to_zero(self) -> None:
        # Non-zero means a raw file was ingested with no column meanings, which is
        # the 0.8 failure this pipeline cannot produce.
        self.assertEqual(
            verify.BASELINE_FIELDS["undeclared_layouts"],
            "SELECT COUNT(*) FROM source_files WHERE layout_channels IS NULL",
        )


class TestCli(unittest.TestCase):
    def test_every_stage_is_reachable(self) -> None:
        parser = build_parser()
        for stage in ("ingest", "aggregate", "export", "report", "audit", "verify", "all", "query"):
            with self.subTest(stage=stage):
                self.assertIsNotNone(
                    parser.parse_args([stage] if stage != "query" else [stage, "SELECT 1"])
                )

    def test_a_stage_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_flags_work_on_either_side_of_the_subcommand(self) -> None:
        # Both spellings are in the docs, so both have to work.
        for argv in (
            ["-q", "ingest"],
            ["ingest", "-q"],
            ["ingest", "--quiet"],
        ):
            with self.subTest(argv=argv):
                self.assertTrue(build_parser().parse_args(argv).quiet)

    def test_verify_can_re_record_but_only_with_a_reason(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["verify", "--update-baseline"])
        self.assertTrue(args.update_baseline)
        self.assertEqual(args.reason, "")

    def test_the_paths_are_configurable(self) -> None:
        args = build_parser().parse_args(
            ["ingest", "--raw-dir", "/tmp/raw", "--out-dir", "/tmp/out", "--export-dir", "/tmp/pub"]
        )
        self.assertEqual(args.raw_dir, "/tmp/raw")
        self.assertEqual(args.out_dir, "/tmp/out")
        self.assertEqual(args.export_dir, "/tmp/pub")

    def test_query_refuses_a_database_that_was_never_built(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main(["--out-dir", "/nonexistent", "query", "SELECT 1"])
        self.assertIn("no database at", str(caught.exception))


class TestVersionConsistency(unittest.TestCase):
    def test_the_version_is_the_same_everywhere(self) -> None:
        import etl

        # Derived from the canonical version rather than written out, so bumping
        # the version does not require editing a test that would then agree with
        # whatever was typed -- which is the failure this test exists to catch.
        # `package.json` is canonical; `etl/__init__.py` and `pyproject.toml` are
        # checked against it, never read from it.
        package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(etl.__version__, package["version"], "etl and package.json")
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{etl.__version__}"', pyproject, "pyproject.toml")
        init = (REPO / "etl/__init__.py").read_text(encoding="utf-8")
        self.assertIn(f'__version__ = "{etl.__version__}"', init, "etl/__init__.py")

    def test_the_changelog_has_a_section_for_this_version(self) -> None:
        import etl

        # Likewise derived: the point is that the changelog has a section for
        # whatever the code says it is, not that it has a section for 0.9.0.
        changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(changelog, rf"(?m)^## \[{re.escape(etl.__version__)}\]")

    def test_no_module_still_imports_a_deleted_one(self) -> None:
        # 0.9 deleted build_regimes, normalize/units, rollup_schema, stations and
        # the Parquet stage. An import that survived is a build that fails on the
        # first run rather than a test that fails here.
        import ast

        gone = ("build_regimes", "normalize", "rollup_schema", "stations", "build_parquet")
        for path in sorted((REPO / "etl").rglob("*.py")) + sorted((REPO / "tests").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                for name in names:
                    for dead in gone:
                        self.assertNotIn(dead, name, f"{path.name} still imports {dead}")


class TestWorkflowFiles(unittest.TestCase):
    """The workflows, and what they are each allowed to assume."""

    def load(self, name: str) -> str:
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_data_fresh_rebuilds_from_scratch_and_can_be_dispatched(self) -> None:
        text = self.load("data_fresh.yml")
        self.assertIn("workflow_dispatch:", text, "it has to be triggerable by hand")
        self.assertIn("python -m etl ingest", text)
        # From scratch: the database is deleted before the build, so a run can
        # never append to or patch what a previous run left.
        self.assertRegex(text, r"(?i)rm -[rf]*\s+.*solardata\.db")
        self.assertIn("python -m etl verify", text)
        self.assertIn("python -m etl all", text)
        # etl all is ingest, aggregate, export, report and audit. The audit has
        # to be in it, because it is the only stage that reads the raw archive
        # rather than the database the others produced.
        from etl.cli import ALL_STAGES

        self.assertIn("audit", ALL_STAGES)

    def test_data_fresh_verifies_so_it_cannot_publish_a_drifted_build(self) -> None:
        # Every workflow that calls `verify` has to run every stage the baseline
        # depends on, or the guard compares a partial build against a full one and
        # fails for the wrong reason -- which is how a release once shipped with
        # empty regime tables.
        text = self.load("data_fresh.yml")
        if "etl verify" not in text:
            self.skipTest("no verify in this workflow")
        self.assertTrue(
            "etl all" in text
            or all(
                stage in text
                for stage in ("etl ingest", "etl aggregate", "etl export", "etl report")
            ),
            "verify is called without the stages it measures",
        )

    def test_ci_does_not_build_the_data(self) -> None:
        # Lint, unit tests, the frontend checks and the build. A full run over 364
        # files is `data.yml`'s job, and the two are different promises.
        text = self.load("ci.yml")
        self.assertIn("pytest", text)
        self.assertIn("check_frontend", text)
        self.assertIn("check:render", text)
        self.assertNotIn("etl all", text)
        self.assertNotIn("etl verify", text)

    def test_ci_is_not_path_gated(self) -> None:
        # A path-gated required check produces no run at all when the paths do not
        # match, which leaves a pull request waiting forever.
        text = self.load("ci.yml")
        head = text.split("jobs:", 1)[0]
        self.assertNotIn("paths:", head.split("pull_request:")[0].split("push:")[-1])

    def test_data_is_path_gated_with_a_schedule(self) -> None:
        # The gate is the point, and the weekly sweep is the backstop: a path
        # filter only sees the paths GitHub reports, so anything it misses is
        # caught on Monday.
        text = self.load("data.yml")
        self.assertIn("paths:", text)
        self.assertIn("data/raw/**", text)
        self.assertIn("etl/**", text)
        self.assertIn("schedule:", text)
        self.assertIn("cron:", text)

    def test_data_does_not_run_on_a_frontend_only_change(self) -> None:
        # A pull request that touches only `src/` cannot change the data, because
        # the data comes from `data/raw` through `etl/`.
        text = self.load("data.yml")
        paths = text.split("paths:", 1)[1].split("pull_request:", 1)[0]
        self.assertNotIn("src/**", paths)
        self.assertNotIn("public/data/**", paths)

    def test_pages_asserts_the_data_reached_the_bundle(self) -> None:
        text = self.load("pages.yml")
        for name in ("stations.json", "metrics.json", "quality.json"):
            self.assertIn(name, text, name)
        self.assertIn("dist/data", text)

    def test_pages_runs_the_two_frontend_checks(self) -> None:
        text = self.load("pages.yml")
        self.assertIn("check_frontend", text)
        self.assertIn("check:render", text)


if __name__ == "__main__":
    unittest.main()
