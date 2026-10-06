# B2C-ставки мастеров по правилам на руки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ставка смены на лабе/интенсиве/курсе с 01.09.2026 считается из категории мастера и общих правил на руки, налог добавляется в расчёте; исключения — особая ставка на продукте; прошлое не меняется.

**Architecture:** Всё в OzmaDB. Новые справочники с датой начала (`hrm.*`), поля в `crm.masters_for_actions` и `crm.actions`. SQL-функция `b2c_visit_rate_info(visit)` считает ставку и её источник; `calc_visit_payout` получает новую ветку и налог из истории. Число платных на день занятия фиксирует ночной Python-скрипт на сервере ozma (по образцу `scripts/b2b_product_tasks`). Все вью, считающие ставку × часы сами, переводятся на `calc_visit_payout`.

**Tech Stack:** OzmaDB (FunQL, PostgreSQL SQL в `public.user_functions`, JS-триггеры), ozma MCP (`create_entity`, `upsert_column_field`, `create_user_function`, `transaction`, `funql_query`, `safe_update_view_query`, `safe_update_trigger_function`), Python 3 stdlib + pytest, systemd-таймер на сервере `ozma`.

**Спека:** `docs/superpowers/specs/2026-10-06-b2c-master-rates-design.md`

## Global Constraints

- Область: типы продукта «Лаборатория», «Интенсив», «Интенсив YNG», «Интенсив СПБ», «Курс»; смены с `visits_for_lessons."datetime" >= '2026-09-01 00:00:00+03'`; не режиссёр (`masters_for_actions.is_stage_producer`); `b2b_lesson_kind IS NULL`.
- Суммы категорий на руки с 01.09.2026: 1 — 2 500, 2 — 1 750, 3 — 1 500, 4 — 1 250 ₽/ч.
- Константы с 01.09.2026: шаг за мастера 250; курс ×1,3; надбавка 250; порог 25 платных.
- Шаг: 1 мастер 0, 2 мастера −250, 3 и больше −500 (4+ = как 3). Мастер без категории → 4-я.
- Порядок: `rate_manually` (итоговая, с налогом) → особая ставка на продукте (окончательная, без шага и надбавки, + налог) → правило.
- Налог сверху: ставка = ceil(на руки / (1 − налог/100)), округление вверх до рубля за час, затем × часы.
- Выплаченные записи ЗП не трогаем; рост → строка «доплата» генератором; снижение → оставляем.
- Всё вне области (смены до 01.09.2026, ОУ, HEAD, МК, АМК, B2B …) — суммы до копейки как до изменений.
- Запись метаданных Озмы — строго последовательно (параллельные падают «Another migration is in progress»); после любого неуспешного ответа перечитывать факт записи (`funql_query` по `public.user_views` / `public.user_functions` / `public.column_fields`).
- Время в базе UTC, показывать МСК (+3 ч).
- `backup/2026-10-XX_*` — `XX` = день выполнения шага; бэкапы коммитятся.

---

## Структура изменений

| Объект | Ответственность |
|---|---|
| `hrm.b2c_category_amounts` | сумма категории на руки с даты |
| `hrm.b2c_master_categories` | категория мастера с даты |
| `hrm.master_tax_history` | налог мастера с даты |
| `hrm.b2c_rate_rules` | шаг / курс / надбавка / порог с даты |
| `crm.masters_for_actions.special_rate_net`, `.special_reason` | особая ставка на продукте |
| `crm.actions.paid_requests_on_day` | зафиксированное число платных на занятии |
| `public.master_tax_on(int, date)` | налог на дату |
| `public.paid_requests_on(int, date)` | число платных на дату по правилу спеки (для снимка) |
| `public.b2c_visit_rate_info(int)` → jsonb `{rate, net, source}` | ставка смены в области, null вне её |
| `public.b2c_visit_rate(int)`, `public.b2c_visit_rate_source(int)` | обёртки для FunQL |
| `public.calc_visit_payout(int)` (id 16) | новая ветка + налог из истории |
| `scripts/b2c_paid_snapshot/` | ночной снимок платных (скрипт, тесты, unit-файлы) |
| 9 вью ЗП/актов | переход на `calc_visit_payout` |
| `crm.b2c_rates_check` | проверка перед ЗП |

---

### Task 1: Справочники и поля

**Files:** метаданные Озмы (через MCP), без файлов в репо.

**Interfaces:**
- Produces: сущности `hrm.b2c_category_amounts(category int, amount_net decimal, start_date date)`, `hrm.b2c_master_categories(master ref base.people, category int, start_date date, comment string)`, `hrm.master_tax_history(master ref base.people, tax_percent decimal, start_date date)`, `hrm.b2c_rate_rules(start_date date, step_per_master decimal, course_multiplier decimal, high_rate_extra decimal, high_rate_threshold int)`; поля `crm.masters_for_actions.special_rate_net decimal null`, `crm.masters_for_actions.special_reason string null`, `crm.actions.paid_requests_on_day int null`.

