"""Typed repository operations. Upstream content never grants authority."""

import base64
from urllib.parse import quote

from . import policy as p
from .github import COMMIT_FILES, DRAFT, READY, REVIEW, UPDATE_REFS, GitHub
from .models import (
    BranchCreate,
    BranchDelete,
    FileDelete,
    FileWrite,
    Merge,
    PullCreate,
    PullUpdate,
    Search,
)
from .settings import Settings


class Service:
    def __init__(self, github: GitHub, settings: Settings):
        self.gh = github
        self.settings = settings

    async def head(self, branch: str) -> str:
        result = await self.gh.request(
            "GET", "/git/ref/heads/" + quote(branch, safe="")
        )
        if result["object"]["type"] != "commit":
            raise p.Denied("BRANCH_NOT_COMMIT")
        return p.sha(result["object"]["sha"])

    async def resolve(self, ref: str) -> str:
        p.ref(ref)
        return ref if p.SHA_RE.fullmatch(ref) else await self.head(ref)

    async def repository_status(self):
        head = await self.head(p.MAIN)
        pulls = await self.gh.request(
            "GET", "/pulls", params={"state": "open", "base": "main", "per_page": 30}
        )
        branches = await self.gh.request("GET", "/branches", params={"per_page": 100})
        return {
            "mainSha": head,
            "openPullRequests": [
                {
                    "number": x["number"],
                    "headSha": x["head"]["sha"],
                    "draft": x["draft"],
                }
                for x in pulls
            ],
            "branches": [
                {"name": p.clean_text(x["name"]), "sha": x["commit"]["sha"]}
                for x in branches
                if x["name"] == "main" or x["name"].startswith(p.PREFIX)
            ],
            "listingMayBeIncomplete": len(pulls) == 30 or len(branches) == 100,
            "mergeEnabled": self.settings.merge_enabled,
        }

    async def tree(self, commit: str):
        result = await self.gh.request(
            "GET", "/git/trees/" + p.sha(commit), params={"recursive": "1"}
        )
        if result.get("truncated") or len(result["tree"]) > 20000:
            raise p.Denied("TREE_INCOMPLETE")
        return result["tree"]

    async def entry(
        self, path: str, commit: str, *, missing_ok=False, max_bytes=p.MAX_FILE_BYTES
    ):
        entries = await self.tree(commit)
        found = next((x for x in entries if x["path"] == path), None)
        if found is None and missing_ok:
            # Also reject an existing symlink/submodule in any parent path.
            parents = {
                "/".join(path.split("/")[:i]) for i in range(1, len(path.split("/")))
            }
            if any(x["path"] in parents and x["type"] != "tree" for x in entries):
                raise p.Denied("NON_REGULAR_FILE", 403)
            return None
        if found is None:
            raise p.Denied("FILE_NOT_FOUND", 404)
        if found["type"] != "blob" or found["mode"] not in {"100644", "100755"}:
            raise p.Denied("NON_REGULAR_FILE", 403)
        if found.get("size", max_bytes + 1) > max_bytes:
            raise p.Denied("FILE_TOO_LARGE", 413)
        return found

    async def blob_text(self, entry: dict, *, max_bytes=p.MAX_FILE_BYTES):
        blob = await self.gh.request("GET", "/git/blobs/" + p.sha(entry["sha"]))
        if (
            blob.get("encoding") != "base64"
            or blob.get("size", max_bytes + 1) > max_bytes
        ):
            raise p.Denied("TEXT_SIZE_OR_ENCODING_DENIED", 422)
        try:
            data = base64.b64decode(
                blob["content"].replace("\n", ""), validate=True
            ).decode("utf-8")
        except (ValueError, UnicodeError):
            raise p.Denied("TEXT_SIZE_OR_ENCODING_DENIED", 422) from None
        return p.clean_text(data, max_bytes=max_bytes)

    async def get_file(self, path: str, ref: str, start_at: int = 0):
        p.path(path)
        commit = await self.resolve(ref)
        entry = await self.entry(path, commit, max_bytes=p.MAX_READ_BYTES)
        content = await self.blob_text(entry, max_bytes=p.MAX_READ_BYTES)
        if not 0 <= start_at <= len(content):
            raise p.Denied("INVALID_OFFSET", 422)
        end = min(start_at + 6000, len(content))
        return {
            "path": path,
            "commitSha": commit,
            "blobSha": entry["sha"],
            "content": content[start_at:end],
            "startAt": start_at,
            "nextStartAt": end if end < len(content) else None,
            "complete": end == len(content),
            "totalCharacters": len(content),
        }

    async def search(self, request: Search):
        # Literal, case-sensitive, bounded search. Never forward query operators.
        p.clean_text(request.query)
        prefix = request.pathPrefix
        if prefix:
            p.path(prefix.rstrip("/") + "/probe.py")
        commit = await self.resolve(request.ref)
        entries = []
        for entry in await self.tree(commit):
            if (
                entry["type"] != "blob"
                or entry["mode"] not in {"100644", "100755"}
                or not entry["path"].startswith(prefix)
                or entry.get("size", 999999) > p.MAX_FILE_BYTES
            ):
                continue
            try:
                p.path(entry["path"])
                entries.append(entry)
            except p.Denied:
                continue
        entries.sort(key=lambda x: x["path"])
        results = []
        end = request.startAt
        for entry in entries[request.startAt : request.startAt + 12]:
            end += 1
            try:
                content = await self.blob_text(entry)
            except p.Denied as error:
                if error.code in {
                    "SENSITIVE_CONTENT_DENIED",
                    "TEXT_SIZE_OR_ENCODING_DENIED",
                }:
                    continue
                raise
            if request.query in content:
                # No source snippets: getFile is the dedicated content boundary.
                results.append(
                    {
                        "path": entry["path"],
                        "blobSha": entry["sha"],
                        "lines": [
                            i
                            for i, line in enumerate(content.splitlines(), 1)
                            if request.query in line
                        ][:20],
                    }
                )
            if len(results) >= request.limit:
                break
        return {
            "commitSha": commit,
            "matches": results,
            "complete": end >= len(entries),
            "nextStartAt": end if end < len(entries) else None,
            "searchScope": "allowed UTF-8 source files; blocked and oversized files excluded",
        }

    async def write_file(self, request: FileWrite | FileDelete):
        p.branch(request.branch)
        p.path(request.path, write=True)
        p.clean_text(request.commitMessage)
        if isinstance(request, FileWrite):
            p.clean_text(request.content)
        if await self.head(request.branch) != request.expectedHeadSha:
            raise p.Denied("STALE_BRANCH_HEAD")
        current = await self.entry(
            request.path,
            request.expectedHeadSha,
            missing_ok=isinstance(request, FileWrite),
        )
        actual = current["sha"] if current else None
        if actual != request.expectedBlobSha:
            raise p.Denied("STALE_FILE_BLOB")
        changes = (
            {"deletions": [{"path": request.path}]}
            if isinstance(request, FileDelete)
            else {
                "additions": [
                    {
                        "path": request.path,
                        "contents": base64.b64encode(request.content.encode()).decode(),
                    }
                ]
            }
        )
        result = await self.gh.graphql(
            COMMIT_FILES,
            {
                "input": {
                    "branch": {
                        "repositoryNameWithOwner": p.REPOSITORY,
                        "branchName": request.branch,
                    },
                    "expectedHeadOid": request.expectedHeadSha,
                    "message": {"headline": request.commitMessage},
                    "fileChanges": changes,
                }
            },
        )
        return {
            "branch": request.branch,
            "commitSha": p.sha(result["createCommitOnBranch"]["commit"]["oid"]),
        }

    async def create_branch(self, request: BranchCreate):
        p.branch(request.branchName)
        if await self.head("main") != request.baseSha:
            raise p.Denied("STALE_BASE_SHA")
        repo = await self.gh.request("GET")
        await self.gh.graphql(
            UPDATE_REFS,
            {
                "input": {
                    "repositoryId": repo["node_id"],
                    "refUpdates": [
                        {
                            "name": "refs/heads/main",
                            "beforeOid": request.baseSha,
                            "afterOid": request.baseSha,
                            "force": False,
                        },
                        {
                            "name": "refs/heads/" + request.branchName,
                            "beforeOid": "0" * 40,
                            "afterOid": request.baseSha,
                            "force": False,
                        },
                    ],
                }
            },
        )
        return {"branch": request.branchName, "commitSha": request.baseSha}

    async def raw_pr(self, number: int):
        pr = await self.gh.request("GET", f"/pulls/{number}")
        if (
            pr["base"]["repo"]["full_name"] != p.REPOSITORY
            or not pr["head"].get("repo")
            or pr["head"]["repo"]["full_name"] != p.REPOSITORY
            or pr["base"]["ref"] != "main"
        ):
            raise p.Denied("PR_SCOPE_DENIED", 403)
        return pr

    async def get_pr(self, number: int):
        pr = await self.raw_pr(number)
        return {
            "number": pr["number"],
            "title": p.clean_text(pr["title"]),
            "body": p.clean_text(pr.get("body") or ""),
            "state": pr["state"],
            "draft": pr["draft"],
            "merged": pr["merged"],
            "headBranch": p.clean_text(pr["head"]["ref"]),
            "headSha": pr["head"]["sha"],
            "baseSha": pr["base"]["sha"],
            "mergeable": pr["mergeable"],
            "mergeState": pr["mergeable_state"],
            "url": f"https://github.com/{p.REPOSITORY}/pull/{number}",
        }

    async def create_pr(self, request: PullCreate):
        p.branch(request.headBranch)
        p.clean_text(request.title)
        p.clean_text(request.body)
        if await self.head(request.headBranch) != request.expectedHeadSha:
            raise p.Denied("STALE_BRANCH_HEAD")
        result = await self.gh.request(
            "POST",
            "/pulls",
            body={
                "head": request.headBranch,
                "base": "main",
                "title": request.title,
                "body": request.body,
                "draft": request.draft,
            },
        )
        return {
            "number": result["number"],
            "url": f"https://github.com/{p.REPOSITORY}/pull/{result['number']}",
        }

    async def update_pr(self, number: int, request: PullUpdate):
        pr = await self.raw_pr(number)
        p.branch(pr["head"]["ref"])
        if pr["head"]["sha"] != request.expectedHeadSha:
            raise p.Denied("STALE_PR_HEAD")
        if pr["merged"]:
            raise p.Denied("PR_ALREADY_MERGED")
        body = request.model_dump(
            exclude_none=True, exclude={"expectedHeadSha", "draft"}
        )
        for field in ("title", "body"):
            if field in body:
                p.clean_text(body[field])
        if body and request.draft is not None:
            raise p.Denied("SEPARATE_DRAFT_AND_METADATA_UPDATES", 422)
        if request.draft is not None:
            await self.gh.graphql(
                DRAFT if request.draft else READY,
                {"input": {"pullRequestId": pr["node_id"]}},
            )
        elif body:
            await self.gh.request("PATCH", f"/pulls/{number}", body=body)
        else:
            raise p.Denied("EMPTY_UPDATE", 422)
        return {"number": number, "metadataUpdated": True}

    async def delete_branch(self, request: BranchDelete):
        p.branch(request.branchName)
        pr = await self.raw_pr(request.mergedPullRequest)
        if (
            not pr["merged"]
            or pr["head"]["ref"] != request.branchName
            or pr["head"]["sha"] != request.expectedSha
        ):
            raise p.Denied("MERGED_BRANCH_PROOF_REQUIRED")
        if await self.head(request.branchName) != request.expectedSha:
            raise p.Denied("STALE_BRANCH_HEAD")
        open_prs = await self.gh.request(
            "GET",
            "/pulls",
            params={
                "state": "open",
                "head": "sohamsadegaonkar:" + request.branchName,
                "per_page": 1,
            },
        )
        if open_prs:
            raise p.Denied("BRANCH_HAS_OPEN_PR")
        repo = await self.gh.request("GET")
        await self.gh.graphql(
            UPDATE_REFS,
            {
                "input": {
                    "repositoryId": repo["node_id"],
                    "refUpdates": [
                        {
                            "name": "refs/heads/" + request.branchName,
                            "beforeOid": request.expectedSha,
                            "afterOid": "0" * 40,
                            "force": False,
                        }
                    ],
                }
            },
        )
        return {
            "branch": request.branchName,
            "deletedSha": request.expectedSha,
            "deleted": True,
        }

    async def checks(self, sha: str):
        p.sha(sha)
        runs = await self.gh.pages(
            f"/commits/{sha}/check-runs", key="check_runs", params={"filter": "latest"}
        )
        statuses = await self.gh.pages(f"/commits/{sha}/statuses")
        return {
            "commitSha": sha,
            "checks": [
                {
                    "id": x["id"],
                    "name": p.clean_text(x["name"]),
                    "status": x["status"],
                    "conclusion": x["conclusion"],
                    "appId": x["app"]["id"],
                    "headSha": x["head_sha"],
                }
                for x in runs
            ],
            "statuses": [
                {"context": p.clean_text(x["context"]), "state": x["state"]}
                for x in statuses
            ],
        }

    async def workflow(self, run_id: int):
        run = await self.gh.request("GET", f"/actions/runs/{run_id}")
        if run["repository"]["full_name"] != p.REPOSITORY:
            raise p.Denied("WORKFLOW_SCOPE_DENIED", 403)
        jobs = await self.gh.pages(
            f"/actions/runs/{run_id}/jobs", key="jobs", params={"filter": "latest"}
        )
        return {
            "id": run["id"],
            "headSha": run["head_sha"],
            "status": run["status"],
            "conclusion": run["conclusion"],
            "jobs": [
                {
                    "id": x["id"],
                    "name": p.clean_text(x["name"]),
                    "status": x["status"],
                    "conclusion": x["conclusion"],
                    "steps": [
                        {
                            "number": s["number"],
                            "name": p.clean_text(s["name"]),
                            "status": s["status"],
                            "conclusion": s.get("conclusion"),
                        }
                        for s in x.get("steps", [])
                    ],
                }
                for x in jobs
            ],
        }

    def require_checks(self, report: dict, expected: str):
        if not self.settings.required_checks:
            raise p.Denied("REQUIRED_CHECK_POLICY_MISSING")
        if report["commitSha"] != expected:
            raise p.Denied("CHECK_SHA_MISMATCH")
        for name, app_id in self.settings.required_checks.items():
            matching = [
                x
                for x in report["checks"]
                if x["name"] == name and x["appId"] == app_id
            ]
            if not matching or any(
                x["headSha"] != expected
                or x["status"] != "completed"
                or x["conclusion"] != "success"
                for x in matching
            ):
                raise p.Denied("REQUIRED_CHECK_NOT_GREEN")

    async def review_evidence(self, number: int, request: Merge):
        cursor = None
        repo = None
        for _ in range(10):
            data = await self.gh.graphql(
                REVIEW,
                {
                    "owner": "sohamsadegaonkar",
                    "name": "Nexa_Care",
                    "number": number,
                    "after": cursor,
                },
            )
            repo = data["repository"]
            pr = repo["pullRequest"]
            if (
                pr["headRefOid"] != request.expectedHeadSha
                or pr["baseRefOid"] != request.expectedBaseSha
            ):
                raise p.Denied("STALE_PR_HEAD_OR_BASE")
            if pr["reviewDecision"] != "APPROVED":
                raise p.Denied("REVIEW_NOT_APPROVED")
            threads = pr["reviewThreads"]
            if any(x["isResolved"] is not True for x in threads["nodes"]):
                raise p.Denied("UNRESOLVED_REVIEW_THREADS")
            if not threads["pageInfo"]["hasNextPage"]:
                break
            new_cursor = threads["pageInfo"]["endCursor"]
            if not new_cursor or new_cursor == cursor:
                raise p.Denied("REVIEW_EVIDENCE_INCOMPLETE")
            cursor = new_cursor
        else:
            raise p.Denied("REVIEW_EVIDENCE_INCOMPLETE")
        rule = (repo.get("ref") or {}).get("branchProtectionRule")
        required_flags = (
            "requiresApprovingReviews",
            "dismissesStaleReviews",
            "requiresConversationResolution",
            "requiresStatusChecks",
            "requiresStrictStatusChecks",
            "isAdminEnforced",
        )
        if (
            not rule
            or any(rule.get(x) is not True for x in required_flags)
            or rule.get("allowsForcePushes") is not False
            or rule.get("allowsDeletions") is not False
        ):
            raise p.Denied("NATIVE_PROTECTION_REQUIRED")
        native = set(rule.get("requiredStatusCheckContexts") or [])
        if not native or not native.issubset(self.settings.required_checks):
            raise p.Denied("REQUIRED_CHECK_POLICY_INCOMPLETE")
        return repo["id"], max(1, rule["requiredApprovingReviewCount"])

    async def merge(self, number: int, request: Merge):
        if not self.settings.merge_enabled:
            raise p.Denied("MERGE_NOT_LIVE_QUALIFIED", 403)
        pr = await self.raw_pr(number)
        p.branch(pr["head"]["ref"])
        if (
            pr["head"]["sha"] != request.expectedHeadSha
            or pr["base"]["sha"] != request.expectedBaseSha
            or await self.head("main") != request.expectedBaseSha
        ):
            raise p.Denied("STALE_PR_HEAD_OR_BASE")
        if (
            pr["state"] != "open"
            or pr["draft"]
            or pr["merged"]
            or pr["mergeable"] is not True
        ):
            raise p.Denied("PR_NOT_MERGEABLE")
        merge_sha = p.sha(pr["merge_commit_sha"])
        candidate = await self.gh.request("GET", "/git/commits/" + merge_sha)
        if [x["sha"] for x in candidate["parents"]] != [
            request.expectedBaseSha,
            request.expectedHeadSha,
        ]:
            raise p.Denied("MERGE_COMMIT_PARENTS_MISMATCH")
        self.require_checks(
            await self.checks(request.expectedHeadSha), request.expectedHeadSha
        )
        self.require_checks(await self.checks(merge_sha), merge_sha)
        repo_id, required_reviews = await self.review_evidence(number, request)
        reviews = await self.gh.pages(f"/pulls/{number}/reviews")
        latest = {}
        for review in sorted(reviews, key=lambda x: x["id"]):
            if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest[review["user"]["login"]] = review
        if any(x["state"] == "CHANGES_REQUESTED" for x in latest.values()):
            raise p.Denied("CHANGES_REQUESTED")
        approvals = [
            x
            for login, x in latest.items()
            if login != pr["user"]["login"]
            and x["state"] == "APPROVED"
            and x["commit_id"] == request.expectedHeadSha
            and x["author_association"] in {"OWNER", "MEMBER", "COLLABORATOR"}
        ]
        if len(approvals) < required_reviews:
            raise p.Denied("EXACT_HEAD_REVIEW_REQUIRED")
        # GitHub evaluates native protection at mutation time. No bypass actor,
        # force push, ordinary merge fallback or protection edits are used.
        await self.gh.graphql(
            UPDATE_REFS,
            {
                "input": {
                    "repositoryId": repo_id,
                    "refUpdates": [
                        {
                            "name": "refs/heads/main",
                            "beforeOid": request.expectedBaseSha,
                            "afterOid": merge_sha,
                            "force": False,
                        },
                        {
                            "name": "refs/heads/" + pr["head"]["ref"],
                            "beforeOid": request.expectedHeadSha,
                            "afterOid": request.expectedHeadSha,
                            "force": False,
                        },
                    ],
                }
            },
        )
        return {
            "number": number,
            "commitSha": merge_sha,
            "status": "REF_UPDATED_VERIFY_PR_STATE",
        }
