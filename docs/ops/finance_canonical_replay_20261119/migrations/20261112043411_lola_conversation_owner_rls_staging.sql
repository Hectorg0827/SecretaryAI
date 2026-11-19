DROP POLICY IF EXISTS company_isolation ON public.conversations;
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
