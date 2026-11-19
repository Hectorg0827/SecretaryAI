"""Preflight assertions for the read-only Supabase replay auditor.

Synthetic fixtures only; no database, credentials, migrations, or network.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.audit_supabase_preview_replay import diagnose, main


def migrations(tmp_path, *versions):
    folder = tmp_path / "migrations"
    folder.mkdir()
    for version in versions:
        (folder / f"{version}_fixture.sql").write_text("select 1;")
    return folder


def healthy():
    version = "20240101000000"
    content = b"select 1;"
    git_blob_sha = hashlib.sha1(
        b"blob " + str(len(content)).encode("ascii") + b"\x00" + content
    ).hexdigest()
    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source_main_project_ref": "main-fixture",
        "source_preview_project_ref": "preview-fixture",
        "linked_github_branch": "fixture-branch",
        "candidate_commit": "fixture-commit",
        "main_sql_blob_sha": {version: git_blob_sha},
        "main_applied": [version],
        "linked_git_versions": [version],
        "linked_git_blob_sha": {version: git_blob_sha},
        "branch_applied": [version],
        "main_versions_without_sql": [],
        "branch_versions_without_sql": [],
        "main_public_tables": 3,
        "branch_public_tables": 3,
        "main_schema_objects": {
            "tables": 3, "views": 0, "sequences": 0, "functions": 1,
            "triggers": 1, "rls_policies": 1,
        },
        "branch_schema_objects": {
            "tables": 3, "views": 0, "sequences": 0, "functions": 1,
            "triggers": 1, "rls_policies": 1,
        },
        "main_schema_digests": {
            kind: "a" * 32 for kind in (
                "columns", "constraints", "indexes", "functions",
                "triggers", "policies", "rls_flags", "grants",
                "views", "sequences", "sequence_acls",
            )
        },
        "branch_schema_digests": {
            kind: "a" * 32 for kind in (
                "columns", "constraints", "indexes", "functions",
                "triggers", "policies", "rls_flags", "grants",
                "views", "sequences", "sequence_acls",
            )
        },
        "branch_relations": {
            "companies": True,
            "users": True,
            "stock_warehouses": True,
        },
        "branch_status": "FUNCTIONS_DEPLOYED",
    }


def test_clean_replay_is_eligible_for_follow_on_staging_checks(tmp_path):
    result = diagnose(healthy(), migrations(tmp_path, "20240101000000"))
    assert result["ready_for_staging"] is True
    assert result["issues"] == []
    assert result["main_versions_missing_in_candidate"] == []


def test_marked_initial_migrations_with_zero_schema_are_release_blocker(tmp_path):
    snapshot = healthy()
    snapshot["main_public_tables"] = 78
    snapshot["main_schema_objects"]["tables"] = 78
    snapshot["branch_public_tables"] = 0
    snapshot["branch_schema_objects"] = {
        "tables": 0, "views": 0, "sequences": 0, "functions": 0,
        "triggers": 0, "rls_policies": 0,
    }
    snapshot["branch_relations"]["companies"] = False
    snapshot["branch_relations"]["users"] = False
    snapshot["branch_relations"]["stock_warehouses"] = False
    snapshot["branch_status"] = "MIGRATIONS_FAILED"

    report = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert not report["ready_for_staging"]
    assert "applied_history_without_public_schema" in report["issues"]
    assert "missing_required_baseline_relations" in report["issues"]
    assert "branch_not_migration_ready" in report["issues"]
    assert len(report["missing_baseline_relations"]) == 3


def test_linked_git_and_candidate_migration_history_both_checked(tmp_path):
    snapshot = healthy()
    snapshot["main_applied"] += ["20261024041419"]
    report = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert not report["ready_for_staging"]
    assert report["main_versions_missing_in_candidate"] == ["20261024041419"]
    assert report["main_versions_missing_in_linked_git"] == ["20261024041419"]


def test_duplicate_version_files_fail_closed(tmp_path):
    folder = migrations(tmp_path, "20240101000000")
    (folder / "20240101000000_other.sql").write_text("select 1;")
    result = diagnose(healthy(), folder)
    assert "duplicate_migration_version:20240101000000" in result["issues"]


def test_invalid_filenames_fail_closed(tmp_path):
    folder = migrations(tmp_path, "20240101000000")
    (folder / "migration.sql").write_text("select 1;")
    result = diagnose(healthy(), folder)
    assert "invalid_migration_filename:migration.sql" in result["issues"]


@pytest.mark.parametrize("field,value", [
    ("main_applied", ["not-a-version"]),
    ("main_applied", ["20240101000000", "20240101000000"]),
    ("branch_public_tables", True),
    ("main_public_tables", -2),
    ("branch_relations", {}),
])
def test_invalid_metadata_rejected(field, value, tmp_path):
    snapshot = healthy()
    snapshot[field] = value
    with pytest.raises(ValueError):
        diagnose(snapshot, migrations(tmp_path, "20240101000000"))


def test_strict_cli_reports_drift_as_failed_check_not_success(tmp_path, capsys):
    data = healthy()
    data["branch_status"] = "MIGRATIONS_FAILED"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(data))
    path = migrations(tmp_path, "20240101000000")
    code = main([
        "--snapshot", str(snapshot),
        "--migrations", str(path),
        "--strict",
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert "branch_not_migration_ready" in report["issues"]


def test_partially_replayed_nonempty_schema_is_not_ready(tmp_path):
    snapshot = healthy()
    snapshot["main_public_tables"] = 78
    snapshot["main_schema_objects"]["tables"] = 78
    snapshot["branch_public_tables"] = 3
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert result["ready_for_staging"] is False
    assert "public_table_count_mismatch" in result["issues"]
    assert "applied_history_without_public_schema" not in result["issues"]


def test_branch_migration_subset_cannot_be_accepted_even_if_tables_match(tmp_path):
    snapshot = healthy()
    snapshot["main_applied"].append("20261017000000")
    snapshot["linked_git_versions"].append("20261017000000")
    folder = migrations(tmp_path, "20240101000000", "20261017000000")
    result = diagnose(snapshot, folder)
    assert result["ready_for_staging"] is False
    assert "branch_migration_versions_not_equal_to_main" in result["issues"]
    assert result["main_versions_missing_in_branch"] == ["20261017000000"]


def test_branch_extra_history_is_not_silently_trusted(tmp_path):
    snapshot = healthy()
    snapshot["branch_applied"].append("20261017000000")
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert result["ready_for_staging"] is False
    assert "branch_migration_versions_not_equal_to_main" in result["issues"]
    assert result["branch_versions_not_in_main"] == ["20261017000000"]


def test_candidate_extra_migration_requires_preview_replay_even_if_main_matches(
    tmp_path,
):
    # Regression from independent Codex review: a branch cannot be declared
    # ready when one new candidate migration has not run, despite a healthy
    # baseline and an otherwise exact matching main/linked history.
    snapshot = healthy()
    folder = migrations(tmp_path, "20240101000000", "20261024041419")
    result = diagnose(snapshot, folder)
    assert result["ready_for_staging"] is False
    assert "preview_migration_versions_not_equal_to_candidate" in result["issues"]
    assert result["candidate_versions_missing_in_branch"] == ["20261024041419"]


def test_preview_migration_not_in_candidate_is_review_blocker(tmp_path):
    snapshot = healthy()
    snapshot["branch_applied"].append("20261024041419")
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert "preview_migration_versions_not_equal_to_candidate" in result["issues"]
    assert result["branch_versions_not_in_candidate"] == ["20261024041419"]


def test_unexpected_linked_git_migration_cannot_pass_three_way_replay(tmp_path):
    # Regression from Codex P2 review: matching main and candidate files
    # cannot override an extra migration on Supabase's linked Git branch.
    snapshot = healthy()
    snapshot["linked_git_versions"].append("20261024041419")
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert result["ready_for_staging"] is False
    assert "linked_git_versions_not_in_main" in result["issues"]
    assert "linked_git_history_not_equal_to_candidate" in result["issues"]
    assert result["linked_git_versions_not_in_candidate"] == ["20261024041419"]


def test_unapplied_candidate_version_missing_from_linked_git_blocks(tmp_path):
    snapshot = healthy()
    folder = migrations(tmp_path, "20240101000000", "20261024041419")
    result = diagnose(snapshot, folder)
    assert result["ready_for_staging"] is False
    assert "linked_git_history_not_equal_to_candidate" in result["issues"]
    assert result["candidate_versions_not_in_linked_git"] == ["20261024041419"]


def test_metadata_with_missing_sql_definitions_blocks_branch_release(tmp_path):
    snapshot = healthy()
    snapshot["main_versions_without_sql"] = ["20240101000000"]
    snapshot["branch_versions_without_sql"] = ["20240101000000"]
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert result["ready_for_staging"] is False
    assert "main_migration_history_missing_sql_definitions" in result["issues"]
    assert "branch_migration_history_missing_sql_definitions" in result["issues"]
    assert result["counts"]["main_without_stored_sql"] == 1
    assert result["counts"]["branch_without_stored_sql"] == 1


@pytest.mark.parametrize("field", [
    "main_versions_without_sql", "branch_versions_without_sql",
])
def test_missing_sql_versions_must_exist_in_respective_history(field, tmp_path):
    snapshot = healthy()
    snapshot[field] = ["20261024041419"]
    with pytest.raises(ValueError, match="must belong"):
        diagnose(snapshot, migrations(tmp_path, "20240101000000"))


@pytest.mark.parametrize("field", [
    "main_versions_without_sql", "branch_versions_without_sql",
])
def test_missing_sql_metadata_is_required_to_prove_replay(field, tmp_path):
    snapshot = healthy()
    del snapshot[field]
    with pytest.raises(ValueError, match=field):
        diagnose(snapshot, migrations(tmp_path, "20240101000000"))


def test_same_migration_version_with_different_sql_is_release_blocker(tmp_path):
    snapshot = healthy()
    folder = migrations(tmp_path, "20240101000000")
    (folder / "20240101000000_fixture.sql").write_text("select 2;")
    result = diagnose(snapshot, folder)
    assert not result["ready_for_staging"]
    assert "linked_git_sql_content_differs_from_candidate" in result["issues"]
    assert result["linked_git_sql_content_mismatch_versions"] == [
        "20240101000000",
    ]


def test_missing_linked_git_blob_fingerprint_fails_closed(tmp_path):
    snapshot = healthy()
    snapshot["linked_git_blob_sha"] = {}
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert "linked_git_fingerprint_coverage_incomplete" in result["issues"]
    assert result["linked_git_fingerprint_missing_versions"] == [
        "20240101000000",
    ]


def test_invalid_fingerprint_metadata_rejected(tmp_path):
    snapshot = healthy()
    snapshot["linked_git_blob_sha"]["20240101000000"] = "invalid"
    with pytest.raises(ValueError, match="linked_git_blob_sha"):
        diagnose(snapshot, migrations(tmp_path, "20240101000000"))


def test_function_trigger_or_policy_drift_blocks_even_equal_table_count(tmp_path):
    snapshot = healthy()
    snapshot["branch_schema_objects"]["functions"] = 0
    snapshot["branch_schema_objects"]["rls_policies"] = 0
    result = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert result["ready_for_staging"] is False
    assert "public_schema_object_count_mismatch" in result["issues"]
    assert result["mismatched_schema_object_kinds"] == [
        "functions", "rls_policies",
    ]


def test_inconsistent_schema_table_counts_are_invalid_input(tmp_path):
    snapshot = healthy()
    snapshot["branch_schema_objects"]["tables"] = 99
    with pytest.raises(ValueError, match="contradicts public table count"):
        diagnose(snapshot, migrations(tmp_path, "20240101000000"))


def test_same_schema_object_counts_with_changed_column_definition_are_blocked(tmp_path):
    snapshot = healthy()
    snapshot["branch_schema_digests"]["columns"] = "b" * 32
    report = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert not report["ready_for_staging"]
    assert "public_schema_definition_or_grant_drift" in report["issues"]
    assert report["mismatched_schema_definition_or_grant_kinds"] == ["columns"]


def test_same_object_counts_with_broader_privileges_are_blocked(tmp_path):
    snapshot = healthy()
    snapshot["branch_schema_digests"]["grants"] = "b" * 32
    report = diagnose(snapshot, migrations(tmp_path, "20240101000000"))
    assert "public_schema_definition_or_grant_drift" in report["issues"]
    assert report["mismatched_schema_definition_or_grant_kinds"] == ["grants"]


def test_missing_or_invalid_schema_fingerprint_cannot_pass(tmp_path):
    snapshot = healthy()
    del snapshot["branch_schema_digests"]["policies"]
    with pytest.raises(ValueError, match="branch_schema_digests"):
        diagnose(snapshot, migrations(tmp_path, "20240101000000"))


def test_main_sql_provenance_mismatch_blocks_even_when_candidate_equals_git(tmp_path):
    data = healthy()
    data["main_sql_blob_sha"]["20240101000000"] = "f" * 40
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert "main_stored_sql_content_differs_from_candidate" in report["issues"]
    assert report["ready_for_staging"] is False


def test_missing_main_sql_provenance_blocks(tmp_path):
    data = healthy()
    data["main_sql_blob_sha"] = {}
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert "main_stored_sql_fingerprint_coverage_incomplete" in report["issues"]


def test_stale_observation_is_not_ready(tmp_path):
    data = healthy()
    data["observed_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=3)
    ).isoformat()
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert "stale_or_future_observation" in report["issues"]


def test_missing_timestamp_and_identity_fail_closed(tmp_path):
    data = healthy()
    data.pop("observed_at")
    data.pop("source_main_project_ref")
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert "utc_observation_timestamp_missing" in report["issues"]
    assert "snapshot_identity_missing:source_main_project_ref" in report["issues"]


def test_expected_identity_mismatch_is_not_ready(tmp_path):
    data = healthy()
    report = diagnose(
        data, migrations(tmp_path, "20240101000000"),
        expected_identity={
            "source_main_project_ref": "wrong",
            "source_preview_project_ref": "preview-fixture",
            "linked_github_branch": "fixture-branch",
            "candidate_commit": "fixture-commit",
        },
    )
    assert "snapshot_identity_mismatch:source_main_project_ref" in report["issues"]


def test_candidate_migration_is_allowed_only_with_expected_schema(tmp_path):
    data = healthy()
    version = "20261024041419"
    data["linked_git_versions"].append(version)
    data["linked_git_blob_sha"][version] = data["linked_git_blob_sha"]["20240101000000"]
    data["branch_applied"].append(version)
    data["branch_public_tables"] = 4
    data["branch_schema_objects"]["tables"] = 4
    data["candidate_schema_objects"] = dict(data["branch_schema_objects"])
    data["candidate_schema_digests"] = dict(data["branch_schema_digests"])
    result = diagnose(data, migrations(tmp_path, "20240101000000", version))
    assert result["ready_for_staging"] is True
    assert result["candidate_versions_not_in_linked_git"] == []
    del data["candidate_schema_digests"]
    no_proof = diagnose(data, migrations(tmp_path, "20240101000000", version))
    assert "candidate_schema_expectations_missing" in no_proof["issues"]


@pytest.mark.parametrize("kind", ["views", "sequences", "sequence_acls"])
def test_changed_view_or_sequence_definitions_do_not_pass(tmp_path, kind):
    data = healthy()
    data["branch_schema_digests"][kind] = "b" * 32
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert kind in report["mismatched_schema_definition_or_grant_kinds"]
    assert not report["ready_for_staging"]


def test_sequence_count_drift_fails_even_when_schema_digests_match(tmp_path):
    data = healthy()
    data["branch_schema_objects"]["sequences"] = 1
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert "sequences" in report["mismatched_schema_object_kinds"]


def test_grant_exception_requires_matching_explicit_review(tmp_path):
    data = healthy()
    data["branch_schema_digests"]["grants"] = "b" * 32
    data["grant_review"] = {
        "review_url": "https://github.com/example/repo/pull/9",
        "reviewer": "independent-reviewer",
        "approved_branch_grants_md5": "b" * 32,
    }
    folder = migrations(tmp_path, "20240101000000")
    denied = diagnose(data, folder)
    assert not denied["grant_exception_accepted"]
    assert "branch_grant_difference_requires_independent_approval" in denied["issues"]
    accepted = diagnose(
        data, folder,
        approved_grant_review_url=data["grant_review"]["review_url"],
    )
    assert accepted["grant_exception_accepted"]
    assert accepted["ready_for_staging"]


def test_migrations_passed_is_not_misreported_as_failed_replay(tmp_path):
    data = healthy()
    data["branch_status"] = "MIGRATIONS_PASSED"
    report = diagnose(data, migrations(tmp_path, "20240101000000"))
    assert report["migration_replay_reported_success"]
    assert "branch_not_migration_ready" not in report["issues"]
    assert "branch_functions_not_deployed" in report["issues"]
    assert not report["ready_for_staging"]


@pytest.mark.parametrize("status", [[], {}, 0, None])
def test_malformed_status_rejected_without_traceback(tmp_path, status):
    data = healthy()
    data["branch_status"] = status
    with pytest.raises(ValueError, match="branch_status"):
        diagnose(data, migrations(tmp_path, "20240101000000"))


def test_oversized_migration_rejected_before_memory_read(tmp_path, monkeypatch):
    folder = migrations(tmp_path, "20240101000000")
    migration = folder / "20240101000000_fixture.sql"
    migration.write_bytes(b"a" * (5 * 1024 * 1024 + 1))
    original = type(migration).read_bytes
    def protected_read(path):
        if path == migration:
            raise AssertionError("oversized migration must not be loaded")
        return original(path)
    monkeypatch.setattr(type(migration), "read_bytes", protected_read)
    report = diagnose(healthy(), folder)
    assert "oversized_migration_file:20240101000000" in report["issues"]
    assert not report["ready_for_staging"]


def test_strict_cli_requires_externally_bound_project_identity(tmp_path, capsys):
    data = healthy()
    path = migrations(tmp_path, "20240101000000")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(data))
    failed = main([
        "--snapshot", str(snapshot), "--migrations", str(path), "--strict",
    ])
    assert failed == 2
    capsys.readouterr()
    passed = main([
        "--snapshot", str(snapshot), "--migrations", str(path), "--strict",
        "--expect-main-project", "main-fixture",
        "--expect-preview-project", "preview-fixture",
        "--expect-linked-git", "fixture-branch",
        "--expect-candidate-commit", "fixture-commit",
    ])
    assert passed == 0
