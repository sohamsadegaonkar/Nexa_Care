"""Fixed-host GitHub adapter. Only service-owned paths and GraphQL documents."""

import asyncio
import base64
import json
import time
from datetime import datetime

import aiohttp
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .policy import REPOSITORY, Denied
from .settings import Settings

REST = "/repos/" + REPOSITORY
UPDATE_REFS = (
    """mutation($input:UpdateRefsInput!){updateRefs(input:$input){clientMutationId}}"""
)
COMMIT_FILES = """mutation($input:CreateCommitOnBranchInput!){createCommitOnBranch(input:$input){commit{oid}}}"""
REVIEW = """query($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name){
 id pullRequest(number:$number){id headRefOid baseRefOid reviewDecision
 reviewThreads(first:100,after:$after){nodes{isResolved} pageInfo{hasNextPage endCursor}}}
 ref(qualifiedName:"refs/heads/main"){branchProtectionRule{
 requiresApprovingReviews requiredApprovingReviewCount dismissesStaleReviews
 requiresConversationResolution requiresStatusChecks requiresStrictStatusChecks
 requiredStatusCheckContexts isAdminEnforced allowsForcePushes allowsDeletions
 }}}}"""
DRAFT = """mutation($input:ConvertPullRequestToDraftInput!){convertPullRequestToDraft(input:$input){pullRequest{id}}}"""
READY = """mutation($input:MarkPullRequestReadyForReviewInput!){markPullRequestReadyForReview(input:$input){pullRequest{id}}}"""


class GitHub:
    def __init__(self, settings: Settings, client: aiohttp.ClientSession | None = None):
        self.settings = settings
        self.client = client or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10), trust_env=False
        )
        self.token = ""
        self.expires = 0.0
        self.token_lock = asyncio.Lock()

    async def close(self):
        await self.client.close()

    async def _send(
        self, method: str, path: str, bearer: str, *, body=None, params=None
    ):
        try:
            async with self.client.request(
                method,
                "https://api.github.com" + path,
                json=body,
                params=params,
                allow_redirects=False,
                headers={
                    "Authorization": "Bearer " + bearer,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2026-03-10",
                },
            ) as response:
                if response.status >= 300:
                    if response.status == 404:
                        raise Denied("GITHUB_NOT_FOUND", 404)
                    if response.status in {401, 403}:
                        raise Denied("GITHUB_ACCESS_DENIED", 502)
                    if response.status in {409, 422}:
                        raise Denied("GITHUB_PRECONDITION_REJECTED")
                    raise Denied("GITHUB_UNAVAILABLE", 502)
                raw = bytearray()
                async for block in response.content.iter_chunked(65536):
                    raw.extend(block)
                    if len(raw) > 2_000_000:
                        raise Denied("GITHUB_RESPONSE_TOO_LARGE", 502)
                data = json.loads(raw) if raw else {}
                if not isinstance(data, (dict, list)):
                    raise Denied("GITHUB_RESPONSE_INVALID", 502)
                return data
        except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError):
            raise Denied("GITHUB_UNAVAILABLE", 502) from None

    async def credential(self):
        async with self.token_lock:
            if self.token and time.time() + 60 < self.expires:
                return self.token
            now = int(time.time())
            try:
                signed = sign_app_jwt(
                    self.settings.private_key, self.settings.app_id, now
                )
                result = await self._send(
                    "POST",
                    f"/app/installations/{self.settings.installation_id}/access_tokens",
                    signed,
                    body={
                        "repositories": ["Nexa_Care"],
                        "permissions": {
                            "contents": "write",
                            "pull_requests": "write",
                            "checks": "read",
                            "actions": "read",
                            "metadata": "read",
                        },
                    },
                )
                self.token = result["token"]
                self.expires = datetime.fromisoformat(result["expires_at"]).timestamp()
            except Denied:
                raise
            except (
                ValueError,
                TypeError,
                KeyError,
                OverflowError,
                UnsupportedAlgorithm,
            ):
                raise Denied("GITHUB_CREDENTIAL_UNAVAILABLE", 503) from None
            return self.token

    async def request(self, method: str, suffix: str = "", *, body=None, params=None):
        return await self._send(
            method, REST + suffix, await self.credential(), body=body, params=params
        )

    async def graphql(self, query: str, variables: dict):
        result = await self._send(
            "POST",
            "/graphql",
            await self.credential(),
            body={"query": query, "variables": variables},
        )
        if result.get("errors") or not result.get("data"):
            raise Denied("GITHUB_GRAPHQL_REJECTED")
        return result["data"]

    async def pages(
        self, suffix: str, *, key: str | None = None, params: dict | None = None
    ):
        items = []
        for page in range(1, 11):
            result = await self.request(
                "GET", suffix, params={**(params or {}), "per_page": 100, "page": page}
            )
            batch = result[key] if key else result
            if not isinstance(batch, list):
                raise Denied("GITHUB_RESPONSE_INVALID", 502)
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise Denied("GITHUB_EVIDENCE_LIMIT_REACHED", 409)


def sign_app_jwt(private_key: str, app_id: str, now: int) -> str:
    """Standard RS256 JWS, using cryptography for the signing primitive."""

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    key = serialization.load_pem_private_key(private_key.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("GitHub App RSA key required")
    header = b64(b'{"alg":"RS256","typ":"JWT"}')
    payload = b64(
        json.dumps(
            {"iat": now - 60, "exp": now + 540, "iss": app_id}, separators=(",", ":")
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return (signing_input + b"." + b64(signature)).decode()
