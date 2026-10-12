from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
from typing import Any
from uuid import uuid4

from .sandbox import DevPathError, ProjectSandbox


TEXT_EXTENSIONS = {
    ".css", ".csv", ".html", ".ini", ".js", ".json", ".jsx", ".md", ".mjs",
    ".py", ".sql", ".svg", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
SKIP_DIRS = {".git", "node_modules", "release", "dist", "build", "__pycache__", ".venv", "venv"}
MAX_READ_BYTES = 512_000
MAX_SEARCH_FILES = 4_000
MAX_OUTPUT = 40_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DevModeService:
    def __init__(self, project_root: Path, state_root: Path) -> None:
        self.sandbox = ProjectSandbox(project_root)
        self.state_root = state_root
        self.proposal_root = state_root / "proposals"
        self.snapshot_root = state_root / "snapshots"
        self.audit_path = state_root / "audit.jsonl"
        self.proposal_root.mkdir(parents=True, exist_ok=True)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._approvals: dict[str, dict[str, str]] = {}
        self._last_change: str | None = None

    def _audit(self, action: str, **details: Any) -> None:
        safe = {key: value for key, value in details.items() if key not in {"content", "token", "secret"}}
        with self._lock, self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": now(), "action": action, **safe}, separators=(",", ":")) + "\n")

    def _files(self):
        count = 0
        for path in self.sandbox.root.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts) or not path.is_file():
                continue
            count += 1
            if count > MAX_SEARCH_FILES:
                break
            yield path

    def list_files(self, relative: str = ".", depth: int = 3, pattern: str | None = None) -> dict[str, Any]:
        base = self.sandbox.resolve(relative)
        if not base.is_dir():
            raise ValueError("List target must be a directory")
        depth = min(max(int(depth), 0), 8)
        entries = []
        for path in base.rglob("*"):
            rel_parts = path.relative_to(base).parts
            if len(rel_parts) > depth or any(part in SKIP_DIRS for part in rel_parts):
                continue
            rel = self.sandbox.relative(path)
            if pattern and not Path(rel).match(pattern):
                continue
            entries.append({"path": rel, "type": "directory" if path.is_dir() else "file"})
            if len(entries) >= 1000:
                break
        self._audit("read.list_files", path=relative, count=len(entries))
        return {"root": str(self.sandbox.root), "entries": entries, "truncated": len(entries) >= 1000}

    def read_file(self, relative: str, start_line: int = 1, max_lines: int = 400) -> dict[str, Any]:
        path = self.sandbox.resolve(relative)
        if not path.is_file() or path.suffix.casefold() not in TEXT_EXTENSIONS:
            raise ValueError("Only supported text/source files may be read")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise ValueError("File exceeds the Dev Mode read limit")
        raw = path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("Binary files are not supported")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        start = min(max(int(start_line), 1), max(len(lines), 1))
        count = min(max(int(max_lines), 1), 1000)
        selected = lines[start - 1:start - 1 + count]
        self._audit("read.file", path=self.sandbox.relative(path), startLine=start, lineCount=len(selected))
        return {"path": self.sandbox.relative(path), "startLine": start, "lines": selected, "truncated": start - 1 + count < len(lines)}

    def search_code(self, query: str, pattern: str | None = None, limit: int = 100) -> dict[str, Any]:
        if not query or len(query) > 500:
            raise ValueError("Search query is required and must be under 500 characters")
        expression = re.compile(re.escape(query), re.IGNORECASE)
        results = []
        for path in self._files():
            rel = self.sandbox.relative(path)
            if path.suffix.casefold() not in TEXT_EXTENSIONS or (pattern and not Path(rel).match(pattern)):
                continue
            try:
                if path.stat().st_size > MAX_READ_BYTES:
                    continue
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, 1):
                if expression.search(line):
                    results.append({"path": rel, "line": index, "text": line[:500]})
                    if len(results) >= min(max(int(limit), 1), 200):
                        self._audit("read.search_code", queryHash=hashlib.sha256(query.encode()).hexdigest()[:12], resultCount=len(results))
                        return {"matches": results, "truncated": True}
        self._audit("read.search_code", queryHash=hashlib.sha256(query.encode()).hexdigest()[:12], resultCount=len(results))
        return {"matches": results, "truncated": False}

    def git_status(self) -> dict[str, Any]:
        return self._git(["status", "--short"])

    def git_diff(self, stat: bool = False) -> dict[str, Any]:
        return self._git(["diff", "--stat"] if stat else ["diff", "--"])

    def _git(self, args: list[str]) -> dict[str, Any]:
        result = subprocess.run(
            ["git", *args], cwd=self.sandbox.root, shell=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self._audit("read.git", command="git " + " ".join(args), exitCode=result.returncode)
        return {"exitCode": result.returncode, "stdout": result.stdout[:MAX_OUTPUT], "stderr": result.stderr[:MAX_OUTPUT]}

    def propose_change(self, user_request: str, files: list[str], changes: list[dict[str, str]], checks: list[dict[str, Any]]) -> dict[str, Any]:
        if not user_request.strip() or not files or not changes:
            raise ValueError("A request, files, and per-file change descriptions are required")
        canonical = []
        for value in files:
            path = self.sandbox.resolve(value, must_exist=True, write=True)
            if not path.is_file():
                raise ValueError("Dev Mode V1 proposals may target existing files only")
            canonical.append(self.sandbox.relative(path))
        canonical = sorted(set(canonical))
        change_by_path: dict[str, str] = {}
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("Proposal changes must be structured per-file descriptions")
            path = self.sandbox.resolve(str(change.get("path", "")), must_exist=True, write=True)
            relative = self.sandbox.relative(path)
            description = str(change.get("description", "")).strip()
            if relative not in canonical or not description or relative in change_by_path:
                raise ValueError("Proposal changes must map exactly once to each approved file")
            change_by_path[relative] = description[:1000]
        if set(change_by_path) != set(canonical):
            raise ValueError("Proposal changes must cover exactly the approved files")
        proposal_id = str(uuid4())
        proposal = {
            "proposalId": proposal_id, "revision": 1, "createdAt": now(), "active": True,
            "status": "pending", "userRequest": user_request[:4000], "files": canonical,
            "changes": [{"path": path, "description": change_by_path[path]} for path in canonical],
            "checks": checks[:10], "repairAttempts": 0,
        }
        self._save_proposal(proposal)
        self._audit("proposal.created", proposalId=proposal_id, revision=proposal["revision"], manifestHash=proposal["manifestHash"], files=proposal["files"], checks=checks)
        return proposal

    def approve(self, proposal_id: str, *, expected_revision: int | None = None, expected_manifest_hash: str | None = None) -> dict[str, Any]:
        proposal = self._proposal(proposal_id)
        if proposal["status"] != "pending":
            raise ValueError("Only a pending proposal can be approved")
        if expected_revision is not None and proposal.get("revision") != expected_revision:
            raise PermissionError("The presented proposal revision is no longer current")
        if expected_manifest_hash is not None and not secrets.compare_digest(str(proposal.get("manifestHash", "")), expected_manifest_hash):
            raise PermissionError("The presented proposal scope is no longer current")
        token = secrets.token_urlsafe(32)
        proposal["status"] = "approved"
        proposal["approvedAt"] = now()
        proposal["approvalHash"] = hashlib.sha256(token.encode()).hexdigest()
        self._save_proposal(proposal)
        self._approvals[proposal_id] = {"token": token, "manifestHash": proposal["manifestHash"], "revision": proposal["revision"]}
        self._audit("proposal.approved", proposalId=proposal_id, revision=proposal["revision"], manifestHash=proposal["manifestHash"], files=proposal["files"])
        return {"proposalId": proposal_id, "revision": proposal["revision"], "manifestHash": proposal["manifestHash"], "approvalToken": token, "approvedFiles": proposal["files"]}

    def _authorized(self, proposal_id: str, token: str) -> dict[str, Any]:
        proposal = self._proposal(proposal_id)
        approval = self._approvals.get(proposal_id)
        if not approval or not secrets.compare_digest(approval["token"], token or ""):
            raise PermissionError("A current proposal-specific approval is required")
        if proposal["status"] != "approved" or approval["revision"] != proposal.get("revision") or not secrets.compare_digest(approval["manifestHash"], str(proposal.get("manifestHash", ""))):
            raise PermissionError("Approval is stale or the proposal changed")
        return proposal

    def apply_patch(self, proposal_id: str, approval_token: str, edits: list[dict[str, Any]]) -> dict[str, Any]:
        return self.apply_change_set(proposal_id, approval_token, edits)

    def apply_change_set(self, proposal_id: str, approval_token: str, edits: list[dict[str, Any]]) -> dict[str, Any]:
        proposal = self._authorized(proposal_id, approval_token)
        targets = {str(item.get("path", "")) for item in edits}
        if not targets or not targets.issubset(set(proposal["files"])):
            raise PermissionError("Patch targets exceed the approved proposal")
        change_id = str(uuid4())
        snapshot = self.snapshot_root / change_id
        snapshot.mkdir(parents=True)
        manifest = []
        try:
            for edit in edits:
                rel = str(edit["path"])
                path = self.sandbox.resolve(rel, write=True)
                if not path.is_file() or path.suffix.casefold() not in TEXT_EXTENSIONS or path.stat().st_size > MAX_READ_BYTES:
                    raise ValueError("Patch target must be an existing supported text file")
                original = path.read_text(encoding="utf-8")
                updated = original
                for replacement in edit.get("replacements", []):
                    old, new = str(replacement.get("old", "")), str(replacement.get("new", ""))
                    if not old or updated.count(old) != 1:
                        raise ValueError(f"Patch anchor must occur exactly once in {rel}")
                    updated = updated.replace(old, new, 1)
                if updated == original:
                    raise ValueError(f"Patch made no change to {rel}")
                backup = snapshot / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(original, encoding="utf-8")
                manifest.append({"path": rel, "existed": True, "beforeHash": hashlib.sha256(original.encode()).hexdigest()})
                path.write_text(updated, encoding="utf-8")
            (snapshot / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception:
            self._restore_snapshot(snapshot, manifest)
            raise
        proposal["status"] = "applied"
        proposal["active"] = False
        proposal["changeId"] = change_id
        self._save_proposal(proposal)
        self._approvals.pop(proposal_id, None)
        self._last_change = change_id
        self._audit("change.applied", proposalId=proposal_id, changeId=change_id, files=sorted(targets))
        return {"proposalId": proposal_id, "changeId": change_id, "filesModified": sorted(targets), "diff": self.git_diff()}

    async def draft_proposal(self, brain, user_request: str, request_id: str, cancel_event: asyncio.Event) -> dict[str, Any]:
        listing = self.list_files(".", 5)["entries"]
        files = [item["path"] for item in listing if item["type"] == "file"][:350]
        keywords = [word for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", user_request) if word.casefold() not in {"isaac", "basic", "please", "would", "could", "change", "create", "build", "modify", "implement"}][:6]
        matches = []
        for keyword in keywords:
            matches.extend(self.search_code(keyword, limit=12)["matches"])
        inspected = []
        for path in dict.fromkeys(item["path"] for item in matches):
            if len(inspected) >= 6:
                break
            try:
                excerpt = self.read_file(path, 1, 100)
            except (OSError, ValueError):
                continue
            inspected.append({"path": path, "lines": excerpt["lines"]})
        prompt = (
            "You are planning a change inside one approved source repository. Return JSON only with keys "
            "files (array of project-relative paths), changes (array of {path,description}), and checks "
            "(array of {check,args}). Do not include protected security/control files. Use only these checks: "
            "git_status, git_diff, pytest, npm_test, npm_run_test. V1 can modify existing files only.\n\n"
            f"USER REQUEST:\n{user_request}\n\nPROJECT FILES:\n" + "\n".join(files)[:2500] +
            "\n\nSEARCH MATCHES:\n" + json.dumps(matches[:35])[:2500] +
            "\n\nINSPECTED FILE EXCERPTS:\n" + json.dumps(inspected)[:3500]
        )
        result = await brain.generate(
            request_id,
            [{"role": "user", "content": prompt}],
            lambda _delta: asyncio.sleep(0),
            cancel_event,
            {"temperature": 0, "max_tokens": 480},
        )
        draft = result.get("text", "")
        try:
            plan = self._proposal_json(draft)
        except (json.JSONDecodeError, ValueError, TypeError):
            correction_prompt = (
                "The previous Dev Mode proposal was not valid JSON or did not match the required schema. "
                "Correct it once. Return JSON only: {\"files\":[\"project-relative/path\"],"
                "\"changes\":[{\"path\":\"project-relative/path\",\"description\":\"short description\"}],"
                "\"checks\":[{\"check\":\"git_status|git_diff|pytest|npm_test|npm_run_test\",\"args\":[]}]}. "
                "Do not add commentary or change the requested scope.\n\nPREVIOUS OUTPUT:\n" + str(draft)[:6000]
            )
            corrected = await brain.generate(
                f"{request_id}:proposal-json-correction",
                [{"role": "user", "content": correction_prompt}],
                lambda _delta: asyncio.sleep(0),
                cancel_event,
                {"temperature": 0, "max_tokens": 480},
            )
            try:
                plan = self._proposal_json(corrected.get("text", ""))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError("ISAAC returned invalid structured proposal JSON after one correction attempt") from exc
        raw_files = [str(value) for value in plan.get("files", [])][:20]
        changes = [value for value in plan.get("changes", []) if isinstance(value, dict)][:20]
        checks = []
        for value in plan.get("checks", []):
            if not isinstance(value, dict):
                continue
            check = str(value.get("check", ""))
            args = [str(item) for item in value.get("args", [])]
            if check in {"git_status", "git_diff", "npm_test", "npm_run_test"} and not args:
                checks.append({"check": check, "args": []})
            elif check == "pytest" and args and len(args) <= 20 and all(len(item) <= 300 for item in args):
                flags = {"-q", "--quiet", "-x", "--maxfail=1"}
                paths = [item for item in args if not item.startswith("-")]
                try:
                    valid_paths = bool(paths) and all(
                        self.sandbox.resolve(item).is_file() and Path(item).suffix.casefold() == ".py"
                        for item in paths
                    )
                except (OSError, DevPathError):
                    valid_paths = False
                if valid_paths and all(not item.startswith("-") or item in flags for item in args):
                    checks.append({"check": check, "args": args})
            if len(checks) >= 10:
                break
        if not raw_files:
            raise ValueError("ISAAC could not identify a safe file scope for this request")
        return self.propose_change(user_request, raw_files, changes or [{"path": path, "description": "Implement the requested change"} for path in raw_files], checks)

    async def draft_read_only_plan(self, brain, user_request: str, request_id: str, cancel_event: asyncio.Event) -> str:
        listing = self.list_files(".", 5)["entries"]
        files = [item["path"] for item in listing if item["type"] == "file"][:350]
        keywords = [
            word for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", user_request)
            if word.casefold() not in {"isaac", "want", "that", "with", "your", "through", "their", "while", "anything", "existing", "smallest", "working"}
        ][:8]
        matches = []
        for keyword in keywords:
            matches.extend(self.search_code(keyword, limit=12)["matches"])
        inspected = []
        for path in dict.fromkeys(item["path"] for item in matches):
            if len(inspected) >= 6:
                break
            try:
                excerpt = self.read_file(path, 1, 100)
            except (OSError, ValueError):
                continue
            inspected.append({"path": path, "lines": excerpt["lines"]})
        prompt = (
            "Inspect the supplied repository evidence and answer the user's read-only architecture/planning request. "
            "Give a concise, concrete smallest-V1 plan grounded in the files shown. Mention likely files or components, "
            "boundaries, and focused validation. Do not propose a mutation, claim anything was changed, request approval, "
            "or output raw source.\n\n"
            f"USER REQUEST:\n{user_request}\n\nPROJECT FILES:\n" + "\n".join(files)[:2500] +
            "\n\nSEARCH MATCHES:\n" + json.dumps(matches[:35])[:2500] +
            "\n\nINSPECTED FILE EXCERPTS:\n" + json.dumps(inspected)[:3500]
        )
        result = await brain.generate(
            request_id,
            [{"role": "user", "content": prompt}],
            lambda _delta: asyncio.sleep(0),
            cancel_event,
            {"temperature": 0.2, "max_tokens": 480},
        )
        response = result.get("text", "").strip()
        if not response:
            raise ValueError("ISAAC could not produce a read-only architecture plan")
        return response

    async def execute_proposal(self, brain, proposal_id: str, request_id: str, cancel_event: asyncio.Event, *, expected_revision: int | None = None, expected_manifest_hash: str | None = None) -> dict[str, Any]:
        proposal = self._proposal(proposal_id)
        approval = self.approve(proposal_id, expected_revision=expected_revision, expected_manifest_hash=expected_manifest_hash)
        source_blocks = []
        for rel in proposal["files"]:
            path = self.sandbox.resolve(rel, must_exist=False, write=True)
            if not path.is_file():
                raise ValueError("Dev Mode V1 can patch existing files only")
            source_blocks.append(f"<FILE path={json.dumps(rel)}>\n{path.read_text(encoding='utf-8', errors='replace')[:80000]}\n</FILE>")
        prompt = (
            "Implement the approved proposal. Return JSON only: {edits:[{path,replacements:[{old,new}]}]}. "
            "Every old anchor must be copied exactly and occur once. Only modify approved existing files. "
            "Do not use markdown or add commentary.\n\nAPPROVED PROPOSAL:\n" + json.dumps(proposal) +
            "\n\nCURRENT FILES:\n" + "\n\n".join(source_blocks)[:180000]
        )
        generated = await brain.generate(request_id, [{"role": "user", "content": prompt}], lambda _delta: asyncio.sleep(0), cancel_event, {"temperature": 0})
        operations = self._model_json(generated.get("text", ""))
        result = self.apply_change_set(proposal_id, approval["approvalToken"], operations.get("edits", []))
        check_results = []
        for check in proposal.get("checks", []):
            check_results.append(self.run_check(str(check.get("check", "")), check.get("args", [])))
        repair_attempts = 0
        while check_results and any(item.get("exitCode") != 0 for item in check_results) and repair_attempts < 1:
            repair_attempts += 1
            failure_text = "\n".join((item.get("stdout", "") + "\n" + item.get("stderr", ""))[-12000:] for item in check_results if item.get("exitCode") != 0)
            current_files = []
            for rel in proposal["files"]:
                path = self.sandbox.resolve(rel, must_exist=False, write=True)
                if path.is_file():
                    current_files.append(f"<FILE path={json.dumps(rel)}>\n{path.read_text(encoding='utf-8', errors='replace')[:80000]}\n</FILE>")
            repair_prompt = (
                "Repair the failed approved change without expanding its file scope. Return JSON only: "
                "{edits:[{path,replacements:[{old,new}]}]}. Use exact one-occurrence anchors.\n\n"
                f"APPROVED FILES: {json.dumps(proposal['files'])}\nTEST FAILURE:\n{failure_text}\nCURRENT FILES:\n" + "\n".join(current_files)[:180000]
            )
            repaired = await brain.generate(request_id, [{"role": "user", "content": repair_prompt}], lambda _delta: asyncio.sleep(0), cancel_event, {"temperature": 0})
            repair_ops = self._model_json(repaired.get("text", ""))
            self._apply_repair(proposal_id, repair_ops.get("edits", []), repair_attempts)
            check_results = [self.run_check(str(check.get("check", "")), check.get("args", [])) for check in proposal.get("checks", [])]
        result["checks"] = check_results
        result["repairAttempts"] = repair_attempts
        result["success"] = all(item.get("exitCode") == 0 for item in check_results)
        return result

    def _apply_repair(self, proposal_id: str, edits: list[dict[str, Any]], attempt: int) -> None:
        proposal = self._proposal(proposal_id)
        if proposal.get("status") != "applied" or attempt != 1:
            raise PermissionError("Repair is not authorized for this applied proposal")
        targets = {str(item.get("path", "")) for item in edits}
        if not targets or not targets.issubset(set(proposal["files"])):
            raise PermissionError("Repair exceeds the approved file scope")
        for edit in edits:
            rel = str(edit["path"])
            path = self.sandbox.resolve(rel, write=True)
            original = path.read_text(encoding="utf-8")
            updated = original
            for replacement in edit.get("replacements", []):
                old, new = str(replacement.get("old", "")), str(replacement.get("new", ""))
                if not old or updated.count(old) != 1:
                    raise ValueError(f"Repair anchor must occur exactly once in {rel}")
                updated = updated.replace(old, new, 1)
            if updated == original:
                raise ValueError("Repair made no change")
            path.write_text(updated, encoding="utf-8")
        proposal["repairAttempts"] = attempt
        self._save_proposal(proposal)
        self._audit("change.repaired", proposalId=proposal_id, attempt=attempt, files=sorted(targets))

    @staticmethod
    def _model_json(text: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("Model did not return a structured Dev Mode response")
        value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("Structured Dev Mode response must be an object")
        return value

    @classmethod
    def _proposal_json(cls, text: str) -> dict[str, Any]:
        value = cls._model_json(text)
        if set(value) != {"files", "changes", "checks"}:
            raise ValueError("Structured proposal must contain only files, changes, and checks")
        if not isinstance(value["files"], list) or not all(isinstance(item, str) for item in value["files"]):
            raise ValueError("Structured proposal files must be an array of paths")
        if not isinstance(value["changes"], list) or not all(isinstance(item, dict) for item in value["changes"]):
            raise ValueError("Structured proposal changes must be an array of objects")
        if not isinstance(value["checks"], list) or not all(isinstance(item, dict) for item in value["checks"]):
            raise ValueError("Structured proposal checks must be an array of objects")
        return value

    def run_check(self, check: str, args: list[str] | None = None) -> dict[str, Any]:
        args = [str(value) for value in (args or [])]
        if any(len(value) > 300 for value in args) or len(args) > 20:
            raise ValueError("Unsafe check arguments")
        if check == "pytest":
            allowed_flags = {"-q", "--quiet", "-x", "--maxfail=1"}
            paths = [value for value in args if not value.startswith("-")]
            if not paths or any(value.startswith("-") and value not in allowed_flags for value in args):
                raise ValueError("Only bounded pytest paths and safe flags are allowed")
            for value in paths:
                path = self.sandbox.resolve(value)
                if not path.is_file() or path.suffix.casefold() != ".py":
                    raise ValueError("pytest targets must be Python files inside the project")
            command = [os.environ.get("ISAAC_PYTHON", os.sys.executable), "-m", "pytest", *args]
        elif check in {"npm_test", "npm_run_test"}:
            if args:
                raise ValueError("npm test does not accept Dev Mode arguments")
            command = ["npm", "test"] if check == "npm_test" else ["npm", "run", "test"]
        elif check in {"git_status", "git_diff"}:
            if args:
                raise ValueError("Git checks do not accept Dev Mode arguments")
            result = self.git_status() if check == "git_status" else self.git_diff()
            return {"check": check, **result}
        else:
            raise ValueError("Command is not allowlisted")
        result = subprocess.run(
            command, cwd=self.sandbox.root, shell=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        self._audit("check.run", check=check, args=args, exitCode=result.returncode)
        return {"check": check, "exitCode": result.returncode, "stdout": result.stdout[:MAX_OUTPUT], "stderr": result.stderr[:MAX_OUTPUT]}

    def rollback_last_change(self) -> dict[str, Any]:
        if not self._last_change:
            raise ValueError("There is no ISAAC-managed change to undo in this Core session")
        snapshot = self.snapshot_root / self._last_change
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        self._restore_snapshot(snapshot, manifest)
        change_id, self._last_change = self._last_change, None
        self._audit("change.rolled_back", changeId=change_id, files=[item["path"] for item in manifest])
        return {"rolledBack": True, "changeId": change_id, "files": [item["path"] for item in manifest]}

    def _restore_snapshot(self, snapshot: Path, manifest: list[dict[str, Any]]) -> None:
        for item in manifest:
            path = self.sandbox.resolve(item["path"], must_exist=False, write=True)
            if item["existed"]:
                backup = snapshot / item["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup, path)
            elif path.exists() and path.is_file():
                path.unlink()

    def _proposal(self, proposal_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f-]{36}", proposal_id or ""):
            raise ValueError("Invalid proposal ID")
        path = self.proposal_root / f"{proposal_id}.json"
        if not path.is_file():
            raise KeyError("Proposal not found")
        proposal = json.loads(path.read_text(encoding="utf-8"))
        if "manifestHash" not in proposal:
            # Pending proposals written before canonical manifests are upgraded
            # once, preserving their existing scope while making future approval
            # checks deterministic.
            proposal.setdefault("revision", 1)
            proposal.setdefault("active", proposal.get("status") != "applied")
            self._save_proposal(proposal)
        expected = self._manifest_hash(proposal)
        if not secrets.compare_digest(str(proposal.get("manifestHash", "")), expected):
            raise ValueError("Proposal manifest integrity check failed")
        return proposal

    def _save_proposal(self, proposal: dict[str, Any]) -> None:
        proposal["manifestHash"] = self._manifest_hash(proposal)
        path = self.proposal_root / f"{proposal['proposalId']}.json"
        path.write_text(json.dumps(proposal, indent=2), encoding="utf-8")

    @staticmethod
    def _manifest_payload(proposal: dict[str, Any]) -> dict[str, Any]:
        return {
            "proposalId": proposal["proposalId"], "revision": proposal["revision"],
            "goal": proposal["userRequest"], "files": proposal["files"],
            "changes": proposal["changes"], "checks": proposal["checks"],
            "createdAt": proposal["createdAt"], "active": proposal["active"],
        }

    @classmethod
    def _manifest_hash(cls, proposal: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(cls._manifest_payload(proposal), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def render_proposal(cls, proposal: dict[str, Any]) -> str:
        manifest = cls._manifest_payload(proposal)
        changes = "\n".join(f"- {item['path']}: {item['description']}" for item in manifest["changes"])
        checks = "\n".join(f"- {item['check']}: {' '.join(item.get('args', []))}" for item in manifest["checks"]) or "- No automated check identified"
        return (
            f"Proposal {manifest['proposalId']} · revision {manifest['revision']}\n\n"
            f"Goal: {manifest['goal']}\n\nFiles:\n{changes}\n\nChecks:\n{checks}\n\n"
            "Nothing has been changed. Say Yes to approve this exact active proposal."
        )
