"""
SecretaryAI · Feature Factory — Validator (Layer 3)
===================================================
Before a generated feature can EVER run, it must pass this validator. This is
the machine check that backs up the human approval: the user approves a
plain-English capability list; this code proves the actual spec cannot do
anything beyond that list.

The validator is deterministic and has no AI in it. It cannot be talked out of
its decision. If the spec asks to do something not covered by a declared,
allowlisted capability -> hard error -> the feature is blocked.
"""
from __future__ import annotations

from . import capabilities as caps
from .contracts import (
    Capability,
    FeatureSpec,
    InterpretedFeature,
    ValidationIssue,
    ValidationResult,
)


def validate_interpreted(feature: InterpretedFeature) -> ValidationResult:
    """Validate the full interpreter output before showing it for approval."""
    issues: list[ValidationIssue] = []

    # 1. Every declared capability must be on the allowlist. -----------------
    declared_keys: set[str] = set()
    for cap in feature.declared_capabilities:
        declared_keys.add(cap.capability_key)
        if not caps.is_registered(cap.capability_key):
            issues.append(ValidationIssue(
                severity="error",
                code="UNKNOWN_CAPABILITY",
                message=f"'{cap.capability_key}' is not an allowed capability.",
            ))
        else:
            # The declared scope must match what the allowlist says — no
            # quietly declaring 'read' for something that actually writes.
            true_class = caps.action_class_for(cap.capability_key)
            if cap.scope != true_class:
                issues.append(ValidationIssue(
                    severity="error",
                    code="SCOPE_MISMATCH",
                    message=(f"'{cap.capability_key}' is declared as {cap.scope.value} "
                             f"but is actually {true_class.value}."),
                ))

    # 2. The spec must only use capabilities that were declared. -------------
    issues.extend(_validate_spec_against_declared(feature.spec, declared_keys))

    # 3. Tier sanity: this module only EXECUTES Tier 1. Tier 2/3 are gated
    #    elsewhere and must not reach the engine yet.
    if feature.tier != 1:
        issues.append(ValidationIssue(
            severity="warning",
            code="NON_TIER1",
            message=f"Tier {feature.tier} features require the sandbox/review path, not the rule engine.",
        ))

    ok = not any(i.severity == "error" for i in issues)
    return ValidationResult(ok=ok, issues=issues)


def _validate_spec_against_declared(spec: FeatureSpec, declared_keys: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    # 2a. Reading the source entity needs the matching read capability. ------
    read_cap = caps.SOURCE_ENTITY_TO_READ_CAPABILITY.get(spec.source_entity)
    if read_cap is None:
        issues.append(ValidationIssue(
            severity="error",
            code="UNKNOWN_SOURCE",
            message=f"'{spec.source_entity}' is not a readable source.",
        ))
    elif read_cap not in declared_keys:
        issues.append(ValidationIssue(
            severity="error",
            code="MISSING_READ_GRANT",
            message=f"Reading {spec.source_entity} needs the '{read_cap}' capability, "
                    f"which was not declared.",
        ))

    # 2b. Every action needs its backing capability declared. ----------------
    for action in spec.actions:
        needed = caps.ACTION_TYPE_TO_CAPABILITY.get(action.type)
        if needed is None:
            issues.append(ValidationIssue(
                severity="error",
                code="UNKNOWN_ACTION",
                message=f"Action '{action.type}' is not a known action type.",
            ))
            continue
        if needed not in declared_keys:
            issues.append(ValidationIssue(
                severity="error",
                code="MISSING_ACTION_GRANT",
                message=f"Action '{action.type}' needs the '{needed}' capability, "
                        f"which was not declared.",
            ))

    return issues


def validate_against_grants(spec: FeatureSpec, granted: list[Capability]) -> ValidationResult:
    """
    Re-check at RUN TIME against what the human actually approved (the grants
    stored in ff_capability_grants). Belt-and-suspenders: even if the stored
    spec were tampered with, it cannot exceed the granted capabilities.
    """
    granted_keys = {g.capability_key for g in granted}
    return ValidationResult(
        ok=not _validate_spec_against_declared(spec, granted_keys),
        issues=_validate_spec_against_declared(spec, granted_keys),
    )
