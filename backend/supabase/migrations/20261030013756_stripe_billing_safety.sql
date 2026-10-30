-- SecretaryAI: atomic subscription writes and one Stripe Checkout per company.
-- Apply only after checking the company_subscriptions duplicate preflight below.
-- All functions run as the backend service_role, never as an end user.

do $$
begin
  if exists (
    select 1 from public.company_subscriptions
    group by company_id having count(*) > 1
  ) then
    raise exception 'Duplicate company_subscriptions require manual reconciliation before billing migration';
  end if;
end $$;

create unique index if not exists company_subscriptions_one_row_per_company
  on public.company_subscriptions (company_id);

alter table public.company_subscriptions
  add column if not exists last_stripe_synced_at timestamptz;

create table if not exists public.stripe_checkout_attempts (
  company_id uuid primary key references public.companies(id) on delete cascade,
  attempt_id uuid not null unique default gen_random_uuid(),
  plan_id text not null references public.subscription_plans(id),
  billing_period text not null check (billing_period in ('monthly', 'yearly')),
  state text not null default 'reserved' check (state in ('reserved', 'ready', 'completed')),
  stripe_customer_id text,
  stripe_session_id text unique,
  checkout_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.stripe_checkout_attempts enable row level security;
revoke all on public.stripe_checkout_attempts from public, anon, authenticated;
grant select, insert, update on public.stripe_checkout_attempts to service_role;

create or replace function public.billing_reserve_checkout(
  p_company_id uuid, p_plan_id text, p_billing_period text
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r public.stripe_checkout_attempts%rowtype;
begin
  if p_billing_period not in ('monthly', 'yearly') then
    raise exception 'Invalid billing period';
  end if;
  perform 1 from public.companies where id = p_company_id for update;
  if not found then raise exception 'Unknown company'; end if;
  if exists (select 1 from public.company_subscriptions
     where company_id = p_company_id and stripe_subscription_id is not null
       and status in ('active', 'trialing', 'past_due')) then
    return jsonb_build_object('state', 'subscribed');
  end if;
  select * into r from public.stripe_checkout_attempts
    where company_id = p_company_id for update;
  if not found then
    insert into public.stripe_checkout_attempts(company_id, plan_id, billing_period)
    values (p_company_id, p_plan_id, p_billing_period) returning * into r;
  end if;
  if r.state = 'completed' then
    if exists (select 1 from public.company_subscriptions
      where company_id = p_company_id and status = 'canceled') then
      update public.stripe_checkout_attempts set
        attempt_id = gen_random_uuid(), plan_id = p_plan_id,
        billing_period = p_billing_period, state = 'reserved',
        stripe_session_id = null, checkout_url = null, created_at = now(), updated_at = now()
      where company_id = p_company_id returning * into r;
    else
      return jsonb_build_object('state', 'completed');
    end if;
  end if;
  if r.plan_id != p_plan_id or r.billing_period != p_billing_period then
    return jsonb_build_object('state', 'different_plan');
  end if;
  return jsonb_build_object('state', r.state, 'attempt_id', r.attempt_id,
    'stripe_session_id', r.stripe_session_id, 'stripe_customer_id', r.stripe_customer_id,
    'checkout_url', r.checkout_url, 'created_at', r.created_at);
end $$;

create or replace function public.billing_finalize_checkout(
  p_company_id uuid, p_attempt_id uuid, p_customer_id text,
  p_session_id text, p_checkout_url text
) returns boolean language plpgsql security invoker set search_path = '' as $$
declare r public.stripe_checkout_attempts%rowtype;
begin
  perform 1 from public.companies where id = p_company_id for update;
  select * into r from public.stripe_checkout_attempts
    where company_id = p_company_id and attempt_id = p_attempt_id for update;
  if not found or r.state = 'completed' then return false; end if;
  if r.state = 'ready' and r.stripe_session_id is distinct from p_session_id then
    return false;
  end if;
  update public.stripe_checkout_attempts set state = 'ready',
    stripe_customer_id = p_customer_id, stripe_session_id = p_session_id,
    checkout_url = p_checkout_url, updated_at = now()
  where company_id = p_company_id;
  return true;
end $$;

-- Only rotate a checkout after the backend has checked its expired status
-- directly with Stripe. Compare-and-swap keeps simultaneous retries safe.
create or replace function public.billing_rotate_expired_checkout(
  p_company_id uuid, p_session_id text
) returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  perform 1 from public.companies where id = p_company_id for update;
  update public.stripe_checkout_attempts
    set attempt_id = gen_random_uuid(), state = 'reserved',
        stripe_session_id = null, checkout_url = null, created_at = now(), updated_at = now()
  where company_id = p_company_id and state = 'ready'
    and stripe_session_id = p_session_id
    and not exists (select 1 from public.company_subscriptions
      where company_id = p_company_id and stripe_subscription_id is not null
        and status in ('active', 'trialing', 'past_due'));
  return found;
end $$;

-- Ledger insertion and entitlement mutation share one database transaction.
-- The Stripe snapshot was retrieved before the RPC; stale concurrent snapshots
-- are recorded for audit but cannot overwrite a newer Stripe snapshot.
create or replace function public.billing_apply_stripe_event(
  p_company_id uuid, p_event_id text, p_event_type text,
  p_customer_id text, p_subscription_id text, p_plan_id text,
  p_status text, p_period_start timestamptz, p_period_end timestamptz,
  p_trial_end timestamptz, p_cancel_at_period_end boolean,
  p_amount_usd numeric, p_metadata jsonb, p_retrieved_at timestamptz,
  p_checkout_session_id text default null, p_checkout_attempt_id uuid default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare s public.company_subscriptions%rowtype;
declare a public.stripe_checkout_attempts%rowtype;
begin
  if p_event_id is null or length(p_event_id) = 0 or p_subscription_id is null
     or p_customer_id is null or p_retrieved_at is null
     or p_event_type not in ('checkout.session.completed', 'invoice.paid',
        'invoice.payment_failed', 'customer.subscription.updated',
        'customer.subscription.deleted') then
    raise exception 'Invalid Stripe billing event';
  end if;
  perform 1 from public.companies where id = p_company_id for update;
  if not found then raise exception 'Unknown billing company'; end if;
  if exists (select 1 from public.billing_events where stripe_event_id = p_event_id) then
    return jsonb_build_object('duplicate', true, 'applied', false);
  end if;
  select * into s from public.company_subscriptions
    where company_id = p_company_id for update;
  if p_event_type = 'checkout.session.completed' then
    if p_plan_id is null or p_checkout_session_id is null then
      raise exception 'Incomplete checkout event';
    end if;
    select * into a from public.stripe_checkout_attempts
      where company_id = p_company_id for update;
    if found and a.state != 'completed' and
       ((a.stripe_session_id is not null and a.stripe_session_id != p_checkout_session_id)
          or (a.stripe_session_id is null and a.attempt_id is distinct from p_checkout_attempt_id)) then
      raise exception 'Checkout does not match reserved attempt';
    end if;
    if s.company_id is null then
      insert into public.company_subscriptions
        (company_id, plan_id, status, stripe_customer_id, stripe_subscription_id,
         current_period_start, current_period_end, trial_end, cancel_at_period_end,
         last_stripe_synced_at, updated_at)
      values (p_company_id, p_plan_id, p_status, p_customer_id, p_subscription_id,
         p_period_start, p_period_end, p_trial_end, p_cancel_at_period_end,
         p_retrieved_at, now());
    elsif s.stripe_subscription_id is distinct from p_subscription_id and
       s.stripe_subscription_id is not null and s.status != 'canceled' then
      raise exception 'Company already has a different Stripe subscription';
    end if;
    update public.stripe_checkout_attempts set state = 'completed', updated_at = now()
      where company_id = p_company_id and
        (stripe_session_id = p_checkout_session_id or attempt_id = p_checkout_attempt_id);
  elsif s.company_id is null then
    -- Out-of-order invoice/subscription events must be retried after checkout.
    raise exception 'Stripe subscription has not been associated with a company';
  elsif s.stripe_customer_id is distinct from p_customer_id or
      s.stripe_subscription_id is distinct from p_subscription_id then
    raise exception 'Stripe customer or subscription mismatch';
  end if;

  insert into public.billing_events
    (company_id, event_type, amount_usd, stripe_event_id, metadata)
  values (p_company_id, p_event_type, p_amount_usd, p_event_id, coalesce(p_metadata, '{}'::jsonb));

  if s.last_stripe_synced_at is null or p_retrieved_at >= s.last_stripe_synced_at then
    update public.company_subscriptions set
      plan_id = coalesce(p_plan_id, plan_id), status = p_status,
      stripe_customer_id = p_customer_id,
      stripe_subscription_id = p_subscription_id,
      current_period_start = coalesce(p_period_start, current_period_start),
      current_period_end = coalesce(p_period_end, current_period_end),
      trial_end = p_trial_end, cancel_at_period_end = p_cancel_at_period_end,
      last_stripe_synced_at = p_retrieved_at, updated_at = now()
    where company_id = p_company_id;
    return jsonb_build_object('duplicate', false, 'applied', true);
  end if;
  return jsonb_build_object('duplicate', false, 'applied', false, 'stale', true);
end $$;

revoke all on function public.billing_reserve_checkout(uuid, text, text) from public, anon, authenticated;
revoke all on function public.billing_finalize_checkout(uuid, uuid, text, text, text) from public, anon, authenticated;
revoke all on function public.billing_rotate_expired_checkout(uuid, text) from public, anon, authenticated;
revoke all on function public.billing_apply_stripe_event(uuid, text, text, text, text, text, text,
 timestamptz, timestamptz, timestamptz, boolean, numeric, jsonb, timestamptz, text, uuid)
 from public, anon, authenticated;
grant execute on function public.billing_reserve_checkout(uuid, text, text) to service_role;
grant execute on function public.billing_finalize_checkout(uuid, uuid, text, text, text) to service_role;
grant execute on function public.billing_rotate_expired_checkout(uuid, text) to service_role;
grant execute on function public.billing_apply_stripe_event(uuid, text, text, text, text, text, text,
 timestamptz, timestamptz, timestamptz, boolean, numeric, jsonb, timestamptz, text, uuid)
 to service_role;
