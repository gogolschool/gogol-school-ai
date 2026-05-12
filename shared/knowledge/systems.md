# База знаний: системы Gogol School

Документ-справочник по всем системам, с которыми мы работали: Ozma (CRM/ERP), Gogol Site (Bitrix), Notion, Telegram, Google Sheets. Здесь собраны конвенции, правила, типовые паттерны и «подводные камни», которые накопились в ходе работы.

---

## 1. OZMA CRM/ERP

### 1.1 Общая архитектура

Ozma построена на FunQL (функциональный язык запросов) поверх PostgreSQL. Данные организованы в **схемы** → **entities** (таблицы) → **поля** (колонки + computed fields). Дополнительно: **user views** (именованные представления), **actions** (серверные JS-процедуры), **triggers** (JS-обработчики событий), **modules** (переиспользуемый JS-код).

### 1.2 Ключевые схемы и сущности

**base** — базовые справочники:
- `base.people` — все люди (студенты, сотрудники, контрагенты)
- Поля паспорта (обязательны для самозанятых в рекламных договорах): `date_of_birth`, `passport_no`, `passport_registered_at`, `passport_div_code`, `passport_issued`, `birth_place`, `sex`

**crm** — CRM-функционал:
- `crm.refund_requests` — заявки на возврат
- другие сущности по продажам

**fin** — финансы:
- `fin.economics` — экономические записи (основная сущность для учёта)
- `fin.transactions` — транзакции
- `fin.pl_report_final` — отчёт P&L (должен очищаться до удаления записей из `fin.economics`)

**staffs** — персонал:
- `staffs.masters_salary_records` — записи о зарплатах мастеров
- Action: `push_salary_to_economics` — пушит зарплаты в экономику с дедупликацией по `economics_id`

**sales** — продажи:
- `sales.actions_table_all` — сводная таблица действий по продажам

### 1.3 Именование продуктов в Ozma

Префиксы продуктов + формат даты `YYYYMM`:
- `INT` — интенсивы
- `LCOURSE` — курсы (Laboratory course?)
- `OU` — открытый урок
- `ISEEYOU` — "Я тебя вижу" (формат `ISEEYOU{НН}-{ГГГГММ}`, где НН — глобальный сквозной номер по всем запускам)
- `АМК` — АМК-продукты

### 1.4 Правила работы с продуктами и расписанием

- `ADD_NAME` = только название продукта (например, "Философия кайфа"), без префиксов
- `MAIN_SEVICE` (чекбокс) — **ставится вручную в админке**, не обновляется через MCP
- `PAYMENT_AVAILABLE` — **не обновляется через MCP**
- `agreeText` — блок договора-оферты, использует разбивку **30/50/20%** по модулям, с указанием `refund@gogol.school` для возвратов
- Для интенсивов: `showRequestForm = false`
- **Расписание (iblock 34 в Bitrix)**: элементы создаются только через `iblock_element_create`. Поля `TIME` и `TIME_TWO` **не сохраняются через update** — только при создании.

### 1.5 FunQL: ключевые паттерны

**Стрелочная нотация для FK-переходов:**
```
entity=>field
```
Например: `transaction=>person=>full_name`

**Фильтрация по датам через `date_part()`**:
```
date_part('year', created_at) = 2026
```

**Пагинация**: offset инкрементами по 50.

**Запросы по выручке** (cohort-анализ):
```
JOIN fin.pl_report_final → fin.transactions → base.people
WHERE class_type NOT IN ('HEAD', 'ОУ HEAD', 'B2B', 'Сертификат')
```

**FunQL guide**: инструмент `funql_guide` возвращает краткий справочник по синтаксису с Ozma-специфичными особенностями. Использовать при сомнениях.

**Валидация**: `validate_funql` проверяет запрос на сервере до выполнения — удобно для тестирования сложных view-запросов.

### 1.6 Actions, Triggers, Modules — безопасное обновление

Все три категории имеют **safe-update** варианты:
- `safe_update_action_function` — обновляет JS-код action
- `safe_update_trigger_function` — обновляет JS-код trigger
- `safe_update_module` — обновляет JS-код модуля
- `safe_update_view_query` — обновляет FunQL-текст view через text replacement

Safe-версии делают атомарное обновление с проверкой. Использовать именно их, а не прямые транзакции по `public.*.procedure/function/query`.

**Анализ производительности** (перед деплоем тяжёлых изменений):
- `analyze_action_performance`
- `analyze_trigger_performance`
- `analyze_module_performance`
- `analyze_user_view_performance`

