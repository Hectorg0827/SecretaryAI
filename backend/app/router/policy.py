from enum import Enum

# Roles ordered from most to least privileged
_ROLE_RANK = {
    "owner": 4,
    "manager": 3,
    "back_office": 2,
    "sales_rep": 1,
    "viewer": 0,
}


class PermissionClass(Enum):
    READ = "read"
    DRAFT = "draft"
    COMMIT = "commit"
    PROHIBITED = "prohibited"


# (capability, operation_type) → PermissionClass
CAPABILITY_PERMISSIONS: dict[tuple[str, str], PermissionClass] = {
    # reads
    ("inventory", "read"): PermissionClass.READ,
    ("orders", "read"): PermissionClass.READ,
    ("customers", "read"): PermissionClass.READ,
    ("customs_status", "read"): PermissionClass.READ,
    ("distributor_orders", "read"): PermissionClass.READ,
    ("report_download", "read"): PermissionClass.READ,
    # drafts
    ("inventory", "draft"): PermissionClass.DRAFT,
    ("orders", "draft"): PermissionClass.DRAFT,
    ("report_download", "draft"): PermissionClass.DRAFT,
    # commits
    ("orders", "commit"): PermissionClass.COMMIT,
    ("inventory", "commit"): PermissionClass.COMMIT,
    ("report_download", "commit"): PermissionClass.COMMIT,
    # always prohibited
    ("system_settings", "read"): PermissionClass.PROHIBITED,
    ("system_settings", "draft"): PermissionClass.PROHIBITED,
    ("system_settings", "commit"): PermissionClass.PROHIBITED,
    ("terminal", "read"): PermissionClass.PROHIBITED,
    ("terminal", "commit"): PermissionClass.PROHIBITED,
    ("record_delete", "commit"): PermissionClass.PROHIBITED,
}

# Minimum role rank required per PermissionClass
_REQUIRED_RANK: dict[PermissionClass, int] = {
    PermissionClass.READ: 0,    # all roles
    PermissionClass.DRAFT: 1,   # sales_rep and above
    PermissionClass.COMMIT: 2,  # back_office and above
    PermissionClass.PROHIBITED: 99,  # nobody
}


def check_permission(capability: str, operation_type: str, user_role: str) -> bool:
    """Return True if user_role may perform capability+operation_type."""
    perm_class = CAPABILITY_PERMISSIONS.get(
        (capability, operation_type), PermissionClass.READ
    )
    if perm_class == PermissionClass.PROHIBITED:
        return False
    role_rank = _ROLE_RANK.get(user_role, 0)
    required = _REQUIRED_RANK.get(perm_class, 99)
    return role_rank >= required


def requires_approval(capability: str, operation_type: str) -> bool:
    """Return True for any COMMIT operation."""
    perm_class = CAPABILITY_PERMISSIONS.get(
        (capability, operation_type), PermissionClass.READ
    )
    return perm_class == PermissionClass.COMMIT
