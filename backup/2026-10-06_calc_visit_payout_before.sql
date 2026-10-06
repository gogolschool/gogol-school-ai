create or replace function public.calc_visit_payout(_visit integer)
returns numeric
language sql
stable
as $$
  with v as (
    select
      vfl.id,
      vfl.action        as lesson_id,
      les.parent_action as product_id,
      vfl.contact       as contact,
      vfl.rate_manually as rate_manually,
      vfl.masters       as masters,
      vfl."datetime"    as dt,
      coalesce(vfl.hours_manually, hcalc.hc, 0) as hrs,
      les.b2b_lesson_kind as kind,
      les.name          as lesson_name,
      prod.price        as price,
      prod.program_hours as program_hours,
      prod.class_type   as class_type,
      coalesce(nullif(p.people__tax::text, '')::numeric, 0) as tax,
      coalesce(mfa.is_stage_producer, false) as is_producer,
      -- Новая система ставок B2B: занятие-роль B2B-продукта со стартом с 01.10.2026.
      -- Старт = «Начальная дата» карточки (pl_start_date), иначе action_datetime по Москве; пусто = старые правила.
      (les.b2b_lesson_kind is not null
        and coalesce(prod.pl_start_date, (prod.action_datetime at time zone 'Europe/Moscow')::date) >= date '2026-10-01') as is_new_b2b,
      prod.b2b_product_type as product_type
    from crm.visits_for_lessons vfl
      left join base.contacts p  on p.id = vfl.contact
      left join crm.actions les  on les.id = vfl.action
      left join crm.actions prod on prod.id = les.parent_action
      left join crm.masters_for_actions mfa
             on mfa.action = les.parent_action and mfa.contact = vfl.contact
      left join lateral (
        select coalesce(date_trunc('seconds', (vfl.datetime_end - vfl."datetime")), interval '0 seconds') as sec
      ) hs on true
      left join lateral (
        select case
            when date_part('hour', hs.sec) < 1 and date_part('minute', hs.sec) > 0 and date_part('minute', hs.sec) < 45 then 0.5
            when date_part('minute', hs.sec) >= 45 then date_part('hour', hs.sec) + 1
            when date_part('minute', hs.sec) < 15 then date_part('hour', hs.sec)
            when date_part('minute', hs.sec) >= 15 and date_part('minute', hs.sec) < 45 then date_part('hour', hs.sec) + 0.5
          end as hc
      ) hcalc on true
    where vfl.id = _visit
  ),
  cnt as (
    select
      coalesce((
        select count(*)
        from crm.visits_for_lessons v2
          join base.contacts p2 on p2.id = v2.contact
        where v2.action = (select lesson_id from v)
          and p2.people__is_employee and v2."datetime" is not null
      ), 0)
      -
      coalesce((
        select count(*)
        from crm.visits_for_lessons v3
          join base.contacts p3 on p3.id = v3.contact
          join crm.masters_for_actions m3
               on m3.action = (select product_id from v) and m3.contact = v3.contact
        where v3.action = (select lesson_id from v)
          and p3.people__is_employee and v3."datetime" is not null and m3.is_stage_producer
      ), 0) as masters_count
  ),
  cfg as (
    select
      coalesce((select value from fin.payroll_constants where code_name = 'b2b_dev_rate_per_hour'), 7500) as dev_rate,
      coalesce((select value from fin.payroll_constants where code_name = 'b2b_sales_percent'), 5)      as sales_pct
  )
  select
    case
      when v.is_producer then 0
      -- ===== Новая система ставок B2B (продукты со стартом с 01.10.2026) =====
      -- Ставки НА РУКИ из crm.b2b_product_roles / hrm.b2b_conduct_rates (b2b_role_rate_net), налог мастера делением / (1 - налог).
      -- rate_manually перекрывает, как раньше: Проведение - ставка за час с налогом × часы, остальное - итоговая сумма.
      -- Проведение: роль 'Основной', если есть такая строка, иначе 'Ко-фасилитатор'; ставка не определена -> 0 (ловит crm.b2b_rates_check).
      when v.is_new_b2b and v.kind = 'Проведение'
        then coalesce(v.rate_manually, ceil(coalesce(public.b2b_role_rate_net(v.product_id, v.contact,
               case when exists (select 1 from crm.b2b_product_roles r
                                  where r.product = v.product_id and r.person = v.contact and r.role = 'Основной')
                    then 'Основной' else 'Ко-фасилитатор' end), 0) / (1 - v.tax / 100.0))) * v.hrs
      -- Разработка: есть роль 'Разработка' -> b2b_role_rate_net (особая ставка или 5 % поровну между ролями 'Разработка');
      -- роли нет -> как раньше: 5 % поровну между отметившимися (поле masters / счёт отметок); мини-продукт (тип 6) -> 0.
      when v.is_new_b2b and v.kind = 'Разработка'
        then coalesce(v.rate_manually, round(
               case
                 when exists (select 1 from crm.b2b_product_roles r
                               where r.product = v.product_id and r.person = v.contact and r.role = 'Разработка')
                   then coalesce(public.b2b_role_rate_net(v.product_id, v.contact, 'Разработка'), 0)
                 when v.product_type = 6 then 0
                 else (cfg.sales_pct / 100.0) * coalesce(v.price, 0) / nullif(coalesce(nullif(v.masters, 0), cnt.masters_count), 0)
               end / (1 - v.tax / 100.0), 0))
      -- Лид / Аккаунтинг: особая ставка роли ('Лид' / 'Аккаунт'), иначе 5 % стоимости. Без налога; повторный заказ не обнуляет.
      when v.is_new_b2b and v.kind in ('Лид', 'Аккаунтинг')
        then coalesce(v.rate_manually, public.b2b_role_rate_net(v.product_id, v.contact,
               case when v.kind = 'Аккаунтинг' then 'Аккаунт' else 'Лид' end))
      -- ===== дальше - прежние правила (всё, что стартовало до 01.10.2026, и не-B2B) =====
      when v.kind in ('Лид', 'Аккаунтинг')
        then coalesce(v.rate_manually, (cfg.sales_pct / 100.0) * coalesce(v.price, 0))
      -- Разработка: ручная корректировка (rate_manually) = итоговая сумма, как у Лида/Аккаунтинга.
      -- Без корректировки с 01.09.2026: 5 % от стоимости сделки поровну между разработчиками + налог мастера делением: / (1 − налог), округление до рубля (решение 29.09.2026).
      -- До 01.09.2026: 7500 × часы программы / число мастеров × (1 + налог).
      when v.kind = 'Разработка'
        then coalesce(v.rate_manually, case
          when v.dt >= timestamptz '2026-09-01 00:00:00+03'
            then round((cfg.sales_pct / 100.0) * coalesce(v.price, 0) / nullif(coalesce(nullif(v.masters, 0), cnt.masters_count), 0) / (1 - v.tax / 100.0), 0)
          else (cfg.dev_rate * coalesce(v.program_hours, 0) / nullif(cnt.masters_count, 0)) * (1 + v.tax / 100.0)
        end)
      -- Проведение: B2B-ставки в hrm.rates_for_masters и rate_manually уже с налогом, налог сверху не добавляем (решение 29.09.2026)
      when v.kind = 'Проведение'
        then coalesce(v.rate_manually, (
                select case
                    when v.masters = 1 then b.rate_for_one
                    when v.masters = 2 then b.rate_for_two
                    when v.masters = 3 then b.rate_for_three
                    when v.masters >= 4 then b.rate_for_four
                    when cnt.masters_count = 1 then b.rate_for_one
                    when cnt.masters_count = 2 then b.rate_for_two
                    when cnt.masters_count = 3 then b.rate_for_three
                    when cnt.masters_count >= 4 then b.rate_for_four
                    else b.rate_for_one
                  end
                from hrm.rates_for_masters b
                where b.master = v.contact and b.type = 23
                  and v.dt >= coalesce(b.start_date, date '2000-01-01')
                  and v.dt <= coalesce(b.end_date + interval '1 day' - interval '1 minute', timestamptz '2100-01-01')
                limit 1
              )) * v.hrs
      -- B2C: ставки в hrm.rates_for_masters и rate_manually уже с налогом, налог сверху не добавляем (решение 29.09.2026)
      else coalesce(v.rate_manually, (
                select case
                    when v.lesson_name in ('ACT03-2021H1', 'ACT10-2021H2', 'ACT03-2022H1') then r.rate_for_one
                    when v.masters = 1 then r.rate_for_one
                    when v.masters = 2 then r.rate_for_two
                    when v.masters = 3 then r.rate_for_three
                    when v.masters >= 4 then r.rate_for_four
                    when cnt.masters_count = 1 then r.rate_for_one
                    when cnt.masters_count = 2 then r.rate_for_two
                    when cnt.masters_count = 3 then r.rate_for_three
                    when cnt.masters_count >= 4 then r.rate_for_four
                  end
                from hrm.rates_for_masters r
                where r.master = v.contact and r.type = v.class_type
                  and v.dt >= coalesce(r.start_date, date '2000-01-01')
                  and v.dt <= coalesce(r.end_date + interval '1 day' - interval '1 minute', timestamptz '2100-01-01')
                limit 1
              ), 0) * v.hrs
    end
  from v, cnt, cfg
$$;