- [ ] **Step 1: Создать сущности** — по одной, последовательно:

```
create_entity(schema="hrm", name="b2c_category_amounts")
create_entity(schema="hrm", name="b2c_master_categories")
create_entity(schema="hrm", name="master_tax_history")
create_entity(schema="hrm", name="b2c_rate_rules")
```

- [ ] **Step 2: Поля** — по одному вызову `upsert_column_field`:

```
hrm.b2c_category_amounts: category int (not null), amount_net decimal (not null), start_date date (not null)
hrm.b2c_master_categories: master reference(base.people) (not null), category int (not null), start_date date (not null), comment string (null)
hrm.master_tax_history: master reference(base.people) (not null), tax_percent decimal (not null), start_date date (not null)
hrm.b2c_rate_rules: start_date date (not null), step_per_master decimal (not null), course_multiplier decimal (not null), high_rate_extra decimal (not null), high_rate_threshold int (not null)
crm.masters_for_actions: special_rate_net decimal (null), special_reason string (null)
crm.actions: paid_requests_on_day int (null)
```

- [ ] **Step 3: Уникальность** — `upsert_unique_constraint`:

```
hrm.b2c_category_amounts  name=category_start  columns=[category, start_date]
hrm.b2c_master_categories name=master_start    columns=[master, start_date]
hrm.master_tax_history    name=master_start    columns=[master, start_date]
hrm.b2c_rate_rules        name=start           columns=[start_date]
```

- [ ] **Step 4: Проверить факт записи**

```sql
SELECT cf.entity_id=>schema_id=>name AS sch, cf.entity_id=>name AS ent, cf.name, cf.type
FROM public.column_fields AS cf
WHERE (cf.entity_id=>schema_id=>name = 'hrm' AND cf.entity_id=>name IN ('b2c_category_amounts','b2c_master_categories','master_tax_history','b2c_rate_rules'))
   OR (cf.entity_id=>name = 'masters_for_actions' AND cf.name IN ('special_rate_net','special_reason'))
   OR (cf.entity_id=>name = 'actions' AND cf.name = 'paid_requests_on_day')
ORDER BY 1, 2, 3
```

Expected: 18 строк (3 + 4 + 3 + 5 + 2 + 1), типы как в Step 2.

- [ ] **Step 5: Записать в спеке «Статус: в реализации», коммит**

```bash
git add docs/superpowers/specs/2026-10-06-b2c-master-rates-design.md
git commit -m "B2C-ставки: справочники и поля в Озме созданы"
```

---

### Task 2: Наполнение справочников

**Interfaces:**
- Consumes: сущности Task 1.
- Produces: данные с `start_date = 2026-09-01`.

- [ ] **Step 1: Суммы категорий и правила** — `transaction`:

```json
[
 {"type":"insert","entity":{"schema":"hrm","name":"b2c_category_amounts"},"entries":{"category":1,"amount_net":2500,"start_date":"2026-09-01"}},
 {"type":"insert","entity":{"schema":"hrm","name":"b2c_category_amounts"},"entries":{"category":2,"amount_net":1750,"start_date":"2026-09-01"}},
 {"type":"insert","entity":{"schema":"hrm","name":"b2c_category_amounts"},"entries":{"category":3,"amount_net":1500,"start_date":"2026-09-01"}},
 {"type":"insert","entity":{"schema":"hrm","name":"b2c_category_amounts"},"entries":{"category":4,"amount_net":1250,"start_date":"2026-09-01"}},
 {"type":"insert","entity":{"schema":"hrm","name":"b2c_rate_rules"},"entries":{"start_date":"2026-09-01","step_per_master":250,"course_multiplier":1.3,"high_rate_extra":250,"high_rate_threshold":25}}
]
```

- [ ] **Step 2: Категории мастеров** (из листа «ставки мастеров 2026-2027», колонка «один мастер»: 2500 → 1, 1750 → 2, 1500 → 3) — `transaction`, 19 вставок в `hrm.b2c_master_categories` с `start_date = "2026-09-01"`:

| master id | мастер | category |
|---|---|---|
| 12 | Белых | 1 |
| 11362 | Вавилина | 1 |
| 9252 | Жарова | 1 |
| 20 | Карабань | 1 |
| 24 | Любимов | 1 |
| 2403 | Миххалёв | 1 |
| 25 | Нарутто | 1 |
| 27 | Петров | 1 |
| 29 | Старцев | 1 |
| 31 | Тимошенко | 1 |
| 8302 | Шадрин | 1 |
| 6860 | Шуйская | 1 |
| 33 | Шуйский | 1 |
| 17603 | Казанский | 2 |
| 11058 | Лашкевич | 2 |
| 12271 | Арутюнян | 2 |
| 10761 | Повтарь | 2 |
| 14927 | Пушкин | 2 |
| 8353 | Сучкова | 3 |

