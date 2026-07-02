---
name: add-marketing-list
description: Формирование маркетингового списка рассылки в OzmaDB по сегменту. Считает аудиторию по критериям (LTV, тип продукта, мастера, прогрев, прочтение писем, город, баллы, наличие сертификата, «не был N времени», комбинации + исключения), показывает количество и preview, после подтверждения создаёт запись в marketing.lists и заливает контакты в marketing.contacts_in_lists пакетами. Подтягивает мастер-промты из Notion и работает по ним. Используй, когда просят «собери список», «сделай сегмент/аудиторию для рассылки», «список холодных / по мастеру X / LTV 50–100к / купивших интенсивы», «выгрузи контакты по критерию» — даже если слово «скилл» не звучит. НЕ заводит кампанию (это /new-campaign), НЕ клонирует шаблон письма, НЕ для TG-бот рассылок по мастерам (там отдельный промт выдаёт telegram_id, а не пишет в marketing.lists).
---

# /add-marketing-list

## Что делает

Собирает сегментированный список контактов в Озме: считает аудиторию по критериям → показывает количество и preview → после подтверждения создаёт `marketing.lists` и заливает контакты в `marketing.contacts_in_lists`. Возвращает ссылку на список. Только список — кампанию заводит `/new-campaign`, шаблон делает отдельный скилл.

## Как работает

1. **Загрузи актуальные мастер-промты из Notion** (рабочий MCP `c2755fd9-…`, не личный — память `feedback_notion_workspace_routing`):
   - **Воркфлоу заливки** — page `358612c762af8165bd45d03618eef4f7` (пошагово: посчитать → создать список → выгрузить contact_id пагинацией → батч-insert по 100 → дедуп → проверка).
   - **Зонтичная (источники + «что обязан уточнить»)** — page `36b612c762af81dab345d312eeb09c6c` (источник на каждый тип сегмента, готовые exclusion-подзапросы, готовые actions Озмы).
2. **Определи тип сегмента** и согласуй критерии (см. карту источников ниже). Не угадывай пороги/тематики — переспроси.
3. **Посчитай аудиторию ДО заливки**, покажи количество (и при необходимости несколько вариантов фильтра с числами). **Дождись подтверждения.**
4. **Создай `marketing.lists`** (имя с датой формирования + `responsible_person`), выгрузи `contact_id` пагинацией, залей `contacts_in_lists` батчами по 100 через `mcp__ozma__transaction`.
5. **Дедуп и отчёт:** проверь `total = uniq`, удали дубли (старые id), верни ссылку `https://ozma.gogol.school/views/marketing/list_form?id=<id>` и размер.

> Прежде чем строить запрос вручную — проверь готовые actions Озмы: `marketing.create_list_from_selected_elements(ids, name)`, `crm.create_marketing_list_from_product(id)`, `crm.create_marketing_list_from_lesson(id)`. Если кейс ложится на них — используй их.

## Карта источников по типам сегмента (выверено по схеме Озмы 2026-06-30)

| Сегмент | Источник | Примечание |
|---|---|---|
| **ЛТВ** | view `crm.students_table_all`, arg `ltv_group` (`0`/`до 20K`/`20K — 50K`/`50K — 100K`/`100K — 200K`/`200K +`) | **дефолт — поле `spent` из карточки** (решение Беллы). Альтернатива по запросу: `SUM(amount)` из `fin.transactions` по `class_type` |
| **Тип продукта** | `crm.actions.class_type` | покупка → `crm.actions_for_contacts` (старые до-CRM оплаты только тут, не в transactions); абонементы — `subscription.*` |
| **По мастерам** | `crm.masters_for_actions` (class_type=18) + `actions_for_contacts` (rs=7) | для email адаптировать запись в `marketing.lists` (TG-промт `34a612c7…bde7` пишет telegram_id) |
| **Прогрев** | `base.people.stage` / `stage_softs`, история `base.people_stages_history` | завершённый продукт → исторический прогрев на дату заявки; идущий → текущий stage; NULL=холодный |
| **Прочтение писем** | `marketing.email_statuses_for_actions` + словарь `marketing.email_event_types` (`ok_read`/`ok_link_visited`…), привязка `pm.actions=>sys_related_contact` | уточни: open vs click, период, какая тема (`pm.actions=>subject`) |
| **Баллы (количество)** | `loyalty.loyalty_card.calculated_current_bonus` | текущий баланс (НЕ из `base.loyalty_bonuses` — память `reference_ozma_bonus_balance`) |
| **Город** | `base.people.city_of_residence_value` → `base.cities` (+ строка `city_of_residence`) | чаще для исключений; уточни Москва+МО, пустой город |
| **Сертификат: наличие** | `fin.accounts` (`certificate_number`…) / `fin.transactions.is_certificate`, `linked_certificate` | |
| **Не был N времени** | даты в `crm.actions_for_contacts` / `fin.transactions` | **уточни метрику:** нет покупки / нет посещения / не открывал письма |
| **Схожие тематики (речь/тело)** | `crm.actions.class_category` или маппинг по `crm.actions.name` | маппинг тем ручной, согласуй список продуктов |
| **Комбинации** | `NOT IN`-подзапросы (готовые в зонтичном промте) | |

