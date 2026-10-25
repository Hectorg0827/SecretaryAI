-- Outlook / O365
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_tenant_id text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_client_id text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_client_secret text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_access_token text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ms_refresh_token text;

-- Shopify
ALTER TABLE companies ADD COLUMN IF NOT EXISTS shopify_shop_domain text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS shopify_access_token text;

-- Shipment tracking
ALTER TABLE companies ADD COLUMN IF NOT EXISTS fedex_api_key text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS fedex_secret_key text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ups_client_id text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS ups_client_secret text;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS dhl_api_key text;
