"""Guidance and refusals that cut write turns (AC-T-WRITE-GUIDE-01, AC-T-WRITE-REFUSAL-01)."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import acr, batch, cli, contract, criterion, journal, layout, mutate, task, task_update  # noqa: E402
from test_praukron import FixtureCase, build_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    "journal append": ("journal.append", journal.SCHEMA),
    "criterion set": ("criterion.set", criterion.SCHEMA),
    "task add": ("task.add", task.SCHEMA),
    "contract add": ("contract.add", contract.SCHEMA),
    "task update": ("task.update", task_update.SCHEMA),
    "acr add": ("acr.add", acr.SCHEMA),
}
# The commands AGENTS.md gives an example for: the writes, and the batch that composes them.
EXAMPLE_COMMANDS = {**COMMANDS, "batch apply": ("batch.apply", batch.schema())}
# AGENTS.md on main before T-WRITE-GUIDE-01 (2.0.0 plus #36 and #37), LF-normalized, and the
# net growth that task allows; T-WRITE-ACR-01 then adds the sixth command, its example and its rule,
# and T-WRITE-BATCH-01 the batch command, its example and its rule.
AGENTS_BASE_BYTES, AGENTS_BUDGET, ACR_BYTES, BATCH_BYTES = 10800, 1200, 300, 400

# What the AGENTS.md examples name, beside the shared fixture.
EXTRA_MODULE = "\n## M-CORE — Core\n- Phase: P1\n- Outcome: The core works.\n"
EXTRA_TASKS = """
## T-1: Start the core
- Status: TODO
- Module: M-CORE
- Validation: UNTESTED
- Dependencies: none
- Owner: unassigned
- AC: AC-T-1
- Evidence: —

## T-3: Import
- Status: TODO
- Module: M-CORE
- Validation: UNTESTED
- Dependencies: none
- Owner: unassigned
- AC: —
- Evidence: —

## T-4: Save
- Status: DONE
- Module: M-CORE
- Validation: UNTESTED
- Dependencies: none
- Owner: agent
- AC: AC-T-4
- Evidence: tests pass
"""
EXTRA_ACCEPTANCE = """
## AC-T-1 — Start the core

- `AC-T-1-01` — Given the core, When it starts, Then it answers.
  `TEST` · `NOT_RUN`
- `AC-T-1-02` — Given the core, When it stops, Then it says so.
  `TEST` · `NOT_RUN`

## AC-T-4 — Save

- `AC-T-4-01` — Given a file, When saved, Then it is fine.
  `TEST` · `PASS`
