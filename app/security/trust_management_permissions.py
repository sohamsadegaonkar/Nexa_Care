"""Server-owned organizational trust-management permission vocabulary."""

from enum import Enum


class TrustManagementPermission(str, Enum):
    PROFESSIONAL_REVIEW = "PROFESSIONAL_REVIEW"
    FACILITY_REVIEW = "FACILITY_REVIEW"
    AFFILIATION_MANAGE = "AFFILIATION_MANAGE"
    TRUST_PERMISSION_MANAGE = "TRUST_PERMISSION_MANAGE"


class TrustPermissionScope(str, Enum):
    GLOBAL = "GLOBAL"
    FACILITY = "FACILITY"


PERMISSION_SCOPES: dict[TrustManagementPermission, TrustPermissionScope] = {
    TrustManagementPermission.PROFESSIONAL_REVIEW: TrustPermissionScope.GLOBAL,
    TrustManagementPermission.FACILITY_REVIEW: TrustPermissionScope.FACILITY,
    TrustManagementPermission.AFFILIATION_MANAGE: TrustPermissionScope.FACILITY,
    TrustManagementPermission.TRUST_PERMISSION_MANAGE: TrustPermissionScope.GLOBAL,
}


def scope_for_permission(permission: TrustManagementPermission) -> TrustPermissionScope:
    if not isinstance(permission, TrustManagementPermission):
        raise TypeError("permission must be server-owned")
    return PERMISSION_SCOPES[permission]
