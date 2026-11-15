"""Disposable PostgreSQL concurrency acceptance: consent-revoke vs replay write.

NO hosted Supabase project or external API is accessed. CI must set
SECRETARYAI_TEST_DATABASE_ONLY=1 and connect to the localhost PostgreSQL
service established by the migrations job. Uses synthetic employee/source IDs.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import uuid4

import asyncpg


def _test_guard() -> None:
    if (os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
            or os.getenv("PGHOST", "localhost") not in ("localhost", "127.0.0.1")
            or os.getenv("PGDATABASE") != "sai"):
        raise RuntimeError("Lola concurrency fixture requires an isolated CI database")


def _fingerprint(company, employee, contact, work, mapping_rev, email_rev, wa_rev):
    return hashlib.sha256("\n".join([
        str(company), str(employee), contact, work, mapping_rev,
        "email:" + email_rev, "whatsapp_business:" + wa_rev,
    ]).encode()).hexdigest()


def _args(company, employee, revision, generation, replay_digest):
    return (
        company, employee, "gmail", "a" * 64, "b" * 64, revision,
        generation, "synthetic-encrypted-envelope-not-a-real-token" + "z" * 32,
        "c" * 64, replay_digest, generation == 0, "[]",
    )


async def _commit(conn, scope, generation: int, revision: str, digest: str) -> int:
    company, employee = scope
    statement = """SELECT public.lola_commit_provider_replay(
        $1::uuid, $2::uuid, $3::text, $4::text, $5::text, $6::text,
        $7::bigint, $8::text, $9::text, $10::text, $11::boolean,
        now()::timestamptz, $12::jsonb
    )"""
    return await conn.fetchval(statement, *_args(company, employee, revision, generation, digest))


async def main() -> None:
    _test_guard()
    conninfo = dict(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"),
        database=os.getenv("PGDATABASE", "sai"),
    )
    admin = await asyncpg.connect(**conninfo)
    worker = await asyncpg.connect(**conninfo)
    revoker = await asyncpg.connect(**conninfo)
    company, employee = uuid4(), uuid4()
    contact, work = "private-fixture-contact", "private-fixture-task"
    mapping_rev, email_rev, wa_rev = "case-v1", "email-v1", "whatsapp-v1"
    fingerprint = _fingerprint(
        company, employee, contact, work, mapping_rev, email_rev, wa_rev
    )
    try:
        # Only disposable CI role/fixtures. Hosted Supabase role grants are
        # NOT changed, and actual credentials are not provided.
        await admin.execute("GRANT SELECT,UPDATE ON public.users TO service_role")
        await admin.execute("""
            CREATE OR REPLACE FUNCTION auth.role()
            RETURNS text LANGUAGE sql STABLE AS
            $$ SELECT current_setting('request.jwt.claim.role', true) $$
        """)
        await admin.execute(
            "INSERT INTO public.companies(id,name) VALUES ($1,'Synthetic CI concurrency')",
            company,
        )
        await admin.execute(
            """INSERT INTO public.users(
                id,company_id,email,hashed_password,role,is_active
            ) VALUES($1,$2,$3,'synthetic-only','sales_rep',TRUE)""",
            employee, company, f"{employee}@invalid.example",
        )
        for channel, provider_name, identity_hash, identity, revision in (
            ("email","gmail","a"*64,"synthetic-email","email-v1"),
            ("whatsapp_business","meta_whatsapp_business","f"*64,
             "synthetic-whatsapp","whatsapp-v1"),
        ):
            await admin.execute(
                """INSERT INTO public.lola_channel_authorizations(
                    company_id,employee_id,channel,provider_name,
                    provider_identity_ref,provider_identity_sha256,
                    provider_verification_state,connector_authorization_state,
                    employee_consent_at,provider_verified_at,
                    authorization_expires_at,source_revision
                ) VALUES ($1,$2,$3,$4,$5,$6,'verified','connected',
                          now()-interval '1 day',now()-interval '1 day',
                          now()+interval '3 days',$7)""",
                company, employee, channel, provider_name,
                identity, identity_hash, revision,
            )
        await admin.execute(
            """INSERT INTO public.lola_verified_case_links(
                company_id,employee_id,contact_id,work_item_id,
                email_provider_contact_ref,whatsapp_provider_contact_ref,
                link_verification_state,verification_source,
                employee_confirmed_at,link_verified_at,
                verification_expires_at,mapping_revision
            ) VALUES($1,$2,$3,$4,'synthetic-contact-email',
                     'synthetic-contact-whatsapp','verified',
                     'provider_plus_employee',now()-interval '1 day',
                     now()-interval '1 day',now()+interval '3 days',$5)""",
            company, employee, contact, work, mapping_rev,
        )
        await admin.execute(
            """INSERT INTO public.lola_authorized_email_resources(
                company_id,employee_id,provider_name,
                provider_identity_sha256,resource_sha256,
                verified_at,authorization_expires_at,verification_source
            ) VALUES($1,$2,'gmail',$3,$4,
                     now()-interval '1 day',now()+interval '3 days',
                     'oauth_principal_and_employee')""",
            company, employee, "a" * 64, "b" * 64,
        )

        # In one transaction, the replay checkpoint trigger holds FOR SHARE
        # on the verified grant rows until the actual cursor commit.
        tx = worker.transaction()
        await tx.start()
        try:
            await worker.execute("SET LOCAL ROLE service_role")
            await worker.execute("SET LOCAL request.jwt.claim.role = 'service_role'")
            result = await _commit(
                worker, (company, employee), 0, fingerprint, "d"*64,
            )
            if result != 1:
                raise AssertionError("Expected first replay generation 1")
            revocation = asyncio.create_task(revoker.execute(
                """UPDATE public.lola_channel_authorizations
                   SET employee_consent_revoked_at=now()
                   WHERE company_id=$1 AND employee_id=$2
                     AND channel='whatsapp_business'""",
                company, employee,
            ))
            try:
                await asyncio.wait_for(asyncio.shield(revocation), timeout=0.15)
            except asyncio.TimeoutError:
                pass  # Correct: revocation is waiting for committed FOR SHARE lock
            else:
                raise AssertionError("Revocation bypassed the checkpoint row lock")
            await tx.commit()
        except BaseException:
            if tx._state == "started":
                await tx.rollback()
            raise
        await asyncio.wait_for(revocation, timeout=5)

        # A later replay must fail after the persisted revocation; the old
        # cursor remains in the database and cannot be silently advanced.
        try:
            async with worker.transaction():
                await worker.execute("SET LOCAL ROLE service_role")
                await worker.execute("SET LOCAL request.jwt.claim.role = 'service_role'")
                await _commit(worker, (company, employee), 1, fingerprint, "e"*64)
        except asyncpg.PostgresError:
            pass
        else:
            raise AssertionError("Revoked employee consent allowed a replay write")
        row = await admin.fetchrow(
            """SELECT generation,replay_digest
               FROM public.lola_provider_replay_checkpoints
               WHERE company_id=$1 AND employee_id=$2""",
            company, employee,
        )
        if row is None or row["generation"] != 1 or row["replay_digest"] != "d"*64:
            raise AssertionError("Revoked cursor advanced or changed its evidence")
        print("Lola Stage 10 simultaneous revoke/commit race passed")
    finally:
        # Dispose all synthetic records from this disposable PostgreSQL only.
        try:
            await admin.execute(
                "DELETE FROM public.lola_provider_replay_events WHERE company_id=$1", company,
            )
            await admin.execute(
                "DELETE FROM public.lola_provider_replay_checkpoints WHERE company_id=$1", company,
            )
            await admin.execute(
                "DELETE FROM public.lola_authorized_email_resources WHERE company_id=$1", company,
            )
            await admin.execute(
                "DELETE FROM public.lola_verified_case_links WHERE company_id=$1", company,
            )
            await admin.execute(
                "DELETE FROM public.lola_channel_authorizations WHERE company_id=$1", company,
            )
            await admin.execute("DELETE FROM public.users WHERE company_id=$1", company)
            await admin.execute("DELETE FROM public.companies WHERE id=$1", company)
        finally:
            await asyncio.gather(admin.close(), worker.close(), revoker.close())


if __name__ == "__main__":
    asyncio.run(main())
