from __future__ import annotations

import json
import os
import re
from pathlib import Path
import tempfile
import unittest
import asyncio
import subprocess

from isaac_core.dev import DevModeService
from isaac_core.dev.sandbox import DevPathError
from isaac_core.brain.base import BrainAdapter
from isaac_core.conversations.service import ConversationService


class DevModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.state = Path(self.temp.name) / "state"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src/app.js").write_text("const answer = 41;\n", encoding="utf-8")
        (self.root / "README.md").write_text("Dev fixture\n", encoding="utf-8")
        (self.root / "isaac-core/src/isaac_core/dev").mkdir(parents=True)
        (self.root / "isaac-core/src/isaac_core/dev/service.py").write_text("protected\n", encoding="utf-8")
        (self.root / "isaac-core/tests").mkdir(parents=True)
        (self.root / "isaac-core/tests/test_fixture.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n", encoding="utf-8"
        )
        self.dev = DevModeService(self.root, self.state)

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self, path="src/app.js"):
        return self.dev.propose_change(
            "Update fixture", [path], [{"path": path, "description": "Change the answer"}],
            [{"check": "git_status", "args": []}],
        )

    def test_reads_do_not_require_approval(self):
        self.assertTrue(any(item["path"] == "src/app.js" for item in self.dev.list_files()["entries"]))
        self.assertIn("answer", "\n".join(self.dev.read_file("src/app.js")["lines"]))
        self.assertEqual(self.dev.search_code("answer")["matches"][0]["line"], 1)

    def test_mutation_requires_current_proposal_specific_approval(self):
        proposal = self.proposal()
        edits = [{"path": "src/app.js", "replacements": [{"old": "41", "new": "42"}]}]
        with self.assertRaises(PermissionError):
            self.dev.apply_patch(proposal["proposalId"], "wrong", edits)
        approval = self.dev.approve(proposal["proposalId"])
        result = self.dev.apply_patch(proposal["proposalId"], approval["approvalToken"], edits)
        self.assertIn("42", (self.root / "src/app.js").read_text())
        self.assertEqual(result["filesModified"], ["src/app.js"])
        with self.assertRaises(PermissionError):
            self.dev.apply_patch(proposal["proposalId"], approval["approvalToken"], edits)

    def test_old_approval_cannot_authorize_another_proposal(self):
        first = self.proposal(); token = self.dev.approve(first["proposalId"])["approvalToken"]
        second = self.proposal()
        with self.assertRaises(PermissionError):
            self.dev.apply_patch(second["proposalId"], token, [{"path": "src/app.js", "replacements": [{"old": "41", "new": "42"}]}])

    def test_manifest_is_the_single_display_and_authorization_scope(self):
        proposal = self.proposal()
        self.assertEqual([change["path"] for change in proposal["changes"]], proposal["files"])
        rendered = self.dev.render_proposal(proposal)
        for change in proposal["changes"]:
            self.assertIn(f"- {change['path']}: {change['description']}", rendered)
        approval = self.dev.approve(
            proposal["proposalId"],
            expected_revision=proposal["revision"],
            expected_manifest_hash=proposal["manifestHash"],
        )
        self.assertEqual(approval["approvedFiles"], proposal["files"])
        self.assertEqual(approval["manifestHash"], proposal["manifestHash"])

    def test_manifest_scope_or_revision_change_invalidates_approval(self):
        proposal = self.proposal()
        approval = self.dev.approve(proposal["proposalId"])
        changed = self.dev._proposal(proposal["proposalId"])
        changed["files"] = ["README.md"]
        self.dev._save_proposal(changed)
        with self.assertRaisesRegex(PermissionError, "stale"):
            self.dev.apply_patch(proposal["proposalId"], approval["approvalToken"], [{"path": "README.md", "replacements": [{"old": "fixture", "new": "changed"}]}])

        proposal = self.proposal()
        approval = self.dev.approve(proposal["proposalId"])
        changed = self.dev._proposal(proposal["proposalId"])
        changed["revision"] += 1
        self.dev._save_proposal(changed)
        with self.assertRaisesRegex(PermissionError, "stale"):
            self.dev.apply_patch(proposal["proposalId"], approval["approvalToken"], [{"path": "src/app.js", "replacements": [{"old": "41", "new": "42"}]}])

    def test_path_escapes_and_protected_writes_are_blocked(self):
        outside = Path(self.temp.name) / "outside.txt"; outside.write_text("no")
        for value in ("../outside.txt", "../missing.txt", str(outside), "\\\\server\\share\\x", "%TEMP%/x"):
            with self.assertRaises(DevPathError):
                self.dev.read_file(value)
        with self.assertRaises(DevPathError):
            self.proposal("isaac-core/src/isaac_core/dev/service.py")

    def test_symlink_escape_is_blocked(self):
        outside = Path(self.temp.name) / "outside"; outside.mkdir(); (outside / "secret.txt").write_text("secret")
        link = self.root / "linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is unavailable on this Windows configuration")
        with self.assertRaises(DevPathError):
            self.dev.read_file("linked/secret.txt")

    def test_arbitrary_command_rejected_and_allowlisted_check_runs(self):
        with self.assertRaises(ValueError):
            self.dev.run_check("shell", ["whoami"])
        result = self.dev.run_check("git_status")
        self.assertEqual(result["check"], "git_status")
        self.assertIn("exitCode", result)

    def test_patch_diff_audit_and_rollback(self):
        # Initialize only this disposable fixture as a repository for diff visibility.
        import subprocess
        subprocess.run(["git", "init"], cwd=self.root, capture_output=True, check=True)
        subprocess.run(["git", "add", "src/app.js"], cwd=self.root, capture_output=True, check=True)
        subprocess.run(["git", "-c", "user.name=ISAAC Test", "-c", "user.email=test@local", "commit", "-m", "fixture"], cwd=self.root, capture_output=True, check=True)
        proposal = self.proposal(); approval = self.dev.approve(proposal["proposalId"])
        result = self.dev.apply_patch(proposal["proposalId"], approval["approvalToken"], [{"path": "src/app.js", "replacements": [{"old": "41", "new": "42"}]}])
        self.assertIn("42", result["diff"]["stdout"])
        rolled_back = self.dev.rollback_last_change()
        self.assertTrue(rolled_back["rolledBack"])
        self.assertEqual((self.root / "src/app.js").read_text(), "const answer = 41;\n")
        actions = [json.loads(line)["action"] for line in self.dev.audit_path.read_text().splitlines()]
        self.assertIn("change.applied", actions)
        self.assertIn("change.rolled_back", actions)

