-- Subscription plans
create table if not exists subscription_plans (
  id text primary key,           -- 'starter' | 'growth' | 'enterprise'
  name text not null,
  price_monthly_usd numeric(10,2) not null,
  price_yearly_usd numeric(10,2) not null,
  max_users int not null default 5,
  max_skus int not null default 500,
  features jsonb not null default '[]',
  stripe_price_id_monthly text,
  stripe_price_id_yearly text,
  created_at timestamptz default now()
);

-- Company subscriptions
create table if not exists company_subscriptions (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  plan_id text not null references subscription_plans(id),
  status text not null default 'active',  -- active | past_due | canceled | trialing
  stripe_customer_id text,
  stripe_subscription_id text,
  current_period_start timestamptz,
  current_period_end timestamptz,
  trial_end timestamptz,
  cancel_at_period_end boolean default false,
  seats_used int not null default 1,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Billing events / invoice history
create table if not exists billing_events (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  event_type text not null,   -- invoice.paid | invoice.payment_failed | subscription.updated | etc.
  amount_usd numeric(10,2),
  stripe_event_id text unique,
  metadata jsonb default '{}',
  created_at timestamptz default now()
);

-- Seed plans
insert into subscription_plans (id, name, price_monthly_usd, price_yearly_usd, max_users, max_skus, features)
values
  ('starter',    'Starter',    99,  990,  3,   500,  '["QB sync","AI chat","inventory alerts","email digest"]'),
  ('growth',     'Growth',     249, 2490, 10,  2000, '["QB sync","AI chat","inventory alerts","email digest","logistics pipeline","proactive intelligence","workflow automation"]'),
  ('enterprise', 'Enterprise', 599, 5990, 50,  10000,'["QB sync","AI chat","inventory alerts","email digest","logistics pipeline","proactive intelligence","workflow automation","multi-location","API access","dedicated support"]')
on conflict (id) do nothing;