"""


def flat(text: str) -> str:
    return " ".join(text.split())


def agents_examples() -> list[tuple[str, str]]:
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    block = re.search(r"Minimal payloads[^\n]*\n\s*```text\n(.*?)```", text, re.S)
    assert block, "AGENTS.md has no payload example block"
    pattern = re.compile(r"(" + "|".join(EXAMPLE_COMMANDS) + r")\s+(\{.*\})")
    return [pattern.fullmatch(line.strip()).groups() for line in block.group(1).strip().splitlines()]


def run(root: Path, command: str, payload: object) -> tuple[int, dict]:
    out, err = io.StringIO(), io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(raw)):
        code = cli.main(["-C", str(root), *command.split(), "--input", "-"])
    lines = out.getvalue().splitlines()
    assert len(lines) == 1, out.getvalue()
    return code, json.loads(lines[0])


class Guidance(unittest.TestCase):
    """AC-T-WRITE-GUIDE-01."""

    def setUp(self):
        self.text = flat((ROOT / "AGENTS.md").read_text(encoding="utf-8"))

    def test_writes_validate_and_refresh_themselves(self):
        self.assertIn("Each validates before and after it writes and refreshes every compiled view itself, "
                      "so run neither `validate` nor `compile && graph && dashboard` after it.", self.text)
        self.assertIn("After a direct edit of a chronicle file, run `validate`, then refresh everything in "
                      "`.praukron/compiled/`", self.text)
        self.assertNotIn("Before finishing, refresh everything", self.text)

    def test_every_write_command_has_one_example(self):
        self.assertEqual(sorted(name for name, _ in agents_examples()), sorted(EXAMPLE_COMMANDS))

    def test_examples_are_one_line_and_schema_valid(self):
        for name, raw in agents_examples():
            with self.subTest(name):
                mutate.validate_payload(json.loads(raw.replace("<rev>", "0" * 16)), EXAMPLE_COMMANDS[name][1])

    def test_examples_are_accepted_as_written(self):
        for name, raw in agents_examples():
            with self.subTest(name), tempfile.TemporaryDirectory() as temp:
                root = self.chronicle(Path(temp))
                authority = root / layout.AUTHORITY_DIR
                target = {"criterion set": ("ACCEPTANCE.md", "AC-T-1"), "contract add": ("TASKS.md", "T-3"),
                          "task update": ("TASKS.md", "T-1"), "batch apply": ("ACCEPTANCE.md", "AC-T-1")}.get(name)
                if target:
                    revision = mutate.section((authority / target[0]).read_bytes(), target[1]).revision
                    # A batch reuses the one revision it read for every operation on that record.
                    self.assertEqual(raw.count("<rev>"), 2 if name == "batch apply" else 1)
                    raw = raw.replace("<rev>", revision)
                code, result = run(root, name, raw)
                self.assertEqual(code, 0, result)
                self.assertTrue(result["written"] and result["refreshed"], result)

    def chronicle(self, base: Path) -> Path:
        root = build_fixture(base).resolve()
        authority = root / layout.AUTHORITY_DIR
        for name, extra in (("MODULES.md", EXTRA_MODULE), ("TASKS.md", EXTRA_TASKS),
                            ("ACCEPTANCE.md", EXTRA_ACCEPTANCE)):
            path = authority / name
            path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
        return root

    def test_a_write_naming_its_record_may_skip_the_index(self):
        self.assertIn("A write whose request names its record may skip this step and go straight to "
                      "`.praukron/praukron context <task>` for the task the record belongs to.", self.text)
        steps = [self.text.index(step) for step in (
            "1. Read `.praukron/chronicle/INDEX.md`.", "2. Identify your task", "3. Read only those records.",
            "4. Do not load the whole chronicle by default.", "5. Explore the code only after")]
        self.assertEqual(steps, sorted(steps))
        self.assertIn("expand progressively: index, then the task packet, then `retrieve`", self.text)

    def test_agents_md_stays_within_its_byte_budget(self):
        size = len((ROOT / "AGENTS.md").read_bytes().replace(b"\r\n", b"\n"))
        self.assertLessEqual(size - AGENTS_BASE_BYTES, AGENTS_BUDGET + ACR_BYTES + BATCH_BYTES)

    def test_every_installed_copy_comes_from_the_one_source(self):
        # The installer writes the Praukron block from the root AGENTS.md; no other copy exists to drift.
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('install_block AGENTS.md "$source_dir/AGENTS.md"', installer)
        copies = [p for p in ROOT.rglob("AGENTS.md") if ".git" not in p.parts and p != ROOT / "AGENTS.md"]
        self.assertEqual(copies, [])


class Refusals(FixtureCase):
    """AC-T-WRITE-REFUSAL-01-01."""

    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()
        self.authority = self.root / layout.AUTHORITY_DIR

    def snapshot(self):
        return {p: p.read_bytes() for p in self.authority.rglob("*") if p.is_file()}

    def refuse(self, command: str, payload: object, code: str) -> dict:
        before = self.snapshot()
        status, result = run(self.root, command, payload)
        self.assertEqual(status, 1, result)
        self.assertEqual(result["error"]["code"], code, result)
        self.assertFalse(result["written"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / layout.RECOVERY_DIR).exists())
        return result["error"]

    def assert_accepted_shape(self, command: str, example: dict) -> None:
        """The example passes every payload check; only the fixture's records may still refuse it."""
        mutate.validate_payload(example, COMMANDS[command][1])
        _, result = run(self.root, command, {**example})
        if not result["ok"]:
            self.assertNotIn(result["error"]["code"], mutate.SHAPE_CODES | {"invalid-value", "invalid-id"}, result)

    def test_unknown_and_missing_fields_carry_an_accepted_payload(self):
        for command, (operation, schema) in COMMANDS.items():
            example = mutate.example(*mutate.SCHEMAS[operation])
            missing = schema["required"][0]
            for payload, code, field in (({**example, "surplus": 1}, "unknown-field", "surplus"),
                                         ({k: v for k, v in example.items() if k != missing}, "missing-field", missing)):
                with self.subTest(command=command, code=code):
                    error = self.refuse(command, payload, code)
                    self.assertEqual(error["field"], field)
                    self.assertIn(field, error["message"])
                    self.assertEqual(error["example"], example)
                    self.assert_accepted_shape(command, error["example"])

    def test_wrong_type_and_invalid_json_carry_an_accepted_payload(self):
        for command in COMMANDS:
            for raw, code in (("[]", "wrong-type"), ("{", "invalid-json")):
                with self.subTest(command=command, code=code):
                    self.assertIn("example", self.refuse(command, raw, code))

    def test_a_field_beside_set_is_named_and_shown_inside_it(self):
        payload = {"id": "T-TWO", "expectRevision": "0123456789abcdef", "status": "WIP"}
        error = self.refuse("task update", payload, "misplaced-field")
        self.assertEqual(error["field"], "status")
        self.assertIn("status belongs inside set", error["message"])
        self.assertEqual(error["expected"], "set.status")
        self.assertIn("status", error["example"]["set"])
        self.assert_accepted_shape("task update", error["example"])

    def test_criteria_beside_contract_is_named_and_shown_inside_it(self):
        payload = {**copy.deepcopy(task.EXAMPLE), "criteria": []}
        error = self.refuse("task add", payload, "misplaced-field")
        self.assertIn("criteria belongs inside contract", error["message"])
        self.assertIn("criteria", error["example"]["contract"])

    def test_a_nested_example_keeps_the_misplaced_field(self):
        example = mutate.example(task_update.SCHEMA, ("set.claimed",))
        self.assertIn("claimed", example["set"])
        mutate.validate_payload(example, task_update.SCHEMA)

    def test_fields_a_command_needs_beyond_required_are_filled(self):
        self.assertIn("contract", mutate.example(*mutate.SCHEMAS["task.add"]))
        self.assertTrue(mutate.example(*mutate.SCHEMAS["task.update"])["set"])
        self.assertEqual(len(mutate.example(*mutate.SCHEMAS["contract.add"])["criteria"]), 1)
        error = self.refuse("task update", {"id": "T-TWO", "expectRevision": "0123456789abcdef"}, "empty-set")
        self.assertTrue(error["example"]["set"])

    def test_record_refusals_carry_no_example(self):
        error = self.refuse("task update", {**task_update.EXAMPLE, "id": "T-MISSING"}, "record-not-found")
        self.assertNotIn("example", error)


