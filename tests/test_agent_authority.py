"""Agent authority and delegated verification (T-AGENT-AUTHORITY-01, T-VERIFY-DELEGATE-01).

One setting lists what agents may do on the owner's authority. A grant of
`verify` lets a named agent verify an `Authority: owner` task it did not build;
the record says so, and the task becomes DELEGATE_VERIFIED, never
HUMAN_VERIFIED.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import (  # noqa: E402
    analytics, authority, compile as compiler, dashboard, i18n, retrieve, validate,
)
from test_i18n import island, uncovered  # noqa: E402
from test_owner_verify import OWNERS_ADR, OwnerCase, owner_task  # noqa: E402
from test_serve import ServeCase  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AGENT = "reviewer-agent"


class AuthorityCase(OwnerCase):
    """The owner `thien-owner` (Owners in ADR-003) and owner task T-TWO in M-FOUNDATION (P1)."""

    def grant(self, *args: str, tty: bool = True, confirm: str = "grant") -> tuple[int, str, str]:
        return self.run_cli("authority", "grant", *args, tty=tty, answers=(confirm,))

    def grant_verify(self, scope: str = "T-TWO", *extra: str) -> str:
        code, out, err = self.grant("verify", "--scope", scope, "--delegate", AGENT, *extra)
        self.assertEqual(code, 0, err)
        self.load()
        return self.project.grants[-1].id

    def revoke(self, grant_id: str, tty: bool = True) -> tuple[int, str, str]:
        return self.run_cli("authority", "revoke", grant_id, tty=tty, answers=(grant_id,))

    def delegate_verify(self, delegate: str = AGENT, task: str = "T-TWO",
                        text: str = "Ran the export end to end.") -> tuple[int, str, str]:
        return self.run_cli("respond", task, "VERIFY", "--delegate", delegate, stdin=text)

    def file(self, name: str) -> Path:
        return self.authority / name

    def set_task_field(self, label: str, value: str, task: str = "T-TWO") -> None:
        path = self.file("TASKS.md")
        text = path.read_text(encoding="utf-8")
        start = text.index(f"## {task}:")
        end = text.find("\n## ", start + 1)
        end = len(text) if end < 0 else end
        section = text[start:end]
        lines = [line for line in section.split("\n") if not line.startswith(f"- {label}:")]
        lines.insert(2, f"- {label}: {value}")
        path.write_text(text[:start] + "\n".join(lines) + text[end:], encoding="utf-8")
        self.load()

    def listing(self) -> dict:
        code, out, err = self.run_cli("authority", "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)


class TestTheSetting(AuthorityCase):
    """T-AGENT-AUTHORITY-01."""

    def test_every_capability_is_listed_with_state_who_and_when(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-01."""
        names = [c["name"] for c in self.listing()["capabilities"]]
        self.assertEqual(names, [c.name for c in authority.CAPABILITIES])
        self.assertEqual(names, ["verify", "assumptions"])
        verify = self.listing()["capabilities"][0]
        self.assertEqual((verify["state"], verify["grants"]), ("off", []))
        grant_id = self.grant_verify()
        data = self.listing()
        verify = data["capabilities"][0]
        self.assertEqual(verify["state"], "on")
        entry = verify["grants"][0]
        self.assertEqual((entry["id"], entry["grantedBy"], entry["date"], entry["via"], entry["state"]),
                         (grant_id, "thien-owner", authority.today(), "cli", "in force"))
        code, text, _ = self.run_cli("authority")
        self.assertEqual(code, 0)
        self.assertIn("verify  ON", text)
        self.assertIn(f"{grant_id} IN FORCE · {AGENT} · scope T-TWO", text)
        self.assertIn(f"granted by thien-owner on {authority.today()} via cli", text)
        self.assertIn("assumptions  POLICY", text)
        # The dashboard lists the same, in one place of its own.
        page = dashboard.render(self.project, analytics.report(self.project), compiler.as_json(self.project))
        section = page[page.index('id="agent-authority"'):]
        section = section[:section.index("</section>")]
        for expected in ('data-capability="', "verify", "assumptions", grant_id, "thien-owner",
                         authority.today()):
            self.assertIn(expected, i18n.unmark(section))
        self.assertEqual(section.count("data-capability="), len(authority.CAPABILITIES))

    def test_a_switch_is_a_record_under_the_login_and_history_stays(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-02."""
        grant_id = self.grant_verify()
        written = self.file("AUTHORITY.md").read_text(encoding="utf-8")
        record = written[written.index(f"## {grant_id}"):]
        self.assertIn("- Granted by: thien-owner\n", record)
        code, _, err = self.revoke(grant_id)
        self.assertEqual(code, 0, err)
        after = self.file("AUTHORITY.md").read_text(encoding="utf-8")
        # The grant as written stays readable; the revocation is added to it.
        self.assertTrue(after.startswith(written.rstrip("\n")), after)
        self.assertIn(f"- Revoked: {authority.today()}\n- Revoked by: thien-owner\n- Revoked via: cli\n", after)
        self.load()
        self.assertEqual(self.listing()["capabilities"][0]["grants"][0]["state"], "revoked")
        self.assertEqual(self.listing()["capabilities"][0]["state"], "off")
        self.assertEqual(self.errors(), [])
        # An agent cannot make or widen one: no terminal, no write.
        for attempt in (("authority", "grant", "verify", "--scope", "project", "--delegate", AGENT),
                        ("authority", "revoke", grant_id)):
            code, _, err = self.run_cli(*attempt, tty=False)
            self.assertEqual(code, 1)
            self.assertIn("only by the owner in person", err)
        self.assertEqual(self.file("AUTHORITY.md").read_text(encoding="utf-8"), after)
        # No structured write reaches AUTHORITY.md.
        from praukron import mutate

        self.assertFalse([name for name in mutate.SCHEMAS if "authority" in name or "grant" in name])
        # A login not named in Owners is refused.
        with mock.patch("praukron.respond.identity", return_value="mallory"):
            code, _, err = self.grant("verify", "--scope", "T-TWO", "--delegate", AGENT)
        self.assertEqual(code, 1)
        self.assertIn("not named in an ACCEPTED ADR's Owners", err)
        self.assertEqual(self.file("AUTHORITY.md").read_text(encoding="utf-8"), after)

    def test_an_off_capability_refuses_with_its_name_and_how_to_switch_it_on(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-03."""
        before = self.file("RESPONSES.md").read_bytes()
        code, _, err = self.delegate_verify()
        self.assertEqual(code, 1)
        self.assertIn("capability verify is off for T-TWO", err)
        self.assertIn("praukron authority grant verify --scope T-TWO --delegate", err)
        self.assertIn("praukron dashboard --serve", err)
        self.assertEqual(self.file("RESPONSES.md").read_bytes(), before)

    def test_an_act_under_a_capability_names_agent_owner_and_record(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-04."""
        grant_id = self.grant_verify()
        code, _, err = self.delegate_verify()
        self.assertEqual(code, 0, err)
        self.load()
        response = self.project.responses[-1]
        self.assertEqual((response.action, response.delegate, response.on_authority_of, response.grant),
                         ("VERIFY", AGENT, "thien-owner", grant_id))
        text = self.file("RESPONSES.md").read_text(encoding="utf-8")
        self.assertIn(f"- Via: delegate (cli)\n- Delegate: {AGENT}\n- On authority of: thien-owner\n"
                      f"- Grant: {grant_id}\n", text)

    def test_the_assumption_policy_appears_unchanged_and_old_chronicles_validate(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-05."""
        self.assertFalse(self.file("AUTHORITY.md").exists())
        self.assertEqual(self.errors(), [])
        policy = self.project.policy
        entry = next(c for c in self.listing()["capabilities"] if c["name"] == "assumptions")
        self.assertEqual((entry["reserved"], entry["notify"], entry["decision"]),
                         (policy.reserved, policy.notify, policy.decision))
        self.assertEqual(entry["state"], "policy")
        self.assertEqual(packet_policy := analytics.context(self.project, "T-TWO")["policy"], policy.as_json())
        self.file("AUTHORITY.md").write_text(
            (ROOT / "templates" / "chronicle" / "AUTHORITY.md").read_text(encoding="utf-8"), encoding="utf-8")
        self.load()
        self.assertEqual(self.errors(), [])
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["policy"], packet_policy)
        policy_entry = next(c for c in packet["capabilities"] if c["capability"] == "assumptions")
        self.assertEqual((policy_entry["reserved"], policy_entry["notify"]), (policy.reserved, policy.notify))
        self.assertEqual(packet["policy"], policy.as_json())
        # A grant does not touch the policy, which still lives in its ADR.
        adr = (self.authority / "ADR" / "ADR-002.md").read_bytes()
        self.grant_verify()
        self.assertEqual((self.authority / "ADR" / "ADR-002.md").read_bytes(), adr)
        self.assertEqual(self.project.policy, policy)
        self.assertEqual(self.errors(), [])

    def test_a_task_packet_states_which_capabilities_are_on(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-06."""
        # Without AUTHORITY.md nothing is granted and the packet is as before.
        self.assertNotIn("capabilities", analytics.context(self.project, "T-TWO"))
        self.file("AUTHORITY.md").write_text(
            (ROOT / "templates" / "chronicle" / "AUTHORITY.md").read_text(encoding="utf-8"), encoding="utf-8")
        self.load()

        def verify_entry(task: str) -> dict:
            packet = analytics.context(self.project, task)
            return next(c for c in packet["capabilities"] if c["capability"] == "verify")

        off = verify_entry("T-TWO")
        self.assertEqual(off["state"], "off")
        self.assertIn("praukron authority grant verify", off["switchOn"])
        grant_id = self.grant_verify("M-FOUNDATION")
        on = verify_entry("T-TWO")
        self.assertEqual(on["state"], "on")
        self.assertEqual([(g["id"], g["delegate"], g["grantedBy"]) for g in on["grants"]],
                         [(grant_id, AGENT, "thien-owner")])
        self.assertEqual(verify_entry("T-ONE")["state"], "on")  # same module
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.dir, "T-TWO")
        granted = [item for item in pack.items if item.kind == "GRANT"]
        self.assertEqual([item.id for item in granted], [grant_id])
        self.assertIn("capability verify is on for T-TWO", granted[0].reason)
        named = retrieve.retrieve(self.project, analytics.report(self.project), self.dir, grant_id)
        self.assertEqual([item.id for item in named.items], [grant_id])

    def test_a_grant_states_one_capability_and_a_scope_and_may_carry_an_end(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-08 (as amended: whole project allowed, end optional)."""
        for bad in ((["", AGENT, None], "states its scope"), (["  ,  ", AGENT, None], "states its scope"),
                    (["project, T-TWO", AGENT, None], "not both"), (["T-NOPE", AGENT, None], "T-NOPE"),
                    (["T-TWO", "", None], "names its delegate"),
                    (["T-TWO", AGENT, "2001-01-01"], "not before today")):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(authority.AuthorityError, bad[1]):
                    authority.grant(self.dir, "verify", *bad[0])
        with self.assertRaisesRegex(authority.AuthorityError, "not a capability that can be granted"):
            authority.grant(self.dir, "assumptions", "T-TWO", AGENT)
        with self.assertRaisesRegex(authority.AuthorityError, "not a capability"):
            authority.grant(self.dir, "deploy", "T-TWO", AGENT)
        self.assertFalse(self.file("AUTHORITY.md").exists())
        with self.assertRaises((SystemExit, argparse.ArgumentError)):  # --scope is required; nothing is assumed
            self.run_cli("authority", "grant", "verify", "--delegate", AGENT, tty=True)
        # Whole project, no end: allowed, written out, and made to stand out.
        code, out, err = self.grant("verify", "--scope", "project", "--delegate", AGENT)
        self.assertEqual(code, 0, err)
        self.assertIn("WHOLE PROJECT", out)
        self.assertIn("NO END", out)
        self.load()
        grant = self.project.grants[-1]
        text = self.file("AUTHORITY.md").read_text(encoding="utf-8")
        self.assertIn("- Capability: verify\n- Scope: project\n", text)
        self.assertIn("- Until: none\n", text)
        self.assertTrue(grant.whole_project and grant.until is None)
        listed = self.listing()["capabilities"][0]["grants"][0]
        self.assertTrue(listed["wholeProject"] and listed["noEnd"])
        code, plain, _ = self.run_cli("authority")
        self.assertIn("scope WHOLE PROJECT · NO END (until revoked)", plain)
        page = i18n.unmark(dashboard.render(self.project, analytics.report(self.project),
                                            compiler.as_json(self.project)))
        self.assertIn('<strong class="stand-out">Whole project</strong>', page)
        self.assertIn('<strong class="stand-out">No end: until revoked</strong>', page)
        # With an end date.
        self.grant_verify("T-TWO", "--until", "2999-12-31")
        self.assertEqual(self.project.grants[-1].until, "2999-12-31")
        self.assertEqual(self.errors(), [])
        # Hand-written records: a missing scope is an error; project and no end are not.
        self.file("AUTHORITY.md").write_text(text + (
            "\n## AG-09: verify for nobody\n- Capability: verify\n- Delegate: x\n- Granted by: thien-owner\n"
            "- Date: 2026-01-01\n- Via: cli\n"), encoding="utf-8")
        self.load()
        self.assertEqual(self.errors(), ["grant-without-scope"])
        self.file("AUTHORITY.md").write_text(text.replace("- Capability: verify", "- Capability: deploy"),
                                             encoding="utf-8")
        self.load()
        self.assertEqual(self.errors(), ["unknown-capability"])

    def test_grants_and_revocations_are_refused_anywhere_but_in_person(self) -> None:
        """AC-T-AGENT-AUTHORITY-01-09."""
        code, _, err = self.grant("verify", "--scope", "T-TWO", "--delegate", AGENT, tty=False)
        self.assertEqual(code, 1)
        self.assertIn("recorded only by the owner in person", err)
        self.assertFalse(self.file("AUTHORITY.md").exists())
        for via in ("relayed by agent from chat", "batch", "agent"):
            with self.subTest(via=via):
                with self.assertRaisesRegex(authority.AuthorityError, "only in person"):
                    authority.grant(self.dir, "verify", "T-TWO", AGENT, via=via)
        grant_id = self.grant_verify()
        with self.assertRaisesRegex(authority.AuthorityError, "only in person"):
            authority.revoke(self.dir, grant_id, via="relayed by agent")
        code, _, err = self.revoke(grant_id, tty=False)
        self.assertEqual(code, 1)
        self.assertIn("recorded only by the owner in person", err)
        # Not confirmed at the terminal: nothing is recorded.
        before = self.file("AUTHORITY.md").read_bytes()
        code, _, err = self.grant("verify", "--scope", "T-TWO", "--delegate", AGENT, confirm="yes")
        self.assertEqual(code, 1)
        self.assertEqual(self.file("AUTHORITY.md").read_bytes(), before)
        # A hand-written relayed grant fails validation.
        self.file("AUTHORITY.md").write_text(
            before.decode().replace("- Via: cli", "- Via: relayed by agent"), encoding="utf-8")
        self.load()
        self.assertIn("grant-not-in-person", self.errors())
        spec = " ".join((ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8").split())
        self.assertIn("does not stop a program that imitates a terminal", spec)


class TestDelegatedVerify(AuthorityCase):
    """T-VERIFY-DELEGATE-01."""

    def test_verify_without_a_delegation_is_refused_as_today(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-01."""
        before = self.file("RESPONSES.md").read_bytes()
        code, _, err = self.run_cli("respond", "T-TWO", "VERIFY", tty=False)
        self.assertEqual(code, 1)
        self.assertIn("VERIFY is recorded only interactively", err)
        code, _, err = self.delegate_verify()
        self.assertEqual(code, 1)
        self.assertIn("capability verify is off", err)
        # A grant for another task or another agent does not cover it.
        self.grant_verify("T-THREE")
        code, _, err = self.delegate_verify()
        self.assertEqual(code, 1)
        self.grant_verify("T-TWO")
        code, _, err = self.delegate_verify("someone-else")
        self.assertEqual(code, 1)
        self.assertIn("capability verify is off for T-TWO and someone-else", err)
        # Not an owner task: VERIFY never applies.
        code, _, err = self.delegate_verify(task="T-THREE")
        self.assertEqual(code, 1)
        self.assertIn("only to an Authority: owner task", err)
        self.assertEqual(self.file("RESPONSES.md").read_bytes(), before)

    def test_a_covered_delegate_verifies_and_the_record_says_who_whose_which_how(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-02."""
        grant_id = self.grant_verify("P1")
        code, out, err = self.delegate_verify()
        self.assertEqual(code, 0, err)
        self.assertIn("DELEGATE_VERIFIED", out)
        self.load()
        response = self.project.responses[-1]
        self.assertEqual((response.target, response.action, response.by, response.delegate,
                          response.on_authority_of, response.grant, response.via, response.date),
                         ("T-TWO", "VERIFY", "thien-owner", AGENT, "thien-owner", grant_id,
                          "delegate (cli)", authority.today()))
        self.assertEqual(response.text, "Ran the export end to end.")
        self.assertEqual(self.errors(), [])
        # A delegated VERIFY is dated today: no backdating into a grant's term.
        code, _, err = self.run_cli("respond", "T-TWO", "VERIFY", "--delegate", AGENT, "--date",
                                    "2026-01-01", stdin="x")
        self.assertEqual(code, 1)
        self.assertIn("dated today", err)

    def test_only_an_owner_records_a_delegation_with_delegate_scope_and_end(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-03."""
        with mock.patch("praukron.respond.identity", return_value="someone-else"):
            code, _, err = self.grant("verify", "--scope", "T-TWO", "--delegate", AGENT)
        self.assertEqual(code, 1)
        self.assertIn("only an owner grants", err)
        self.assertFalse(self.file("AUTHORITY.md").exists())
        grant_id = self.grant_verify("T-TWO", "--until", "2999-01-31")
        grant = self.project.grant(grant_id)
        self.assertEqual((grant.capability, grant.delegate, grant.scope, grant.until, grant.granted_by),
                         ("verify", AGENT, ["T-TWO"], "2999-01-31", "thien-owner"))
        # A record naming a granter outside Owners fails validation.
        text = self.file("AUTHORITY.md").read_text(encoding="utf-8")
        self.file("AUTHORITY.md").write_text(text.replace("- Granted by: thien-owner", "- Granted by: mallory"),
                                             encoding="utf-8")
        self.load()
        self.assertIn("grant-not-from-owner", self.errors())
        # Such a record never switches the capability on.
        code, _, err = self.delegate_verify()
        self.assertEqual(code, 1)
        self.assertIn("capability verify is off", err)
        # Agents have no path to widen it: non-interactive grant is refused.
        self.file("AUTHORITY.md").write_text(text, encoding="utf-8")
        code, _, _ = self.grant("verify", "--scope", "project", "--delegate", AGENT, tty=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.file("AUTHORITY.md").read_text(encoding="utf-8"), text)

    def test_a_delegated_verification_is_never_shown_as_the_owners(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-04."""
        grant_id = self.grant_verify()
        self.assertEqual(self.delegate_verify()[0], 0)
        self.set_task_field("Validation", "DELEGATE_VERIFIED")
        compiled = compiler.as_json(self.project)
        delegated = [r for r in compiled["responses"] if r.get("delegate")]
        self.assertEqual(len(delegated), 1)
        self.assertFalse(delegated[0]["fromOwner"])
        self.assertEqual(self.project.verified_by_owner("T-TWO"), [])
        # explain
        detail = analytics.explain(self.project, "T-TWO")
        self.assertEqual(detail["validation"], "DELEGATE_VERIFIED")
        self.assertEqual([(v["kind"], v["delegate"], v["onAuthorityOf"], v["grant"]) for v in detail["verifications"]],
                         [("delegate", AGENT, "thien-owner", grant_id)])
        code, text, _ = self.run_cli("explain", "T-TWO")
        self.assertIn(f"verified by delegate {AGENT} on the authority of thien-owner, under {grant_id}; "
                      "not the owner in person", text)
        self.assertNotIn("HUMAN_VERIFIED", text)
        # context
        packet = analytics.context(self.project, "T-TWO")
        self.assertEqual(packet["task"]["validation"], "DELEGATE_VERIFIED")
        self.assertEqual(packet["task"]["verifications"][0]["kind"], "delegate")
        # retrieve
        pack = retrieve.retrieve(self.project, analytics.report(self.project), self.dir, "T-TWO")
        verified = [i for i in pack.items if i.kind == "RESPONSE"]
        self.assertEqual(len(verified), 1)
        self.assertIn("DELEGATE_VERIFIED, not the owner in person", verified[0].reason)
        self.assertIn(f"- Delegate: {AGENT}", verified[0].text)
        # dashboard: the owner's card stays open and says who verified
        page = i18n.unmark(dashboard.render(self.project, analytics.report(self.project), compiled))
        self.assertIn(f"Verified by delegate {AGENT} on the authority of thien-owner", page)
        self.assertIn("DELEGATE_VERIFIED, not verified by you in person.", page)
        # Counted apart in status and metrics, never as HUMAN_VERIFIED.
        metrics = analytics.report(self.project).metrics()
        self.assertEqual(metrics["validationBreakdown"].get("DELEGATE_VERIFIED"), 1)
        self.assertNotIn("HUMAN_VERIFIED", metrics["validationBreakdown"])
        code, status, _ = self.run_cli("status")
        self.assertIn("· DELEGATE_VERIFIED 1", status)
        # Every interface string on the page with grants is in the catalogue.
        for interactive in (False, True):
            data = island(dashboard.render(self.project, analytics.report(self.project), compiled,
                                           interactive=interactive))
            sources = data['texts'] + [v for attrs in data['attributes'] for v in attrs.values()]
            self.assertEqual({uncovered(s).strip() for s in sources if uncovered(s)}, set())

    def test_revoked_or_ended_refuses_and_earlier_records_stay(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-05."""
        grant_id = self.grant_verify()
        self.assertEqual(self.delegate_verify()[0], 0)
        self.assertEqual(self.revoke(grant_id)[0], 0)
        self.load()
        kept = self.file("RESPONSES.md").read_bytes()
        code, _, err = self.delegate_verify(text="Again.")
        self.assertEqual(code, 1)
        self.assertIn(f"capability verify is off for T-TWO and {AGENT} ({grant_id} is revoked)", err)
        self.assertEqual(self.file("RESPONSES.md").read_bytes(), kept)
        self.assertEqual(self.errors(), [])  # the verification made in force stays valid
        # An ended grant: its Until has passed.
        text = self.file("AUTHORITY.md").read_text(encoding="utf-8")
        self.file("AUTHORITY.md").write_text(text + (
            "\n## AG-02: verify, ended\n- Capability: verify\n- Scope: T-TWO\n"
            f"- Delegate: {AGENT}\n- Granted by: thien-owner\n- Date: 2020-01-01\n"
            "- Until: 2020-01-31\n- Via: cli\n"), encoding="utf-8")
        self.load()
        code, _, err = self.delegate_verify(text="Later.")
        self.assertEqual(code, 1)
        self.assertIn("(AG-02 is ended)", err)
        self.assertEqual(self.errors(), [])

    def test_validate_reports_a_verification_without_a_grant_that_held(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-06."""
        grant_id = self.grant_verify()
        self.assertEqual(self.delegate_verify()[0], 0)
        self.load()
        self.assertEqual(self.errors(), [])
        good = self.file("RESPONSES.md").read_text(encoding="utf-8")
        grants = self.file("AUTHORITY.md").read_text(encoding="utf-8")
        cases = (
            (good.replace(f"- Grant: {grant_id}", "- Grant: AG-99"), grants, "unknown-delegation"),
            (good, grants.replace("- Scope: T-TWO", "- Scope: T-THREE"), "delegation-does-not-cover"),
            (good, grants.replace(f"- Date: {authority.today()}", "- Date: 2999-01-01"), "delegation-not-in-force"),
            (good, grants.replace("- Until: none", "- Until: 2000-01-01").replace(
                f"- Date: {authority.today()}", "- Date: 1999-01-01"), "delegation-not-in-force"),
            (good, grants + f"- Revoked: 2000-01-01\n- Revoked by: thien-owner\n- Revoked via: cli\n",
             "delegation-not-in-force"),
            (good.replace(f"- Delegate: {AGENT}", "- Delegate: other-agent"), grants, "delegation-wrong-delegate"),
        )
        for responses, authority_text, code in cases:
            with self.subTest(code=code):
                self.file("RESPONSES.md").write_text(responses, encoding="utf-8")
                self.file("AUTHORITY.md").write_text(authority_text, encoding="utf-8")
                self.load()
                self.assertIn(code, self.errors())

    def test_documentation_and_guidance_say_when_how_and_what(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-07 (INSPECTION; these phrases keep it from drifting)."""
        def flat(path: str) -> str:
            return " ".join((ROOT / path).read_text(encoding="utf-8").split())

        agents = flat("AGENTS.md")
        self.assertIn("Never issue `VERIFY` unless your packet's `capabilities` grant `verify` for a task "
                      "you did not build", agents)
        self.assertIn("--delegate <you>", agents)
        spec = flat("docs/SPEC.md")
        for phrase in ("praukron authority grant verify", "praukron authority revoke",
                       "`Delegate:`", "`On authority of:`", "`Grant:`", "DELEGATE_VERIFIED",
                       "never the one who built"):
            self.assertIn(phrase, spec)
        for name in ("README.md", "RESPONSES.md", "AUTHORITY.md"):
            self.assertIn("--delegate", flat(f"templates/chronicle/{name}"))

    def test_the_builder_never_verifies(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-08."""
        self.grant_verify("project")
        for owner in (AGENT, "  Reviewer-Agent ", "reviewer-agent (Claude, builder)"):
            with self.subTest(owner=owner):
                self.set_task_field("Owner", owner)
                code, _, err = self.delegate_verify()
                self.assertEqual(code, 1)
                self.assertIn("the one who verifies is never the one who built", err)
        # Validate catches a verification whose delegate is recorded as the builder.
        self.set_task_field("Owner", "builder-agent")
        self.assertEqual(self.delegate_verify()[0], 0)
        self.set_task_field("Owner", AGENT)
        self.assertIn("delegate-built-task", self.errors())

    def test_delegate_verified_is_its_own_state_and_gives_way_to_the_owner(self) -> None:
        """AC-T-VERIFY-DELEGATE-01-09."""
        self.grant_verify()
        self.assertEqual(self.delegate_verify()[0], 0)
        # HUMAN_VERIFIED is refused on a delegate's VERIFY alone.
        self.set_task_field("Validation", "HUMAN_VERIFIED")
        self.assertIn("verification-without-owner", self.errors())
        # DELEGATE_VERIFIED serves where owner verification is required: closing the task.
        self.set_task_field("Validation", "DELEGATE_VERIFIED")
        self.set_task_field("Status", "DONE")
        codes = self.errors()
        self.assertNotIn("owner-task-not-verified", codes)
        self.assertNotIn("delegate-verification-without-response", codes)
        # Not counted as HUMAN_VERIFIED.
        metrics = analytics.report(self.project).metrics()
        self.assertEqual(metrics["validationCounts"], ["AI_REVIEWED", "HUMAN_VERIFIED"])
        # Without a delegated VERIFY, DELEGATE_VERIFIED is an error.
        self.set_task_field("Validation", "DELEGATE_VERIFIED", task="T-ONE")
        self.assertIn("delegate-verification-without-response", self.errors())
        self.set_task_field("Validation", "SYNTHETIC", task="T-ONE")
        # No command sets it.
        from praukron import task as task_add, task_update

        self.assertNotIn("DELEGATE_VERIFIED", json.dumps(task_update.SCHEMA))
        self.assertNotIn("DELEGATE_VERIFIED", json.dumps(task_add.SCHEMA))
        # The owner verifies in person: DELEGATE_VERIFIED gives way to HUMAN_VERIFIED.
        code, _, err = self.run_cli("respond", "T-TWO", "VERIFY", tty=True, answers=("Checked it myself.", "T-TWO"))
        self.assertEqual(code, 0, err)
        self.load()
        self.assertIn("delegate-verification-superseded",
                      [f.code for f in validate.check(self.project) if f.severity == "warning"])
        self.set_task_field("Validation", "HUMAN_VERIFIED")
        self.assertNotIn("verification-without-owner", self.errors())
        self.assertEqual(len(self.project.verified_by_owner("T-TWO")), 1)


class TestServedAuthority(ServeCase):
    """AC-T-AGENT-AUTHORITY-01-01, -02 and -09 through `praukron dashboard --serve`."""

    def setUp(self) -> None:
        super().setUp()
        owner_task(self)
        (self.authority / "ADR" / "ADR-003.md").write_text(OWNERS_ADR, encoding="utf-8")
        self.load()

    def send(self, body: object, token: str | None = None, **headers: str) -> tuple[int, dict]:
        merged = {"Content-Type": "application/json", "X-Praukron-Token": token or self.token,
                  "Origin": f"http://127.0.0.1:{self.port}", **headers}
        status, text = self.request("/authority", body, {k: v for k, v in merged.items() if v})
        return status, json.loads(text)

    def test_the_served_page_grants_and_revokes_for_the_owner_only(self) -> None:
        status, page = self.request(f"/?token={self.token}")
        self.assertEqual(status, 200)
        self.assertIn('class="authority-grant"', page)
        self.assertIn("fetch('/authority'", page)
        static = dashboard.render(self.project, analytics.report(self.project), compiler.as_json(self.project))
        self.assertNotIn("fetch('/authority'", static)
        self.assertNotIn('class="authority-grant"', static)
        body = {"op": "grant", "capability": "verify", "scope": "T-TWO", "delegate": AGENT, "until": ""}
        self.assertEqual(self.send(body, token="wrong")[0], 403)
        self.assertEqual(self.send(body, Origin="http://evil.example")[0], 403)
        self.assertFalse((self.authority / "AUTHORITY.md").exists())
        status, result = self.send(body)
        self.assertEqual(status, 200, result)
        self.load()
        grant = self.project.grants[0]
        self.assertEqual((grant.id, grant.via, grant.granted_by), (result["ids"][0], "dashboard", "thien-owner"))
        status, result = self.send({"op": "grant", "capability": "verify", "scope": "", "delegate": AGENT})
        self.assertEqual(status, 400)
        self.assertIn("states its scope", result["error"])
        status, result = self.send({"op": "revoke", "grant": grant.id})
        self.assertEqual(status, 200, result)
        self.load()
        self.assertEqual((self.project.grants[0].revoked_via, self.project.grants[0].revoked_by),
                         ("dashboard", "thien-owner"))
        with mock.patch("praukron.respond.identity", return_value="mallory"):
            status, result = self.send(body)
        self.assertEqual(status, 400)
        self.assertIn("Owners", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