### 1.7 Поиск по коду

Критически полезные инструменты для рефакторинга и понимания системы:
- `search_in_all` — поиск по всему JS-коду (actions + triggers + modules)
- `search_in_actions` / `search_in_triggers` / `search_in_modules` — таргетированный поиск
- `search_in_metadata` — поиск по computed fields и метаданным
- `search_field` — поиск поля по имени во всех схемах и entities
- `where_used_field` — где используется поле в коде и метаданных
- `search_http_api_usage` — поиск использований `OzmaDB.fetchHttp*`, `OzmaDB.enqueueHttpRequest`
- `search_js_api_usage` — поиск использований OzmaDB JS runtime API

### 1.8 HTTP API и outbox

- Исходящие HTTP-запросы из triggers/actions идут через `OzmaDB.enqueueHttpRequest` → ставятся в очередь `public.outbox_messages`
- `list_outbox_messages` — инспекция очереди (полезно при отладке интеграций)

### 1.9 Роли и права доступа

- `list_roles` — список ролей с глобальными флагами
- `upsert_role_entity` — права роли на сущность
- `set_role_allow_all_entities` — флаг «разрешить все entities»
- `set_role_global_permissions` — глобальные флаги прав
- `upsert_denied_user_view` / `list_denied_user_views` / `delete_denied_user_view` — запрет определённых views для роли
- Администраторов в `base.people` — четыре человека (с известными ID), добавлены в рамках зарплатного workflow

### 1.10 События и аудит

- `query_events` — лог событий (`public.events`), полезен для отладки и аудита кто-что-когда менял
- `set_request_theme` — установка темы UI через заголовок `X-OzmaDB-Theme`

### 1.11 Две инстанции Ozma

В инфраструктуре доступны **две Ozma-инстанции**:
- `ozma` — основная
- `ozma-gelfand` — отдельная инсталляция (идентичный набор инструментов, но разные базы)

При работе выбирать нужную по контексту задачи.

---

## 2. GOGOL SITE (BITRIX) — gogol-site MCP

### 2.1 Структура iblocks

Сайт `gogolschool.ru` работает на Bitrix. Ключевые iblocks:
- **iblock 2** — Продукты (программы, курсы, интенсивы)
- **iblock 14** — Заказы (Tinkoff payment orders)
- **iblock 16** — Платные услуги (services)
- **iblock 34** — Расписание (schedule elements)

Инструмент `list_iblocks` выведет полный список если нужно.

### 2.2 Работа с iblock-элементами

- `iblock_element_create` / `iblock_element_update` / `iblock_element_deactivate` — базовые CRUD
- `iblock_element_get` — получение со всеми свойствами
- `iblock_elements` — листинг с фильтрами
- `iblock_fields` — **обязательно вызывать первым** для нового iblock: возвращает поля, properties, sections
- `element_history` / `element_history_record` — история изменений элемента

### 2.3 Sections (категории)

- `iblock_section_create` / `update` / `deactivate` / `get`
- `iblock_sections` — листинг

### 2.4 Каталог и цены (iblock 16 — services)

**Каталожные поля** (отдельно от свойств iblock):
- `catalog_get` — параметры (quantity, weight, dimensions, VAT, canBuyZero)
- `catalog_update` — обновление параметров
- `catalog_prices_get` / `catalog_prices_set` — все типы цен (bulk upsert)
- `catalog_price_types` — список типов цен

**Скидки**:
- `catalog_discount_create` / `update` / `get` / `deactivate`
- `catalog_discounts` — листинг

### 2.5 Услуги (services) — высокоуровневые операции

Поскольку услуги — самая частая сущность, для них есть специализированные shortcut-инструменты:

**CRUD и информация**:
- `services_create` / `services_update` / `services_deactivate` / `services_restore` / `services_get`
- `services_search` — поиск по iblock 16
- `services_sections` / `services_section_create` / `update` / `get` / `deactivate`
- `services_clone` — клонирование услуги со всеми полями, свойствами и ценой
- `services_copy_properties` — копирование свойств между услугами

**Каталог и цены**:
- `services_get_catalog` — параметры + все цены
- `services_update_catalog` — обновить торговый каталог
- `services_update_price` — обновить только цену

**Bulk-операции** (сильно экономят время):
- `services_batch` — общий batch
- `services_batch_catalog` — массовое обновление каталожных параметров
- `services_batch_price` — массовое обновление цен
- `services_batch_move_section` — перемещение нескольких услуг в другую секцию

