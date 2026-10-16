"""Create tasks and their optional contracts in one transaction (ADR-068)."""

from __future__ import annotations

import json
from pathlib import Path

from . import contract, mutate
from .layout import AUTHORITY_DIR
from .model import DOMAINS, TASK_AUTHORITIES, VALIDATIONS, Project

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "description": "T- identifier", "examples": ["T-NEW"]},
        "title": {"type": "string", "minLength": 1, "examples": ["New work"]},
        "module": {"type": "string", "examples": ["M-FOUNDATION"]},
        "domain": {"type": "string", "enum": list(DOMAINS), "examples": ["execution"]},
        "dependencies": {"type": "array", "description": "Task IDs"},
        "validation": {"type": "string", "enum": [v for v in VALIDATIONS if v != "HUMAN_VERIFIED"]},
        "governedBy": {"type": "array", "description": "ADR IDs"},
        "files": {"type": "array", "description": "File paths"},
        "symbols": {"type": "array", "description": "Symbol names"},
        "authority": {"type": "string", "enum": list(TASK_AUTHORITIES)},
        "contract": contract.DEFINITION_SCHEMA,
        "ac": {"type": "string"},
    },
    "required": ["id", "title", "module", "domain", "dependencies"],
}
EXAMPLE = {
    "id": "T-NEW", "title": "New work", "module": "M-FOUNDATION",
    "domain": "execution", "dependencies": [],
    "contract": {k: v for k, v in contract.EXAMPLE.items() if k in contract.DEFINITION_SCHEMA["properties"]},
}


SCHEMA["description"] = "\n".join(mutate.GUIDANCE["task.add"]) + "\nPAYLOAD: give exactly one of contract (a new definition) and ac (an existing contract ID)"
mutate.SCHEMAS["task.add"] = (SCHEMA, ("contract",))


def add(root: Path, payload: dict, *, dry_run: bool = False) -> dict:
    root = root.resolve()
    operation = "task.add"
    try:
        mutate.validate_payload(payload, SCHEMA)
        contract.identifier(payload["id"], "T-", "id")
        contract.single_line(payload["title"], "title")
        contract.identifier(payload["module"], "M-", "module")
        for field, prefix in (("dependencies", "T-"), ("governedBy", "ADR-"), ("files", None), ("symbols", None)):
            contract.string_list(payload.get(field, []), field, prefix)
        if ("contract" in payload) == ("ac" in payload):
            raise mutate.WriteError("invalid-contract", "give exactly one of contract and ac", "contract")
        if "contract" in payload:
            contract.validate_definition(payload["contract"])
        else:
            contract.identifier(payload["ac"], "AC-", "ac")

        def prepare(project: Project) -> mutate.Plan:
            if mutate.id_exists(project, payload["id"]):
                raise mutate.WriteError("duplicate-id", f"id already exists: {payload['id']}", "id")
            if payload["module"] not in {m.id for m in project.modules}:
                raise mutate.WriteError("unknown-module", f"no module named {payload['module']}", "module")
            for dependency in payload["dependencies"]:
                if dependency not in {t.id for t in project.tasks}:
                    raise mutate.WriteError("unknown-dependency", f"no task named {dependency}", "dependencies")
            for decision in payload.get("governedBy", []):
                if decision not in {d.id for d in project.decisions}:
                    raise mutate.WriteError("unknown-decision", f"no ADR named {decision}", "governedBy")
            changes = {}
            authority = root / AUTHORITY_DIR
            if "contract" in payload:
                definition = payload["contract"]
                contract.check_new(project, definition)
                ac = definition["id"]
                path = authority / "ACCEPTANCE.md"
                changes[path] = contract.append_section(path, "Acceptance", contract.render(definition))
            else:
                ac = payload["ac"]
                if ac not in project.contracts:
                    raise mutate.WriteError("unknown-contract", f"no contract named {ac}", "ac")
                mutate.ensure_mutable(project.contracts[ac].source)
            lines = [f"## {payload['id']}: {payload['title']}", "- Status: TODO",
                     f"- Module: {payload['module']}", f"- Domain: {payload['domain']}"]
            if "authority" in payload:
                lines.append(f"- Authority: {payload['authority']}")
            lines += [f"- Validation: {payload.get('validation', 'UNTESTED')}",
                     "- Dependencies: " + (", ".join(payload["dependencies"]) or "none"),
                     "- Owner: unassigned", f"- AC: {ac}", "- Evidence: —",
                     "- Governed by: " + (", ".join(payload.get("governedBy", [])) or "none")]
            for field, key in (("Files", "files"), ("Symbols", "symbols")):
                if key in payload:
                    value = ", ".join(payload[key]) or "none"
                    lines.append(f"- {field}: {value}")
            path = authority / "TASKS.md"
            changes = {path: contract.append_section(path, "Tasks", "\n".join(lines) + "\n"), **changes}
            return mutate.Plan(changes, payload["id"], f"TASKS.md#{payload['id']}")

        return mutate.transaction(root, operation, prepare, dry_run=dry_run)
    except mutate.WriteError as error:
        return mutate.refusal(operation, error)


def command(root: Path, args) -> int:
    if args.schema:
        result = {"schema": SCHEMA, "example": EXAMPLE}
    else:
        try:
            result = add(root, mutate.load_payload(args.input, SCHEMA), dry_run=args.dry_run)
        except mutate.WriteError as error:
            result = mutate.refusal("task.add", error)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result.get("ok", args.schema) else 1