def nested_paths(schema: dict, prefix: str = "") -> list[str]:
    """Every field defined inside a nested object: each can be sent one level too high."""
    paths = []
    for name, spec in schema["properties"].items():
        if spec.get("type") == "object" and "properties" in spec:
            paths += [f"{prefix}{name}.{child}" for child in spec["properties"]]
            paths += nested_paths(spec, f"{prefix}{name}.")
    return paths


# Records the examples name but the shared fixture lacks: a TODO task with a contract for
# task update, and a TODO task without one for contract add.
EXAMPLE_RECORDS = {
    "task.update": ("\n## T-EXAMPLE-01: Example\n- Status: TODO\n- Module: M-FOUNDATION\n- Validation: UNTESTED\n"
                    "- Dependencies: none\n- Owner: unassigned\n- AC: AC-T-TWO\n- Evidence: —\n"),
    "contract.add": ("\n## T-NEW: New work\n- Status: TODO\n- Module: M-FOUNDATION\n- Validation: UNTESTED\n"
                     "- Dependencies: none\n- Owner: unassigned\n- AC: —\n- Evidence: —\n"),
}
# Where each example's expected revision is read: (file, record, field).
EXAMPLE_REVISIONS = {
    "criterion.set": ("ACCEPTANCE.md", "AC-T-ONE", "expectRevision"),
    "task.update": ("TASKS.md", "T-EXAMPLE-01", "expectRevision"),
    "contract.add": ("TASKS.md", "T-NEW", "expectTaskRevision"),
}


