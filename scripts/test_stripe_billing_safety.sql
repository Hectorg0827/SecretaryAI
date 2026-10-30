-- Disposable PostgreSQL contract test: no Stripe calls and no persistent rows.
begin;

insert into public.companies(id, name)
values ('10000000-0000-4000-8000-000000000001', 'Synthetic Stripe Company');

do $test$
declare
  c uuid := '10000000-0000-4000-8000-000000000001';
  attempt uuid;
  r jsonb;
  output jsonb;
begin
  r := public.billing_reserve_checkout(c, 'starter', 'monthly');
  if r->>'state' != 'reserved' then raise exception 'Checkout not reserved'; end if;
  attempt := (r->>'attempt_id')::uuid;
  r := public.billing_reserve_checkout(c, 'starter', 'monthly');
  if (r->>'attempt_id')::uuid != attempt then
    raise exception 'Double reservation was created';
  end if;
  r := public.billing_reserve_checkout(c, 'growth', 'yearly');
  if r->>'state' != 'different_plan' then
    raise exception 'Conflicting plan was not prevented';
  end if;
  if not public.billing_finalize_checkout(c, attempt, 'cus_test', 'cs_test', 'https://checkout.stripe.com/test') then
    raise exception 'Checkout finalize failed';
  end if;
  if public.billing_finalize_checkout(c, attempt, 'cus_test', 'cs_other', 'https://checkout.stripe.com/other') then
    raise exception 'Checkout finalize was overwritten';
  end if;

  output := public.billing_apply_stripe_event(
    p_company_id => c, p_event_id => 'evt_checkout',
    p_event_type => 'checkout.session.completed', p_customer_id => 'cus_test',
    p_subscription_id => 'sub_test', p_plan_id => 'starter',
    p_status => 'active', p_period_start => '2026-10-01T00:00:00Z',
    p_period_end => '2026-11-01T00:00:00Z', p_trial_end => null,
    p_cancel_at_period_end => false, p_amount_usd => null,
    p_metadata => '{}'::jsonb, p_retrieved_at => '2026-10-02T10:00:00Z',
    p_checkout_session_id => 'cs_test', p_checkout_attempt_id => attempt);
  if output->>'applied' != 'true' then raise exception 'Checkout not applied'; end if;
  output := public.billing_apply_stripe_event(
    p_company_id => c, p_event_id => 'evt_checkout',
    p_event_type => 'checkout.session.completed', p_customer_id => 'cus_test',
    p_subscription_id => 'sub_test', p_plan_id => 'starter',
    p_status => 'past_due', p_period_start => null, p_period_end => null,
    p_trial_end => null, p_cancel_at_period_end => false, p_amount_usd => null,
    p_metadata => '{}'::jsonb, p_retrieved_at => '2026-10-03T10:00:00Z',
    p_checkout_session_id => 'cs_test', p_checkout_attempt_id => attempt);
  if output->>'duplicate' != 'true' then raise exception 'Replay not deduplicated'; end if;

  output := public.billing_apply_stripe_event(
    p_company_id => c, p_event_id => 'evt_paid', p_event_type => 'invoice.paid',
    p_customer_id => 'cus_test', p_subscription_id => 'sub_test', p_plan_id => null,
    p_status => 'active', p_period_start => null, p_period_end => null,
    p_trial_end => null, p_cancel_at_period_end => false, p_amount_usd => 99,
    p_metadata => '{}'::jsonb, p_retrieved_at => '2026-10-04T10:00:00Z');
  output := public.billing_apply_stripe_event(
    p_company_id => c, p_event_id => 'evt_old_failure',
    p_event_type => 'invoice.payment_failed', p_customer_id => 'cus_test',
    p_subscription_id => 'sub_test', p_plan_id => null,
    p_status => 'past_due', p_period_start => null, p_period_end => null,
    p_trial_end => null, p_cancel_at_period_end => false, p_amount_usd => 99,
    p_metadata => '{}'::jsonb, p_retrieved_at => '2026-10-03T10:00:00Z');
  if output->>'stale' != 'true' then raise exception 'Old snapshot not rejected'; end if;
  if (select status from public.company_subscriptions where company_id = c) != 'active' then
    raise exception 'Old payment failure regressed subscription';
  end if;
  if (select count(*) from public.billing_events where company_id = c) != 3 then
    raise exception 'Duplicate event ledger row inserted';
  end if;
  r := public.billing_reserve_checkout(c, 'starter', 'monthly');
  if r->>'state' != 'subscribed' then
    raise exception 'A second active checkout was allowed';
  end if;
end $test$;

do $privileges$
declare fname text;
begin
  if has_table_privilege('authenticated', 'public.stripe_checkout_attempts', 'SELECT')
    or has_table_privilege('anon', 'public.stripe_checkout_attempts', 'INSERT')
    or not (select relrowsecurity from pg_class
             where oid = 'public.stripe_checkout_attempts'::regclass) then
    raise exception 'Checkout reservations are not backend-only';
  end if;
  foreach fname in array array[
    'billing_reserve_checkout(uuid,text,text)',
    'billing_finalize_checkout(uuid,uuid,text,text,text)',
    'billing_rotate_expired_checkout(uuid,text)',
    'billing_apply_stripe_event(uuid,text,text,text,text,text,text,timestamptz,timestamptz,timestamptz,boolean,numeric,jsonb,timestamptz,text,uuid)'
  ] loop
    if has_function_privilege('anon', 'public.' || fname, 'EXECUTE')
      or has_function_privilege('authenticated', 'public.' || fname, 'EXECUTE')
      or not has_function_privilege('service_role', 'public.' || fname, 'EXECUTE') then
      raise exception 'Billing RPC privilege mismatch: %', fname;
    end if;
  end loop;
end $privileges$;

rollback;
