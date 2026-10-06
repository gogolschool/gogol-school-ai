create or replace function public.b2c_visit_rate_info(_visit integer)
returns jsonb
language sql
stable
as $$
  -- B2C-ставка смены по правилам на руки (docs/superpowers/specs/2026-10-06-b2c-master-rates-design.md).
  -- null = смена вне области. rate - с налогом за час; net - на руки за час; source - откуда ставка.
  with v as (
    select vfl.id, vfl.contact, vfl.masters,
           (vfl."datetime" at time zone 'Europe/Moscow')::date as d,
           vfl.action as lesson_id,
           les.parent_action as product_id,
           les.paid_requests_on_day as paid,
           ct.name as ct_name,
           mfa.special_rate_net, mfa.special_reason
    from crm.visits_for_lessons vfl
      join crm.actions les on les.id = vfl.action
      join crm.actions prod on prod.id = les.parent_action
      join crm.class_type ct on ct.id = prod.class_type
      left join crm.masters_for_actions mfa on mfa.action = prod.id and mfa.contact = vfl.contact
    where vfl.id = _visit
      and vfl."datetime" >= timestamptz '2026-09-01 00:00:00+03'
      and les.b2b_lesson_kind is null
      and not coalesce(mfa.is_stage_producer, false)
      and ct.name in ('Лаборатория', 'Интенсив', 'Интенсив YNG', 'Интенсив СПБ', 'Курс')
  ),
  cnt as (
    select count(*)::int as n
    from crm.visits_for_lessons v2
      join base.contacts p2 on p2.id = v2.contact
      left join crm.masters_for_actions m2 on m2.action = (select product_id from v) and m2.contact = v2.contact
    where v2.action = (select lesson_id from v)
      and p2.people__is_employee and v2."datetime" is not null
      and not coalesce(m2.is_stage_producer, false)
  ),
  m as (
    select least(greatest(coalesce(nullif(v.masters, 0), nullif(cnt.n, 0), 1), 1), 3) as n from v, cnt
  ),
  rules as (
    select r.* from hrm.b2c_rate_rules r, v where r.start_date <= v.d order by r.start_date desc limit 1
  ),
  cat as (
    select mc.category from hrm.b2c_master_categories mc, v
    where mc.master = v.contact and mc.start_date <= v.d
    order by mc.start_date desc limit 1
  ),
  amt as (
    select a.amount_net from hrm.b2c_category_amounts a, v
    where a.category = coalesce((select category from cat), 4) and a.start_date <= v.d
    order by a.start_date desc limit 1
  ),
  tax as (
    select coalesce(public.master_tax_on(v.contact, v.d), 0) as t from v
  ),
  net as (
    select case
             when v.special_rate_net is not null then v.special_rate_net
             else ((select amount_net from amt) - (m.n - 1) * rules.step_per_master)
                  * (case when v.ct_name = 'Курс' then rules.course_multiplier else 1 end)
                  + (case when coalesce(v.paid, 0) >= rules.high_rate_threshold then rules.high_rate_extra else 0 end)
           end as net
    from v, m, rules
  )
  select jsonb_build_object(
    'rate', ceil(net.net / (1 - tax.t / 100.0)),
    'net', net.net,
    'source',
      case when v.special_rate_net is not null
           then 'особая: ' || coalesce(nullif(v.special_reason, ''), 'причина не указана')
           else 'категория ' || coalesce((select category from cat)::text, '4 (не назначена)')
                || ', мастеров: ' || m.n
                || case when v.ct_name = 'Курс' then ', курс ×' || rules.course_multiplier else '' end
                || case when coalesce(v.paid, 0) >= rules.high_rate_threshold
                        then ', повышенная: ' || v.paid || ' платных' else '' end
      end || ', налог ' || tax.t || ' %'
  )
  from v, m, rules, net, tax
$$;