class EveryRefusalExampleIsAccepted(unittest.TestCase):
    """AC-T-WRITE-REFUSAL-01-01: each example a shape refusal returns is written as sent.

    Only the revision, which no example can know, is replaced by the record's own.
    """

    def refusal_examples(self, command: str, operation: str, schema: dict) -> list[tuple[str, dict]]:
        base = mutate.example(*mutate.SCHEMAS[operation])
        payloads = [("unknown-field", {**base, "surplus": 1}), ("wrong-type", "[]"), ("invalid-json", "{")]
        payloads += [("missing-field", {k: v for k, v in base.items() if k != name}) for name in schema["required"]]
        for path in nested_paths(schema):
            field = path.rsplit(".", 1)[1]
            if field not in schema["properties"]:
                payloads.append(("misplaced-field", {**base, field: _value_at(mutate.example(schema, (path,)), path)}))
        examples = []
        with tempfile.TemporaryDirectory() as temp:
            root = build_fixture(Path(temp)).resolve()
            for code, payload in payloads:
                status, result = run(root, command, payload)
                self.assertEqual((status, result["error"]["code"]), (1, code), (command, payload, result))
                examples.append((f"{code} {payload}", result["error"]["example"]))
        return examples

    def test_each_example_is_written(self):
        for command, (operation, schema) in COMMANDS.items():
            for label, example in self.refusal_examples(command, operation, schema):
                with self.subTest(command=command, refusal=label), tempfile.TemporaryDirectory() as temp:
                    root = build_fixture(Path(temp)).resolve()
                    authority = root / layout.AUTHORITY_DIR
                    if operation in EXAMPLE_RECORDS:
                        tasks = authority / "TASKS.md"
                        tasks.write_text(tasks.read_text(encoding="utf-8") + EXAMPLE_RECORDS[operation],
                                         encoding="utf-8")
                    payload = copy.deepcopy(example)
                    if operation in EXAMPLE_REVISIONS:
                        name, record, field = EXAMPLE_REVISIONS[operation]
                        payload[field] = mutate.section((authority / name).read_bytes(), record).revision
                    status, result = run(root, command, payload)
                    self.assertEqual(status, 0, (example, result))
                    self.assertTrue(result["written"], result)

    def test_misplaced_constrained_fields_return_valid_examples(self):
        for field, value in (("owner", "worker"), ("evidence", "done"), ("claimed", "2026-10-16"),
                             ("validation", "SYNTHETIC"), ("status", "WIP")):
            with self.subTest(field), tempfile.TemporaryDirectory() as temp:
                root = build_fixture(Path(temp)).resolve()
                payload = {"id": "T-TWO", "expectRevision": "0" * 16, field: value}
                status, result = run(root, "task update", payload)
                self.assertEqual(result["error"]["code"], "misplaced-field")
                example = result["error"]["example"]
                self.assertIn(field, example["set"])
                mutate.validate_payload(example, task_update.SCHEMA)
                # Submitted, the example passes every payload check and stops only at the revision.
                _, retry = run(root, "task update", {**example, "id": "T-TWO"})
                self.assertEqual(retry["error"]["code"], "stale-revision", retry)


def _value_at(payload: dict, path: str):
    for part in path.split("."):
        payload = payload[part]
    return payload


# The 2.0.0 schemas, less the keywords this task adds; see test_schema_output_only_gains_keywords.
SCHEMA_DIGEST_2_0_0 = {
    "journal.append": "4a41ae374e0bf94c", "criterion.set": "b10b2a09e0c44c20", "task.add": "dc3e28d88a2d5ee1",
    "contract.add": "5d10d6391def0c50", "task.update": "854d45c7b84d3827",
}
ADDED = {"examples"}


