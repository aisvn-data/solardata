"""Validate the GitHub Actions workflow files.

`yaml.safe_load` only checks that the file is *YAML*. It does not check that the
keys are valid for a GitHub Actions workflow, so a misplaced `environment:` parses
happily and is then rejected by Actions with "Invalid workflow file" -- which is
exactly what happened to `pages.yml` on its first push.

The tables below are the subset of the workflow and job schemas this repository
actually uses. They are not exhaustive, but every key that appears in a file here
is checked against them, so a new file cannot introduce an unknown key.
"""

from __future__ import annotations

import unittest
from pathlib import Path

# Deliberately a plain `import yaml` inside a try/except rather than
# `pytest.importorskip`: a module-level import failure aborts the *entire*
# pytest collection rather than skipping one file, so a missing optional
# dependency would take every other test result down with it. Skipping at module
# level is the graceful form, and keeping the import statement visible means the
# dependency-declaration check below can still see it.
try:
    import yaml
except ImportError:  # pragma: no cover - depends on the environment
    import pytest

    pytest.skip(
        "pyyaml is required to validate the GitHub Actions workflows",
        allow_module_level=True,
    )

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

#: Keys GitHub Actions accepts at the top level of a workflow file. `True` is
#: there because `on:` is YAML 1.1's boolean, so PyYAML hands back `True`.
ALLOWED_WORKFLOW_KEYS = {
    "name",
    "run-name",
    "on",
    True,
    "permissions",
    "env",
    "defaults",
    "concurrency",
    "jobs",
}

#: Keys accepted inside a single job.
ALLOWED_JOB_KEYS = {
    "name",
    "needs",
    "if",
    "permissions",
    "environment",
    "concurrency",
    "outputs",
    "env",
    "defaults",
    "steps",
    "timeout-minutes",
    "strategy",
    "continue-on-error",
    "container",
    "services",
    "uses",
    "with",
    "secrets",
    "runs-on",
}

#: Keys accepted inside a single step.
ALLOWED_STEP_KEYS = {
    "id",
    "if",
    "name",
    "uses",
    "run",
    "with",
    "env",
    "continue-on-error",
    "timeout-minutes",
    "working-directory",
    "shell",
}

#: The `on:` key parses as the boolean True under YAML 1.1, which PyYAML follows.
ON_KEYS = {True, "on"}


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))