**Статистика**:
- `services_stats` — заказы и выручка по конкретной услуге

### 2.6 Промокоды

Централизованная система промокодов:
- `promo_create` / `update` / `get` / `deactivate` / `search`
- `promo_batch` / `promo_batch_create` — bulk-операции
- `promo_stats` — детальная статистика использования
- `promo_get_linked_services` — услуги, привязанные к промокоду
- `services_link_promo` — добавить/убрать привязку промокода к услуге (свойство `PROMO_CODE`)

### 2.7 Заказы (Tinkoff acquiring)

- `orders_search` — поиск заказов в iblock 14 (это заказы Tinkoff эквайринга)
- `orders_get` — получить конкретный заказ
- `orders_update` — сменить статус, пометить оплаченным/неоплаченным, отменить, добавить комментарий
- `orders_add_item` — добавить продукт/услугу в существующий заказ

### 2.8 Формы

Bitrix web forms:
- `forms_list` — список всех форм
- `forms_get` — полная метадата формы
- `forms_fields` — поля формы (включая enum-варианты для dropdown/radio)
- `forms_statuses` — статусы заявок
- `forms_results` — список submissions с фильтрами
- `forms_result_get` / `forms_result_update` / `forms_result_delete`
- `forms_stats` — агрегированная статистика: общее число, по статусам, по дням

### 2.9 SEO

- `seo_element_get` / `seo_element_set` — meta-поля (title, description, keywords) для элемента
- `seo_section_get` / `seo_section_set` — то же для секции

### 2.10 Загрузка файлов

- `upload_preview_picture` — thumbnail для элемента
- `upload_detail_picture` — основная картинка
- `upload_property_file` — загрузить файл в свойство типа F

### 2.11 Пользователи сайта

- `users_search` / `users_get` (read-only)
- `users_update` — обновление профиля
- `users_orders` — заказы пользователя
- `users_stats` — статистика покупок: общее число заказов, оплачено, купленные услуги

### 2.12 Статистика и аналитика

- `stats_overview` — дашборд: активные услуги/промокоды, заказы сегодня, выручка за месяц
- `stats_orders` — заказы: totals, выручка, по статусам, по дням, по платёжным системам
- `stats_services` — разбивка по секциям и по типу (подписка)
- `stats_promo` — использование промокодов: totals, неиспользованные, топ-20

### 2.13 Полнотекстовый поиск

- `search` — full-text по iblocks (имена, preview text, common properties)

### 2.14 Связки между продуктом в Ozma и на сайте

**Типовой флоу запуска нового продукта**:
1. Создать CRM-продукт в Ozma (с правильным именованием по префиксам)
2. Создать платёжную ссылку на сайте: элемент в iblock 2 (продукты) + элемент в iblock 34 (расписание, через `iblock_element_create`)
3. Синхронизировать Notion product card

**Ограничения MCP при обновлении на сайте**:
- `PAYMENT_AVAILABLE` — только вручную
- `MAIN_SEVICE` — только вручную через админку Bitrix
- `TIME`, `TIME_TWO` в iblock 34 — только при создании, не через update

---

## 3. ТИПОВОЙ ФЛОУ ЗАПУСКА ПРОДУКТА

Собранный из практики алгоритм:

1. **Определение типа продукта** и префикса (INT/LCOURSE/OU/ISEEYOU/АМК)
2. **Нумерация** — особое внимание к ISEEYOU (глобальный сквозной номер)
3. **Ozma**: создать CRM-продукт + связанные сущности
4. **Bitrix / gogol-site**:
   - `iblock_element_create` в iblock 2 — продукт
   - `iblock_element_create` в iblock 34 — расписание (TIME/TIME_TWO сразу!)
   - `services_create` в iblock 16 если нужна платная услуга
   - `agreeText` — 30/50/20% split с `refund@gogol.school`
   - `showRequestForm = false` для интенсивов
   - `ADD_NAME` = только название, без префиксов
5. **Ручные шаги в админке Bitrix** (нельзя через MCP):
   - Поставить `MAIN_SEVICE`
   - Проставить `PAYMENT_AVAILABLE`
6. **Notion**: синхронизировать карточку продукта
7. **Проверка**: пройти флоу оплаты глазами пользователя

---

## 4. ФИНАНСОВЫЙ УЧЁТ В OZMA

### 4.1 Мастер-промпт

