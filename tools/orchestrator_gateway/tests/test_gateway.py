"""Synthetic tests. No GitHub, cloud, clinical data or external secrets."""

import asyncio
import base64
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from gateway import policy as p
from gateway.api import ROUTES, create_app, openapi
from gateway.github import COMMIT_FILES, UPDATE_REFS, GitHub, sign_app_jwt
from gateway.journal import Journal
from gateway.models import (
    BranchCreate,
    BranchDelete,
    FileDelete,
    FileWrite,
    Merge,
    PullCreate,
    PullUpdate,
    Search,
)
from gateway.service import Service
from gateway.settings import Settings
from pydantic import ValidationError

A, B, C, D = (x * 40 for x in "abcd")
BRANCH = "orchestrator/test-change"


def config(path="unused.db", **extra):
    return Settings(
        public_url="https://gateway.nexa-care.dev",
        api_key="a" * 48,
        journal_key="b" * 48,
        journal_path=path,
        app_id="123",
        installation_id="456",
        private_key="SYNTHETIC-NOT-A-KEY",
        **extra,
    )


def pull():
    return {
        "number": 7,
        "node_id": "PR_test",
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "merge_commit_sha": C,
        "head": {"ref": BRANCH, "sha": B, "repo": {"full_name": p.REPOSITORY}},
        "base": {"ref": "main", "sha": A, "repo": {"full_name": p.REPOSITORY}},
        "user": {"login": "gateway[bot]"},
        "title": "Synthetic change",
        "body": "Synthetic body",
    }


def evidence():
    return {
        "repository": {
            "id": "R_test",
            "pullRequest": {
                "headRefOid": B,
                "baseRefOid": A,
                "reviewDecision": "APPROVED",
                "reviewThreads": {
                    "nodes": [{"isResolved": True}],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                },
            },
            "ref": {
                "branchProtectionRule": {
                    "requiresApprovingReviews": True,
                    "requiredApprovingReviewCount": 1,
                    "dismissesStaleReviews": True,
                    "requiresConversationResolution": True,
                    "requiresStatusChecks": True,
                    "requiresStrictStatusChecks": True,
                    "requiredStatusCheckContexts": ["unit"],
                    "isAdminEnforced": True,
                    "allowsForcePushes": False,
                    "allowsDeletions": False,
                }
            },
        }
    }


def checks(sha):
    return {
        "commitSha": sha,
        "checks": [
            {
                "name": "unit",
                "appId": 99,
                "status": "completed",
                "conclusion": "success",
                "headSha": sha,
            }
        ],
        "statuses": [],
    }


