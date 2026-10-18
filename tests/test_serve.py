"""`praukron dashboard --serve`: local write-back through respond (T-SERVE-01, ADR-055)."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from praukron import analytics, compile as compiler, dashboard, review, serve  # noqa: E402
from test_assumptions import AssumptionCase  # noqa: E402


class ServeCase(AssumptionCase):
    def setUp(self) -> None:
        super().setUp()
        self.server, self.token, self.url = serve.make_server(self.dir)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path: str, body: object | None = None, headers: dict | None = None) -> tuple[int, str]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                         headers=headers or {}, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def post(self, body: object, token: str | None = None, **headers: str) -> tuple[int, dict]:
        merged = {"Content-Type": "application/json", "X-Praukron-Token": token or self.token,
                  "Origin": f"http://127.0.0.1:{self.port}", **headers}
        status, text = self.request("/respond", body, {k: v for k, v in merged.items() if v})
        return status, json.loads(text)


class TestServe(ServeCase):
    def test_it_binds_loopback_with_a_token_and_stops_cleanly(self) -> None:
        """AC-T-SERVE-01-01."""
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertRegex(self.url, r"^http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_\-]{32}$")
        self.assertNotEqual(serve.make_server(self.dir)[1], self.token)
        before = sorted(p for p in self.dir.rglob("*"))
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(serve.HTTPServer, "serve_forever",
                                                     side_effect=KeyboardInterrupt):
            self.assertEqual(serve.run(self.dir), 0)
        self.assertIn("Serving the dashboard at http://127.0.0.1:", out.getvalue())
        self.assertIn("Stopped.", out.getvalue())
        self.assertEqual(sorted(p for p in self.dir.rglob("*")), before)

    def test_a_tokened_same_origin_post_writes_through_respond(self) -> None:
        """AC-T-SERVE-01-02."""
        status, page = self.request(f"/?token={self.token}")
        self.assertEqual(status, 200)
        self.assertIn('class="aform"', page)
        status, result = self.post({"items": [
            {"target": "A-2", "action": "CONFIRM", "text": "OK"},
            {"target": "T-TWO", "action": "GUIDE", "text": "Hãy dùng bản staging."}]})
        self.assertEqual((status, result), (200, {"ok": True, "ids": ["R-3", "R-4"]}))
        self.load()
        self.assertEqual([(r.id, r.via, r.text) for r in self.project.responses[-2:]],
                         [("R-3", "dashboard", "OK"), ("R-4", "dashboard", "Hãy dùng bản staging.")])
        _, page = self.request(f"/?token={self.token}")
        self.assertIn("Hãy dùng bản staging.", page)
        status, result = self.post({"items": [{"target": "A-404", "action": "CONFIRM", "text": "OK"}]})
        self.assertEqual(status, 400)
        self.assertIn("not an assumption", result["error"])

    def test_untrusted_requests_write_nothing(self) -> None:
        """AC-T-SERVE-01-02 (refusals)."""
        responses = self.authority / "RESPONSES.md"
        original = responses.read_bytes()
        body = {"items": [{"target": "A-1", "action": "CONFIRM", "text": "OK"}]}
        self.assertEqual(self.post(body, token="wrong")[0], 403)
        self.assertEqual(self.post(body, Origin="http://evil.example")[0], 403)
        self.assertEqual(self.post(body, **{"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(self.request("/")[0], 403)
        self.assertEqual(self.request("/?token=wrong")[0], 403)
        self.assertEqual(responses.read_bytes(), original)

    def test_served_and_static_differ_only_in_the_forms(self) -> None:
        """AC-T-SERVE-01-03."""
        project = compiler.load(self.dir)
        report = analytics.report(project)
        compiled = compiler.as_json(project)
        static = dashboard.render(project, report, compiled)
        self.assertEqual(static, dashboard.render(project, report, compiled))
        self.assertNotIn("fetch(", static)
        served = serve.page(self.dir)

        def strip(page: str) -> str:
            # The localization map mirrors the form text removed below.
            page = re.sub(r'<script type="application/json" id="i18n-data">.*?</script>', "", page, flags=re.S)
            page = page.replace(review.SERVE_SCRIPT, "")
            page = re.sub(r'<div class="(?:controls review-bar|aform)".*?</div>', "", page, flags=re.S)
            page = re.sub(r'<(p|span) class="respond-hint">.*?</\1>', "", page, flags=re.S)
            page = re.sub(r'<p class="note">Each card starts at OK\..*?</p>', "", page, flags=re.S)
            return page

        self.assertEqual(strip(served), strip(static))
        self.assertNotEqual(served, static)

    def test_the_review_rule_confirms_only_what_was_opened(self) -> None:
        """AC-T-SERVE-01-04."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        cases = [
            {"target": "A-1", "choice": "CONFIRM", "seen": True, "reviewed": False, "text": "", "action": "REVISE", "guide": False},
            {"target": "A-2", "choice": "CONFIRM", "seen": False, "reviewed": False, "text": "", "action": "REVISE", "guide": False},
            {"target": "A-3", "choice": "CONFIRM", "seen": False, "reviewed": True, "text": "", "action": "REVISE", "guide": False},
            {"target": "A-4", "choice": "FEEDBACK", "seen": False, "reviewed": False, "text": "7 ngày", "action": "REVISE", "guide": False},
            {"target": "A-5", "choice": "FEEDBACK", "seen": True, "reviewed": False, "text": "No.", "action": "REJECT", "guide": False},
            {"target": "T-TWO", "choice": "CONFIRM", "seen": False, "reviewed": False, "text": "Staging only.", "action": "REVISE", "guide": True},
            {"target": "T-ONE", "choice": "CONFIRM", "seen": False, "reviewed": False, "text": " ", "action": "REVISE", "guide": True},
        ]
        program = review.DECIDE_JS + (
            f"console.log(JSON.stringify(reviewItems({json.dumps(cases)})));"
            "try { reviewItems([{target:'A-9',choice:'FEEDBACK',text:'  ',guide:false}]); console.log('no error'); }"
            " catch (e) { console.log(e.message); }"
        )
        out = subprocess.run([node, "-e", program], capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
        self.assertEqual(json.loads(out[0]), [
            {"target": "A-1", "action": "CONFIRM", "text": "OK"},
            {"target": "A-3", "action": "CONFIRM", "text": "OK"},
            {"target": "A-4", "action": "REVISE", "text": "7 ngày"},
            {"target": "A-5", "action": "REJECT", "text": "No."},
            {"target": "T-TWO", "action": "GUIDE", "text": "Staging only."},
        ])
        self.assertEqual(out[1], "A-9: write your feedback, or choose OK")
        page = serve.page(self.dir)
        self.assertIn('value="CONFIRM" checked> OK', page)
        self.assertIn("Recording as GitHub user <strong>thien-owner</strong>", page)
        self.assertNotIn('<input type="text" class="review-by"', page)
        self.assertIn("Each card starts at OK.", page)

    def test_an_answered_card_offers_no_second_answer(self) -> None:
        """Found in the exit run: reopening an answered card confirmed it again."""
        page = serve.page(self.dir)
        answered = page.split('id="assumption-A-1"', 1)[1].split("</details>", 1)[0]
        self.assertNotIn('class="aform"', answered)
        self.assertIn("Answered; waiting for the agent to reconcile it.", answered)

    def test_a_permission_card_says_what_was_assumed(self) -> None:
        """AC-T-SERVE-01-05."""
        card = serve.page(self.dir).split('id="assumption-A-2"', 1)[1].split("</details>", 1)[0]
        self.assertIn("This is an access rule the agent assumed: sales read own clients.", card)
        self.assertIn('value="CONFIRM" checked> OK', card)
        self.assertIn('class="reviewed"', card)


if __name__ == "__main__":
    unittest.main(verbosity=2)
