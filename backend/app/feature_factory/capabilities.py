"""
SecretaryAI · Feature Factory — Capability Allowlist (Layer 1)
=============================================================
This is the closed universe of things a generated feature is ALLOWED to do.
It is an ALLOW-list, not a deny-list. If a capability is not registered here,
it does not exist and cannot be declared, approved, or executed.

Why allow-list, not deny-list: a deny-list ("don't delete the database") is
always incomplete — there is always one more dangerous thing you forgot to
ban. An allow-list is complete by construction: the feature can do these
things and *nothing else*.

Adding a new capability is a deliberate engineering decision (a code change +
review), never something the AI or a customer can do at runtime.
"""
from __future__ import annotations

from .contracts import ActionClass, Capability


# ---------------------------------------------------------------------------
# The registry. key -> (resource, scope/action-class, human summary template)
# ---------------------------------------------------------------------------
# Each entry pairs a machine key with the plain-English line the user approves.
_REGISTRY: dict[str, dict] = {
    # ---- READ (safe; never needs approval) --------------------------------
    "invoices.read":   {"resource": "invoices",  "scope": ActionClass.READ,  "human": "Read your invoices"},
    "customers.read":  {"resource": "customers", "scope": ActionClass.READ,  "human": "Read your customer records"},
    "orders.read":     {"resource": "orders",    "scope": ActionClass.READ,  "human": "Read your orders"},
    "inventory.read":  {"resource": "inventory", "scope": ActionClass.READ,  "human": "Read your inventory levels"},
    "payments.read":   {"resource": "payments",  "scope": ActionClass.READ,  "human": "Read payment/AR data"},

    # ---- DRAFT (prepares something for a human; no state change) -----------
    "email.draft":     {"resource": "email",     "scope": ActionClass.DRAFT, "human": "Prepare email drafts for your review (never sends automatically)"},
    "report.summarize":{"resource": "report",    "scope": ActionClass.DRAFT, "human": "Produce summaries and reports for you to read"},

    # ---- COMMIT (changes business state; ALWAYS needs approval in act mode)-
    "flags.write":     {"resource": "flags",     "scope": ActionClass.COMMIT, "human": "Flag records for your attention"},
    "tasks.write":     {"resource": "tasks",     "scope": ActionClass.COMMIT, "human": "Create follow-up tasks"},
    "tags.write":      {"resource": "tags",      "scope": ActionClass.COMMIT, "human": "Add labels/tags to records"},
    "notify.send":     {"resource": "notify",    "scope": ActionClass.COMMIT, "human": "Send you (or a teammate) alerts"},

    # NOTE: There is intentionally NO 'delete', NO external network, NO
    # 'write to QuickBooks', NO cross-tenant capability registered here.
    # Those are deliberately impossible at Tier 1.
}

# Map each Action.type (from contracts) to the capability it requires.
# The validator uses this to prove every action is backed by an approved grant.
ACTION_TYPE_TO_CAPABILITY: dict[str, str] = {
    "flag":        "flags.write",
    "notify":      "notify.send",
    "create_task": "tasks.write",
    "draft_email": "email.draft",
    "summarize":   "report.summarize",
    "tag":         "tags.write",
}

# Which capability is needed simply to READ a given source entity.
SOURCE_ENTITY_TO_READ_CAPABILITY: dict[str, str] = {
    "invoices":  "invoices.read",
    "customers": "customers.read",
    "orders":    "orders.read",
    "inventory": "inventory.read",
    "payments":  "payments.read",
}


def is_registered(capability_key: str) -> bool:
    return capability_key in _REGISTRY


def get_capability(capability_key: str) -> Capability:
    """Return the typed Capability for a key, or raise if it's not allowed."""
    if capability_key not in _REGISTRY:
        raise KeyError(f"capability '{capability_key}' is not on the allowlist")
    entry = _REGISTRY[capability_key]
    return Capability(
        capability_key=capability_key,
        resource=entry["resource"],
        scope=entry["scope"],
        human_summary=entry["human"],
    )


def action_class_for(capability_key: str) -> ActionClass:
    return _REGISTRY[capability_key]["scope"]


def all_capabilities() -> list[Capability]:
    """Used by the interpreter prompt so the model only ever picks real ones."""
    return [get_capability(k) for k in _REGISTRY]


def requires_approval(capability_key: str) -> bool:
    """COMMIT and DESTRUCTIVE actions always require a human in act mode."""
    return action_class_for(capability_key) in (ActionClass.COMMIT, ActionClass.DESTRUCTIVE)