Мастер-промпт для обработки банковских выписок (Точка CSV → `fin.economics`) лежит в Notion:
**page ID**: `320612c762af81ceb53bcfc18fd27514`

### 4.2 Паттерн Tinkoff acquiring

Один реестр Tinkoff-эквайринга = **4 записи в fin.economics**:
1. **Доход** (income) — на полную сумму
2. **Зеркальный расход** (mirror expense) — техническая запись
3. **Комиссия** (commission) — расход в пользу Tinkoff
4. **НДС** (VAT) — отдельной записью

Все помечаются `notification_who = "Клодик"` (чтобы отличить записи, внесённые через Claude).

### 4.3 Другие источники

- Депозитные операции
- Зарплаты (Консоль.Про)
- Платежи вендорам

### 4.4 Удаление записей

**Порядок удаления важен**: сначала очищаются зависимости в `fin.pl_report_final`, потом удаляются записи из `fin.economics`. Иначе — constraint violation.

---

## 5. TELEGRAM — аккаунт @gs_corporate

### 5.1 Общая схема

Корпоративный Telegram-аккаунт управляется через MCP (`telegram_ozma_mcp` + `telegram_vo_mcp` — две параллельных инсталляции на разных аккаунтах).

### 5.2 Dialog Audit (GPT-аудит продающих диалогов)

**Активное правило**: `sales_dialog_quality`
- **Модель**: gpt-5.4-mini
- **Two-stage**: выключено

Инструменты:
- `create_dialog_audit_rule` / `update_dialog_audit_rule` / `delete_dialog_audit_rule`
- `list_dialog_audit_rules` / `list_dialog_audit_events` / `list_dialog_audit_contexts`
- `list_dialog_audit_flags` — флаги от GPT по конкретным сообщениям
- `get_dialog_audit_flag_stats` — агрегированная статистика по flag_type + severity
- `get_dialog_audit_config` / `set_dialog_audit_config`

### 5.3 Unanswered alerts — мониторинг времени ответа

- `create_unanswered_alert_rule` (создаётся **отключённым** по умолчанию!)
- `enable_unanswered_alert_rule` / `disable_unanswered_alert_rule`
- `list_unanswered_alert_rules` / `list_unanswered_alert_log`
- `update_unanswered_alert_rule` / `delete_unanswered_alert_rule`

### 5.4 AI Autoreply

- `get_autoreply_config` / `set_autoreply_config`
- `get_autoreply_model` / `switch_autoreply_model` (gpt-5-nano ↔ gpt-5-mini)
- `create_autoreply_rule` / `update_autoreply_rule` / `delete_autoreply_rule` / `list_autoreply_rules`
- `bulk_upsert_autoreply_rules` / `bulk_delete_autoreply_rules`
- `export_autoreply_rules` / `import_autoreply_rules`
- `test_autoreply` / `simulate_autoreply` — сухой прогон без отправки
- `list_autoreply_events` — журнал срабатываний
- `list_admin_routing_log` — журнал эскалаций админам

### 5.5 Conversation monitor (мониторинг враждебности)

- `create_conversation_monitor_rule` / `update` / `delete` / `list`
- `test_conversation_monitor_rule` — dry-run

### 5.6 Аналитика

- `get_analytics_config` / `set_analytics_config`
- `refresh_analytics` — пересчёт материализованных таблиц
- `get_analytics_table` — чтение строк с фильтрами
- `count_recent_messages` — входящие за последние N минут, с разбивкой по чатам
- `dialog_activity_stats` — топ-N активных диалогов за N дней
- `most_active_senders` — топ отправителей в конкретном чате
- `message_count_by_hour` — распределение по часам дня
- `word_frequency` — топ-N частых слов в чате
- `get_categorization_stats` — агрегированная статистика GPT-категоризации

### 5.7 Knowledge base для AI

- `upsert_account_knowledge_profile` — профиль знаний для аккаунта/сессии
- `get_account_knowledge_profile`
- `create_account_knowledge_entry` / `update` / `delete` / `list`

### 5.8 Верификация студентов (Telegram → Ozma)

Flow для поиска и записи Telegram ID студентов в базу. Используется `resolve_username` для преобразования @username в ID.

### 5.9 Экспорт и история

- `export_chat_history` — последние N сообщений в JSON-файл
- `export_messages_in_range` — оптимизированный экспорт больших диапазонов
- `get_messages_in_range` — сообщения в datetime-диапазоне (пагинация внутри)
- `get_message_context` — N сообщений до и после конкретного ID

