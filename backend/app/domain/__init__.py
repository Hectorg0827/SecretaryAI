"""
Domain layer — canonical contracts, typed models, and cross-cutting protocols.

This package is the single source of truth for field names, type shapes, and
inter-service contracts.  All other modules (connectors, API routers, Celery
tasks, AI layer) should import domain types from here rather than defining
their own ad-hoc dicts or local dataclasses.

Sub-modules
-----------
contracts        — Canonical domain entity types (Customer, Invoice, etc.)
data_result      — Typed DataResult envelope returned by AccessRouter / adapters
connector_protocol — Cloud↔connector message protocol (registration, heartbeat, tasks)
policy           — Per-tenant policy engine (loaded from DB policy_rules table)
"""