Формат одной операции: `{"type":"insert","entity":{"schema":"hrm","name":"b2c_master_categories"},"entries":{"master":12,"category":1,"start_date":"2026-09-01"}}`.

- [ ] **Step 3: История налога** — сначала список:

```sql
SELECT p.id, p.main_name, p.tax FROM base.people AS p WHERE p.is_employee AND p.tax IS NOT NULL ORDER BY p.id
```

Для каждой строки — вставка `{"type":"insert","entity":{"schema":"hrm","name":"master_tax_history"},"entries":{"master":<id>,"tax_percent":<tax>,"start_date":"2026-09-01"}}` одной `transaction`. Если строк > 50 — `funql_query` с `offset`.

- [ ] **Step 4: Проверка**

```sql
SELECT (SELECT count(*) FROM hrm.b2c_category_amounts) AS amounts,
       (SELECT count(*) FROM hrm.b2c_rate_rules) AS rules,
       (SELECT count(*) FROM hrm.b2c_master_categories) AS cats,
       (SELECT count(*) FROM hrm.master_tax_history) AS taxes,
       (SELECT count(*) FROM base.people WHERE is_employee AND tax IS NOT NULL) AS taxes_expected
```

Expected: amounts 4, rules 1, cats 19, taxes = taxes_expected.

---

### Task 3: SQL-функции расчёта

**Interfaces:**
- Consumes: Task 1–2.
- Produces:
  - `public.master_tax_on(_person integer, _date date) returns numeric` — null, если налога нет нигде;
  - `public.paid_requests_on(_product integer, _date date) returns integer`;
  - `public.b2c_visit_rate_info(_visit integer) returns jsonb` — `{"rate": numeric, "net": numeric, "source": text}` или null вне области;
  - `public.b2c_visit_rate(_visit integer) returns numeric`;
  - `public.b2c_visit_rate_source(_visit integer) returns text`.

- [ ] **Step 1: Узнать тип `fin.transactions.transaction_date`**

```sql
SELECT cf.name, cf.type FROM public.column_fields AS cf
WHERE cf.entity_id=>schema_id=>name = 'fin' AND cf.entity_id=>name = 'transactions'
  AND cf.name IN ('transaction_date', 'real_transaction_datetime')
```

Если `transaction_date` — `date`, в Step 3 оставить `t.transaction_date`; если `datetime` — заменить на `(t.transaction_date at time zone 'Europe/Moscow')::date`.

- [ ] **Step 2: `master_tax_on`** — `create_user_function(schema="public", name="master_tax_on", signature="(integer,date)", priority=10, ddl=…)`:

```sql
create or replace function public.master_tax_on(_person integer, _date date)
returns numeric
language sql
stable
as $$
  -- Налог мастера на дату: история hrm.master_tax_history, иначе текущее поле карточки.
  select coalesce(
    (select h.tax_percent
       from hrm.master_tax_history h
      where h.master = _person and h.start_date <= _date
      order by h.start_date desc
      limit 1),
    (select nullif(c.people__tax::text, '')::numeric from base.contacts c where c.id = _person)
  )
$$;
```

- [ ] **Step 3: `paid_requests_on`** — `create_user_function(…, name="paid_requests_on", signature="(integer,date)", priority=10)`:

```sql
create or replace function public.paid_requests_on(_product integer, _date date)
returns integer
language sql
stable
as $$
  -- Платные заявки на продукт на день (спека B2C-ставок): чистые деньги > 0 на дату
  -- (платежи нам минус возвраты от нас) и нет отмены на дату.
  with req as (
    select r.id, r.request_status
    from crm.actions_for_contacts r
    where r.action = _product
  ),
  money as (
    select t.request,
           sum(case when coalesce(c.is_our_organization, false) then -t.amount else t.amount end) as net
    from fin.transactions t
      join req on req.id = t.request
      left join fin.accounts af on af.id = t.account_from
      left join base.contacts c on c.id = af.contractor
    where not t.is_deleted and not t.is_plan
      and coalesce((t.real_transaction_datetime at time zone 'Europe/Moscow')::date, t.transaction_date) <= _date
    group by t.request
  )
  select count(*)::int
  from req
    join money on money.request = req.id
  where money.net > 0
    and not exists (select 1 from crm.refund_requests rr
                     where rr.request = req.id
                       and (rr.cancel_request_date at time zone 'Europe/Moscow')::date <= _date)
    and not (req.request_status in (9, 15)  -- «Отмена», «Отмена без возврата»
             and not exists (select 1 from crm.refund_requests rr2 where rr2.request = req.id))
$$;
```

