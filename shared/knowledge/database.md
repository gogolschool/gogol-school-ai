# Документация базы данных OzmaDB

> Сгенерировано автоматически на основе анализа кода. Дата: 2026-03-05

## Содержание

1. [Обзор схем](#обзор-схем)
2. [Схема `base` — Контакты и люди](#схема-base)
3. [Схема `crm` — CRM и учебные продукты](#схема-crm)
4. [Схема `fin` — Финансы](#схема-fin)
5. [Схема `marketing` — Маркетинг](#схема-marketing)
6. [Схема `pm` — Управление задачами](#схема-pm)
7. [Схема `integration` — Интеграции](#схема-integration)
8. [Схема `subscription` — Абонементы](#схема-subscription)
9. [Схема `loyalty` — Лояльность](#схема-loyalty)
10. [Схема `reports` — Документы и отчёты](#схема-reports)
11. [Схема `sales` — Продажи](#схема-sales)
12. [Схема `staffs` — Персонал и мастера](#схема-staffs)
13. [Архитектурные паттерны](#архитектурные-паттерны)

---

## Обзор схем

| Схема | Назначение |
|-------|-----------|
| `base` | Контакты, люди, организации, способы связи |
| `crm` | Продукты, занятия, заявки, посещения, мастера |
| `fin` | Транзакции, счета, сертификаты, P&L отчёт |
| `marketing` | Кампании, списки рассылки, промокоды |
| `pm` | Задачи (CRM-действия менеджеров) |
| `integration` | WhatsApp/Telegram сообщения, шаблоны |
| `subscription` | Абонементы, привязка заявок |
| `loyalty` | Карты лояльности, баллы |
| `reports` | Генерация договоров, счетов-оферт |
| `sales` | Действия менеджеров по продажам |
| `staffs` | Расчёт оплаты мастеров |
| `hrm` | Сотрудники, ставки |
| `ad` | Рекламные договоры |
| `docs` | Трудовые договоры |
| `timepadApi` | Интеграция с Timepad |
| `analytics` | Аналитика |
| `wiki` | Внутренняя wiki |

---

## Схема `base`

### Назначение
Базовая схема. Хранит всех людей, организации, способы связи (email, телефон, Telegram, соцсети), историю стадий, баллы лояльности.

### Ключевые сущности
- **`people` / `contacts`** — основная таблица людей/клиентов
- **`communication_ways`** — способы связи (Email, Телефон, Telegram, Соцсети)
- **`people_stages_history`** — история изменения стадии (Холодный → Прохладный → Тёплый → Горячий)
- **`people_softs_stages_history`** — история стадий для soft-skills направления
- **`duplicate_contacts`** — список дублей для объединения
- **`organizations`** — организации (юрлица)
- **`loyalty_bonuses`** — баллы лояльности контакта

### Actions

#### `merge_contacts`
Объединение дублирующихся контактов (полная версия). Получает список дублей из `base.duplicate_contacts_table`, для каждого контакта загружает все связанные данные (транзакции, способы связи, заявки, счета, pm-действия, историю стадий, абонементы, контакты в списках, занятия, Яндекс-метрику, баллы и карты лояльности), выбирает главный контакт по максимальному количеству связанных данных, переносит все связи, удаляет второстепенные.

**Аргументы:** `ids` — массив ID из `duplicate_contacts`

#### `new_merge_contacts`
Новая версия объединения дублей (только для 2 контактов). Контакт с меньшим ID всегда становится главным. Использует представление `base.connections_between_entity_and_other_entities_table` для динамического обнаружения всех FK-связей. Автоматически переподвязывает все найденные ссылки с учётом уникальных ограничений.

**Аргументы:** `ids` — массив из 2 ID

#### `delete_contact`
Удаление одного контакта из списка дублей. Удаляет запись из `duplicate_contacts` и сам контакт из `people`.

**Аргументы:** `ids` — массив из 1 ID

#### `put_contacts_to_duplicates_list`
Добавление контактов в список дублей (проверяет дубли перед добавлением).

**Аргументы:** `ids` — массив ID людей

#### `add_to_duplicates`
Добавление одного контакта в список дублей (упрощённая версия).

**Аргументы:** `id` — ID контакта

#### `update_stage_all_students`
Массовое обновление стадии всех клиентов на основе вычисляемого значения из `base.current_students_stage`.

**Аргументы:** `stage` (необязательный) — название стадии

### Triggers

#### `set_main_email_or_phone_or_social_or_telegram` (на `communication_ways`, INSERT)
При добавлении способа связи автоматически проставляет его как основной у контакта в полях `email`, `phone`, `social_network`, `telegram`.

#### `insert_stage_history_before_update_people_stage` (на `people`, BEFORE INSERT/UPDATE)
Сохраняет запись в историю стадий при изменении стадии человека. По умолчанию стадия = `Холодный`.

#### `insert_stage_history_after_insert_people_stage` (на `people`, AFTER INSERT)
Записывает начальную стадию нового человека в историю.

#### `create_loyalty_card` (на `people`, INSERT/UPDATE)
Автоматически создаёт карту лояльности при добавлении нового человека.

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `contacts_table_all` | Полная таблица всех контактов |
| `people_table` | Таблица людей (физлица) |
| `duplicate_contacts_table` | Список дублей для объединения |
| `current_students_stage` | Текущая вычисляемая стадия клиента |
| `communication_ways_for_contact_table_conn` | Способы связи для контакта |
| `people_stages_history_table` | История изменений стадий |
| `all_active_contacts_full_info` | Полная информация по активным контактам |
| `contact_loyalty_bonuses` | Баллы лояльности контакта |
| `contact_form` | Форма контакта |
| `connections_between_entity_and_other_entities_table` | Все FK-связи между сущностями (для merge) |
| `contacts_ym_client_ids_table` | Яндекс.Метрика client ID |
| `contacts_analytics_client_ids_table` | Roistat ID контактов |

---

## Схема `crm`

### Назначение
Основная CRM-схема. Хранит продукты (курсы, интенсивы, лаборатории), занятия, заявки студентов, посещения, мастеров, корзины.

### Ключевые сущности
- **`actions`** — продукты (тип=`Продукт`) и занятия (тип=`Занятие`)
- **`actions_for_contacts`** — заявки студентов на продукты
- **`visits_for_lessons`** — посещения конкретных занятий
- **`masters_for_actions`** — мастера/преподаватели продукта
- **`actions_for_baskets`** — продукты в корзинах
- **`newsletter_templates`** — шаблоны рассылок

### Статусы заявок (`request_status`)

| ID | Название |
|----|---------|
| 1 | Заявка |
| 2 | Список ожидания |
| 3 | Оплатил |
| 6 | Обучается |
| 7 | Прошёл обучение, всё ок |
| 9 | Отмена |
| 10 | Набор |
| 11 | Оплатил и не посетил |
| 12 | Заплатил за другого |
| 13 | Не пришёл |
| 14 | Требуется оплата |
| 15 | Отмена без возврата |
| 16 | Пропуск |
| 17 | Не оплатил |

### Статусы занятий (`class_status`)

| ID | Название |
|----|---------|
| 1 | Анонсировано |
| 2 | Набор |
| 3 | Проведено |
| 5 | Отменено |

### Actions

#### `generate_lessons`
Генерация расписания занятий для продукта на основе генератора. Читает периодичность, день недели, время; вычисляет все даты в диапазоне; фильтрует паузы (`pause_start/end`); создаёт занятия с копированием полей из продукта.

**Аргументы:** `id` — ID генератора уроков

#### `generate_visit_list_for_lesson`
Создание списка посещений для занятия. Берёт студентов в статусах 3/6/7 и всех мастеров, создаёт записи в `visits_for_lessons`.

**Аргументы:** `lesson` — ID занятия

#### `visit_lesson`
Отметка прихода/ухода/пропуска студента. `Пришел` → `datetime`; `Ушел` → `datetime_end` (триггерит пометку занятия как «Проведено»); `Пропуск` → `is_skipped=true`.

**Аргументы:** `id` — ID посещения, `direction` — направление

#### `visit_lesson_mass_action`
Массовая отметка всех не пропустивших студентов как пришедших.

**Аргументы:** `id` — ID занятия

#### `update_action_status`
Устанавливает `end_datetime = now()` при завершении продукта.

**Аргументы:** `id`, `status='done'`

#### `archive_crm_actions`
Архивирование/разархивирование продукта (флаг `is_deleted`).

**Аргументы:** `id`, `status` — true/false

#### `add_new_visit_for_master`
Добавление посещения для мастера вручную.

**Аргументы:** `action` — ID занятия, `master` — ID контакта

#### `monitor_payments_crm_actions`
Включение/выключение мониторинга оплат (`is_monitor_payments`).

#### `monitor_timepad_crm_actions`
Включение/выключение мониторинга Timepad (`is_monitor_timepad`).

#### `actions_for_contacts_mass_update`
Массовое обновление заявок: можно изменить `action`, `request_status` или `set_status`.

**Аргументы:** `ids`, `column`, `request_status`, `set_status`, `action`

#### `mass_update_class_status`
Массовое обновление статуса класса у занятий.

**Аргументы:** `ids`, `class_status`

#### `create_interview_for_student`
Создание заявки из формы сайта (форма «Пробное занятие»). Ищет студента по email; если не найден — создаёт нового; находит продукт по `name_for_payments`; создаёт заявку; если продукт не найден — добавляет в `LOSTINTERVIEW`.

**Аргументы:** `type`, `name`, `format`, `date`, `time`, `amount`, `form` {name/email/phone}

#### `create_order_for_student`
Универсальная форма заказа с сайта. Нормализует данные, определяет продукты по тегам (`acting_labs_site`, `intensives_msk_site` и др.), создаёт/находит студента через `getCustomerNew`, создаёт заявки.

**Аргументы:** `name`, `email`, `phone`, `birthday`, `lab`, `city`, `request`, `telegram`

#### `duplicate_action`
Создание точной копии продукта (без описания). Копирует все поля и открывает форму нового продукта.

**Аргументы:** `id` — ID оригинала

#### `laboratory_recruitment_from_action`
Создание заявок в «набор» из выбранных заявок. Для каждой заявки проверяет отсутствие дубля, создаёт заявку в продукт «Набор: все» (ID 1708) со статусом 10.

**Аргументы:** `ids`, `action` — ID продукта-источника

#### `send_reminders_for_product`
Рассылка напоминаний студентам через WhatsApp. Использует шаблон из `template_newsletter` если есть, иначе старую логику. Опционально помечает как отправленные.

**Аргументы:** `product`, `ids`, `should_mark_as_sent`

#### `send_reminders_to_telegram_for_product`
Рассылка напоминаний через Telegram в `integration.requests_for_sending`.

**Аргументы:** `product`, `ids`, `reminder_type`

#### `create_marketing_list_from_product`
Создание маркетингового списка из всех студентов продукта.

**Аргументы:** `id` — ID продукта

#### `create_marketing_list_from_lesson`
Создание маркетингового списка из посетителей занятия.

**Аргументы:** `id` — ID занятия

#### `update_lessons_text_by_product`
Обновление текстовых полей для договора: расписание по модулям (1/2/3/4+ занятий), `due_days_text`, `price_text`, `schedule_text`.

**Аргументы:** `id` — ID продукта

#### `insert_products_into_basket`
Добавление продуктов в корзину.

**Аргументы:** `basket`, `ids` — массив ID продуктов

#### `update_product_marketing_description`
Копирование маркетингового описания с предыдущих продуктов той же темы и типа.

**Аргументы:** `id` — ID продукта

#### `send_notification_after_open_lesson` / `send_notifications_after_open_lesson`
Отправка WhatsApp-уведомления после открытого урока (шаблон `0cccff37-871a-4095-aada-74c81742ca08`). Помечает как отправленное в `whatsapp_notification_for_ol_requests`.

### Triggers

#### `link_base_contact_to_action` (на `actions`, INSERT)
При создании продукта с `base_contact` создаёт связь в `contacts_in_actions`.

#### `add_default_master` (на `actions`, INSERT)
При создании продукта верхнего уровня (без `parent_action`) добавляет дефолтного мастера (ID 17917).

#### `change_class_status` (на `actions`, UPDATE)
Когда занятие → статус «Проведено» (3): обновляет статусы заявок. Посетил → статус 7; не пришёл → статус 13; «Требуется оплата» → статус 17 («Не оплатил»). Бартерные, отменённые, уже завершённые заявки пропускаются.

#### `update_product_status` (на `actions`, UPDATE)
Когда все занятия продукта завершены — закрывает продукт (статус 3).

#### `fill_create_datetime` (на `actions_for_contacts`, INSERT)
Заполняет `create_datetime = now()`.

#### `update_set_status_change_date` (на `actions_for_contacts`, BEFORE UPDATE)
При изменении `set_status` → записывает `set_status_change_datetime = now()`.

#### `update_student_stage_before_done_action_for_contacts` (на `actions_for_contacts`, BEFORE UPDATE)
При переходе заявки в статус 7 («Прошёл обучение») — повышает стадию студента через `sys_get_student_stage`. Обновляет `stage` или `stage_softs` только если новая стадия выше текущей. Для абонементов: 2+ посещённых абонемента → следующая стадия.

#### `update_student_stage_after_done_action` (на `actions_for_contacts`, UPDATE)
Статус 9 (отмена) → освобождает слот в абонементе и привязывает следующую заявку. Статусы 7/15/11/6/13 → активирует абонемент.

#### `update_request_result_date_change_request_result` (на `actions_for_contacts`, BEFORE UPDATE)
При `request_result` → `request_result_date = now()`. При сбросе → `null`.

#### `update_refund_amount_text_actions_for_contacts` (на `actions_for_contacts`, BEFORE UPDATE)
Вычисляет текстовое представление суммы возврата и остатка оплаты.

#### `unique_mass_actions_for_contacts` (на `actions_for_contacts`, BEFORE INSERT)
Блокирует дублирование заявок (контакт + продукт). Исключения: `acting_labs_site`, `plastic_labs_site`, `cinema_labs_site`, `intensives_msk_site`, `intensives_spb_site`, продукты с «Абонемент» в названии.

#### `update_loyalty_bonuses` (на `actions_for_contacts`, INSERT/UPDATE)
Начисление бонусов лояльности при завершении. **Временно отключён** (`return true` в начале функции).

#### `datetime_direction_filling` (на `visits_for_lessons`, UPDATE)
При установке `datetime_end` → помечает занятие как «Проведено».

#### `people_doubles_check` (на `people`, BEFORE INSERT)
Блокирует вставку при совпадении (телефон ИЛИ email) + (имя ИЛИ фамилия).

#### `set_blacklist_date` (на `people`, INSERT/UPDATE)
`loyalty = 2` → `blacklist_date = today`; иначе → `null`.

#### `students_import` (на `people`, INSERT)
При импорте: если переданы `phone_sys`/`email_sys` — создаёт записи в `communication_ways`.

#### `generate_lesson_after_master_adding` (на `masters_for_actions`, INSERT/UPDATE)
При добавлении мастера: если нет генератора и у продукта одно занятие в тот же день — создаёт генератор и занятие. Дефолтный мастер (ID 17917) игнорируется.

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `actions_table_all` | Все продукты и занятия |
| `action_form` | Форма продукта |
| `actions_for_contacts_table` | Таблица заявок |
| `classes_table_all` | Все занятия |
| `visits_for_lesson_table_conn` | Посещения занятия |
| `requests_for_class_table_conn` | Заявки продукта |
| `classes_for_student_table_conn` | Занятия студента |
| `get_all_lessons_by_product` | Все занятия с датами/временем |
| `get_all_masters_and_producers` | Мастера и продюсеры |
| `get_action_info_for_reminders` | Данные для напоминаний |
| `done_requests_for_actions` | Завершённые заявки |
| `absent_subscription_users_last_30_days` | Абонементщики без визитов 30 дней |
| `counter_of_active_students` | Счётчик активных студентов |
| `subscription_actions_for_contact` | Заявки для привязки к абонементу |

---

## Схема `fin`

### Назначение
Финансовая схема. Хранит транзакции (платежи), счета, сертификаты, P&L отчёт, экономику.

### Ключевые сущности
- **`transactions`** — финансовые транзакции (платежи, возвраты, переводы)
- **`accounts`** — счета (банковские, сертификатные)
- **`economics`** — внешние расходы
- **`pl_report_final`** — строки P&L отчёта
- **`pl_categories`** — категории P&L

### Статусы транзакций Tinkoff (`tks_state`)
- `CONFIRMED` — подтверждён
- `SIGNED` — подписан (B2B)
- `AUTHORIZED` — авторизован
- `REFUNDED` — полный возврат
- `PARTIAL_REFUNDED` — частичный возврат

### ⚠️ Правила подсчёта дохода, возвратов и выручки (КРИТИЧНО)

Эти правила нужно использовать **всегда**, когда задаётся вопрос про доход / возвраты / выручку. Не отступать от них без явного запроса.

**Статус `Ожидается оплата` НИКОГДА не учитывается** — игнорировать.

Поле-ключ к различию входящих и исходящих платежей — **`from_our_organization`** в `fin.transactions`:
- `from_our_organization = false` → деньги идут **в Gogol School** (от клиента/контрагента)
- `from_our_organization = true` → деньги идут **из Gogol School** (наружу, например возврат клиенту)

#### Доход
Транзакции с `tks_state IN ('CONFIRMED', 'AUTHORIZED')` **И** `from_our_organization = false`.

#### Возвраты
Транзакции с `tks_state IN ('CONFIRMED', 'REFUNDED', 'PARTIAL_REFUNDED')` **И** `from_our_organization = true`.

#### Выручка (revenue)
```
выручка = SUM(amount) WHERE tks_state IN ('CONFIRMED', 'AUTHORIZED') AND from_our_organization = false
        − SUM(amount) WHERE tks_state IN ('CONFIRMED', 'REFUNDED', 'PARTIAL_REFUNDED') AND from_our_organization = true
```

То есть: **доход минус возвраты**. Статус `CONFIRMED` появляется в обеих частях формулы — различие только по `from_our_organization`.

### Actions

#### `approve_transaction`
Ручное подтверждение: `tks_state = 'CONFIRMED'`, `transaction_date = now()`.

**Аргументы:** `id`

#### `make_refund`
Создание транзакции возврата. Копирует все поля, меняет направление (`account_from` leftrightarrow `account_to`), привязывает через `linked_transaction`, списывает бонусы лояльности.

**Аргументы:** `id` — ID оригинала

#### `create_subscription_for_payment`
Создание абонемента при оплате (только `class_type = 19`). Не создаёт повторно если уже есть. Привязывает ожидающие заявки студента.

**Аргументы:** `id` — ID транзакции

#### `add_transaction_to_pl_report`
Добавление транзакций в P&L.

**Аргументы:** `ids`

#### `add_all_transactions_from_economics`
Массовое добавление внешних расходов в P&L (батчами по 100).

#### `check_delay_certificates`
Деактивация просроченных сертификатов через `get_delay_is_active_certificate`.

#### `merge_accounts`
Объединение банковских счетов контрагента: счёт с наименьшим ID — основной, все транзакции переподвязываются, дублирующие счета удаляются.

**Аргументы:** `user_id`

#### `notification_sent`
Запись факта отправки уведомления (`notification_date`, `notification_who`).

**Аргументы:** `id`, `now`, `person`

#### `edit_account_balance_comment`
Открытие формы редактирования комментария к балансу счёта.

**Аргументы:** `account_type`

### Triggers

#### `tinkoff_shop_import` (на `transactions`, INSERT/UPDATE)
Главный триггер импорта платежей из Tinkoff. Срабатывает при наличии `tks_*` полей. Определяет покупателя (`getCustomerNew`), продукт (`getProduct`), заявку (`getRequest`), счета. Обновляет транзакцию полным набором полей.

#### `create_account_for_contact` (на `fin.contacts`, INSERT)
Автоматически создаёт банковский счёт при добавлении контакта.

#### `on_confirmed_payment` (на `transactions`, INSERT/UPDATE)
Главный триггер подтверждённых платежей (`tks_state IN CONFIRMED/SIGNED/AUTHORIZED`, `amount > 0`). Параллельно:
1. **Wazzup** — WhatsApp-уведомление студенту об оплате
2. **Subscription** — создаёт абонемент если `class_type = 19`
3. **EmailConnect** — привязывает email к заявке

#### `update_transaction` (на `transactions`, UPDATE)
Если нет заявки — находит и привязывает. Обновляет `account_from` по контрагенту.

#### `update_transaction_in_pl` (на `transactions`, UPDATE)
При изменении `tks_state` — удаляет старую запись P&L и добавляет новую (пересчёт).

#### `add_certificate_number` (на `transactions`, INSERT/UPDATE)
При подтверждении транзакции-сертификата генерирует номер: формат `YYYYMM` + порядковый номер (2 цифры).

#### `create_certificate_if_needed` (на `transactions`, INSERT/UPDATE)
При покупке сертификата (продукт ID 1198): создаёт счёт-сертификат для получателя подарка с номиналом = сумма + использованные сертификаты. Если на конкретный продукт — создаёт транзакцию использования.

#### `use_bonuses_and_certificate_if_needed` (на `transactions`, INSERT/UPDATE)
При `tks_state = CONFIRMED/AUTHORIZED/SIGNED`: списывает/начисляет бонусы за карту лояльности; начисляет кэшбэк (5% для обычных, 100% для типов 6/15/30); при использовании сертификата — уменьшает баланс сертификатного счёта.

#### `update_bonuses_by_promocode` (на `transactions`, INSERT/UPDATE)
Начисляет 10% от суммы покупки владельцу промокода при первом использовании. Создаёт уведомление в `referral_bonus_notifications`.

#### `update_amount_text` (на `transactions`, BEFORE INSERT/UPDATE)
Генерирует текстовое представление суммы. При возврате (`linked_transaction`) обновляет `cancellation_status` в заявке (8=полный, 9=частичный).

#### `update_refunded_state` (на `transactions`, BEFORE INSERT/UPDATE)
Нормализует `PARTIALLY_REFUNDED` → `PARTIAL_REFUNDED`.

#### `update_ym_client_id` (на `transactions`, INSERT/UPDATE)
Сохраняет Яндекс.Метрика client_id и Roistat ID в `base.yandex_ids_for_contacts` и `base.analytics_ids_for_contacts`.

#### `create_requests_for_ticket` (на `transactions`, INSERT/UPDATE)
При покупке «Билета» (`class_type = 35`) — создаёт заявки на все связанные продукты из `ticket_products_table` со статусом 6.

#### `add_date_creation` (на `economics`, INSERT)
`date_creation = now()`.

#### `update_pl_after_amount_changing` (на `economics`, UPDATE)
Обновление записи P&L при изменении суммы расхода.

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `combined_transactions_table` | Все транзакции (основная таблица) |
| `accounts_table_all` | Все счета |
| `certificates` | Все сертификаты |
| `certificate_info` | Информация по сертификату |
| `find_certificate_by_number_email_phone` | Поиск сертификата |
| `monitor_payments` | Мониторинг платежей |
| `economics_table` | Внешние расходы |
| `pl_by_categories_table` | P&L по категориям |
| `get_finance_report` | Финансовый отчёт |
| `income_compare_table` | Сравнение выручки |
| `bank_stat_by_month_table` | Банковская статистика |
| `get_total_amount_paid_by_action_for_contact` | Общая сумма оплат студента |
| `check_promocode_uses_by_contact` | Использование промокода |

---

## Схема `marketing`

### Назначение
Маркетинговые кампании, списки рассылки, промокоды, реферальная программа.

### Создание списка для рассылки

Если просят «создать список для рассылки» — он создаётся в Ozma, в маркетинговых списках: <https://ozma.gogol.school/views/marketing/lists_table> (view `marketing.lists_table`, контакты — `marketing.contacts_in_lists_table`).

**Лимит 500 контактов на список.** Если по выборке получается больше 500 контактов — разбивать на несколько списков по 500 штук, в название каждого добавлять пометку «ч.1», «ч.2», «ч.3» и т.д. (напр. `<имя> ч.1`, `<имя> ч.2`).

### Actions

#### `create_list_from_campaign`
Сегментированные списки из кампании по размеру скидки (только «Горячих»): 1500 руб. → список 416, 3000 руб. → 417, 4500 руб. → 419, 6000 руб. → 420, 7500 руб. → 421, 9000 руб. → 422.

#### `create_list_from_selected_elements`
Новый список из выбранных контактов.

**Аргументы:** `ids`, `list_name`

#### `send_action`
Запуск маркетинговой задачи: pm.action → `in_progress`, кампания → `in_progress`.

**Аргументы:** `action`, `campaign`

#### `spread_action`
Рассылка кампании через `marketing/campaigning.mjs`.

**Аргументы:** `action`, `campaign`

#### `update_students_list`
Обновление фиксированных списков по стадиям (494=Горячий, 498=Прохладный, 499=Тёплый, 500=Холодный). «Горячие» дополнительно распределяются по 5 подспискам (879-883, не более 599 в каждом).

**Аргументы:** `id` — ID списка

#### `send_bonuses_messages_whatsapp`
WhatsApp рассылка о баллах лояльности из списка (шаблон `3f778c36-a6fd-4c40-a62d-081a2c4af31c`).

**Аргументы:** `id` — ID списка

#### `clean_list`
Полная очистка маркетингового списка.

**Аргументы:** `list` — ID списка

### Triggers

#### `set_fields_values_before_insert_campaign` (на `campaigns`, BEFORE INSERT)
Заполняет `created_datetime`, `created_person`, `responsible_person`.

#### `cancel_actions` (на `campaigns`, UPDATE)
При переводе кампании в статус 1 (отменена) — переводит все pm.actions в статус 9 (отменён).

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `campaigns_table` | Список кампаний |
| `lists_table` | Маркетинговые списки |
| `contacts_in_lists_table` | Контакты в списке |
| `students_list_joined_with_marketing_list` | Студенты с наличием в списке |
| `check_actions_done_by_marketing` | Выполненность задач кампании |
| `ref_promocodes_table` | Реферальные промокоды |
| `referral_bonus_notifications_table` | Уведомления о реферальных бонусах |
| `email_campigns_view` | Email-кампании (Unisender) |

### Статусы email-рассылок (open/click/payment)

Статистика по письмам приходит в Ozma (Unisender API дёргать не нужно):

- **`marketing.email_statuses_for_actions`** — события по рассылкам:
  - `action` → `pm.actions` (у action есть `sys_related_contact` → `base.people` — это получатель).
  - `campaign_id` (string) — id кампании в Unisender (искать в `marketing.email_campigns_view`).
  - `event_time` (datetime).
  - `event_type` → `marketing.email_event_types`.
- **`marketing.email_event_types`** — словарь событий:
  - `ok_sent` — отправлено
  - `ok_delivered` — доставлено
  - `ok_read` — прочитано (open)
  - `ok_link_visited` — клик по ссылке
  - `payment_completed` — оплата после письма
  - `payment_completed_72` — оплата в течение 72 часов после рассылки

Шаблон выборки получателей по событию:

```funql
SELECT DISTINCT a=>sys_related_contact AS contact
FROM marketing.email_statuses_for_actions AS es
LEFT JOIN pm.actions AS a ON a.id = es.action
WHERE es.campaign_id IN (<...>)
  AND es.event_type = (SELECT id FROM marketing.email_event_types WHERE name = 'ok_read')
  AND es.event_time BETWEEN <от> AND <до>
```

Для «не открывали»: брать базовое множество с `ok_sent`, вычитать `ok_read` через NOT EXISTS.

---

## Схема `pm`

### Назначение
Project Management — задачи менеджеров по работе с клиентами (звонки, встречи, письма).

### Triggers

#### `set_fields_values_before_insert_action` (на `actions`, BEFORE INSERT)
Заполняет `created_datetime`, `created_person`, стадию «new» если не указана.

#### `insert_actions_for_contacts_after_insert_action` (на `actions`, AFTER INSERT)
Если указан `sys_related_contact` — создаёт связь в `pm.actions_for_contacts`.

#### `update_marketing_stage_after_update_action_stage` (на `actions`, UPDATE)
При переходе задачи в статус 7 (выполнено): если все задачи кампании выполнены — кампания → статус 2 (завершена).

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `actions_table` | Задачи пользователя |
| `all_actions_table` | Все задачи |
| `actions_board` | Kanban-доска |
| `active_actions` | Активные задачи |
| `actions_for_contact_table_conn` | Задачи по контакту |
| `notes_for_action_timeline` | Заметки по задаче |

---

## Схема `integration`

### Назначение
Интеграции с WhatsApp (через Wazzup) и Telegram.

### Ключевые сущности
- **`messages_for_send`** — очередь исходящих сообщений
- **`templates`** / **`templates_for_messengers`** — шаблоны сообщений
- **`requests_for_sending`** — запросы на Telegram-рассылку
- **`whatsapp_notification_for_ol_requests`** — отправленные уведомления об оплате

### Actions

#### `accept_messages`
Помечает сообщения как отправленные (`is_send = true`).

**Аргументы:** `ids`

#### `registrate_user_in_wazzup`
Stub-действие (реальная логика закомментирована).

### Triggers

#### `set_fields_values_before_insert_template` (на `templates`, BEFORE INSERT)
`dt_create = now()`, `creator = current_user`.

#### `transform_message_for_telegram_before_insert` (на `messages_for_send`, BEFORE INSERT)
Подстановка переменных `{{param}}` в текст сообщения из данных продукта (`crm.get_action_info_for_reminders`).

---

## Схема `subscription`

### Назначение
Управление абонементами на занятия (class_type = 19).

### Triggers

#### `change_status` (на `subscription_info`, INSERT/UPDATE)
`activation_date = null` → «не активирован»; дата + остались слоты → «активирован абонемент»; все слоты использованы → «использован».

#### `add_owners_classes` (на `subscription_info`, INSERT/UPDATE)
При назначении `owner`: определяет продукт по кол-ву занятий (1→5205, 2→5315, 4→5206, 6→5207, 8→5204), создаёт заявку, привязывает ожидающие заявки к абонементу.

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `all_subscriptions` | Все абонементы |
| `subs_form` | Форма абонемента |
| `requests_for_subs` | Заявки абонемента |
| `subscription_stat` | Статистика |
| `visits` | Посещения по абонементу |

---

## Схема `loyalty`

### Назначение
Программа лояльности: карты, баллы, кошельки Apple/Google Wallet.

### Как смотреть остаток баллов у студента

Брать поле **«Рассчитанный текущий бонус»** из карточки студента (контакта) в OzmaDB. **Не складывать баллы вручную** из `loyalty_bonuses` / транзакций — поле уже учитывает сроки жизни, списания и начисления.

### Actions

#### `initialize_bonuses`
Начальное начисление: 1000 постоянных баллов. До 2025-12-01 — ещё 4000 «чёропятничных» баллов с lifetime=7 дней.

**Аргументы:** `contact`

### Triggers

#### `update_activation_date` (на `loyalty_card`, INSERT/UPDATE)
`is_active = true` → `activation_date = now()` + обновляет дату постоянных бонусов (lifetime >= 365). `is_active = false` → `activation_date = null`.

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `all_people_with_bonuses` | Контакты с бонусами |
| `active_bonuses_for_contact` | Активные баллы |
| `find_loyalty_card_by_number_phone_email` | Поиск карты |
| `wallet_info` | Информация кошелька |

---

## Схема `reports`

### Назначение
Генерация документов: договоры оказания услуг, счета-оферты.

### Actions

#### `generate_and_export_contract_document`
Генерация договора по заявке. Формирует текст расписания по модулям (1/2/3/4+ занятий), долевое распределение оплаты, генерирует Word/PDF.

**Аргументы:** `id` — ID заявки, `ext` — расширение (.pdf по умолчанию)

#### `generate_and_export_contract_document2` / `_for_agreement` / `_from_subscription`
Варианты договоров для разных типов продуктов.

#### `create_offer_invoice`
Создание счёта-оферты. Создаёт запись в `reports.offer_invoices` и открывает форму.

**Аргументы:** `person`, `action`, `new_first_name`, `new_last_name`, `new_phone`, `new_email`, `new_telegram`, `doc_type`, `pay_type`

#### `generate_offer_invoice_pdf`
Генерация PDF счёта-оферты.

### Ключевые User Views

| View | Назначение |
|------|-----------|
| `all_contracts_and_offer_invoices` | Все договоры и оферты |
| `generate_contract_document` | Данные для договора |
| `get_all_lessons_by_request` | Занятия по заявке |
| `get_full_amount_by_request` | Сумма по заявке |
| `get_subscription_contract_data` | Данные договора абонемента |
| `offer_invoice_form_v2` | Форма счёта-оферты |

---

## Схема `sales`

### Назначение
Действия менеджеров по продажам (звонки, переписка).

### Actions

#### `create_sales_action`
Создание черновиков звонков для выбранных заявок. Создаёт записи в `sales.actions` с `is_draft = true`.

**Аргументы:** `ids`, `product`

#### `publish_actions`
Публикация черновиков (снимает `is_draft`).

**Аргументы:** `action`

---

## Схема `staffs`

### Назначение
Расчёт оплаты мастеров по договорам.

### Triggers

#### `fill_contract_for_masters_additional_lessons` (на `masters_additional_lessons`, INSERT)
Автоматическое определение договора мастера при добавлении дополнительного занятия.

#### `update_bet_manually` (на `masters_additional_lessons`, UPDATE)
Обновление ставки при ручном изменении.

---

## Схема `ad`

### Назначение
Рекламные договоры с рекламодателями (физлица — ИП/СЗ, организации — ООО).

### Triggers

#### `recalculate_amount` (на `ad_contracts`)
Пересчёт суммы рекламного договора.

### User Views

| View | Назначение |
|------|-----------|
| `ad_contracts_table` | Таблица рекламных договоров |
| `advertisers_table` | Рекламодатели |
| `ad_contract_form` | Форма договора |

---

## Схема `docs`

### Назначение
Трудовые договоры с сотрудниками.

### Triggers

#### `check_staff_contracts_is_main_flag` / `check_staff_contracts_work_type` / `update_amount_string`
Валидация и обновление полей трудовых договоров.

---

## Архитектурные паттерны

### 1. BEFORE vs AFTER триггеры
- **BEFORE** — изменяют `args` и возвращают модифицированный объект (поля попадают в запись)
- **AFTER** — создают/обновляют связанные записи на основе только что созданной/обновлённой записи

### 2. Оптимизация: args-first
Оптимизированные триггеры сначала проверяют нужные поля в `args` (из текущего обновления), и только если их нет — делают SELECT из БД. Это сокращает количество запросов.

### 3. Динамический merge-контакт
`new_merge_contacts` использует `connections_between_entity_and_other_entities_table` для автоматического обнаружения всех FK-связей, что позволяет переподвязывать данные без хардкода таблиц.

### 4. Система стадий студента (статус прогрева)

Стадии (enum): `Холодный` → `Прохладный` → `Теплый` → `Горячий`. Это и есть «Статус прогрева» в карточке студента.

#### Где лежат поля

| Поле | Назначение |
|------|-----------|
| `base.people.stage` | Текущий статус прогрева студента (общий) |
| `base.people.stage_softs` | Параллельный статус для soft-skills направления |
| `base.people_stages_history.stage` | История изменений общего статуса |
| `base.people_softs_stages_history.stage` | История изменений soft-статуса |
| `crm.class_type.stage` | **Правило**: до какой стадии тип продукта поднимает студента |
| `crm.class_type.softs_stage` | **Правило** для soft-стадии |

Правила прогрева по типам продуктов смотреть в view [`crm.class_types_table_all`](https://ozma.gogol.school/views/crm/class_types_table_all) (entity `crm.class_type`, поля `stage` / `softs_stage`).

#### Как меняется (триггер)

`crm.update_student_stage_before_done_action_for_contacts` на `crm.actions_for_contacts` (BEFORE UPDATE):

1. Срабатывает только при переходе заявки в `request_status = 7` («Прошёл обучение, всё ок»).
2. Берёт целевую стадию (`to_stage` / `to_stage_softs`) из `crm.class_type` продукта через view `crm.sys_get_student_stage`.
3. Если `to_stage` **выше** текущей — обновляет `base.people.stage` (и/или `stage_softs`). Если ниже или равна — не понижает.
4. **Особый случай для абонементов** (`class_type = 18`): если целевая стадия равна текущей, проверяет количество завершённых абонементов через `crm.classes_for_student_table_conn` (`only_subscriptions=true, only_completed=true`). При 2+ завершённых абонементов — повышает на одну ступень.

Динамический пересчёт стадии «как если бы посчитать заново из истории» — view `base.current_students_stage` (используется в `update_stage_all_students`).

#### Прогрев при оплате (срез на момент транзакции)

В «Ежедневном отчёте» (view [`fin.transactions_table_all`](https://ozma.gogol.school/views/fin/transactions_table_all)) есть две колонки прогрева:

- **«Прогрев»** = `p.stage` — текущая стадия плательщика, как есть прямо сейчас.
- **«Прогрев при оплате»** = последняя запись из `base.people_stages_history` с `date_create ≤ transaction_date`. Это срез на момент оплаты, вычисляется на лету (не хранится отдельным полем).

Плательщик `p` берётся через джойн: из пары `account_from=>contractor` / `account_to=>contractor` — тот, у кого `is_our_organization = false`.

Логика подзапроса:

```funql
SELECT psh.stage
FROM base.people_stages_history AS psh
WHERE psh.person = p.id
  AND psh.date_create <= transactions.transaction_date
ORDER BY psh.date_create DESC
LIMIT 1
```

Те же фильтры доступны и как аргументы view:

- `$payment_stage` (enum) — фильтр по стадии на момент оплаты;
- `$diff_stage = 'Нет'` — показать только транзакции, где стадия на момент оплаты **не совпадает** с текущей (т.е. студент уже повысился после покупки).

**Практический вывод для маркетинга**: если нужен список «кто покупал, когда был холодным» — фильтровать `fin.transactions_table_all` по `$payment_stage`. Если нужен текущий срез прогрева — `base.people.stage` напрямую.

### 5. Жизненный цикл абонемента
1. Оплата → `on_confirmed_payment` → создаёт `subscription.subscription_info`
2. Назначение владельца → `add_owners_classes` → привязывает ожидающие заявки
3. `request_status_updating` → обновляет статусы заявок
4. Отмена заявки → освобождает слот → следующая ожидающая заявка занимает место

### 6. Сертификаты
- Покупка = транзакция на продукт ID 1198
- `create_certificate_if_needed` создаёт счёт-сертификат
- `add_certificate_number` генерирует номер формата `YYYYMMNNN`
- Использование = транзакция с `used_certificate_number` → `use_bonuses_and_certificate_if_needed` уменьшает баланс

### 7. P&L отчёт
- `pl_report_final` — строки отчёта
- `update_transaction_in_pl` пересчитывает строку при каждом изменении статуса транзакции
- Внешние расходы добавляются через `economics` → модуль `fin/pl_report`

### 8. Ключевые модули

| Модуль | Назначение |
|--------|-----------|
| `admin/simple_select` | Базовые SELECT-хелперы для всех actions/triggers |
| `fin/transactions_data` | Парсинг данных транзакции: `getCustomerNew`, `getProduct`, `getRequest` |
| `fin/account` | Работа со счетами: `getAccountByContactId`, `getAccountByTerminalKey` |
| `fin/pl_report` | Добавление строк в P&L: `addTransactionToPLReport`, `addExternalTransactionToPLReport` |
| `fin/logger` | Логирование ошибок: `writeErrorToLog` |
| `reports/doc_generator` | Форматирование для документов: `getAmountText`, `getDateText`, `getAmountTextNew` |
| `marketing/campaigning` | Рассылка кампаний: `spreadAction` |
| `crm/lessons_generator` | Генерация занятий: `generateLessonByLessonGenerator` |
| `admin/user_info` | Текущий пользователь: `getPersonId` |

---

## FunQL — типовые подводные камни

Проверено эмпирически на текущей версии OzmaDB + MCP (на 2026-05-25).

### 1. Нет `HAVING`

FunQL не поддерживает `HAVING`. Попытка → `Parse error: token HAVING: parse error`.

**Обход** — оборачивать `GROUP BY` в подзапрос с алиасом и фильтровать через `WHERE`:

```funql
SELECT contact_id
FROM (
    SELECT customer AS contact_id, SUM(amount) AS total
    FROM fin.transactions
    GROUP BY customer
) AS sub
WHERE total >= 50000 AND total < 100000
```

Алиас подзапроса — через `AS sub` (или другой), без алиаса parse error.

### 2. Все агрегаты в SELECT обязательно с `AS`

`SELECT COUNT(id) FROM ...` → `Unnamed results are allowed only inside expression queries`.

Каждое выражение в SELECT должно иметь имя: `COUNT(id) AS cnt`, `SUM(amount) AS s`. Это касается не только агрегатов — любое вычисление в SELECT нужно именовать.

### 3. Ответ MCP-обёртки обрезается до 50 строк

`funql_query` через MCP отдаёт максимум **50 записей за запрос**, даже если в самом FunQL стоит `LIMIT 200`. В ответе будет `_returned: 50`, реальный размер выборки — в `_total`. Для дочитывания — пагинировать через `offset`.

```funql
SELECT id FROM ... ORDER BY id LIMIT 50 OFFSET 0
SELECT id FROM ... ORDER BY id LIMIT 50 OFFSET 50
...
```

Без `ORDER BY` пагинация даст нестабильный порядок — добавлять обязательно.

Для именованных view (`named_view_query`) лимит тот же, пагинация через параметр `offset`.

---

*Конец документации*