class DevBrain(BrainAdapter):
    async def start(self): pass
    async def stop(self): pass
    async def cancel(self, request_id): pass
    def status(self): return {"state": "ready"}
    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        prompt = messages[-1]["content"]
        if "Implement the approved proposal" in prompt:
            text = json.dumps({"edits": [{"path": "src/app.js", "replacements": [{"old": "41", "new": "42"}]}]})
        elif "read-only architecture/planning request" in prompt:
            text = "Smallest V1: keep the existing Home/Core boundary, add a scoped Campaign service, and validate shared browser sessions without changing the current UI yet."
        elif "planning a change" in prompt:
            text = json.dumps({"files": ["src/app.js"], "changes": [{"path": "src/app.js", "description": "Update answer"}], "checks": [{"check": "npm_test", "args": ["unsafe-extra"]}]})
        else:
            text = "Normal response."
        await on_delta(text)
        return {"text": text, "timings": {}}


class MalformedProposalBrain(DevBrain):
    def __init__(self, corrected=True):
        self.corrected = corrected
        self.proposal_calls = 0

    async def generate(self, request_id, messages, on_delta, cancel_event, options=None):
        prompt = messages[-1]["content"]
        if "planning a change" in prompt:
            self.proposal_calls += 1
            text = '{"files":["src/app.js"],"changes":[{"path":"src/app.js" "description":"Plan Campaigns V1"}],"checks":[]}'
        elif "Correct it once" in prompt:
            self.proposal_calls += 1
            if self.corrected:
                text = json.dumps({"files": ["src/app.js"], "changes": [{"path": "src/app.js", "description": "Plan Campaigns V1"}], "checks": [{"check": "git_diff", "args": []}]})
            else:
                text = json.dumps({"files": "src/app.js", "changes": [], "checks": []})
        else:
            return await super().generate(request_id, messages, on_delta, cancel_event, options)
        await on_delta(text)
        return {"text": text, "timings": {}}


class DevConversationFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_campaign_proposal_gets_one_structured_retry(self):
        request = (
            "Good. One final correction before implementation. Do not modify anything yet. "
            "Inspect the actual existing storage schema and gateway routes before naming exact new files/tables. "
            "Now create the smallest implementation proposal for Campaigns V1, including exact files you intend "
            "to modify/create and focused tests. Do not apply anything until I approve the proposal."
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"; state = Path(temporary) / "state"
            (root / "src").mkdir(parents=True); source = root / "src/app.js"
            source.write_text("const campaigns = false;\n")
            brain = MalformedProposalBrain(); dev = DevModeService(root, state)
            proposal = await dev.draft_proposal(brain, request, "campaign-plan", asyncio.Event())
            self.assertEqual(brain.proposal_calls, 2)
            self.assertEqual(proposal["status"], "pending")
            self.assertEqual(proposal["files"], ["src/app.js"])
            self.assertEqual(source.read_text(), "const campaigns = false;\n")
            self.assertEqual(dev._approvals, {})

    async def test_invalid_corrected_proposal_shape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"; state = Path(temporary) / "state"
            (root / "src").mkdir(parents=True); (root / "src/app.js").write_text("const app = true;\n")
            brain = MalformedProposalBrain(corrected=False); dev = DevModeService(root, state)
            with self.assertRaisesRegex(ValueError, "after one correction attempt"):
                await dev.draft_proposal(brain, "Inspect and propose a safe source change", "bad-plan", asyncio.Event())
            self.assertEqual(brain.proposal_calls, 2)
            self.assertEqual(list(dev.proposal_root.glob("*.json")), [])
            self.assertEqual(dev._approvals, {})

    async def test_read_only_planning_does_not_create_proposal_or_approval(self):
        request = "ISAAC, inspect your existing architecture and tell me how you would build the smallest working V1. Do not modify anything yet."
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"; state = Path(temporary) / "state"
            (root / "src").mkdir(parents=True); (root / "src/app.js").write_text("const home = true;\n")
            dev = DevModeService(root, state); events = []
            async def emit(*event): events.append(event)
            service = ConversationService(DevBrain(), "You are ISAAC.", emit, dev=dev)
            conversation_id = service.create()["conversationId"]
            await service.send(conversation_id, request)
            await service.conversations[conversation_id].active_task
            response = [event for event in events if event[0] == "isaac.response.completed"][-1][1]
            self.assertEqual(response["source"], "dev_read_only_plan")
            self.assertIn("Smallest V1", response["content"])
            self.assertEqual(list(dev.proposal_root.glob("*.json")), [])
            self.assertEqual(dev._approvals, {})
            await service.stop()

    async def test_request_proposal_explicit_approval_and_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"; state = Path(temporary) / "state"
            (root / "src").mkdir(parents=True); (root / "src/app.js").write_text("const answer = 41;\n")
            subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
            subprocess.run(["git", "add", "src/app.js"], cwd=root, capture_output=True, check=True)
            subprocess.run(["git", "-c", "user.name=ISAAC Test", "-c", "user.email=test@local", "commit", "-m", "fixture"], cwd=root, capture_output=True, check=True)
            dev = DevModeService(root, state); events = []
            async def emit(*event): events.append(event)
            service = ConversationService(DevBrain(), "You are ISAAC.", emit, dev=dev)
            conversation_id = service.create()["conversationId"]
            await service.send(conversation_id, "ISAAC, update the app file")
            await service.conversations[conversation_id].active_task
            proposal_response = [event for event in events if event[0] == "isaac.response.completed"][-1][1]["content"]
            proposal_id = re.search(r"[0-9a-f-]{36}", proposal_response).group(0)
            self.assertIn("Nothing has been changed", proposal_response)
            self.assertNotIn("unsafe-extra", proposal_response)
            self.assertIn("41", (root / "src/app.js").read_text())
            await service.send(conversation_id, "Yes")
            await service.conversations[conversation_id].active_task
            self.assertIn("42", (root / "src/app.js").read_text())
            applied_response = [event for event in events if event[0] == "isaac.response.completed"][-1][1]["content"]
            self.assertIn("src/app.js", applied_response)
            self.assertNotIn("diff --git", applied_response)
            await service.send(conversation_id, "show me the diff")
            await service.conversations[conversation_id].active_task
            diff_response = [event for event in events if event[0] == "isaac.response.completed"][-1][1]["content"]
            self.assertIn("diff --git", diff_response)
            await service.stop()

    async def test_intervening_conversation_suspends_plain_approval_but_explicit_id_works(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"; state = Path(temporary) / "state"
            (root / "src").mkdir(parents=True); (root / "src/app.js").write_text("const answer = 41;\n")
            dev = DevModeService(root, state); events = []
            async def emit(*event): events.append(event)
            service = ConversationService(DevBrain(), "You are ISAAC.", emit, dev=dev)
            conversation_id = service.create()["conversationId"]
            await service.send(conversation_id, "ISAAC, update the app file")
            await service.conversations[conversation_id].active_task
            proposal_id = service.conversations[conversation_id].pending_dev_proposal_id
            self.assertIsNotNone(proposal_id)

            await service.send(conversation_id, "Tell me a joke")
            await service.conversations[conversation_id].active_task
            self.assertFalse(service.conversations[conversation_id].pending_dev_plain_approval_allowed)

            await service.send(conversation_id, "Yes")
            await service.conversations[conversation_id].active_task
            self.assertIn("Plain approval expired", [event for event in events if event[0] == "isaac.response.completed"][-1][1]["content"])
            self.assertIn("41", (root / "src/app.js").read_text())

            await service.send(conversation_id, f"Approve proposal {proposal_id}")
            await service.conversations[conversation_id].active_task
            self.assertIn("42", (root / "src/app.js").read_text())
            await service.stop()


if __name__ == "__main__":
    unittest.main()
