"""
SecretaryAI · Feature Factory — Service Orchestrator
====================================================
The conductor. It is the ONLY entry point the API and Celery use. It enforces
the safety layers in order and writes the audit trail for every privileged step.

Flow it governs:
  1. create_feature   — interpret (AI) -> validate (machine) -> store as
                        PENDING_CAPABILITY_APPROVAL. Generation is only ever
                        invoked here, from an authenticated request (Layer 2).
  2. approve_capabilities — human approves the plain-English list -> store
                        grants -> DRY_RUN_READY.
  3. dry_run          — safe preview; changes nothing (Layer 5).
  4. promote_to_observe / promote_to_active — progressive trust (Layer 6).
  5. run_feature      — execute in observe/act, with the circuit breaker and
                        approval routing (Layers 6 & 7).
  6. decide_approval  — human signs off a commit/destructive action.
  7. set_kill_switch  — tenant master off switch (Layer 7).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from .audit import AuditLogger
from .contracts import (
    Capability,
    FeatureSpec,
    FeatureStatus,
    InterpretedFeature,
    RunMode,
    RunStatus,
    ValidationResult,
)
from .engine import BusinessDataGateway, EffectExecutor, RuleEngine
from .interpreter import interpret as ai_interpret
from .repository import FeatureRepository
from .validator import validate_against_grants, validate_interpreted


class PolicyError(Exception):
    """Raised when an action is refused by policy (kill switch, limits, tier)."""


class FeatureFactoryService:
    def __init__(
        self,
        repo: FeatureRepository,
        audit: AuditLogger,
        data_gateway: BusinessDataGateway,
        effect_executor: EffectExecutor,
        tenant_id: UUID,
    ):
        self._repo = repo
        self._audit = audit
        self._data = data_gateway
        self._effects = effect_executor
        self._tenant_id = tenant_id

    # ----------------------------------------------------------------- 1.
    async def create_feature(self, *, request_text: str, user_id: UUID) -> dict:
        """Interpret + validate a plain-English request. Stores it as pending."""
        # Policy: master kill switch and per-tenant feature cap.
        if not await self._repo.is_factory_enabled():
            raise PolicyError("The Feature Factory is turned off for this account.")
        if await self._repo.feature_count() >= 50:  # CONFIRM: read from ff_tenant_settings.max_features
            raise PolicyError("You've reached the maximum number of features for this account.")

        interpreted: InterpretedFeature = ai_interpret(request_text)

        # Machine validation (Layer 3). The AI's output is never trusted blindly.
        result: ValidationResult = validate_interpreted(interpreted)
        if not result.ok:
            await self._audit.record(
                "feature.rejected_by_validator",
                actor_id=user_id,
                detail={"request_text": request_text,
                        "issues": [i.model_dump() for i in result.issues]},
            )
            return {"ok": False, "validation": result.model_dump(),
                    "clarifying_question": interpreted.clarifying_question}

        feature_id = await self._repo.create_feature(
            created_by=user_id,
            name=interpreted.suggested_name,
            description=interpreted.description,
            request_text=request_text,
            tier=interpreted.tier,
            spec=interpreted.spec,
            declared_capabilities=interpreted.declared_capabilities,
            trigger_kind=interpreted.spec.trigger.kind.value,
            schedule_cron=interpreted.spec.trigger.cron,
        )
        await self._audit.record(
            "feature.created", actor_id=user_id, feature_id=feature_id,
            detail={"name": interpreted.suggested_name, "tier": interpreted.tier},
        )
        return {
            "ok": True,
            "feature_id": str(feature_id),
            "description": interpreted.description,
            "declared_capabilities": [c.model_dump() for c in interpreted.declared_capabilities],
            "clarifying_question": interpreted.clarifying_question,
            "confidence": interpreted.confidence,
        }

    # ----------------------------------------------------------------- 2.
    async def approve_capabilities(self, *, feature_id: UUID, user_id: UUID) -> None:
        feature = await self._require_feature(feature_id)
        declared = [Capability.model_validate(c) for c in feature["declared_capabilities"]]
        await self._repo.save_grants(feature_id, declared, approved_by=user_id)
        await self._repo.set_status(feature_id, FeatureStatus.DRY_RUN_READY)
        await self._audit.record(
            "capabilities.approved", actor_id=user_id, feature_id=feature_id,
            detail={"capabilities": [c.capability_key for c in declared]},
        )

    async def reject_feature(self, *, feature_id: UUID, user_id: UUID) -> None:
        await self._require_feature(feature_id)
        await self._repo.set_status(feature_id, FeatureStatus.REJECTED)
        await self._audit.record("feature.rejected", actor_id=user_id, feature_id=feature_id)

    # ----------------------------------------------------------------- 3.
    async def dry_run(self, *, feature_id: UUID, user_id: UUID) -> dict:
        """Safe preview. Reads data, computes intentions, changes nothing."""
        feature = await self._require_feature(feature_id)
        spec = FeatureSpec.model_validate(feature["spec"])
        engine = RuleEngine(self._data, self._effects, self._tenant_id)

        correlation_id = uuid4()
        run_id = await self._repo.create_run(
            feature_id=feature_id, mode=RunMode.DRY_RUN.value,
            status=RunStatus.SUCCESS.value, trigger="preview", correlation_id=correlation_id,
        )
        outcome = await engine.run(spec, RunMode.DRY_RUN)
        planned = [p.model_dump() for p in outcome["planned"]]
        await self._repo.finish_run(
            run_id, status=RunStatus.SUCCESS.value, planned=planned, executed=[],
            result_summary=f"Would affect {len(planned)} item(s).", error=None,
        )
        await self._audit.record(
            "feature.dry_run", actor_id=user_id, feature_id=feature_id,
            detail={"planned_count": len(planned)},
        )
        return {"run_id": str(run_id), "planned_actions": planned}

    # ----------------------------------------------------------------- 4.
    async def promote_to_observe(self, *, feature_id: UUID, user_id: UUID) -> None:
        await self._require_feature(feature_id)
        await self._repo.set_status(feature_id, FeatureStatus.OBSERVE)
        await self._audit.record("feature.promoted_observe", actor_id=user_id, feature_id=feature_id)

    async def promote_to_active(self, *, feature_id: UUID, user_id: UUID) -> None:
        feature = await self._require_feature(feature_id)
        # Progressive trust gate (Layer 6): must have enough clean observe runs.
        if feature["successful_observe_runs"] < feature["observe_runs_required"]:
            raise PolicyError(
                f"This feature needs {feature['observe_runs_required']} successful "
                f"observe runs before it can act (has {feature['successful_observe_runs']})."
            )
        await self._repo.set_status(feature_id, FeatureStatus.ACTIVE)
        await self._audit.record("feature.promoted_active", actor_id=user_id, feature_id=feature_id)

    # ----------------------------------------------------------------- 5.
    async def run_feature(self, *, feature_id: UUID, trigger: str, user_id: Optional[UUID]) -> dict:
        """Executed by the scheduler (observe/act) or manually."""
        if not await self._repo.is_factory_enabled():
            raise PolicyError("Feature Factory is disabled for this tenant (kill switch).")

        feature = await self._require_feature(feature_id)
        status = FeatureStatus(feature["status"])
        if status == FeatureStatus.OBSERVE:
            mode = RunMode.OBSERVE
        elif status == FeatureStatus.ACTIVE:
            mode = RunMode.ACT
        else:
            raise PolicyError(f"Feature in status '{status.value}' cannot run.")

        spec = FeatureSpec.model_validate(feature["spec"])

        # Run-time re-validation against the ACTUAL grants (Layer 3, belt+suspenders).
        grants = [Capability.model_validate({
            "capability_key": g["capability_key"], "resource": g["resource"],
            "scope": g["scope"], "human_summary": "",
        }) for g in await self._repo.get_grants(feature_id)]
        recheck = validate_against_grants(spec, grants)
        if not recheck.ok:
            await self._fail(feature_id, user_id, "spec exceeded approved grants", recheck)
            raise PolicyError("Feature spec no longer matches its approved capabilities; blocked.")

        correlation_id = uuid4()
        run_id = await self._repo.create_run(
            feature_id=feature_id, mode=mode.value, status=RunStatus.SUCCESS.value,
            trigger=trigger, correlation_id=correlation_id,
        )

        try:
            engine = RuleEngine(self._data, self._effects, self._tenant_id)
            outcome = await engine.run(spec, mode)
            planned = [p.model_dump() for p in outcome["planned"]]
            executed = [p.model_dump() for p in outcome["executed"]]

            # In ACT mode, commit/destructive actions become pending approvals.
            approvals_created = 0
            if mode == RunMode.ACT:
                for pa in outcome["planned"]:
                    if pa.requires_approval:
                        await self._repo.create_approval(
                            feature_id=feature_id, run_id=run_id,
                            action=pa.model_dump(), action_class=pa.action_class.value,
                            before_snapshot=None,
                        )
                        approvals_created += 1

            run_status = (RunStatus.AWAITING_APPROVAL if approvals_created
                          else RunStatus.SUCCESS)
            await self._repo.finish_run(
                run_id, status=run_status.value, planned=planned, executed=executed,
                result_summary=f"{len(executed)} done, {approvals_created} awaiting approval.",
                error=None,
            )

            # Progressive-trust counter only advances on clean observe runs.
            if mode == RunMode.OBSERVE:
                await self._repo.record_observe_success(feature_id)

            await self._audit.record(
                "feature.run", actor_id=user_id, feature_id=feature_id,
                detail={"mode": mode.value, "planned": len(planned),
                        "executed": len(executed), "approvals": approvals_created},
            )
            return {"run_id": str(run_id), "planned": planned, "executed": executed,
                    "approvals_created": approvals_created}

        except Exception as exc:  # circuit breaker (Layer 7)
            await self._repo.finish_run(
                run_id, status=RunStatus.FAILED.value, planned=[], executed=[],
                result_summary=None, error=str(exc),
            )
            counters = await self._repo.record_failure(feature_id)
            if counters["consecutive_failures"] >= counters["failure_threshold"]:
                await self._repo.set_status(feature_id, FeatureStatus.FAILED)
                await self._audit.record(
                    "feature.auto_disabled", actor_id=None, feature_id=feature_id,
                    detail={"reason": "circuit_breaker", "failures": counters["consecutive_failures"]},
                )
            raise

    # ----------------------------------------------------------------- 6.
    async def decide_approval(self, *, approval_id: UUID, approve: bool, user_id: UUID) -> dict:
        decided = await self._repo.decide_approval(approval_id, approved=approve, decided_by=user_id)
        if decided is None:
            raise PolicyError("Approval not found or already decided.")
        if approve:
            action = decided["action"]
            # Perform the now-approved commit/destructive effect.
            await self._effects.perform(
                action["action_type"], action.get("params", {}),
                {"ref": action["target_ref"]}, self._tenant_id,
            )
        await self._audit.record(
            "approval.decided", actor_id=user_id, feature_id=decided["feature_id"],
            detail={"approval_id": str(approval_id), "approved": approve},
        )
        return {"approved": approve}

    # ----------------------------------------------------------------- 7.
    async def set_kill_switch(self, *, enabled: bool, user_id: UUID) -> None:
        await self._repo._db.execute(  # direct: tenant-settings upsert
            """
            INSERT INTO ff_tenant_settings (tenant_id, feature_factory_enabled)
            VALUES ($1, $2)
            ON CONFLICT (tenant_id) DO UPDATE SET feature_factory_enabled = $2, updated_at = now()
            """,
            self._tenant_id, enabled,
        )
        await self._audit.record(
            "kill_switch.set", actor_id=user_id,
            detail={"enabled": enabled},
        )

    # ----------------------------------------------------------------- helpers
    async def _require_feature(self, feature_id: UUID) -> dict:
        feature = await self._repo.get_feature(feature_id)
        if feature is None:
            raise PolicyError("Feature not found for this account.")
        return feature

    async def _fail(self, feature_id: UUID, user_id: Optional[UUID], reason: str, result) -> None:
        await self._repo.set_status(feature_id, FeatureStatus.FAILED)
        await self._audit.record(
            "feature.blocked", actor_id=user_id, feature_id=feature_id,
            detail={"reason": reason, "issues": [i.model_dump() for i in result.issues]},
        )
