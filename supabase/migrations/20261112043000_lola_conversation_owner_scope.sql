-- Lola conversation privacy: enforce both tenant and user ownership.
-- Applied to SecretaryAI-Staging on 2026-11-12; committed here for reproducibility.
-- The privileged backend still applies explicit owner filters to its queries.
BEGIN;
DROP POLICY IF EXISTS company_isolation ON public.conversations;
DROP POLICY IF EXISTS conversation_owner_scope ON public.conversations;
CREATE POLICY conversation_owner_scope ON public.conversations
  FOR ALL TO authenticated
  USING (
    company_id = ((SELECT auth.jwt()) ->> 'company_id')::uuid
    AND user_id = (SELECT auth.uid())
  )
  WITH CHECK (
    company_id = ((SELECT auth.jwt()) ->> 'company_id')::uuid
    AND user_id = (SELECT auth.uid())
  );
COMMIT;