- [ ] **Step 4: Тест `paid_requests_on`**

```sql
SELECT paid_requests_on(12048, '2026-10-06'::date) AS lab01,
       paid_requests_on(12049, '2026-10-06'::date) AS lab02,
       paid_requests_on(12050, '2026-10-06'::date) AS lab03,
       paid_requests_on(12051, '2026-10-06'::date) AS lab04,
       paid_requests_on(12043, '2026-10-06'::date) AS lcourse01
```

Expected: lab01 14, lab02 21, lab03 25, lab04 23, lcourse01 17 (сверено 06.10.2026: «Обучается» с оплатой > 0). Если не совпало — вывести заявки, которые дают разницу (`id`, статус, net, дата отмены), и разобрать; не подгонять.

- [ ] **Step 5: `b2c_visit_rate_info`** — `create_user_function(…, name="b2c_visit_rate_info", signature="(integer)", priority=20)`:

```sql
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
```

- [ ] **Step 6: Обёртки** — две `create_user_function` (priority 30):

```sql
create or replace function public.b2c_visit_rate(_visit integer)
returns numeric language sql stable as $$
  select (public.b2c_visit_rate_info(_visit)->>'rate')::numeric
$$;
```

```sql
create or replace function public.b2c_visit_rate_source(_visit integer)
returns text language sql stable as $$
  select public.b2c_visit_rate_info(_visit)->>'source'
$$;
```

- [ ] **Step 7: Тест ставок** (поле `paid_requests_on_day` ещё пустое → надбавки нет):

```sql
SELECT v.id, v.contact=>main_name AS m, b2c_visit_rate(v.id) AS rate, b2c_visit_rate_source(v.id) AS src
FROM crm.visits_for_lessons AS v WHERE v.id IN (160553, 160551, 161186, 160996, 160618, 161577, 157297)
```

Expected:
- 160553 Пушкин, LAB01 01.09, 2 мастера, кат. 2, 8 % → 1500/0,92 → **1631**;
- 160551 Карабань, там же, кат. 1, 6 % → 2250/0,94 → **2394**;
- 161186 Тимошенко, LAB04 24.09, 3 мастера → 2000/0,94 → **2128**;
- 160996 Петров, LAB01-2026H2, 2 мастера → **2394**;
- 160618 (ОУ), 161577 (B2B), 157297 (июль) → **null** (вне области).

---

### Task 4: Ночной снимок «Платных на день занятия»

**Files:**
- Create: `scripts/b2c_paid_snapshot/b2c_paid_snapshot.py`
- Create: `scripts/b2c_paid_snapshot/test_b2c_paid_snapshot.py`
- Create: `scripts/b2c_paid_snapshot/b2c-paid-snapshot.service`
- Create: `scripts/b2c_paid_snapshot/b2c-paid-snapshot.timer`

**Interfaces:**
- Consumes: `public.paid_requests_on(int, date)`, поле `crm.actions.paid_requests_on_day`.
- Produces: заполненное поле на прошедших занятиях области с 01.09.2026.

- [ ] **Step 1: Тест**

```python
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import b2c_paid_snapshot as bs  # noqa: E402


def test_operations_from_rows():
    rows = [{"id": 12170, "n": 23}, {"id": 12171, "n": 0}]
    assert bs.operations(rows) == [
        {"type": "update", "entity": {"schema": "crm", "name": "actions"}, "id": 12170,
         "entries": {"paid_requests_on_day": 23}},
        {"type": "update", "entity": {"schema": "crm", "name": "actions"}, "id": 12171,
         "entries": {"paid_requests_on_day": 0}},
    ]


def test_operations_skip_null_count():
    assert bs.operations([{"id": 1, "n": None}]) == []


def test_query_has_scope_and_date():
    q = bs.LESSONS_QUERY
    assert "paid_requests_on_day IS NULL" in q
    assert "'Интенсив СПБ'" in q and "'Курс'" in q
    assert "2026-08-31T21:00:00Z" in q
```

- [ ] **Step 2: Запустить — падает**

Run: `cd scripts/b2c_paid_snapshot && python3 -m pytest -q`
Expected: FAIL `ModuleNotFoundError: No module named 'b2c_paid_snapshot'`

- [ ] **Step 3: Скрипт**

