"""Task-code references in CODE.md (SPEC §2.9, AC-T-CIL-MAP-01)."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import compile as compiler, layout, validate  # noqa: E402
from praukron.model import REFERENCE_RELATIONS, REFERENCE_SCOPES  # noqa: E402
from praukron.parse import ParseError  # noqa: E402
from test_praukron import FixtureCase, build_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# Compiled by release 2.3.1 (v2.3.1) from the fixture `anchored` builds.
GOLDEN = Path(__file__).resolve().parent / "data" / "release-2.3.1-anchors.json"


def reference(ref_id: str, task: str, path: str, **extra: str) -> str:
    fields = {"Task": task, "Path": path, "Relation": "modifies", "Scope": "planned",
              "Verification": "unverified", "Authorization": "proposed", "Source": "agent",
              "Recorded": "2026-01-02 · agent/builder"}
    fields.update({key.replace("_", " "): value for key, value in extra.items()})
    lines = [f"## {ref_id}: {ref_id.lower()}"]
    lines += [f"- {key}: {value}" for key, value in fields.items() if value is not None]
    return "\n".join(lines) + "\n"


CODE = "# Code references\n\nIntro prose is not a record.\n\n" + "\n".join([
    reference("CR-T-ONE-01", "T-ONE", "src/app/core.py"),
    reference("CR-T-ONE-02", "T-ONE", "src/app/core.py", Symbol="Core.run", Kind="method",
              Relation="creates", Scope="observed", Source="git_diff", Confidence="0.75",
              Lines="10-20"),
    reference("CR-T-ONE-03", "T-ONE", "src/app/util.py", Symbol="Core.run", Kind="function",
              Relation="reads", Authorization="not_applicable"),
    reference("CR-T-TWO-01", "T-TWO", "src/app/core.py", Relation="reads",
              Authorization="not_applicable"),
])

ANCHORS = (
    ("## T-ONE: Lay the foundation\n", "- Files: src/app/core.py, docs/guide.md\n- Symbols: Core.run, helper\n"),
    ("## T-TWO: Build on it\n", "- Files: src/app/core.py\n"),
)


def anchored(root: Path) -> None:
    """Give two fixture tasks `Files:` and `Symbols:` anchors, as release 2.3.1 wrote them."""
    path = root / layout.AUTHORITY_DIR / "TASKS.md"
    text = path.read_text(encoding="utf-8")
    for heading, fields in ANCHORS:
        start = text.index(heading)
        end = text.index("\n\n", start) + 1
        text = text[:end] + fields + text[end:]
    path.write_text(text, encoding="utf-8")


def chronicle_bytes(root: Path) -> dict[str, str]:
    authority = root / layout.AUTHORITY_DIR
    return {path.relative_to(authority).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(authority.rglob("*")) if path.is_file()}


class ReferenceCase(FixtureCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / layout.AUTHORITY_DIR / "CODE.md"
        self.write(CODE)

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")
        self.project = compiler.load(self.root)

    def replace(self, old: str, new: str) -> None:
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.write(text.replace(old, new, 1))

    def data(self) -> dict:
        return compiler.as_json(self.project)

    def compiled(self) -> dict[str, dict]:
        return {r["id"]: r for r in self.data()["references"]}

    def findings(self, code: str) -> list:
        return [f for f in validate.check(self.project) if f.code == code]


class TestTasksAndReferences(ReferenceCase):
    """AC-T-CIL-MAP-01-01."""

    def test_task_with_none_and_task_with_several_are_valid_and_listed(self) -> None:
        self.assertEqual(validate.errors(validate.check(self.project)), [])
        by_task: dict[str, list[str]] = {}
        for item in self.data()["references"]:
            by_task.setdefault(item["task"], []).append(item["id"])
        self.assertEqual(by_task, {"T-ONE": ["CR-T-ONE-01", "CR-T-ONE-02", "CR-T-ONE-03"],
                                   "T-TWO": ["CR-T-TWO-01"]})
        self.assertIsNotNone(self.project.task("T-THREE"))
        symbol = self.compiled()["CR-T-ONE-02"]
        self.assertEqual((symbol["path"], symbol["symbol"], symbol["kind"], symbol["relation"],
                          symbol["scope"], symbol["source"], symbol["lines"]),
                         ("src/app/core.py", "Core.run", "method", "creates", "observed", "git_diff", "10-20"))
        self.assertEqual(self.project.references[0].source.file, "CODE.md")

    def test_compiled_shape_has_every_field(self) -> None:
        self.assertEqual(list(self.compiled()["CR-T-ONE-01"]), [
            "id", "task", "repository", "path", "symbol", "kind", "class", "relation", "scope",
            "lifecycle", "verification", "authorization", "authorizedBy", "source", "provider",
            "recorded", "revision", "base", "head", "lines", "confidence", "was", "replaces",
            "withdrawn", "evidence", "legacy"])
        self.assertIs(self.compiled()["CR-T-ONE-01"]["legacy"], False)

    def test_chronicle_without_code_md_is_valid_with_no_references(self) -> None:
        self.path.unlink()
        self.project = compiler.load(self.root)
        self.assertEqual(self.project.references, [])
        self.assertEqual(validate.errors(validate.check(self.project)), [])
        data = self.data()
        self.assertEqual((data["references"], data["artifacts"]), ([], []))

    def test_installed_template_has_no_records_and_validates(self) -> None:
        self.write((ROOT / "templates/chronicle/CODE.md").read_text(encoding="utf-8"))
        self.assertEqual(self.project.references, [])
        self.assertEqual(validate.errors(validate.check(self.project)), [])

    def test_installer_ships_the_template_and_never_replaces_it(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn(" COORDINATION CODE TECH_DEBT ASSUMPTIONS RESPONSES; do\n  copy_new", installer)
        self.assertIn('cmp "$upgrade/code" "$upgrade/.praukron/chronicle/CODE.md"',
                      (ROOT / "tests/install.sh").read_text(encoding="utf-8"))

    def test_crlf_file_parses_the_same(self) -> None:
        self.path.write_bytes(CODE.replace("\n", "\r\n").encode("utf-8"))
        project = compiler.load(self.root)
        self.assertEqual([r.id for r in project.references],
                         ["CR-T-ONE-01", "CR-T-ONE-02", "CR-T-ONE-03", "CR-T-TWO-01"])
        self.assertEqual(project.references[1].symbol, "Core.run")


class TestArtifacts(ReferenceCase):
    """AC-T-CIL-MAP-01-02."""

    def test_shared_file_is_one_artifact_and_same_named_symbols_stay_distinct(self) -> None:
        artifacts = self.data()["artifacts"]
        core = [a for a in artifacts if a["path"] == "src/app/core.py" and a["symbol"] is None]
        self.assertEqual(len(core), 1)
        self.assertEqual(core[0]["tasks"], ["T-ONE", "T-TWO"])
        self.assertIn("CR-T-ONE-01", core[0]["references"])
        self.assertIn("CR-T-TWO-01", core[0]["references"])
        named = [a for a in artifacts if a["symbol"] == "Core.run"]
        self.assertEqual(sorted((a["path"], a["references"][0]) for a in named),
                         [("src/app/core.py", "CR-T-ONE-02"), ("src/app/util.py", "CR-T-ONE-03")])
        self.assertTrue(all(a["repository"] is None for a in artifacts))

    def test_another_repository_is_another_artifact(self) -> None:
        self.replace("- Task: T-TWO\n- Path: src/app/core.py\n",
                     "- Task: T-TWO\n- Path: src/app/core.py\n- Repository: other\n")
        core = [a for a in self.data()["artifacts"] if a["path"] == "src/app/core.py" and a["symbol"] is None]
        self.assertEqual(sorted((a["repository"] or "", a["tasks"][0]) for a in core),
                         [("", "T-ONE"), ("other", "T-TWO")])


class TestValues(ReferenceCase):
    """AC-T-CIL-MAP-01-03."""

    def test_every_allowed_relation_and_scope_is_valid(self) -> None:
        self.assertEqual(REFERENCE_RELATIONS, ("creates", "modifies", "deletes", "reads", "depends_on",
                                               "verifies", "documents", "affects"))
        self.assertEqual(REFERENCE_SCOPES, ("planned", "observed"))
        text = "# Code references\n\n" + "\n".join(
            reference(f"CR-T-ONE-{n:02d}", "T-ONE", f"src/f{n}.py", Relation=relation, Scope=scope)
            for n, (relation, scope) in enumerate(
                ((r, s) for r in REFERENCE_RELATIONS for s in REFERENCE_SCOPES), start=1))
        self.write(text)
        self.assertEqual(len(self.project.references), 16)
        self.assertEqual(self.findings("invalid-reference-value"), [])
        self.assertEqual(validate.errors(validate.check(self.project)), [])

    def test_any_other_value_is_an_error_naming_the_record(self) -> None:
        for field, value in (("Relation", "touches"), ("Relation", "Modifies"), ("Scope", "actual"),
                             ("Verification", "proposed"), ("Authorization", "verified"),
                             ("Source", "guess"), ("Kind", "widget"), ("Class", "prod"),
                             ("Lifecycle", "gone")):
            with self.subTest(field=field, value=value):
                extra = {field: value}
                if field == "Kind":
                    extra["Symbol"] = "Core.run"
                self.write("# Code references\n\n" + reference("CR-T-TWO-07", "T-TWO", "src/a.py", **extra))
                found = self.findings("invalid-reference-value")
                self.assertEqual(len(found), 1, found)
                self.assertEqual(found[0].severity, "error")
                self.assertEqual(found[0].where, "CODE.md#CR-T-TWO-07")
                self.assertIn("CR-T-TWO-07", found[0].message)
                self.assertIn(f"{field} '{value}'", found[0].message)

    def test_verification_and_authorization_are_separate_fields(self) -> None:
        self.replace("- Verification: unverified\n- Authorization: proposed\n",
                     "- Verification: verified\n- Authorization: rejected\n")
        first = self.compiled()["CR-T-ONE-01"]
        self.assertEqual((first["verification"], first["authorization"]), ("verified", "rejected"))
        self.assertEqual(validate.errors(validate.check(self.project)), [])
        for missing in ("Verification", "Authorization"):
            with self.subTest(missing=missing):
                text = "# Code references\n\n" + reference("CR-T-ONE-01", "T-ONE", "src/a.py",
                                                            **{missing: None})
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ParseError) as raised:
                    compiler.load(self.root)
                self.assertEqual(raised.exception.anchor, "CR-T-ONE-01")
                self.assertIn(missing, str(raised.exception))


class TestLegacyAnchors(unittest.TestCase):
    """AC-T-CIL-MAP-01-04."""

    def setUp(self) -> None:
        # Named as the golden's was: a project with no name of its own is named by its directory.
        base = Path(tempfile.mkdtemp(prefix="praukron-test-"))
        self.addCleanup(shutil.rmtree, base, True)
        self.root = build_fixture(base / "release-fixture")
        anchored(self.root)
        self.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_compile_changes_no_chronicle_file_and_keeps_the_task_output(self) -> None:
        before = chronicle_bytes(self.root)
        project = compiler.load(self.root)
        compiler.write(self.root, project)
        after = chronicle_bytes(self.root)
        after.pop("INDEX.md")
        self.assertEqual(after, before)
        self.assertNotIn("CODE.md", after)
        # INDEX.md is generated, and is generated as release 2.3.1 generated it, but for its version.
        index = (self.root / layout.AUTHORITY_DIR / "INDEX.md").read_text(encoding="utf-8")
        self.assertEqual(index.replace(f"praukron {layout.version()} ", "praukron <version> "),
                         self.golden["index"])
        data = json.loads((self.root / layout.COMPILED_DIR / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(data["tasks"], self.golden["tasks"])
        for key, value in self.golden["fields"].items():
            self.assertEqual(data[key], value, key)

    def test_each_anchor_is_also_a_planned_manual_reference(self) -> None:
        data = compiler.as_json(compiler.load(self.root))
        legacy = [(r["task"], r["path"], r["symbol"], r["scope"], r["source"], r["relation"], r["lifecycle"])
                  for r in data["references"] if r["legacy"]]
        self.assertEqual(legacy, [
            ("T-ONE", "src/app/core.py", None, "planned", "manual", None, None),
            ("T-ONE", "docs/guide.md", None, "planned", "manual", None, None),
            ("T-ONE", None, "Core.run", "planned", "manual", None, "unresolved"),
            ("T-ONE", None, "helper", "planned", "manual", None, "unresolved"),
            ("T-TWO", "src/app/core.py", None, "planned", "manual", None, None),
        ])
        self.assertEqual(len({r["id"] for r in data["references"]}), 5)
        core = [a for a in data["artifacts"] if a["path"] == "src/app/core.py"]
        self.assertEqual(core[0]["tasks"], ["T-ONE", "T-TWO"])
        # A bare symbol name is never an identity, so a Symbols: anchor has no artifact.
        self.assertFalse([a for a in data["artifacts"] if a["path"] is None])
        task = next(t for t in data["tasks"] if t["id"] == "T-ONE")
        self.assertEqual(task["implementation"], {"files": ["src/app/core.py", "docs/guide.md"],
                                                  "symbols": ["Core.run", "helper"]})

    def test_anchors_add_no_finding(self) -> None:
        project = compiler.load(self.root)
        self.assertEqual([f.render() for f in validate.check(project)
                          if "reference" in f.code or f.where.startswith("CODE.md")], [])


class TestPaths(ReferenceCase):
    """AC-T-CIL-MAP-01-05."""

    def test_absolute_or_escaping_path_is_an_error_and_not_compiled(self) -> None:
        for bad in ("/etc/app.py", "../outside.py", "src/../../outside.py", "src/..", "C:/app.py",
                    "c:app.py", "\\\\server\\share\\app.py", "src\\app.py"):
            with self.subTest(path=bad):
                self.write(CODE.replace("- Task: T-TWO\n- Path: src/app/core.py\n",
                                        f"- Task: T-TWO\n- Path: {bad}\n", 1))
                found = self.findings("invalid-reference-path")
                self.assertEqual([(f.severity, f.where) for f in found], [("error", "CODE.md#CR-T-TWO-01")])
                self.assertIn("CR-T-TWO-01", found[0].message)
                data = self.data()
                self.assertNotIn("CR-T-TWO-01", [r["id"] for r in data["references"]])
                self.assertFalse([a for a in data["artifacts"] if "CR-T-TWO-01" in a["references"]])
                self.assertNotIn(bad, [a["path"] for a in data["artifacts"]])

    def test_relative_path_to_a_file_not_yet_written_is_valid(self) -> None:
        self.assertFalse((self.root / "src/app/core.py").exists())
        self.assertEqual(self.findings("invalid-reference-path"), [])
        self.assertIn("CR-T-TWO-01", self.compiled())


class TestConfidence(ReferenceCase):
    """AC-T-CIL-MAP-01-06."""

    def test_no_confidence_compiles_as_absent_not_a_default(self) -> None:
        compiled = self.compiled()
        self.assertIsNone(compiled["CR-T-ONE-01"]["confidence"])
        self.assertEqual(compiled["CR-T-ONE-02"]["confidence"], 0.75)
        legacy = [r for r in self.data()["references"] if r["legacy"]]
        self.assertTrue(all(r["confidence"] is None for r in legacy))

    def test_zero_is_a_recorded_confidence_and_garbage_is_an_error(self) -> None:
        self.replace("- Confidence: 0.75\n", "- Confidence: 0\n")
        self.assertEqual(self.compiled()["CR-T-ONE-02"]["confidence"], 0.0)
        for bad in ("high", "1.5", "-0.1"):
            with self.subTest(value=bad):
                self.write(CODE.replace("- Confidence: 0.75\n", f"- Confidence: {bad}\n"))
                found = self.findings("invalid-reference-value")
                self.assertEqual([f.where for f in found], ["CODE.md#CR-T-ONE-02"])
                self.assertIsNone(self.compiled()["CR-T-ONE-02"]["confidence"])


class TestOtherFindings(ReferenceCase):
    """The remaining codes of SPEC §2.9."""

    def assertOnly(self, code: str, where: str, severity: str = "error") -> None:
        found = [f for f in validate.check(self.project) if f.where.startswith("CODE.md")]
        self.assertEqual([(f.severity, f.code, f.where) for f in found], [(severity, code, where)])

    def test_malformed_heading_or_missing_field_fails_the_load(self) -> None:
        self.path.write_text("# Code references\n\n## CR-ONE: no task\n- Task: T-ONE\n", encoding="utf-8")
        with self.assertRaises(ParseError):
            compiler.load(self.root)
        self.path.write_text(CODE.replace("- Scope: observed\n", ""), encoding="utf-8")
        with self.assertRaises(ParseError) as raised:
            compiler.load(self.root)
        self.assertEqual(raised.exception.anchor, "CR-T-ONE-02")

    def test_duplicate_reference(self) -> None:
        self.write(CODE + "\n" + reference("CR-T-TWO-01", "T-TWO", "src/other.py"))
        self.assertEqual([f.code for f in validate.errors(validate.check(self.project))],
                         ["duplicate-reference"])

    def test_unknown_task(self) -> None:
        self.write(CODE + "\n" + reference("CR-T-GHOST-01", "T-GHOST", "src/other.py"))
        self.assertOnly("unknown-reference-task", "CODE.md#CR-T-GHOST-01")

    def test_identifier_must_follow_its_task(self) -> None:
        self.write(CODE + "\n" + reference("CR-T-ONE-09", "T-TWO", "src/other.py"))
        self.assertOnly("invalid-reference-value", "CODE.md#CR-T-ONE-09")

    def test_symbol_without_kind(self) -> None:
        self.replace("- Kind: function\n", "")
        self.assertOnly("symbol-without-kind", "CODE.md#CR-T-ONE-03")

    def test_authorized_needs_an_owner_response_or_accepted_adr(self) -> None:
        self.replace("- Authorization: proposed\n", "- Authorization: authorized\n")
        self.assertOnly("authorized-without-authority", "CODE.md#CR-T-ONE-01")
        self.replace("- Authorization: authorized\n", "- Authorization: authorized\n- Authorized by: ADR-404\n")
        self.assertOnly("authorized-without-authority", "CODE.md#CR-T-ONE-01")
        self.replace("- Authorized by: ADR-404\n", "- Authorized by: ADR-001\n")
        self.assertEqual(self.project.decisions[0].status, "ACCEPTED")
        self.assertEqual(self.findings("authorized-without-authority"), [])

    def test_graph_provider_needs_a_provider(self) -> None:
        self.replace("- Source: git_diff\n", "- Source: graph_provider\n")
        self.assertOnly("provider-missing", "CODE.md#CR-T-ONE-02")
        self.replace("- Source: graph_provider\n", "- Source: graph_provider\n- Provider: graphtool 1.0\n")
        self.assertEqual(self.findings("provider-missing"), [])

    def test_unknown_replaced_reference(self) -> None:
        self.write(CODE + "\n" + reference("CR-T-TWO-02", "T-TWO", "src/b.py", Replaces="CR-T-TWO-09"))
        self.assertOnly("unknown-replaced-reference", "CODE.md#CR-T-TWO-02")

    def test_repeated_reference_warns_until_one_replaces_the_other(self) -> None:
        again = reference("CR-T-TWO-02", "T-TWO", "src/app/core.py", Relation="reads",
                          Authorization="not_applicable")
        self.write(CODE + "\n" + again)
        self.assertOnly("repeated-reference", "CODE.md#CR-T-TWO-02", "warning")
        self.write(CODE + "\n" + reference("CR-T-TWO-02", "T-TWO", "src/app/core.py", Relation="reads",
                                           Authorization="not_applicable", Replaces="CR-T-TWO-01"))
        self.assertEqual(self.findings("repeated-reference"), [])
        # A different scope is a different claim: observed never replaces planned.
        self.write(CODE + "\n" + reference("CR-T-TWO-02", "T-TWO", "src/app/core.py", Relation="reads",
                                           Scope="observed", Authorization="not_applicable"))
        self.assertEqual(self.findings("repeated-reference"), [])

    def test_possible_secret(self) -> None:
        self.replace("- Lines: 10-20\n", "- Lines: 10-20\n- Evidence: token=abc123def\n")
        self.assertOnly("possible-secret", "CODE.md#CR-T-ONE-02", "warning")

    def test_lines_must_be_a_range(self) -> None:
        self.replace("- Lines: 10-20\n", "- Lines: ten\n")
        self.assertOnly("invalid-reference-value", "CODE.md#CR-T-ONE-02")


if __name__ == "__main__":
    unittest.main()
