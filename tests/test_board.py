"""Offline integration checks: no GitHub writes, credentials, or network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


BOARD = Path(__file__).resolve().parents[1] / "tools" / "board"
FAKE_GH = r'''#!/usr/bin/env python3
import base64, json, os, sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse
args = sys.argv[1:]
if os.environ.get("FAKE_FAIL"):
    sys.exit(1)
if args[0] != "api":
    sys.exit(2)
endpoint = next((s for s in args[1:] if s == "user" or s.startswith("repos/")), "")
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if endpoint == "user":
    print("alice")
elif endpoint == "repos/team/hack":
    print("team/hack")
elif endpoint == "repos/team/hack/issues/7":
    print(len(json.loads(Path(os.environ["FAKE_COMMENTS"]).read_text())))
elif endpoint.startswith("repos/team/hack/issues/7/comments"):
    if "-f" in args:
        print("https://github.com/team/hack/issues/7#issuecomment-999")
    else:
        rows = json.loads(Path(os.environ["FAKE_COMMENTS"]).read_text())
        query = parse_qs(urlparse(endpoint).query)
        if "since" in query:
            rows = [r for r in rows if r["created_at"] >= query["since"][0]]
        else:
            page = int(query.get("page", ["1"])[0])
            rows = rows[(page-1)*100:page*100]
        for row in rows:
            body = base64.b64encode(row["body"].encode()).decode()
            print(f'{row["id"]}\t{row["user"]}\t{row["created_at"]}\t{body}')
else:
    print("Unexpected endpoint: " + endpoint, file=sys.stderr)
    sys.exit(2)
'''


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tools").mkdir()
        self.script = self.root / "tools" / "board"
        shutil.copy2(BOARD, self.script)
        (self.root / "bin").mkdir()
        fake = self.root / "bin" / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        self.comments = self.root / "comments.json"
        self.comments.write_text("[]")
        self.log = self.root / "calls.jsonl"
        self.config = self.root / "board.conf"
        self.config.write_text("BOARD_REPO=team/hack\nBOARD_ISSUE=7\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("BOARD_")}
        self.env.update(PATH=str(fake.parent) + os.pathsep + os.environ["PATH"],
                        FAKE_COMMENTS=str(self.comments), FAKE_LOG=str(self.log))

    def run_board(self, *args, input=None, extra_env=None, cwd=None):
        return subprocess.run(["bash", str(self.script), *args], cwd=cwd or self.root,
                              env={**self.env, **(extra_env or {})}, input=input,
                              capture_output=True, text=True, timeout=4)

    def rows(self, entries):
        self.comments.write_text(json.dumps([
            dict(id=i, user=user, body=body, created_at=f"2026-11-02T12:00:{i:02d}Z")
            for i, user, body in entries
        ]))

    def test_help_and_whoami_do_not_need_board_config(self):
        self.config.unlink()
        self.assertEqual(self.run_board("help").returncode, 0)
        result = self.run_board("whoami")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "alice")

    def test_unconfigured_issue_fails_before_api_calls(self):
        self.config.write_text("BOARD_REPO=\nBOARD_ISSUE=\n")
        result = self.run_board("unread")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BOARD_ISSUE", result.stderr)
        self.assertFalse(self.log.exists())

    def test_environment_overrides_shared_config(self):
        self.config.write_text("BOARD_REPO=other/repo\nBOARD_ISSUE=20\n")
        result = self.run_board("read", extra_env={"BOARD_REPO": "team/hack", "BOARD_ISSUE": "7"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_configuration_is_not_executed(self):
        marker = self.root / "executed"
        self.config.write_text(f"BOARD_REPO=$(touch {marker})\nBOARD_ISSUE=7\n")
        self.assertNotEqual(self.run_board("read").returncode, 0)
        self.assertFalse(marker.exists())

    def test_unread_filters_self_and_advances_cursor(self):
        self.rows([(1, "bob", "@Alice ready"), (2, "alice", "my post")])
        first = self.run_board("unread")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("MENTION", first.stdout)
        self.assertNotIn("my post", first.stdout)
        self.assertEqual(self.run_board("unread").stdout, "")
        self.rows([(1, "bob", "@Alice ready"), (2, "alice", "my post"),
                   (3, "bob", "@all update")])
        result = self.run_board("unread")
        self.assertIn("@all update", result.stdout)
        self.assertIn("MENTION", result.stdout)
        self.assertNotIn("@Alice ready", result.stdout)

    def test_read_does_not_consume_unread_and_mention_has_boundaries(self):
        self.rows([(1, "bob", "@alice-extra not your tag")])
        read = self.run_board("read", "-n", "20")
        self.assertIn("not your tag", read.stdout)
        self.assertNotIn("MENTION", read.stdout)
        self.assertIn("not your tag", self.run_board("unread").stdout)

    def test_fetch_failure_preserves_cursor(self):
        self.rows([(1, "bob", "first")])
        self.assertEqual(self.run_board("unread").returncode, 0)
        cursor = next((self.root / ".board").glob("cursor-*"))
        before = cursor.read_text()
        result = self.run_board("unread", extra_env={"FAKE_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(cursor.read_text(), before)

    def test_post_rejects_missing_identity_and_accepts_stdin(self):
        self.assertNotEqual(self.run_board("post", "anonymous").returncode, 0)
        body = "[Alice's Codex] READY: @bob PR #12\nChecks passed."
        result = self.run_board("post", input=body)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(calls[-1][-3:], ["body=" + body, "--jq", ".html_url"])

    def test_bad_arguments_fail_instead_of_looping(self):
        for args in [("read", "-n"), ("wait", "--interval"), ("wait", "--timeout"),
                     ("wait", "--interval", "0"), ("wait", "--timeout", ""),
                     ("wait", "--interval", "-1"), ("read", "-n", "oops")]:
            with self.subTest(args=args):
                self.assertNotEqual(self.run_board(*args).returncode, 0)

    def test_wait_wakes_for_unmentioned_comments(self):
        self.rows([(1, "bob", "update for somebody else")])
        result = self.run_board("wait", "--timeout", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("update for somebody else", result.stdout)
        self.assertNotIn("MENTION", result.stdout)

    def test_wait_timeout_caps_sleep(self):
        started = time.monotonic()
        result = self.run_board("wait", "--interval", "30", "--timeout", "1")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertLess(time.monotonic() - started, 3)

    def test_state_stays_in_checkout_when_called_from_subfolder(self):
        nested = self.root / "frontend"
        nested.mkdir()
        self.rows([(1, "bob", "new")])
        self.assertEqual(self.run_board("unread", cwd=nested).returncode, 0)
        self.assertTrue((self.root / ".board" / "login").exists())
        self.assertFalse((nested / ".board").exists())

    def test_crlf_board_conf_parses_cleanly(self):
        self.config.write_bytes(b"# CRLF config\r\nBOARD_REPO=team/hack\r\nBOARD_ISSUE=7\r\n")
        self.rows([(1, "bob", "crlf check")])
        result = self.run_board("read")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("crlf check", result.stdout)


if __name__ == "__main__":
    unittest.main()
