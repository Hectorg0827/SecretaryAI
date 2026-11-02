-- Follow-up to 20261030013756_stripe_billing_safety. The earlier migration was
-- already applied to staging; do not edit its history to deploy these fixes.
-- All new RPCs are service_role-only, and no live billing is enabled here.
begin;

-- A conflicting plan must not silently replace a possibly-live Stripe session.
-- The backend reads this state, verifies expiration with Stripe, then uses the
-- compare-and-swap rotation below.
create or replace function public.billing_get_checkout_conflict(p_company_id uuid)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare r public.stripe_checkout_attempts%rowtype;
begin
  select * into r from public.stripe_checkout_attempts
    where company_id = p_company_id;
  if not found then return jsonb_build_object('state', 'missing'); end if;
  return jsonb_build_object('state', r.state,
    'stripe_session_id', r.stripe_session_id);
end $$;

create or replace function public.billing_rotate_expired_checkout_plan(
  p_company_id uuid, p_session_id text, p_plan_id text, p_billing_period text
) returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  if p_billing_period not in ('monthly', 'yearly') or
     not exists (select 1 from public.subscription_plans where id = p_plan_id) then
    raise exception 'Invalid checkout selection';
  end if;
  perform 1 from public.companies where id = p_company_id for update;
  if not found then return false; end if;
  update public.stripe_checkout_attempts
    set attempt_id = gen_random_uuid(), plan_id = p_plan_id,
        billing_period = p_billing_period, state = 'reserved',
        stripe_session_id = null, checkout_url = null,
        created_at = now(), updated_at = now()
  where company_id = p_company_id and state = 'ready'
    and stripe_session_id = p_session_id
    and not exists (select 1 from public.company_subscriptions
      where company_id = p_company_id and stripe_subscription_id is not null
        and status in ('active', 'trialing', 'past_due'));
  return found;
end $$;

-- Record verified historical paid invoices for the same Stripe customer,
-- including after the company starts a newer subscription. Never mutate the
-- current entitlement using a previous subscription's invoice snapshot.
create or replace function public.billing_apply_stripe_event(
  p_company_id uuid, p_event_id text, p_event_type text,
  p_customer_id text, p_subscription_id text, p_plan_id text,
  p_status text, p_period_start timestamptz, p_period_end timestamptz,
  p_trial_end timestamptz, p_cancel_at_period_end boolean,
  p_amount_usd numeric, p_metadata jsonb, p_retrieved_at timestamptz,
  p_checkout_session_id text default null, p_checkout_attempt_id uuid default null,
  p_object_id text default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare s public.company_subscriptions%rowtype;
declare a public.stripe_checkout_attempts%rowtype;
declare previous_company uuid;
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
  if p_event_type in ('invoice.paid', 'checkout.session.completed') then
    if nullif(p_object_id, '') is null then
      raise exception 'Missing Stripe financial object ID';
    end if;
    select company_id into previous_company from public.billing_events
      where event_type = p_event_type and stripe_object_id = p_object_id;
    if found then
      if previous_company != p_company_id then
        raise exception 'Stripe financial object attributed to different company';
      end if;
      return jsonb_build_object('duplicate', true, 'applied', false,
        'duplicate_object', true);
    end if;
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
    raise exception 'Stripe subscription has not been associated with a company';
  elsif s.stripe_customer_id is distinct from p_customer_id then
    raise exception 'Stripe customer mismatch';
  elsif s.stripe_subscription_id is distinct from p_subscription_id then
    if p_event_type != 'invoice.paid' then
      raise exception 'Stripe subscription mismatch';
    end if;
    -- The signed webhook has been checked against Stripe's retrieved
    -- subscription/customer pair by the backend before invoking this RPC.
    -- Object-ID uniqueness and per-company locking still apply to this ledger.
    insert into public.billing_events
      (company_id, event_type, amount_usd, stripe_event_id, stripe_object_id, metadata)
    values (p_company_id, p_event_type, p_amount_usd, p_event_id,
      p_object_id, coalesce(p_metadata, '{}'::jsonb));
    return jsonb_build_object('duplicate', false, 'applied', false,
      'historical', true);
  end if;

  insert into public.billing_events
    (company_id, event_type, amount_usd, stripe_event_id, stripe_object_id, metadata)
  values (p_company_id, p_event_type, p_amount_usd, p_event_id,
    p_object_id, coalesce(p_metadata, '{}'::jsonb));

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

revoke all on function public.billing_get_checkout_conflict(uuid)
  from public, anon, authenticated;
revoke all on function public.billing_rotate_expired_checkout_plan(uuid, text, text, text)
  from public, anon, authenticated;
grant execute on function public.billing_get_checkout_conflict(uuid) to service_role;
grant execute on function public.billing_rotate_expired_checkout_plan(uuid, text, text, text)
  to service_role;
-- Reconfirm existing RPC permissions after replacing its definition.
revoke all on function public.billing_apply_stripe_event(uuid, text, text, text, text, text, text,
  timestamptz, timestamptz, timestamptz, boolean, numeric, jsonb, timestamptz, text, uuid, text)
  from public, anon, authenticated;
grant execute on function public.billing_apply_stripe_event(uuid, text, text, text, text, text, text,
  timestamptz, timestamptz, timestamptz, boolean, numeric, jsonb, timestamptz, text, uuid, text)
  to service_role;
commit;
