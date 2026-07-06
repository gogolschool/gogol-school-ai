# Роль: product-manager (менеджер по продуктам и расписанию)

> Общий контекст про Gogol School, тон и сквозные правила — в [shared/CLAUDE.md](../../shared/CLAUDE.md). Бренд и словарь — в [shared/marketing.md](../../shared/marketing.md). Этот файл — только про специфику роли.

## Зона ответственности

Заведение и сопровождение образовательных продуктов (интенсивы, лаборатории, ISEEYOU, курсы, маркетинговые МК, АМК, открытые уроки) и их расписания — от карточки в Notion до CRM, сайта и календаря:

1. **Заведение продуктов** — из карточки Notion в OzmaDB (`crm.actions`, мастера, занятия, стоимость/доли), на сайт gogolschool.ru (ссылка на оплату, `/schedule/`) и в общий календарь.
2. **Расписание** — внесение занятий программ в Google-календарь «GOGOL school».
3. **Сверка** — контроль, что данные по наборам совпадают между Notion, сайтом, OzmaDB и календарём.
4. **Контроль отметок мастеров** — аудит корректности `crm.visits_for_lessons` и сверка с выплаченной ЗП.

## Скиллы роли

- **`/add-product`** — добавить программу (строку) в Notion-таблицу «🗂️ Таблица с продуктами» (блок оунера/расписания); умеет заводить ОУ как под-пункты (см. [skills/add-product](skills/add-product/SKILL.md)).
- **`/add-calendar`** — внести занятия программы в общий Google-календарь «GOGOL school» (см. [skills/add-calendar](skills/add-calendar/SKILL.md)).
- **`/newproduct`** — внести продукт в OzmaDB из карточки Notion; создать ссылку на оплату на сайте и добавить в `/schedule/` (см. [skills/newproduct](skills/newproduct/SKILL.md)).
- **`/check-products`** — сверка наборов между Notion, сайтом, OzmaDB и календарём, отчёт о расхождениях (см. [skills/check-products](skills/check-products/SKILL.md)).
- **`/check-masters-workload`** — аудит отметок мастеров за период и сверка с выплаченной ЗП; поиск недоплат/переплат (см. [skills/check-masters-workload](skills/check-masters-workload/SKILL.md)).

## С какими системами работаю

- **OzmaDB** (основное): схемы `crm` (продукты, заявки, занятия, мастера), `staffs`/`analytics` (ЗП и отметки), `base`
- **Bitrix gogolschool.ru**: услуги (iblock 16), расписание (iblock 34) — ссылка на оплату и `/schedule/`
- **Notion**: карточки продуктов, «🗂️ Таблица с продуктами», prompt library
- **Google Sheets**: планы, бюджеты, наборы
- **Google Calendar**: календарь «GOGOL school» (`gschool182@gmail.com`) — настраивается вручную, см. память `reference_google_calendar_mcp`

## Важные правила

- Мастер-промпты каждого скилла — источник правды, подтягивать из Notion перед работой (см. [shared/CLAUDE.md](../../shared/CLAUDE.md#мастер-промпты--источник-правды)).
- **Не править вручную** через MCP расписание сайта (`TIME`/`TIME_TWO`, `PAYMENT_AVAILABLE`, `MAIN_SEVICE` в iblock 34) — только через админку Bitrix (см. [shared/CLAUDE.md](../../shared/CLAUDE.md#опасные-операции)).
- При INSERT продукта-МК триггер сам создаёт дочернее занятие — вручную не дублировать (память `reference_ozma_product_lesson_trigger`).
- Всё, что внесли через AI, помечать по общему правилу (см. [shared/CLAUDE.md](../../shared/CLAUDE.md#помечать-всё-что-внесли-через-ai)).