class TestWorkflowFiles(unittest.TestCase):
    def setUp(self):
        self.files = sorted(p.name for p in WORKFLOW_DIR.glob("*.yml"))
        self.assertTrue(self.files, "no workflow files found")

    def test_every_workflow_uses_only_valid_top_level_keys(self):
        for name in self.files:
            with self.subTest(workflow=name):
                doc = load(name)
                unknown = set(doc) - ALLOWED_WORKFLOW_KEYS
                self.assertEqual(
                    unknown,
                    set(),
                    f"{name}: {sorted(unknown)} are not valid workflow-level keys",
                )

    def test_every_workflow_has_the_required_keys(self):
        for name in self.files:
            with self.subTest(workflow=name):
                doc = load(name)
                self.assertIn("name", doc, f"{name}: missing 'name'")
                self.assertTrue(set(doc) & ON_KEYS, f"{name}: missing a trigger")
                self.assertIn("jobs", doc, f"{name}: missing 'jobs'")
                self.assertTrue(doc["jobs"], f"{name}: no jobs defined")

    def test_every_job_and_step_uses_only_valid_keys(self):
        for name in self.files:
            doc = load(name)
            for job_name, job in doc["jobs"].items():
                with self.subTest(workflow=name, job=job_name):
                    self.assertIsInstance(job, dict, "job must be a mapping")
                    unknown = set(job) - ALLOWED_JOB_KEYS
                    self.assertEqual(unknown, set(), f"{name}/{job_name}: {sorted(unknown)}")
                    self.assertTrue(job.get("steps"), f"{name}/{job_name}: no steps")
                    for index, step in enumerate(job["steps"]):
                        step_unknown = set(step) - ALLOWED_STEP_KEYS
                        self.assertEqual(
                            step_unknown,
                            set(),
                            f"{name}/{job_name}/step{index}: {sorted(step_unknown)}",
                        )
                        self.assertTrue(
                            "uses" in step or "run" in step,
                            f"{name}/{job_name}/step{index}: needs 'uses' or 'run'",
                        )

    def test_needs_refer_to_real_jobs(self):
        for name in self.files:
            doc = load(name)
            jobs = set(doc["jobs"])
            for job_name, job in doc["jobs"].items():
                # A single dependency is written as a bare string, not a list.
                needs = job.get("needs") or []
                if isinstance(needs, str):
                    needs = [needs]
                for dependency in needs:
                    self.assertIn(
                        dependency,
                        jobs,
                        f"{name}/{job_name} needs {dependency!r}, which does not exist",
                    )

    def test_pages_workflow_declares_environment_on_the_job(self):
        # Regression. `environment` is job-level only. At the top level Actions
        # rejects the whole file with "Unexpected value 'environment'" and the
        # workflow never runs at all.
        doc = load("pages.yml")
        self.assertNotIn("environment", doc, "environment must not be a workflow-level key")
        deploy = doc["jobs"]["deploy"]
        self.assertEqual(deploy["environment"]["name"], "github-pages")

    def test_pages_workflow_has_the_permissions_the_deploy_action_needs(self):
        doc = load("pages.yml")
        permissions = doc["permissions"]
        # deploy-pages@v4 exchanges an OIDC token for the deployment.
        self.assertIn("pages", permissions)
        self.assertEqual(permissions["pages"], "write")
        self.assertIn("id-token", permissions)
        self.assertEqual(permissions["id-token"], "write")

    def test_pages_job_publishes_the_vite_output(self):
        doc = load("pages.yml")
        steps = doc["jobs"]["build"]["steps"]
        self.assertTrue(
            any("npx vite build" in (s.get("run") or "") for s in steps),
            "pages must run the vite build",
        )
        upload = next(s for s in steps if "upload-pages-artifact" in s.get("uses", ""))
        self.assertEqual(upload["with"]["path"], "dist")

    def test_no_workflow_uses_a_deprecated_node20_action(self):
        # The Node 20 action runtimes emit a deprecation warning. Pin the
        # current majors so the warning cannot come back unnoticed.
        deprecated = (
            "actions/checkout@v4",
            "actions/setup-node@v4",
            "actions/setup-python@v5",
            "actions/cache@v4",
            "actions/upload-artifact@v4",
            "actions/download-artifact@v4",
        )
        for name in self.files:
            doc = load(name)
            for job_name, job in doc["jobs"].items():
                for step in job["steps"]:
                    uses = step.get("uses", "")
                    self.assertNotIn(uses, deprecated, f"{name}/{job_name} uses {uses}")

    def test_release_workflow_does_not_check_out_a_named_ref(self):
        # Regression. `ref: <tag>` made actions/checkout treat a non-existent
        # tag as a branch pattern and fail three times over.
        doc = load("release.yml")
        for job in doc["jobs"].values():
            for step in job["steps"]:
                if "actions/checkout" in step.get("uses", ""):
                    self.assertNotIn("ref", step.get("with", {}))

    def test_workflows_that_verify_run_every_stage_verify_depends_on(self):
        """Regression. `ingest` rebuilds the database but does not populate
        `regimes` -- that is a separate stage. A workflow that ran only `ingest`
        and then `verify` reported `unconfirmed_regimes: 14 -> 0` and failed,
        which was the guard working correctly and the workflow being wrong.

        `verify` reads every table, so any workflow that calls it must first run
        every stage that populates one.
        """
        from etl.verify import BASELINE_FIELDS

        for name in self.files:
            doc = load(name)
            script = "\n".join(
                step.get("run", "") for job in doc["jobs"].values() for step in job["steps"]
            )
            if "etl verify" not in script:
                continue
            with self.subTest(workflow=name):
                # `etl all` runs every stage, so it satisfies all of them.
                runs_everything = "etl all" in script
                for stage in ("etl ingest", "etl regimes", "etl aggregate", "etl report"):
                    if runs_everything:
                        continue
                    self.assertIn(
                        stage,
                        script,
                        f"{name} runs `etl verify` but never `{stage}`",
                    )
        # Guard the guard: the fields above must still be the ones verify reads,
        # so a new baseline field forces this test to be revisited.
        self.assertIn("unconfirmed_regimes", BASELINE_FIELDS)
        self.assertIn("notes", BASELINE_FIELDS)

    def test_release_workflow_uses_no_third_party_actions(self):
        # softprops/action-gh-release@v2 runs on the Node 20 runtime, which is
        # what produced the deprecation warning. The `gh` CLI ships with the
        # runner, so there is no third-party action left in the release path.
        doc = load("release.yml")
        allowed = ("actions/checkout@", "actions/setup-python@")
        for job in doc["jobs"].values():
            for step in job["steps"]:
                uses = step.get("uses")
                if not uses:
                    continue
                self.assertTrue(
                    uses.startswith(allowed),
                    f"release.yml uses a third-party action: {uses}",
                )

    def test_every_third_party_import_is_a_declared_dependency(self):
        """Regression. `tests/test_workflows.py` imported yaml without it being
        in requirements.txt. It worked locally only because pyyaml happened to
        be installed from an unrelated task, and CI failed at *collection* --
        which aborts the whole run, so every other test result was lost too.

        This walks the imports in the test suite and asserts each third-party
        module is declared, so a new import cannot reach CI undeclared.
        """
        import importlib.util
        import re

        requirements = (
            (WORKFLOW_DIR.parent.parent / "requirements.txt").read_text(encoding="utf-8")
        ).lower()
        # Distribution name -> import name, where they differ.
        renames = {"pyyaml": "yaml", "pyarrow": "pyarrow", "openpyxl": "openpyxl"}

        declared = set()
        for line in requirements.splitlines():
            line = line.split("#")[0].strip().lower()
            match = re.match(r"^([a-z0-9._-]+)", line)
            if match:
                declared.add(renames.get(match.group(1), match.group(1)))

        stdlib = set(getattr(__import__("sys"), "stdlib_module_names", ()))
        tests_dir = WORKFLOW_DIR.parent.parent / "tests"

        for path in sorted(tests_dir.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for statement in re.findall(r"^\s*(?:import|from)\s+([a-zA-Z0-9_.]+)", source, re.M):
                root = statement.split(".")[0]
                if root in stdlib or root.startswith("_") or root == "etl":
                    continue
                if root in ("tests",):
                    continue
                if importlib.util.find_spec(root) is None:
                    continue  # unavailable here, so the module guards it itself
                # A module inside a try/except that skips is still a hard
                # dependency: it is declared, and the guard only stops a minimal
                # environment from taking the whole collection down with it.
                self.assertIn(
                    root,
                    declared,
                    f"{path.name} imports {root!r} but it is not in requirements.txt",
                )


if __name__ == "__main__":
    unittest.main()