```python
#!/usr/bin/env python3
"""Фиксирует «Платных на день занятия» (crm.actions.paid_requests_on_day) на прошедших занятиях
лаб/интенсивов/курсов с 01.09.2026. Число считает SQL-функция paid_requests_on.
Запуск — systemd-таймер b2c-paid-snapshot.timer раз в сутки ночью.
Спека: docs/superpowers/specs/2026-10-06-b2c-master-rates-design.md

CLI:
  python3 b2c_paid_snapshot.py            # записать
  python3 b2c_paid_snapshot.py --dry-run  # напечатать, что записал бы
Креды Озмы — из ~/.claude.json (mcpServers.ozma.headers), как у b2b_product_tasks.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

LESSONS_QUERY = """
SELECT l.id AS id,
       paid_requests_on(l.parent_action, (l.action_datetime + '3 hours'::interval)::date) AS n
FROM crm.actions AS l
WHERE l.type = 'Занятие' AND NOT l.is_deleted
  AND l.paid_requests_on_day IS NULL
  AND l.action_datetime >= '2026-08-31T21:00:00Z'::datetime
  AND (l.action_datetime + '3 hours'::interval)::date < ($$transaction_time + '3 hours'::interval)::date
  AND l.parent_action=>class_type=>name IN ('Лаборатория', 'Интенсив', 'Интенсив YNG', 'Интенсив СПБ', 'Курс')
ORDER BY l.action_datetime
"""


def operations(rows):
    return [{"type": "update", "entity": {"schema": "crm", "name": "actions"}, "id": r["id"],
             "entries": {"paid_requests_on_day": int(r["n"])}}
            for r in rows if r.get("n") is not None]


def ozma_creds(path=None):
    path = path or os.path.expanduser("~/.claude.json")
    with open(path) as f:
        return json.load(f)["mcpServers"]["ozma"]["headers"]


def ozma_token(h):
    body = urllib.parse.urlencode({
        "grant_type": "password", "client_id": h["X-Ozma-Client-ID"], "client_secret": h["X-Ozma-Client-Secret"],
        "username": h["X-Ozma-Username"], "password": h["X-Ozma-Password"]}).encode()
    with urllib.request.urlopen(h["X-Ozma-Auth-URL"], body, timeout=60) as r:
        return json.load(r)["access_token"]


def ozma_rows(h, token, query):
    url = h["X-Ozma-URL"].rstrip("/") + "/views/anonymous/entries?" + urllib.parse.urlencode({"__query": query})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    names = [c["name"] for c in d["info"]["columns"]]
    return [{n: v.get("value") for n, v in zip(names, row["values"])} for row in d["result"]["rows"]]


def ozma_transaction(h, token, ops):
    url = h["X-Ozma-URL"].rstrip("/") + "/transaction"
    req = urllib.request.Request(url, data=json.dumps({"operations": ops}).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    h = ozma_creds()
    token = ozma_token(h)
    ops = operations(ozma_rows(h, token, LESSONS_QUERY))
    for op in ops:
        print(op["id"], op["entries"]["paid_requests_on_day"])
    if ops and not args.dry_run:
        ozma_transaction(h, token, ops)
    print("занятий: %d%s" % (len(ops), " (dry-run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Тесты проходят**

Run: `cd scripts/b2c_paid_snapshot && python3 -m pytest -q`
Expected: `3 passed`

- [ ] **Step 5: Unit-файлы**

`b2c-paid-snapshot.service`:

```ini
[Unit]
Description=Озма: «Платных на день занятия» на прошедших занятиях лаб/интенсивов/курсов
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=agentbot
WorkingDirectory=/home/agentbot/bots/b2c-paid-snapshot
ExecStart=/usr/bin/python3 /home/agentbot/bots/b2c-paid-snapshot/b2c_paid_snapshot.py
TimeoutStartSec=600
```

`b2c-paid-snapshot.timer`:

```ini
[Unit]
Description=b2c-paid-snapshot раз в сутки в 03:10 МСК

[Timer]
OnCalendar=*-*-* 03:10:00 Europe/Moscow
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Локальный dry-run и первый (бэкфилл) прогон**

Run: `python3 scripts/b2c_paid_snapshot/b2c_paid_snapshot.py --dry-run`
Expected: список прошедших занятий с 01.09 и числами; LAB03-2026H2 — около 25, LAB04 — ≤ 26.
Показать Бэлле занятия с n ≥ 25 и только после «да» запустить без `--dry-run`.

- [ ] **Step 7: Деплой на ozma**

```bash
ssh ozma 'sudo mkdir -p /home/agentbot/bots/b2c-paid-snapshot'
scp scripts/b2c_paid_snapshot/b2c_paid_snapshot.py ozma:/tmp/
ssh ozma 'sudo mv /tmp/b2c_paid_snapshot.py /home/agentbot/bots/b2c-paid-snapshot/ && sudo chown -R agentbot:agentbot /home/agentbot/bots/b2c-paid-snapshot'
scp scripts/b2c_paid_snapshot/b2c-paid-snapshot.service scripts/b2c_paid_snapshot/b2c-paid-snapshot.timer ozma:/tmp/
ssh ozma 'sudo mv /tmp/b2c-paid-snapshot.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl start b2c-paid-snapshot.service && sudo journalctl -u b2c-paid-snapshot -n 20 --no-pager'
ssh ozma 'sudo systemctl enable --now b2c-paid-snapshot.timer && systemctl list-timers | grep b2c'
```