def without_additions(value):
    if isinstance(value, dict):
        return {k: without_additions(v) for k, v in value.items() if k not in ADDED}
    if isinstance(value, list):
        return [without_additions(v) for v in value]
    return value


def digest(schema: dict) -> str:
    return hashlib.sha256(json.dumps(without_additions(schema), sort_keys=True).encode()).hexdigest()[:16]


class SchemaOutput(unittest.TestCase):
    """AC-T-WRITE-REFUSAL-01-03."""

    def schema(self, command: str) -> dict:
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main([*command.split(), "--schema"])
        return json.loads(out.getvalue())

    def test_schema_output_only_gains_keywords(self):
        for command, (operation, _) in COMMANDS.items():
            if operation not in SCHEMA_DIGEST_2_0_0:
                continue  # acr add is new (T-WRITE-ACR-01)
            with self.subTest(command):
                output = self.schema(command)
                self.assertEqual(set(output), {"schema", "example"})
                if operation in ("criterion.set", "task.update"):
                    # Failed-acceptance guidance is additive; all historical fields,
                    # including their descriptions, must still match the 2.0 schema.
                    self.assertEqual(output["schema"].pop("$comment"), mutate.FAILED_ACCEPTANCE_GUIDANCE)
                self.assertEqual(digest(output["schema"]), SCHEMA_DIGEST_2_0_0[operation])

    def test_printed_example_is_unchanged_and_accepted(self):
        examples = {"journal append": journal.EXAMPLE, "criterion set": criterion.EXAMPLE, "task add": task.EXAMPLE,
                    "contract add": contract.EXAMPLE, "task update": task_update.EXAMPLE, "acr add": acr.EXAMPLE}
        for command, (_, schema) in COMMANDS.items():
            with self.subTest(command):
                self.assertEqual(self.schema(command)["example"], examples[command])
                mutate.validate_payload(copy.deepcopy(examples[command]), schema)


