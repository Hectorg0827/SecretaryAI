-- Store the Conductor EndUser ID so we can route QB Desktop API calls
-- and avoid creating duplicate EndUsers on retries.
ALTER TABLE companies
  ADD COLUMN IF NOT EXISTS conductor_end_user_id TEXT;

COMMENT ON COLUMN companies.conductor_end_user_id IS
  'Conductor (conductor.is) EndUser ID — links this company to their QB Desktop Web Connector session.';