### 5.10 Онлайн-трекинг

- `track_user` / `untrack_user` / `list_tracked_users`
- `get_online_status` — last-seen конкретного пользователя
- `get_online_history` — история онлайн/оффлайн за N часов
- `get_tracked_users_statuses` — batch-снимок

### 5.11 Работа с сессиями (важно!)

- `list_sessions` — локальные session-файлы
- `auth_send_code` / `auth_sign_in_code` / `auth_sign_in_password` / `auth_cancel` — логин другого аккаунта
- `terminate_session` — завершить сессию (разлогин + удаление локальных данных)

---

## 6. NOTION — база знаний и prompt library

### 6.1 Prompt library

**Корневая страница**: `6f0c0859c8ab46e2838107aeb3b4726c`

Конвенция именования: `🤖 Промт: [название задачи]`

Существующие промпты:
- Создание продукта (Ozma + Bitrix)
- Создание рекламного договора
- Работа с расписанием
- Финансовый учёт (выписки)
- Верификация Telegram
- Sales actions
- … и другие

### 6.2 Ключевые отдельные страницы

- **Brand TOV** (Tone of Voice): `32c612c762af8135926dd0f14e384cb5` — отправлять эту ссылку Claude для любой маркетинговой задачи
- **Мастер-промпт банковских выписок**: `320612c762af81ceb53bcfc18fd27514`

### 6.3 Синхронизация Notion с продуктами

Для каждого запуска — карточка продукта в Notion, синхронизированная с Ozma + Bitrix.

### 6.4 MCP tools для Notion

- `notion-search` — поиск по workspace
- `notion-fetch` — получить детали по URL (page/database/data source)
- `notion-create-pages` / `notion-update-page` / `notion-duplicate-page`
- `notion-move-pages` — перенос в нового родителя
- `notion-create-database` / `notion-update-data-source` — DDL-синтаксис SQL
- `notion-create-view` / `notion-update-view`
- `notion-create-comment` / `notion-get-comments`
- `notion-get-users` / `notion-get-teams`

---

## 7. GOOGLE SHEETS — когортная аналитика

### 7.1 Главный документ

**Cohort Revenue spreadsheet**: `16Be0V7GY_mA6DQKyQsOuftd02sqv9bFJ4NouMJO6pDE`

Период: Sep 2024 – Feb 2026.

### 7.2 Корректировки

Корректировалось поле `cohort_b2c` для 66 студентов — баги с месячной границей при оплате.

### 7.3 Паттерн запроса выручки

```
FROM fin.pl_report_final
JOIN fin.transactions
JOIN base.people
WHERE class_type NOT IN ('HEAD', 'ОУ HEAD', 'B2B', 'Сертификат')
```

---

## 8. ОБЩИЕ ПРИНЦИПЫ

### 8.1 Порядок работы с любой задачей

1. **Выяснить, где живут данные** (Ozma/Bitrix/Notion/Sheets/Telegram)
2. **Если есть промпт в Notion prompt library** — использовать его как source of truth
3. **Проверить выходы**: сверка с исходными данными перед принятием результата
4. **Пометить** записи, созданные через Claude (например, `notification_who = "Клодик"`)

### 8.2 Двойные инстанции

Где есть двойники — выбирать по контексту:
- `ozma` + `ozma-gelfand`
- `telegram_ozma_mcp` + `telegram_vo_mcp`

### 8.3 Инструменты для незнакомых систем

- **Ozma**: `funql_guide`, `list_schemas`, `list_entities`, `list_entity_fields`
- **Bitrix**: `list_iblocks`, `iblock_fields`, `server_help`
- **Telegram**: `list_capabilities`, `get_server_manual`

### 8.4 Дебаггинг

- **Ozma**: `query_events` (что изменялось), `list_outbox_messages` (HTTP-исходящее)
- **Bitrix**: `element_history`, `forms_stats`
- **Telegram**: `get_server_logs`, `health_deep`

---

## 9. ЧТО НИКОГДА НЕ ДЕЛАЕТСЯ ЧЕРЕЗ MCP (только руками)

- **Bitrix**:
  - `PAYMENT_AVAILABLE` — только через админку
  - `MAIN_SEVICE` — только через админку
  - `TIME` / `TIME_TWO` в iblock 34 — только при создании (update не работает)
- **Всегда вручную проверяется**:
  - Флоу оплаты после запуска нового продукта
  - `agreeText` — после автогенерации