class PolicyTests(unittest.TestCase):
    def test_path_bypasses(self):
        for path in (
            "../README.md",
            "/README.md",
            "app//x.py",
            "app/./x.py",
            "app/%2e%2e/x.py",
            "app\\x.py",
            ".env",
            ".env.example",
            "app/.env.local",
            ".git/config",
            "docs/secrets.md",
            "a/private-key.txt",
            "a/dump.json",
            "a/key.pem",
            "a/test.db",
            "a\n.py",
            "a/credentials.json",
        ):
            with self.subTest(path=path), self.assertRaises(p.Denied):
                p.path(path)

    def test_control_write_paths(self):
        for path in (
            ".github/workflows/ci.yml",
            "deploy/config.yaml",
            "AGENTS.md",
            "docs/governance/policy.md",
            "tools/orchestrator_gateway/gateway/api.py",
        ):
            with self.subTest(path=path), self.assertRaises(p.Denied):
                p.path(path, write=True)

    def test_normal_source_paths(self):
        for path in (
            "app/services/example.py",
            "docs/ARCHITECTURE.md",
            "nexa-client/app/page.tsx",
        ):
            self.assertEqual(p.path(path, write=True), path)

    def test_branch_bypasses(self):
        for branch in (
            "main",
            "refs/heads/main",
            "feature/a",
            "orchestrator/../main",
            "orchestrator//a",
            "orchestrator/a.lock",
            "orchestrator/A",
            "orchestrator/-a",
            "orchestrator/a%2fb",
        ):
            with self.subTest(branch=branch), self.assertRaises(p.Denied):
                p.branch(branch)

    def test_sensitive_and_binary_text(self):
        for value in (
            "a\x00b",
            "a" * 48001,
            "é" * 24001,
            "-----BEGIN RSA PRIVATE KEY-----",
            "ghp_" + "a" * 40,
        ):
            with self.subTest(), self.assertRaises(p.Denied):
                p.clean_text(value)

    def test_required_preconditions(self):
        data = {
            "branch": BRANCH,
            "path": "app/a.py",
            "content": "x",
            "commitMessage": "test",
            "expectedHeadSha": A,
        }
        with self.assertRaises(ValidationError):
            FileWrite(**data)
        data["expectedBlobSha"] = None
        self.assertIsNone(FileWrite(**data).expectedBlobSha)
        with self.assertRaises(ValidationError):
            FileWrite(**data, repository="attacker/repo")

    def test_merge_method_restricted(self):
        for method in ("squash", "rebase"):
            with self.assertRaises(ValidationError):
                Merge(expectedHeadSha=B, expectedBaseSha=A, mergeMethod=method)

    def test_config_fails_closed(self):
        for updates in (
            {"public_url": "http://gateway.dev"},
            {"public_url": "https://YOUR-NEXA-ORCHESTRATOR-DOMAIN.example.com"},
            {"public_url": "https://user:pass@gateway.dev"},
            {"public_url": "https://gateway.dev/path"},
            {"api_key": "short"},
            {"journal_key": "a" * 48},
            {"journal_path": ":memory:"},
            {"merge_enabled": True},
            {"required_checks": {"unit": -1}},
        ):
            with self.subTest(updates=list(updates)), self.assertRaises(ValueError):
                replace(config(), **updates)

    def test_jwt_signature_and_claims(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        token = sign_app_jwt(pem, "123", 1000)
        header, payload, signature = token.split(".")
        decode = lambda x: base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
        self.assertEqual(json.loads(decode(header)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(
            json.loads(decode(payload)), {"iat": 940, "exp": 1540, "iss": "123"}
        )
        key.public_key().verify(
            decode(signature),
            (header + "." + payload).encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "journal.db")
        self.j = Journal(self.path, "test-only-key", 2)

    def tearDown(self):
        self.j.close()
        self.tmp.cleanup()

    def test_durable_replay_and_collision(self):
        op, _ = self.j.begin(
            "createBranch",
            "synthetic-idempotency-key-1",
            {"content": "private-test-content"},
        )
        self.j.finish(op, "createBranch", {"commitSha": A})
        self.j.close()
        self.j = Journal(self.path, "test-only-key", 2)
        self.assertEqual(
            self.j.begin(
                "createBranch",
                "synthetic-idempotency-key-1",
                {"content": "private-test-content"},
            )[1],
            {"commitSha": A},
        )
        with self.assertRaisesRegex(p.Denied, "IDEMPOTENCY_KEY_REUSED"):
            self.j.begin(
                "createBranch", "synthetic-idempotency-key-1", {"content": "changed"}
            )
        rows = repr(self.j.db.execute("SELECT * FROM operations").fetchall())
        self.assertNotIn("private-test-content", rows)
        self.assertNotIn("synthetic-idempotency-key-1", rows)

    def test_pending_never_reexecutes(self):
        self.j.begin("createBranch", "a" * 20, {})
        with self.assertRaisesRegex(p.Denied, "RECONCILIATION"):
            self.j.begin("createBranch", "a" * 20, {})

    def test_rate_limit(self):
        self.j.begin("createBranch", "a" * 20, {})
        self.j.begin("createBranch", "b" * 20, {})
        with self.assertRaisesRegex(p.Denied, "RATE_LIMIT"):
            self.j.begin("createBranch", "c" * 20, {})


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gh = AsyncMock()
        self.s = Service(
            self.gh, config(required_checks={"unit": 99}, merge_enabled=True)
        )

    async def test_stale_write_stops_before_commit(self):
        self.s.head = AsyncMock(return_value=B)
        with self.assertRaisesRegex(p.Denied, "STALE_BRANCH_HEAD"):
            await self.s.write_file(
                FileWrite(
                    branch=BRANCH,
                    path="app/a.py",
                    content="x",
                    expectedHeadSha=A,
                    expectedBlobSha=None,
                    commitMessage="test",
                )
            )
        self.gh.graphql.assert_not_called()

    async def test_stale_blob_stops_commit(self):
        self.s.head = AsyncMock(return_value=A)
        self.s.entry = AsyncMock(return_value={"sha": B})
        with self.assertRaisesRegex(p.Denied, "STALE_FILE_BLOB"):
            await self.s.write_file(
                FileWrite(
                    branch=BRANCH,
                    path="app/a.py",
                    content="x",
                    expectedHeadSha=A,
                    expectedBlobSha=None,
                    commitMessage="test",
                )
            )
        self.gh.graphql.assert_not_called()

    async def test_create_and_delete_bind_head(self):
        self.s.head = AsyncMock(return_value=A)
        self.s.entry = AsyncMock(return_value=None)
        self.gh.graphql.return_value = {"createCommitOnBranch": {"commit": {"oid": C}}}
        write = FileWrite(
            branch=BRANCH,
            path="app/a.py",
            content="print(1)",
            expectedHeadSha=A,
            expectedBlobSha=None,
            commitMessage="test",
        )
        self.assertEqual((await self.s.write_file(write))["commitSha"], C)
        query, variables = self.gh.graphql.call_args.args
        self.assertEqual(query, COMMIT_FILES)
        self.assertEqual(variables["input"]["expectedHeadOid"], A)
        self.s.entry.return_value = {"sha": B}
        await self.s.write_file(
            FileDelete(
                branch=BRANCH,
                path="app/a.py",
                expectedHeadSha=A,
                expectedBlobSha=B,
                commitMessage="remove test",
            )
        )
        self.assertEqual(
            self.gh.graphql.call_args.args[1]["input"]["fileChanges"],
            {"deletions": [{"path": "app/a.py"}]},
        )

    async def test_symlink_and_submodule_blocked(self):
        for mode, kind in (("120000", "blob"), ("160000", "commit")):
            self.s.tree = AsyncMock(
                return_value=[
                    {"path": "app/a.py", "sha": B, "mode": mode, "type": kind}
                ]
            )
            with self.assertRaisesRegex(p.Denied, "NON_REGULAR"):
                await self.s.entry("app/a.py", A)

    async def test_parent_symlink_blocks_new_file(self):
        self.s.tree = AsyncMock(
            return_value=[{"path": "app", "sha": B, "mode": "120000", "type": "blob"}]
        )
        with self.assertRaisesRegex(p.Denied, "NON_REGULAR"):
            await self.s.entry("app/new.py", A, missing_ok=True)

    async def test_truncated_tree_denied(self):
        self.gh.request.return_value = {"truncated": True, "tree": []}
        with self.assertRaisesRegex(p.Denied, "TREE_INCOMPLETE"):
            await self.s.tree(A)

    async def test_secret_path_no_upstream_read(self):
        with self.assertRaises(p.Denied):
            await self.s.get_file(".env", "main")
        self.gh.request.assert_not_called()

    async def test_get_file_pins_commit_and_decodes(self):
        self.s.head = AsyncMock(return_value=A)
        self.s.entry = AsyncMock(return_value={"sha": B})
        self.gh.request.return_value = {
            "encoding": "base64",
            "size": 5,
            "content": base64.b64encode(b"hello").decode(),
        }
        result = await self.s.get_file("app/a.py", "main")
        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["commitSha"], A)
        self.s.entry.assert_awaited_once_with("app/a.py", A, max_bytes=p.MAX_READ_BYTES)

    async def test_large_governance_read_paginates_at_fixed_sha(self):
        self.s.resolve = AsyncMock(return_value=A)
        self.s.entry = AsyncMock(return_value={"sha": B})
        content = "governance guidance\n" * 8000
        self.gh.request.return_value = {
            "encoding": "base64",
            "size": len(content),
            "content": base64.b64encode(content.encode()).decode(),
        }
        first = await self.s.get_file(
            "docs/governance/SECURITY_NON_REGRESSION.md", "main"
        )
        self.assertEqual(len(first["content"]), 6000)
        self.assertFalse(first["complete"])
        second = await self.s.get_file(
            "docs/governance/SECURITY_NON_REGRESSION.md",
            first["commitSha"],
            first["nextStartAt"],
        )
        self.assertEqual(second["content"], content[6000:12000])
        self.s.resolve.assert_awaited_with(A)

    async def test_secret_after_first_page_blocks_entire_read(self):
        self.s.resolve = AsyncMock(return_value=A)
        self.s.entry = AsyncMock(return_value={"sha": B})
        content = "x" * 8000 + " -----BEGIN RSA PRIVATE KEY-----"
        self.gh.request.return_value = {
            "encoding": "base64",
            "size": len(content),
            "content": base64.b64encode(content.encode()).decode(),
        }
        with self.assertRaisesRegex(p.Denied, "SENSITIVE_CONTENT"):
            await self.s.get_file("docs/notes.md", A)

    async def test_search_uses_literal_and_allowed_files(self):
        self.s.resolve = AsyncMock(return_value=A)
        self.s.tree = AsyncMock(
            return_value=[
                {
                    "path": "app/a.py",
                    "sha": B,
                    "size": 20,
                    "mode": "100644",
                    "type": "blob",
                },
                {
                    "path": "app/.env",
                    "sha": C,
                    "size": 20,
                    "mode": "100644",
                    "type": "blob",
                },
            ]
        )
        self.s.blob_text = AsyncMock(return_value="first\nrepo:evil/source\n")
        result = await self.s.search(Search(query="repo:evil/source"))
        self.assertEqual(result["matches"][0]["lines"], [2])
        self.assertTrue(result["complete"])
        self.s.blob_text.assert_awaited_once()

    async def test_branch_creation_atomic_base_and_absence(self):
        self.s.head = AsyncMock(return_value=A)
        self.gh.request.return_value = {"node_id": "R_test"}
        await self.s.create_branch(BranchCreate(branchName=BRANCH, baseSha=A))
        query, variables = self.gh.graphql.call_args.args
        self.assertEqual(query, UPDATE_REFS)
        refs = variables["input"]["refUpdates"]
        self.assertEqual(refs[0]["beforeOid"], A)
        self.assertEqual(refs[1]["beforeOid"], "0" * 40)
        self.assertFalse(refs[1]["force"])

    async def test_foreign_pr_denied(self):
        pr = pull()
        pr["head"]["repo"]["full_name"] = "attacker/repo"
        self.gh.request.return_value = pr
        with self.assertRaisesRegex(p.Denied, "PR_SCOPE"):
            await self.s.raw_pr(7)

    async def test_pr_create_fixed_base(self):
        self.s.head = AsyncMock(return_value=B)
        self.gh.request.return_value = {"number": 7}
        await self.s.create_pr(
            PullCreate(headBranch=BRANCH, expectedHeadSha=B, title="Test", body="Test")
        )
        self.assertEqual(self.gh.request.call_args.kwargs["body"]["base"], "main")

    async def test_pr_update_one_mutation_at_a_time(self):
        self.s.raw_pr = AsyncMock(return_value=pull())
        with self.assertRaisesRegex(p.Denied, "SEPARATE_DRAFT"):
            await self.s.update_pr(
                7, PullUpdate(expectedHeadSha=B, title="Test", draft=True)
            )
        self.gh.request.assert_not_called()
        self.gh.graphql.assert_not_called()

    async def test_branch_deletion_requires_merged_matching_pr(self):
        self.s.raw_pr = AsyncMock(return_value=pull())
        with self.assertRaisesRegex(p.Denied, "MERGED_BRANCH_PROOF"):
            await self.s.delete_branch(
                BranchDelete(branchName=BRANCH, expectedSha=B, mergedPullRequest=7)
            )
        self.gh.graphql.assert_not_called()

    async def test_branch_deletion_atomic_expected_sha(self):
        pr = pull()
        pr["merged"] = True
        self.s.raw_pr = AsyncMock(return_value=pr)
        self.s.head = AsyncMock(return_value=B)
        self.gh.request.side_effect = [[], {"node_id": "R_test"}]
        await self.s.delete_branch(
            BranchDelete(branchName=BRANCH, expectedSha=B, mergedPullRequest=7)
        )
        update = self.gh.graphql.call_args.args[1]["input"]["refUpdates"][0]
        self.assertEqual(update["beforeOid"], B)
        self.assertEqual(update["afterOid"], "0" * 40)

    def test_checks_pin_app_head_and_success(self):
        self.s.require_checks(checks(B), B)
        for changes in (
            {"appId": 100},
            {"headSha": A},
            {"status": "in_progress"},
            {"conclusion": "skipped"},
            {"conclusion": "neutral"},
            {"conclusion": "failure"},
        ):
            report = checks(B)
            report["checks"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(p.Denied):
                self.s.require_checks(report, B)
        with self.assertRaises(p.Denied):
            self.s.require_checks({"commitSha": B, "checks": []}, B)

    def prepare_merge(self):
        self.s.raw_pr = AsyncMock(return_value=pull())
        self.s.head = AsyncMock(return_value=A)
        self.s.checks = AsyncMock(side_effect=lambda sha: checks(sha))
        self.gh.request.return_value = {"parents": [{"sha": A}, {"sha": B}]}
        self.gh.pages.return_value = [
            {
                "id": 1,
                "state": "APPROVED",
                "commit_id": B,
                "author_association": "OWNER",
                "user": {"login": "human"},
            }
        ]
        self.gh.graphql.side_effect = [
            evidence(),
            {"updateRefs": {"clientMutationId": None}},
        ]

    async def test_merge_disabled_by_default(self):
        self.s.settings = config()
        with self.assertRaisesRegex(p.Denied, "NOT_LIVE_QUALIFIED"):
            await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.gh.request.assert_not_called()

    async def test_merge_atomic_compares_head_and_base(self):
        self.prepare_merge()
        result = await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.assertEqual(result["commitSha"], C)
        refs = self.gh.graphql.call_args.args[1]["input"]["refUpdates"]
        self.assertEqual(
            [(x["beforeOid"], x["afterOid"], x["force"]) for x in refs],
            [(A, C, False), (B, B, False)],
        )

    async def test_merge_stale_base_no_mutation(self):
        self.prepare_merge()
        self.s.head.return_value = D
        with self.assertRaisesRegex(p.Denied, "STALE"):
            await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.gh.graphql.assert_not_called()

    async def test_merge_wrong_parents_no_mutation(self):
        self.prepare_merge()
        self.gh.request.return_value = {"parents": [{"sha": D}, {"sha": B}]}
        with self.assertRaisesRegex(p.Denied, "PARENTS"):
            await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.gh.graphql.assert_not_called()

    async def test_merge_stale_review_no_mutation(self):
        self.prepare_merge()
        self.gh.pages.return_value[0]["commit_id"] = A
        with self.assertRaisesRegex(p.Denied, "EXACT_HEAD_REVIEW"):
            await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.assertEqual(self.gh.graphql.await_count, 1)

    async def test_merge_unresolved_threads_no_mutation(self):
        self.prepare_merge()
        ev = evidence()
        ev["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]["isResolved"] = (
            False
        )
        self.gh.graphql.side_effect = [ev]
        with self.assertRaisesRegex(p.Denied, "UNRESOLVED"):
            await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.assertEqual(self.gh.graphql.await_count, 1)

    async def test_merge_native_rejection_no_fallback(self):
        self.prepare_merge()
        self.gh.graphql.side_effect = [evidence(), p.Denied("GITHUB_GRAPHQL_REJECTED")]
        with self.assertRaisesRegex(p.Denied, "GRAPHQL_REJECTED"):
            await self.s.merge(7, Merge(expectedHeadSha=B, expectedBaseSha=A))
        self.assertEqual(self.gh.request.await_count, 1)  # read computed commit only

    async def test_missing_native_policy_denies(self):
        ev = evidence()
        ev["repository"]["ref"]["branchProtectionRule"] = None
        self.gh.graphql.return_value = ev
        with self.assertRaisesRegex(p.Denied, "NATIVE_PROTECTION"):
            await self.s.review_evidence(7, Merge(expectedHeadSha=B, expectedBaseSha=A))


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = config(str(Path(self.tmp.name) / "journal.db"))
        self.service = AsyncMock()
        self.service.repository_status.return_value = {"mainSha": A}
        self.service.create_branch.return_value = {"branch": BRANCH, "commitSha": A}
        self.j = Journal(self.config.journal_path, self.config.journal_key, 20)
        self.client = TestClient(
            TestServer(create_app(self.config, self.service, self.j))
        )
        await self.client.start_server()
        self.auth = {"Authorization": "Bearer " + self.config.api_key}

    async def asyncTearDown(self):
        await self.client.close()
        self.j.close()
        self.tmp.cleanup()

    async def test_unauthenticated_denied(self):
        for header in (
            {},
            {"Authorization": "Basic x"},
            {"Authorization": "Bearer wrong"},
        ):
            response = await self.client.get("/v1/repository/status", headers=header)
            self.assertEqual(response.status, 401)
        self.service.repository_status.assert_not_called()

    async def test_authenticated_read_audited(self):
        response = await self.client.get("/v1/repository/status", headers=self.auth)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["data"]["mainSha"], A)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(
            self.j.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2
        )

    async def test_missing_idempotency_denied(self):
        response = await self.client.post(
            "/v1/branches", headers=self.auth, json={"branchName": BRANCH, "baseSha": A}
        )
        self.assertEqual(response.status, 422)
        self.service.create_branch.assert_not_called()

    async def test_unknown_fields_not_echoed(self):
        response = await self.client.post(
            "/v1/branches",
            headers=self.auth,
            json={"branchName": BRANCH, "baseSha": A, "token": "private-value"},
        )
        self.assertEqual(response.status, 422)
        self.assertNotIn("private-value", await response.text())

    async def test_concurrent_replay_executes_once(self):
        headers = self.auth
        body = {
            "branchName": BRANCH,
            "baseSha": A,
            "idempotencyKey": "test-operation-0001",
        }
        responses = await asyncio.gather(
            *[
                self.client.post("/v1/branches", headers=headers, json=body)
                for _ in range(2)
            ]
        )
        self.assertEqual([x.status for x in responses], [200, 200])
        self.assertEqual(self.service.create_branch.await_count, 1)

    async def test_upstream_failure_sanitized_and_not_replayed(self):
        self.service.create_branch.side_effect = RuntimeError("SECRET-UPSTREAM-DATA")
        headers = self.auth
        body = {
            "branchName": BRANCH,
            "baseSha": A,
            "idempotencyKey": "test-operation-0002",
        }
        response = await self.client.post("/v1/branches", headers=headers, json=body)
        self.assertEqual(response.status, 503)
        self.assertNotIn("SECRET", await response.text())
        response = await self.client.post("/v1/branches", headers=headers, json=body)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.service.create_branch.await_count, 1)

    async def test_journal_failure_prevents_write(self):
        self.j.begin = lambda *args: (_ for _ in ()).throw(
            sqlite3.OperationalError("sensitive")
        )
        response = await self.client.post(
            "/v1/branches",
            headers=self.auth,
            json={
                "branchName": BRANCH,
                "baseSha": A,
                "idempotencyKey": "test-operation-0003",
            },
        )
        self.assertEqual(response.status, 503)
        self.service.create_branch.assert_not_called()

    async def test_audit_failure_prevents_read(self):
        self.j.audit = lambda *args: (_ for _ in ()).throw(
            sqlite3.OperationalError("sensitive")
        )
        response = await self.client.get("/v1/repository/status", headers=self.auth)
        self.assertEqual(response.status, 503)
        self.service.repository_status.assert_not_called()

    async def test_result_journal_failure_does_not_repeat_remote_write(self):
        self.j.finish = lambda *args: (_ for _ in ()).throw(
            sqlite3.OperationalError("sensitive")
        )
        body = {
            "branchName": BRANCH,
            "baseSha": A,
            "idempotencyKey": "test-result-write-0001",
        }
        response = await self.client.post("/v1/branches", headers=self.auth, json=body)
        self.assertEqual(response.status, 503)
        response = await self.client.post("/v1/branches", headers=self.auth, json=body)
        self.assertEqual(response.status, 409)
        self.assertEqual(self.service.create_branch.await_count, 1)

    async def test_retry_key_validated_in_body(self):
        for key in ("short", "x" * 129, "unsafe/header/key"):
            response = await self.client.post(
                "/v1/branches",
                headers=self.auth,
                json={"branchName": BRANCH, "baseSha": A, "idempotencyKey": key},
            )
            self.assertEqual(response.status, 422)
        self.service.create_branch.assert_not_called()

    async def test_body_limit(self):
        response = await self.client.post(
            "/v1/branches",
            headers={**self.auth, "Content-Type": "application/json"},
            data="a" * 100001,
        )
        self.assertEqual(response.status, 413)

    async def test_unlisted_routes_unavailable(self):
        for route in ("/v1/runShell", "/v1/sql", "/v1/secrets", "/v1/proxy"):
            response = await self.client.post(route, headers=self.auth, json={})
            self.assertEqual(response.status, 404)

    async def test_schema_has_exact_catalog_and_security(self):
        response = await self.client.get("/openapi.json")
        self.assertEqual(response.status, 200)
        schema = await response.json()
        self.assertEqual(schema["security"], [{"GatewayBearer": []}])
        operations = [v for path in schema["paths"].values() for v in path.values()]
        self.assertEqual(len(operations), 13)
        self.assertEqual({x["operationId"] for x in operations}, {x[2] for x in ROUTES})
        self.assertIn("deleteTextFile", {x["operationId"] for x in operations})
        for operation in operations:
            if operation["x-openai-isConsequential"]:
                model_name = operation["requestBody"]["content"]["application/json"][
                    "schema"
                ]["$ref"].split("/")[-1]
                self.assertIn(
                    "idempotencyKey",
                    schema["components"]["schemas"][model_name]["required"],
                )
            self.assertFalse(any(x["in"] == "header" for x in operation["parameters"]))
            self.assertLessEqual(len(operation.get("description", "")), 300)

        def walk(item):
            if isinstance(item, dict):
                if "$ref" in item:
                    self.assertIn(
                        item["$ref"].split("/")[-1], schema["components"]["schemas"]
                    )
                for value in item.values():
                    walk(value)
            elif isinstance(item, list):
                for value in item:
                    walk(value)

        walk(schema)
        self.assertEqual(schema, openapi(self.config.public_url))


class GitHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_rejected_and_never_followed(self):
        client = MagicMock()
        client.request.return_value.__aenter__.return_value.status = 302
        gh = GitHub(config(), client=client)
        with self.assertRaisesRegex(p.Denied, "GITHUB_UNAVAILABLE"):
            await gh._send(
                "GET", "/repos/sohamsadegaonkar/Nexa_Care", "synthetic-token"
            )
        self.assertFalse(client.request.call_args.kwargs["allow_redirects"])
        self.assertEqual(
            client.request.call_args.args[1],
            "https://api.github.com/repos/sohamsadegaonkar/Nexa_Care",
        )

    async def test_upstream_stream_size_bound(self):
        async def chunks(size):
            for _ in range(31):
                yield b"x" * size

        client = MagicMock()
        response = client.request.return_value.__aenter__.return_value
        response.status = 200
        response.content.iter_chunked = chunks
        gh = GitHub(config(), client=client)
        with self.assertRaisesRegex(p.Denied, "GITHUB_RESPONSE_TOO_LARGE"):
            await gh._send(
                "GET", "/repos/sohamsadegaonkar/Nexa_Care", "synthetic-token"
            )

    async def test_pagination_exhaustion_denies(self):
        gh = GitHub(config(), client=AsyncMock())
        gh.request = AsyncMock(return_value=[{}] * 100)
        with self.assertRaisesRegex(p.Denied, "EVIDENCE_LIMIT"):
            await gh.pages("/pulls/7/reviews")

    async def test_graphql_errors_never_exposed(self):
        gh = GitHub(config(), client=AsyncMock())
        gh.credential = AsyncMock(return_value="test-only")
        gh._send = AsyncMock(
            return_value={
                "errors": [{"message": "private-token"}],
                "data": {"partial": True},
            }
        )
        with self.assertRaisesRegex(p.Denied, "GITHUB_GRAPHQL_REJECTED"):
            await gh.graphql(UPDATE_REFS, {})


if __name__ == "__main__":
    unittest.main()
