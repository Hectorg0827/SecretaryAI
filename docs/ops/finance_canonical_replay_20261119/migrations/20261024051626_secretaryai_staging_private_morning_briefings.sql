-- SecretaryAI: private morning briefings for backend-generated summaries.
-- No email worker is activated by this migration. Recording sent_at requires
-- actual delivery confirmation; queue or creation does not imply a send.
CREATE TABLE IF NOT EXISTS public.morning_briefings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES public.companies(id) ON DELETE CASCADE,
  briefing_text text NOT NULL,
  recipient_email text,
  sent_at timestamptz,
  language text NOT NULL DEFAULT 'English',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_morning_briefings_company_created
  ON public.morning_briefings(company_id,created_at DESC);
ALTER TABLE public.morning_briefings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.morning_briefings FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE public.morning_briefings TO service_role;