class Hygiene(FixtureCase):
    """AC-T-WRITE-REFUSAL-01-02."""

    def setUp(self):
        super().setUp()
        self.root = self.root.resolve()

    def test_a_committed_write_removes_its_record_and_directory(self):
        # The record guards a write killed before validation (respond rolls it back), so
        # every write keeps one while it runs; once committed, nothing of it remains.
        for command, payload in (("journal append", journal.EXAMPLE), ("task add", task.EXAMPLE)):
            with self.subTest(command):
                code, result = run(self.root, command, payload)
                self.assertEqual(code, 0, result)
                self.assertFalse((self.root / layout.RECOVERY_DIR).exists())

    def test_an_existing_ignore_file_is_kept_with_unrelated_content(self):
        directory = self.root / layout.RECOVERY_DIR
        directory.mkdir(parents=True)
        (directory / "keep.txt").write_text("not ours", encoding="utf-8")
        code, result = run(self.root, "journal append", journal.EXAMPLE)
        self.assertEqual(code, 0, result)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), [".gitignore", "keep.txt"])

    def test_a_pre_existing_ignore_file_or_directory_is_never_removed(self):
        directory = self.root / layout.RECOVERY_DIR
        for content in (b"*\n", b"# kept by the team\n*\n"):
            with self.subTest(content=content):
                directory.mkdir(parents=True, exist_ok=True)
                (directory / ".gitignore").write_bytes(content)
                code, result = run(self.root, "journal append", {**journal.EXAMPLE, "title": repr(content)})
                self.assertEqual(code, 0, result)
                self.assertEqual([p.name for p in directory.iterdir()], [".gitignore"])
                self.assertEqual((directory / ".gitignore").read_bytes(), content)
        (directory / ".gitignore").unlink()
        code, result = run(self.root, "journal append", {**journal.EXAMPLE, "title": "empty directory"})
        self.assertEqual(code, 0, result)
        self.assertTrue(directory.is_dir())
        self.assertEqual(list(directory.iterdir()), [])

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_a_tracked_custom_ignore_file_stays_tracked_and_unchanged(self):
        directory = self.root / layout.RECOVERY_DIR
        directory.mkdir(parents=True)
        (directory / ".gitignore").write_bytes(b"# recovery records are local\n*\n!.gitignore\n")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false"]
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
            subprocess.run([*git, *args], cwd=self.root, check=True)
        code, result = run(self.root, "journal append", journal.EXAMPLE)
        self.assertEqual(code, 0, result)
        status = subprocess.run([*git, "status", "--porcelain", "--untracked-files=all"], cwd=self.root,
                                capture_output=True, text=True, check=True).stdout.splitlines()
        for line in status:
            self.assertRegex(line[3:], r"^\.praukron/(chronicle|compiled)/", status)

    def test_an_interrupted_write_cleans_up_only_what_it_created(self):
        task_file = self.root / layout.AUTHORITY_DIR / "TASKS.md"
        record = mutate._write_recovery(self.root, {task_file: task_file.read_bytes()}, prefix="write")
        self.assertTrue(mutate.recover_write(self.root))
        self.assertFalse(record.parent.exists())

    def test_a_refused_write_restores_its_bytes_and_leaves_no_record(self):
        path = self.root / layout.AUTHORITY_DIR / "JOURNAL.md"
        path.write_bytes(b"# Journal\r\n")
        before = path.read_bytes()
        failure = mutate.WriteError("validation-failed", "nothing was written: injected")
        with mock.patch.object(mutate, "_errors", side_effect=[set(), failure]):
            code, result = run(self.root, "journal append", journal.EXAMPLE)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"]["code"], "validation-failed")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / layout.RECOVERY_DIR).exists())

    def test_recovery_directory_kept_while_another_record_remains(self):
        task_file = self.root / layout.AUTHORITY_DIR / "TASKS.md"
        first = mutate._write_recovery(self.root, {task_file: task_file.read_bytes()}, prefix="write")
        second = mutate._write_recovery(self.root, {task_file: task_file.read_bytes()}, prefix="write")
        mutate._discard_recovery(first)
        self.assertTrue(second.is_file())
        self.assertTrue((second.parent / ".gitignore").is_file())
        mutate._discard_recovery(second)
        # The first record created the directory and was discarded first, so the
        # tool's own ignore file stays; it ignores itself, so git status never shows it.
        self.assertEqual([p.name for p in second.parent.iterdir()], [".gitignore"])
        self.assertEqual((second.parent / ".gitignore").read_bytes(), b"*\n")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_installed_launcher_leaves_only_chronicle_and_compiled_changes(self):
        home = self.root / layout.HOME_DIR
        shutil.copy2(ROOT / layout.ENTRY_POINT, home / "praukron")
        shutil.copytree(ROOT / "src" / "praukron", self.root / layout.RUNTIME_DIR / "praukron",
                        ignore=shutil.ignore_patterns("__pycache__"))
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}

        def praukron(*args, payload=None):
            return subprocess.run([sys.executable, str(home / "praukron"), *args], input=payload, cwd=self.root,
                                  env=env, capture_output=True, text=True, encoding="utf-8", check=True)

        praukron("compile")
        praukron("graph")
        praukron("dashboard")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.autocrlf=false"]
        subprocess.run([*git, "init", "-q"], cwd=self.root, check=True)
        subprocess.run([*git, "add", "-A"], cwd=self.root, check=True)
        subprocess.run([*git, "commit", "-qm", "base"], cwd=self.root, check=True)
        praukron("journal", "append", "--input", "-", payload=json.dumps(journal.EXAMPLE))
        praukron("task", "add", "--input", "-", payload=json.dumps(task.EXAMPLE))
        status = subprocess.run([*git, "status", "--porcelain", "--untracked-files=all"], cwd=self.root,
                                capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertTrue(status)
        for line in status:
            self.assertRegex(line[3:], r"^\.praukron/(chronicle|compiled)/", status)
        self.assertEqual(list((self.root / layout.RUNTIME_DIR).rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