Expected: в журнале «занятий: 0» (всё уже заполнено бэкфиллом); таймер в списке. Если у `agentbot` нет `~/.claude.json` с кредами ozma — взять путь из `b2b-product-tasks.service` на сервере и повторить.

- [ ] **Step 8: Коммит**

```bash
git add scripts/b2c_paid_snapshot
git commit -m "b2c-paid-snapshot: ночной снимок платных на день занятия"
```

---

### Task 5: Сверка до включения (стоп-точка)

**Interfaces:**
- Consumes: `b2c_visit_rate`, `calc_visit_payout` (ещё старый), заполненное `paid_requests_on_day`.
- Produces: отчёт Бэлле; дальше только после её «да».

- [ ] **Step 1: Смены области, где новая сумма ≠ текущей**

```sql
SELECT v.id, v.contact=>main_name AS m, v.action=>parent_action=>name AS prod,
       (v."datetime" + '3 hours'::interval) AS dt_msk, v.masters, v.rate_manually AS rm, v.high_rate_applied AS hra,
       calc_visit_payout(v.id) AS now_sum,
       b2c_visit_rate(v.id) * COALESCE(v.hours_manually, v.hours_calculated) AS new_sum,
       b2c_visit_rate_source(v.id) AS src
FROM crm.visits_for_lessons AS v
WHERE v.datetime_end IS NOT NULL AND b2c_visit_rate(v.id) IS NOT NULL
  AND calc_visit_payout(v.id) <> b2c_visit_rate(v.id) * COALESCE(v.hours_manually, v.hours_calculated)
ORDER BY v."datetime"
```

Разложить строки на группы: (а) |разница| ≤ 4 ₽ × часы — округление; (б) `hra = true` — надбавка триггера, после Task 6 её посчитает формула; (в) `rm` не пусто и `hra = false` — ручная ставка человека; (г) прочее — разобрать причину (категория, налог, число мастеров).

- [ ] **Step 2: Сводка по мастерам** — та же выборка, сгруппированная по мастеру и месяцу: Σ now_sum, Σ new_sum (для (б) new_sum — по формуле), Δ.

- [ ] **Step 3: Отчёт Бэлле** — группы (а)–(г) таблицами, по (в) вопрос на каждую строку: «оставить разовой правкой / перенести в особую ставку на продукте (сумма на руки, причина)». **Стоп до ответа.**

---

### Task 6: Включение расчёта

**Interfaces:**
- Consumes: решения Бэллы по Task 5 (в).
- Produces: `calc_visit_payout` с новой веткой и налогом из истории; отключённые триггеры повышенной ставки.

- [ ] **Step 1: Контрольные суммы «до»** (сохранить вывод в `backup/2026-10-XX_calc_before.json`):

```sql
SELECT DATE_PART('year', v."datetime") AS y, DATE_PART('month', v."datetime") AS mo,
       v.action=>parent_action=>class_type=>name AS ct, SUM(calc_visit_payout(v.id)) AS s
FROM crm.visits_for_lessons AS v
WHERE v."datetime" >= '2026-05-31T21:00:00Z'::datetime AND v."datetime" IS NOT NULL
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
```

- [ ] **Step 2: Особые ставки** по решениям Task 5 (в) — `transaction` update `crm.masters_for_actions` (`special_rate_net`, `special_reason`) и `rate_manually: null` на перенесённых отметках.

- [ ] **Step 3: Снять надбавки триггеров** на сменах области с 01.09:

```sql
SELECT v.id, v.high_rate_prev_manual FROM crm.visits_for_lessons AS v
WHERE v.high_rate_applied AND v."datetime" >= '2026-08-31T21:00:00Z'::datetime AND b2c_visit_rate(v.id) IS NOT NULL
```

Для каждой строки — `update crm.visits_for_lessons` `{rate_manually: <high_rate_prev_manual>, high_rate_prev_manual: null, high_rate_applied: false}` одной `transaction`.

- [ ] **Step 4: Отключить триггеры 145, 146, 195** — `safe_update_trigger_function` по одному, вставка первой строкой тела `handleEvent`:

```javascript
  // Отключено: с 01.09.2026 надбавку B2C считает calc_visit_payout (спека 2026-10-06-b2c-master-rates-design).
  return true;
```

