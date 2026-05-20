# OzmaDB — контекст для менеджера по документообороту

> Полная справка по схемам — в [shared/knowledge/database.md](../../shared/knowledge/database.md). Здесь — выжимка под задачи этой роли.

## Схемы, в которые я хожу чаще всего

### `fin` — финансы

- **`fin.transactions`** — все платежи. Tinkoff, рассрочки, возвраты. Главный триггер `tinkoff_shop_import` срабатывает на INSERT/UPDATE с `tks_*` полями — сам подтягивает покупателя, продукт, заявку, счета.
- **`fin.economics`** — внешние расходы и доходы (банковские выписки, зарплаты, депозиты). Сюда вносятся строки выписки Точки. Триггер `update_pl_after_amount_changing` пересобирает строку P&L при изменении суммы.
- **`fin.pl_report_final`** — P&L отчёт. **Удалять записи здесь раньше, чем в `fin.economics`** (constraint).
- **`fin.accounts`** — банковские счета. При добавлении контакта автоматически создаётся счёт (триггер `create_account_for_contact`).

### `crm` — продажи и обучение

- **`crm.actions_for_contacts`** — заявки студентов на продукты. Статусы (выборка под эту роль): 1 «Заявка», 3 «Оплатил», 14 «Требуется оплата», 17 «Не оплатил», 9 «Отмена», 15 «Отмена без возврата».
- **`crm.actions`** — продукты и занятия. Тип `Продукт` или `Занятие`. Префиксы имён: `INT`/`LCOURSE`/`OU`/`ISEEYOU`/`АМК` + `YYYYMM`.

### `reports` — генерация документов

- **Action `generate_and_export_contract_document`** — договор оказания услуг по заявке (`id` — ID заявки, `ext` — `.pdf` по умолчанию). Формирует расписание по модулям и долевое распределение оплаты.
- **Action `generate_and_export_contract_document2` / `_for_agreement` / `_from_subscription`** — варианты под разные типы продуктов.
- **Action `create_offer_invoice`** — счёт-оферта. Создаёт запись в `reports.offer_invoices` и открывает форму.
- **Шаблоны `.odt`** (на которые ссылаются actions через `document_template: { schema, name }`) хранятся в отдельном сервисе. Заливать/обновлять — через админку: https://ozma.gogol.school/report-generator/admin/ozma/. Правка локального файла (например, в `~/Documents/odt_templates/`) до Озмы не доезжает, шаблон нужно перезалить.

### `base` — люди

- **`base.people`** = `base.contacts` — это одна сущность с одинаковым ID. Не мэтчить.
- Триггер `people_doubles_check` блокирует вставку при совпадении (телефон ИЛИ email) + (имя ИЛИ фамилия). Если ловим эту ошибку — искать существующего контакта, а не дублировать.

## Полезные user views

- `base.contacts_table_all` — все контакты
- `crm.actions_for_contacts_table` — таблица заявок
- `fin.combined_transactions_table` — все транзакции одним списком
- `fin.economics_table` — внешние расходы
- `fin.monitor_payments` — мониторинг платежей
- `reports.all_contracts_and_offer_invoices` — все договоры и оферты

## Инструменты OzmaMCP, которые я использую

- **Чтение**: `list_schemas`, `list_entities`, `list_entity_fields`, `funql_query`, `named_view_query`, `named_view_info`
- **Запись**: `transaction` (универсальный insert/update/delete), `run_action` (запуск actions типа генерации договора)
- **Поиск**: `search_field`, `where_used_field`, `search_in_all` (если надо понять, где используется поле)
- **Отладка**: `query_events` (что изменялось), `validate_funql` (проверить запрос перед выполнением), `funql_guide` (синтаксис)

## Подводные камни

- **Не обновлять через MCP**: на сайте — `PAYMENT_AVAILABLE`, `MAIN_SEVICE`, `TIME`/`TIME_TWO` в расписании (iblock 34). Только админка Bitrix.
- **Безопасное обновление кода**: всегда `safe_update_*` варианты (action/trigger/module/view), не прямые транзакции по `public.*`.
- **Перед тяжёлыми изменениями кода** — `analyze_*_performance`.