### class_type (справочник `crm.class_type`, факт)
1 Лаборатория · 3 Интенсив · 6 Открытый урок · 18 МК по абонементу · 19 Абонемент · 22 Интенсив СПБ · 24 Публично · 29 Интенсив YNG · 32 Курс · 33 Сертификат. (Справочник тяни запросом, не из памяти.)

### НЕ поддерживается в v1 (нет источника в Озме)
- **Сгорание / окончание действия баллов** — в `loyalty.*` нет поля даты сгорания (баланс считается функцией `compute_balance_for_contact`).
- **Сгорание сертификата** — наличие查ается, но поля срока действия сертификата в Озме нет (поиск `expiration` пуст; срок — в бизнес-правилах оферты).

Если просят такой сегмент — честно скажи, что источника даты нет, и предложи альтернативу (напр. «все владельцы сертификатов» без фильтра по сроку).

## Дефолтные исключения (решение Беллы 2026-06-30)

Применять к каждому списку по умолчанию (показать в preview, дать снять):
- **Чёрный список** — `base.people.loyalty = 2` (`crm.loyalty_statuses`: 2 = «Чёрный список»). Надёжно.
- **Без email** — для mail-списков исключать тех, у кого `email=>data IS NULL` (в почтовой рассылке бесполезны).
- **Отписавшиеся** — ⚠️ **в Озме фактически НЕ хранятся**: поле `base.people.ad_subscribe` почти целиком NULL (false=94, true=96, NULL≈21816). Отписки держит и фильтрует **Unisender при отправке**. Не полагайся на `ad_subscribe` как на фильтр отписок — предупреди пользователя, что отписавшихся отсечёт Unisender, а не этот список. (Если появится надёжный источник отписок — добавить сюда.)
- **Сотрудники ГС** (`base.people.is_employee = true`) — по умолчанию НЕ исключаем, но предлагай как опцию.

## Критические правила (грабли FunQL/Озмы — набиты практикой)

- **Confirm-first.** Любая заливка в прод — только после показанного количества + preview + явного «да» (память `feedback_ozma_refunds`).
- **Таргет:** `marketing.lists` (`name`, `notes`, `responsible_person`, `basket`) + `marketing.contacts_in_lists` (`contact` → `base.contacts`, `list`). Одна строка на контакт. **Батч 100 операций** на транзакцию.
- **contact_id:** `base.people` и `base.contacts` делят общий id (память `reference_lead_segmentation`) — id из `students_table_all`/`base.people` кладётся в `contacts_in_lists.contact` напрямую. В `fin.transactions` поле связи — `customer`, в `crm.actions` — `contact` (оба → `base.contacts`).
- **Нет `HAVING`** — оборачивай `GROUP BY` с агрегатным фильтром в подзапрос `FROM (...) AS sub WHERE ...` (алиас строго `AS sub`).
- **Cap 50–100 на ответ** — пагинируй `offset`-ом, накапливай; отбрасывай `null`-маркер последней страницы.
- **Агрегаты требуют `AS`** (`COUNT(id) AS cnt`).
- **`NOT IN` ломается на NULL** — в подзапросе исключения всегда `... IS NOT NULL`.
- **Старые до-CRM оплаты только в `crm.actions_for_contacts`**, не в `fin.transactions` — для критериев «был на продукте X» (особенно лабы/курсы) фильтруй по `actions_for_contacts`.
- **Именование списка:** `{Сегмент} {критерий} ({дата формирования})` — дата обязательна, список актуален на момент создания.
- **Статусы заявок** (`crm.request_statuses`): «был/в воронке» = `6,7,8,19` (и `7` = «прошёл, всё ок»); справочник тяни запросом.
- **responsible_person:** при ручном запуске человеком — текущий пользователь; в headless/боте — служебный id (как в `/new-campaign`, см. `reference_campaign_responsible_person`).

## Источники

- Мастер-промты: [воркфлоу заливки](https://app.notion.com/p/358612c762af8165bd45d03618eef4f7) · [зонтичная фильтрация](https://app.notion.com/p/36b612c762af81dab345d312eeb09c6c)
- Смежные промты: [прогрев ФОРМ ОС](https://app.notion.com/p/337612c762af81de890dca98b97883c2) · [мастера для TG](https://app.notion.com/p/34a612c762af81098b83fee7c5f7bde7) · методология [LTV-анализ](https://app.notion.com/p/335612c762af81a8b5efe4e5d505bb16) + [правила подсчёта покупок](https://app.notion.com/p/33b612c762af812aa1bdfde63d15bb26)
- Озма (запись): `mcp__ozma__transaction`; список: `https://ozma.gogol.school/views/marketing/list_form?id=<id>`
- Память: `reference_lead_segmentation`, `reference_email_engagement`, `reference_ozma_bonus_balance`, `feedback_ozma_refunds`, `feedback_notion_workspace_routing`
- Потребитель списков: `/new-campaign` (заводит кампанию на готовых `marketing.lists`)