`from_text` для 145/146/195 — первая строка тела `handleEvent` (взять из `get_trigger_code`), `to_text` — она же с вставкой выше. Проверка: `get_trigger_code` содержит «Отключено: с 01.09.2026».

- [ ] **Step 5: Новая `calc_visit_payout`** — взять текущий `ddl` (`SELECT ddl FROM public.user_functions WHERE id = 16`), сохранить в `backup/2026-10-XX_calc_visit_payout_before.sql`, внести две правки и записать `transaction` update `public.user_functions` id 16 `{ddl: <новый текст>}`:

1. Налог из истории — заменить строку
   `coalesce(nullif(p.people__tax::text, '')::numeric, 0) as tax,`
   на
   `coalesce(public.master_tax_on(vfl.contact, (vfl."datetime" at time zone 'Europe/Moscow')::date), 0) as tax,`
2. Новая ветка — перед строкой `-- B2C: ставки в hrm.rates_for_masters и rate_manually уже с налогом, налог сверху не добавляем (решение 29.09.2026)` вставить:

```sql
      -- B2C по правилам на руки (лаба/интенсив/курс с 01.09.2026, спека 2026-10-06): ручная ставка, иначе b2c_visit_rate.
      when public.b2c_visit_rate(v.id) is not null
        then coalesce(v.rate_manually, public.b2c_visit_rate(v.id)) * v.hrs
```

- [ ] **Step 6: Контрольные суммы «после»** — запрос Step 1. Expected: все строки до 2026-09 и все типы вне области за 09–10.2026 совпадают до копейки с «до»; изменились только области за 09–10.2026 на величину сводки Task 5.

- [ ] **Step 7: Повышенная на примере** — `SELECT calc_visit_payout(161030)` (Миххалёв, LAB03 20.09). Expected: 2990 × 4 = 11 960, если у занятия `paid_requests_on_day ≥ 25`, иначе 2718 × 4 = 10 872.

- [ ] **Step 8: Коммит бэкапов**

```bash
git add backup/2026-10-*_calc_*
git commit -m "B2C-ставки: calc_visit_payout — новая ветка, налог из истории; триггеры повышенной отключены"
```

---

### Task 7: Перевод вью на `calc_visit_payout`

**Вью:** `analytics.masters_time_for_lessons`, `analytics.masters_time_for_lessons_lost`, `analytics.masters_salary_by_fin_direction`, `analytics.masters_salary_by_fin_direction_one`, `analytics.masters_konsol_tasks_total`, `docs.acts_for_contracts_view`, `docs.lessons_for_act_info`, `docs.total_sum_of_lessons_price_info`, `staffs.report_amount_by_contracts_staffs`.

**Правило замены (в каждой вью):**
- сумма смены `(CASE WHEN rate_manually IS NULL THEN (CASE … rates_for_masters.rate_for_* … END) ELSE rate_manually END) * COALESCE(hours_manually, hours_calculated)` → `calc_visit_payout(<алиас visits>.id)`;
- колонка ставки → `CASE WHEN COALESCE(<алиас>.hours_manually, <алиас>.hours_calculated) > 0 THEN calc_visit_payout(<алиас>.id) / COALESCE(<алиас>.hours_manually, <алиас>.hours_calculated) END`;
- в `analytics.masters_time_for_lessons` добавить после колонки ставки:

```
    b2c_visit_rate_source(visits_for_lessons.id) AS rate_source @{
        caption = 'Откуда ставка',
        column_width = 220
    },
```

- джойны `hrm.rates_for_masters` / CTE подсчёта мастеров, ставшие неиспользуемыми, удалить.

Для каждой вью отдельно:

- [ ] **Step 1: Снимок «до»** — итог вью за 06, 07, 08, 09.2026 (для актов — по 3 мастерам: Миххалёв 2403, Пушкин 14927, Старцев 29), сохранить в `backup/2026-10-XX_<view>_before.json`. Сохранить текст вью: `get_user_view_query(full=true)` → `backup/2026-10-XX_<view>.funql`.
- [ ] **Step 2: Правка** — `safe_update_view_query` с полным `new_query`, `validate_before_commit: true`; при пустой/валидационной ошибке перечитать `SELECT query LIKE '%calc_visit_payout%' FROM public.user_views WHERE id = <id>`.
- [ ] **Step 3: Снимок «после»** тем же запросом. Expected: 06–08.2026 совпадают; 09.2026 отличаются только на сводку Task 5. Любое расхождение за 06–08 — показать Бэлле (значит, вью раньше считала иначе, чем ведомость) до продолжения.
- [ ] **Step 4: Коммит бэкапов вью**

```bash
git add backup/2026-10-*
git commit -m "B2C-ставки: <view> считает через calc_visit_payout"
```

---

