# Default ACL expansion acceptance (read-only)

Run this query against an **isolated disposable Supabase database** and export the
rows as a JSON array. Never connect this script to production credentials.

```sql
SELECT owner.rolname AS owner,
       COALESCE(n.nspname, '(global)') AS schema,
       d.defaclobjtype::text AS object_type,
       CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE grantee.rolname END AS grantee,
       a.privilege_type AS privilege
FROM pg_default_acl d
JOIN pg_roles owner ON owner.oid = d.defaclrole
LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) a
LEFT JOIN pg_roles grantee ON grantee.oid = a.grantee
WHERE n.nspname = 'public' OR d.defaclnamespace = 0
ORDER BY 1,2,3,4,5;
```

Store a security-reviewed baseline as JSON. Compare with:

```bash
python scripts/check_default_acl_expansion.py --baseline reviewed.json --current isolated.json
python -m unittest discover -s scripts -p 'test_check_default_acl_expansion.py'
```

Exit 0: no additions. Exit 1: any added default privilege (including
service roles), with separate `client_exposure_added` for PUBLIC/anon/authenticated.
Exit 2: invalid input. Removals are reported but not accepted as proof of
application compatibility. Duplicate rows are deduplicated. This inventory
does **not** test effective grants, inherited privileges, RLS, or actual
authorization; use role/tenant tests and independent review before any
permission changes. No automatic grant or revoke is performed.
