"""Preflight assertions for the read-only Supabase replay auditor.

Synthetic fixtures only; no database, credentials, migrations, or network.
"""
import hashlib
import json

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
        "main_applied": [version],
        "linked_git_versions": [version],
        "linked_git_blob_sha": {version: git_blob_sha},
        "branch_applied": [version],
        "main_versions_without_sql": [],
        "branch_versions_without_sql": [],
        "main_public_tables": 3,
        "branch_public_tables": 3,
        "main_schema_objects": {
            "tables": 3, "views": 0, "functions": 1,
            "triggers": 1, "rls_policies": 1,
        },
        "branch_schema_objects": {
            "tables": 3, "views": 0, "functions": 1,
            "triggers": 1, "rls_policies": 1,
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
        "tables": 0, "views": 0, "functions": 0,
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