### Task 8: Где видно

- [ ] **Step 1: Справочники** — вью `hrm.b2c_rates_settings` (форма с тремя таблицами: суммы категорий, правила, категории мастеров; каждая с `@create_link` и редактированием по образцу `hrm.b2b_conduct_rates_table`), пункт в меню «Справочники → Финансы» рядом с B2B-ставками.
- [ ] **Step 2: Карточка мастера** (`base.person_form`) — блок «Категория и налог»: история категорий и налога (две вложенные таблицы `hrm.b2c_master_categories` / `hrm.master_tax_history` с `$master`), видимость — роли со ставками.
- [ ] **Step 3: Карточка продукта** (`crm.class_form`) — в блоке мастеров (найти по `masters_for_actions` в тексте формы) колонки «Особая ставка, на руки/ч» (`special_rate_net`), «Причина» (`special_reason`); видимость только для типов области.
- [ ] **Step 4: Карточка занятия** (`crm.lesson_form`) — поле «Платных на день занятия» (`paid_requests_on_day`), редактируемое.
- [ ] **Step 5: Проверка** — `named_view_info` каждой формы содержит новые колонки; открыть в браузере `https://ozma.gogol.school/views/crm/class_form?id=12050`, `…/crm/lesson_form?id=12170`, `…/base/person_form?id=14927` — без ошибок.

---

### Task 9: Проверка перед ЗП

- [ ] **Step 1: Вью `crm.b2c_rates_check`** (`create_user_view`), строка = проблема, колонки: мастер, продукт, занятие (дата МСК), проблема. Источники проблем (UNION ALL, по образцу `crm.b2b_rates_check`):
  1. смена области (`b2c_visit_rate(v.id) IS NOT NULL`) у мастера без строки в `hrm.b2c_master_categories` на дату — «нет категории, посчитано по 4-й»;
  2. `masters_for_actions.special_rate_net IS NOT NULL AND COALESCE(special_reason, '') = ''` — «особая ставка без причины»;
  3. смена области, `master_tax_on(contact, дата) IS NULL` — «не заполнен налог»;
  4. смена области, `rate_manually IS NOT NULL AND rate_manually <> b2c_visit_rate(v.id)` — «ручная ставка ≠ расчётной»;
  5. смена области, число мастеров ≥ 4 — «4+ мастера, посчитано как 3»;
  6. занятие области прошло (дата < сегодня МСК), `paid_requests_on_day IS NULL` — «не зафиксировано число платных».
  Фильтр `$month`/`$year` по дате смены.
- [ ] **Step 2: Тест** — `funql_query` к вью за 09.2026: ожидаются только осознанные строки (ручные ставки, оставленные по решению Task 5).
- [ ] **Step 3: Текст для мастер-промпта `/check-masters-workload`** (раздел «Проверки», новая буква T: «B2C-ставки — открыть `crm.b2c_rates_check` за месяц, каждую строку разобрать до генерации ЗП») — отдать Бэлле текстом в чат; Notion не править без её прямой просьбы.

---

### Task 10: Перегенерация ЗП за 09–10.2026

- [ ] **Step 1: Список мастеров со сменами области**

```sql
SELECT DISTINCT v.contact AS mid, v.contact=>main_name AS m
FROM crm.visits_for_lessons AS v
WHERE b2c_visit_rate(v.id) IS NOT NULL AND v."datetime" < '2026-10-31T21:00:00Z'::datetime
```

- [ ] **Step 2: Снимок ЗП-статуса** — `SELECT id, master, month, year, fin_project, amount, description, payment_date, economics_id FROM staffs.masters_salary_records WHERE year = 2026 AND month IN (9, 10)` → `backup/2026-10-XX_salary_before.json`.
- [ ] **Step 3: Генерация** — для каждого мастера и месяца 9 (и 10, если октябрь уже генерировали) `run_action(staffs, generate_salary_records, {month, year: 2026, master})`, по одному.
- [ ] **Step 4: Итог Бэлле** — таблица мастер / месяц / было / стало / доплата (новые строки `description = 'доплата'`). Снижение не отражается — так и задумано. Напомнить: «Внести в экономику» выгружает все строки месяца без `economics_id`.
- [ ] **Step 5: Память и спека** — в `reference_master_rate_tax_status.md` дописать: B2C лаба/инт/курс с 01.09.2026 считаются по `b2c_visit_rate_info`, `hrm.rates_for_masters` для них больше не используется; триггеры 145/146/195 отключены. В спеке «Статус: реализовано». Коммит.

```bash
git add docs/superpowers/specs/2026-10-06-b2c-master-rates-design.md backup/2026-10-*_salary_before.json
git commit -m "B2C-ставки: включено, ЗП за 09–10.2026 перегенерирована"
```
