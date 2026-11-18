"""Preflight assertions for the read-only Supabase replay auditor.

Synthetic fixtures only; no database, credentials, migrations, or network.
"""
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
    return {
        "main_applied": [version],
        "linked_git_versions": [version],
        "branch_applied": [version],
        "main_public_tables": 3,
        "branch_public_tables": 3,
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
    snapshot["branch_public_tables"] = 0
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
