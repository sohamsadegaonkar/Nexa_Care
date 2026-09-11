"""Strict requests and bounded, explicit response envelopes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Branch = Annotated[str, Field(min_length=14, max_length=114)]
TextPath = Annotated[str, Field(min_length=1, max_length=240)]
Message = Annotated[str, Field(min_length=1, max_length=200)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FileWrite(Strict):
    branch: Branch
    path: TextPath
    content: Annotated[str, Field(max_length=48000)]
    expectedHeadSha: Sha
    expectedBlobSha: Sha | None  # null explicitly means create only
    commitMessage: Message


class FileDelete(Strict):
    branch: Branch
    path: TextPath
    expectedHeadSha: Sha
    expectedBlobSha: Sha
    commitMessage: Message


class Search(Strict):
    query: Annotated[str, Field(min_length=2, max_length=120)]
    ref: Annotated[str, Field(max_length=114)] = "main"
    pathPrefix: Annotated[str, Field(max_length=200)] = "app/"
    limit: Annotated[int, Field(ge=1, le=30)] = 10
    startAt: Annotated[int, Field(ge=0, le=20000)] = 0


class BranchCreate(Strict):
    branchName: Branch
    baseSha: Sha


class BranchDelete(Strict):
    branchName: Branch
    expectedSha: Sha
    mergedPullRequest: Annotated[int, Field(gt=0)]


class PullCreate(Strict):
    headBranch: Branch
    expectedHeadSha: Sha
    title: Message
    body: Annotated[str, Field(max_length=12000)]
    draft: bool = True


class PullUpdate(Strict):
    expectedHeadSha: Sha
    title: Message | None = None
    body: Annotated[str, Field(max_length=12000)] | None = None
    state: Literal["open", "closed"] | None = None
    draft: bool | None = None


class Merge(Strict):
    expectedHeadSha: Sha
    expectedBaseSha: Sha
    mergeMethod: Literal["merge"] = "merge"


class Result(BaseModel):
    repository: Literal["sohamsadegaonkar/Nexa_Care"] = "sohamsadegaonkar/Nexa_Care"
    data: dict
    untrustedRepositoryData: bool = True


class Error(BaseModel):
    error: str
