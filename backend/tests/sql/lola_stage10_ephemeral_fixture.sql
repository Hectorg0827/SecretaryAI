-- Ephemeral PostgreSQL ONLY. Test fixture for Stage 10 review SQL.
-- Do not execute against any hosted SecretaryAI database.
CREATE ROLE anon NOLOGIN;
CREATE ROLE authenticated NOLOGIN;
CREATE ROLE service_role NOLOGIN BYPASSRLS;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

CREATE TABLE public.companies (
  id uuid PRIMARY KEY
);
CREATE TABLE public.users (
  id uuid PRIMARY KEY,
  company_id uuid NOT NULL REFERENCES public.companies(id),
  role text NOT NULL,
  is_active boolean NOT NULL DEFAULT true,
  UNIQUE(company_id,id)
);
CREATE TABLE public.employee_integrations (
  id uuid PRIMARY KEY,
  company_id uuid NOT NULL,
  user_id uuid NOT NULL,
  provider text NOT NULL CHECK (provider IN ('gmail','outlook')),
  connected_at timestamptz,
  FOREIGN KEY(company_id,user_id) REFERENCES public.users(company_id,id)
);
CREATE TABLE public.lola_private_contacts (
  id uuid PRIMARY KEY,
  company_id uuid NOT NULL,
  user_id uuid NOT NULL,
  UNIQUE(company_id,user_id,id),
  FOREIGN KEY(company_id,user_id) REFERENCES public.users(company_id,id)
);
CREATE TABLE public.lola_employee_whatsapp_connections (
  id uuid PRIMARY KEY,
  company_id uuid NOT NULL,
  user_id uuid NOT NULL,
  phone_number_id text,
  status text NOT NULL,
  meta_verified_at timestamptz,
  employee_consented_at timestamptz,
  employee_revoked_at timestamptz,
  history_sync_consented boolean NOT NULL DEFAULT false,
  policy_clearance_id text,
  FOREIGN KEY(company_id,user_id) REFERENCES public.users(company_id,id)
);
GRANT USAGE ON SCHEMA public,extensions TO service_role;
-- FOR SHARE requires UPDATE privileges on the locked rows; these are
-- explicitly present on the corresponding real SecretaryAI-Staging tables.
GRANT SELECT,UPDATE ON public.users,public.employee_integrations,
  public.lola_private_contacts,public.lola_employee_whatsapp_connections
  TO service_role;
GRANT SELECT ON public.companies TO service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA extensions TO service_role;
