# Disposable hosted acceptance (draft; not authorized for parent)

Only project ref ontqkqgofkghsdgtqwsm, branch c02999ba-981b-48ad-b230-9714212535ad, with_data=false. Verify provider branch identity immediately before execution. The SQL marker is an operator assertion, not proof of server identity. Do not run generated SQL on any other endpoint.

Generate using hosted_replay.py after independent review. Use a direct TLS-verified database connection whose hostname is db.ontqkqgofkghsdgtqwsm.supabase.co, with branch-only credentials from a protected secret store. Set app.disposable_project_ref in that same session only after verifying the connection target. Use psql ON_ERROR_STOP. Do not execute until transaction compatibility of every canonical SQL file and migration history representation has been reviewed. The generator stores each complete source as one statements array element; verify compatibility with hosted replay tooling before use.

After replay, require all catalog fingerprints, exact migration versions and SQL provenance, explicit least-privilege grants, RLS and trusted access-token hook configuration. Do not add broad grants or synthesize JWTs.

Run PR #232 exact reviewed head from a protected runner with branch-only SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, EXPECTED_DISPOSABLE_PROJECT_REF and ALLOW_DISPOSABLE_AUTH_TESTS=yes. Never print keys or tokens. Require real Auth login and cleanup evidence. Delete disposable branch after evidence capture.
