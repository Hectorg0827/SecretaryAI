"""Tests for AccessRouter path chains and policy."""
import pytest

from app.router.access_router import AccessPath, AccessRouter
from app.router.policy import check_permission, requires_approval, PermissionClass


# ─── Path chain tests ──────────────────────────────────────────────────────────

class TestPathChains:
    def _chains(self):
        from app.router.access_router import _DEFAULT_PATH_CHAINS
        return _DEFAULT_PATH_CHAINS

    def test_inventory_starts_with_api(self):
        chains = self._chains()
        assert chains["inventory"][0] == AccessPath.API

    def test_inventory_ends_with_computer_use(self):
        chains = self._chains()
        assert chains["inventory"][-1] == AccessPath.COMPUTER_USE

    def test_customs_status_starts_with_playwright(self):
        chains = self._chains()
        assert chains["customs_status"][0] == AccessPath.PLAYWRIGHT

    def test_distributor_orders_starts_with_playwright(self):
        chains = self._chains()
        assert chains["distributor_orders"][0] == AccessPath.PLAYWRIGHT

    def test_orders_includes_playwright(self):
        chains = self._chains()
        assert AccessPath.PLAYWRIGHT in chains["orders"]

    def test_customers_chain(self):
        chains = self._chains()
        assert chains["customers"] == [AccessPath.API, AccessPath.COMPUTER_USE]

    def test_all_chains_end_with_computer_use(self):
        chains = self._chains()
        for cap, chain in chains.items():
            assert chain[-1] == AccessPath.COMPUTER_USE, (
                f"Chain for '{cap}' should end with COMPUTER_USE, got {chain[-1]}"
            )

    def test_no_chain_is_empty(self):
        chains = self._chains()
        for cap, chain in chains.items():
            assert len(chain) > 0, f"Chain for '{cap}' is empty"


# ─── Policy tests ──────────────────────────────────────────────────────────────

class TestCheckPermission:
    def test_owner_can_commit(self):
        assert check_permission("orders", "commit", "owner") is True

    def test_manager_can_commit(self):
        assert check_permission("orders", "commit", "manager") is True

    def test_back_office_can_commit(self):
        assert check_permission("orders", "commit", "back_office") is True

    def test_sales_rep_cannot_commit(self):
        assert check_permission("orders", "commit", "sales_rep") is False

    def test_viewer_cannot_commit(self):
        assert check_permission("orders", "commit", "viewer") is False

    def test_viewer_can_read(self):
        assert check_permission("inventory", "read", "viewer") is True

    def test_sales_rep_can_read(self):
        assert check_permission("inventory", "read", "sales_rep") is True

    def test_sales_rep_can_draft(self):
        assert check_permission("orders", "draft", "sales_rep") is True

    def test_viewer_cannot_draft(self):
        assert check_permission("orders", "draft", "viewer") is False

    def test_system_settings_always_prohibited(self):
        for role in ["owner", "manager", "back_office", "sales_rep", "viewer"]:
            assert check_permission("system_settings", "commit", role) is False

    def test_unknown_capability_defaults_to_read_permission(self):
        # Unknown capabilities default to READ class → all roles allowed
        assert check_permission("unknown_capability", "read", "viewer") is True


class TestRequiresApproval:
    def test_commit_requires_approval(self):
        assert requires_approval("orders", "commit") is True
        assert requires_approval("inventory", "commit") is True

    def test_read_does_not_require_approval(self):
        assert requires_approval("inventory", "read") is False

    def test_draft_does_not_require_approval(self):
        assert requires_approval("orders", "draft") is False
