---
name: check-transactions
description: Daily payments reconciliation for Gogol School — fetches transactions from CloudPayments / Tinkoff Acquiring / Tinkoff Statement / Mixplat / Yandex Split for a given day, queries fin.transactions in Ozma, and produces a Markdown discrepancy report. Use when user mentions "сверка платежей", "сверить за день/вчера/дату", "reconcile payments", or asks to compare provider data with Ozma.
---

# check-transactions

## When to use

User says one of:
- «сверь за вчера», «сверь 20 мая», «reconcile yesterday», «сверка платежей за дату»
- «покажи отчёт за вчера» (reads cached report)

## How

1. **Parse target date** from the user's message. Russian relative dates: "вчера" = today−1, "сегодня" = today. ISO: `YYYY-MM-DD`. Russian month names: «20 мая» = `2026-05-20` (year = current). If ambiguous, ASK before fetching.

   **Без даты (и в плановом автозапуске) — окно от маркера «+».** В `fin.transactions` комментарий
   «+» стоит на последней сверенной оплате (см. шаг 9). Найти его (`comment = '+'` или «+» в составе
   комментария через запятую), взять его МСК-дату и **отнять двое суток** — это первый день окна;
   последний — **сегодня до текущего момента**. Прогнать шаги 3–5 по каждому дню окна. Двое суток
   назад — потому что строки доезжают в Озму задним числом (охлаждение рассрочки, вечерние
   подтверждения, недоехавшие вебхуки); повторная сверка уже проверенного дня безопасна.
   Сегодняшний день всегда входит в окно отдельным блоком с пометкой «предварительно, день не закрыт».

   **Сверять надо закрытый день.** МСК-день закрывается в 00:00 следующих суток. Пока он открыт, картина неполная: подтверждения по рассрочке приходят вечером (у CP по одной и той же транзакции `CreatedDate` 15:26, `AuthDate` 19:25, `ConfirmDate` 19:39 МСК), а payout уходит следующим днём. Если просят сегодняшний день — сверить можно, но сразу сказать, что это предварительно: скрипт сам поставит ⚠️ в шапку отчёта и в «Что делать».

2. **Determine intent:**
   - "сверь" / "reconcile" / `--force` → always fetch
   - "покажи отчёт" / "show report" → read cache, mention age in header

3. **Run fetch** (only on fetch intent):
   ```bash
   python3 ~/.claude/skills/check-transactions/scripts/fetch_payments.py <YYYY-MM-DD>
   ```
   This writes JSON files to `/tmp/reconcile_<date>/`. Stdout is a short summary — read it.

4. **Query Ozma** via OzmaDB MCP `mcp__ozma__funql_query`. The date semantics
   are **MSK** everywhere — bookkeeper-friendly. `fin.transactions.tks_date_time`
   is a `text` column storing UTC ISO 8601 (e.g. `'2026-05-21 08:26:34.010513+00:00'`),
   so we compare strings against the UTC equivalents of the MSK day boundary:

   - MSK 00:00 of `<date>` = UTC `<date−1> 21:00:00`
   - MSK 00:00 of `<date+1>` = UTC `<date> 21:00:00`

   Compute `<prev_date>` = `<date> − 1 day` and substitute into:
   ```funql
   SELECT
     id, tks_order_id, amount, tks_amount, tks_state, tks_date_time,
     account_to, account_to=>name as account_to_name, account_to=>type as account_to_type,
     account_from, account_from=>name as account_from_name, account_from=>type as account_from_type,
     account_from=>contractor as cert_buyer_id, account_from=>contact as cert_holder_id,
     payment_type, tks_customer_name, tks_description,
     customer, action, tks_email, tks_phone, tks_promocode,
     is_certificate, is_certificate_payment, used_certificate_amount, comment,
     tks_is_gift, tks_paper_certificate, tks_gift_recipient, certificate_number
   FROM "fin"."transactions"
   WHERE is_deleted = false
     AND (
       (tks_date_time >= '<prev_date> 21:00:00+00:00' AND tks_date_time < '<date> 21:00:00+00:00'
        AND (account_to IN (6, 1570, 26728, 27017, 27018, 26729, 23719)
             OR account_from IN (6, 1570, 26728, 27017, 27018, 26729, 23719)))
       OR
       ((is_certificate_payment = true OR is_certificate = true)
        AND transaction_date >= '<prev_date> 21:00:00+00:00'::datetime
        AND transaction_date <  '<date> 21:00:00+00:00'::datetime)
     )
   ORDER BY tks_date_time, id
   ```
   `action` нужен, чтобы найти пару к погашению сертификата; `tks_is_gift` /
   `tks_paper_certificate` / `tks_gift_recipient` — для комментариев к подарочным
   сертификатам; `tks_promocode` — чтобы не флагать «сумма ниже цены», когда скидку дал
   промокод (напр. `ITSTRY`: пробное за 500 ₽ вместо 1 500 ₽). Всё это — шаг 7.
   **Лимит ответа `funql_query` — 50 строк за вызов** (даже если передать `limit` больше): в конце
   приходит маркер `{"_truncated": true, "_total": N, "_returned": 50}`. Если `_total` > 50 —
   листать `offset=50, 100, …` с тем же `ORDER BY tks_date_time, id` и склеивать. Маркер
   `_truncated` из списка убрать. Поля-ссылки (`account_to`, `customer`, …) приходят объектами
   `{id, pun}` — reconcile.py принимает и объект, и число.

   Save returned rows to `/tmp/reconcile_<date>/ozma.json` as `{date, count, fetched_at_utc, transactions: [...]}`.
   (`customer`, `tks_email`, `tks_phone` feed the level-3 contact comparison; `customer` may serialize as int or `{id,...}` — reconcile.py handles both.)

   **Certificate rows use a different date column.** Certificate-usage rows
   (`is_certificate_payment = true`, `account_from=>type = 'Сертификат'`) have
   `tks_date_time = NULL` and `tks_order_id = NULL` — they never match the
   acquiring branch. They are picked up by the second OR branch, date-filtered on
   `transaction_date` (a `timestamp with time zone`, so cast literals with
   `::datetime`) over the same MSK-day UTC window. `account_from=>type` /
   `account_to=>type`, `is_certificate*`, `used_certificate_amount`, `comment`,
   and `cert_buyer_id`/`cert_holder_id` (the certificate's `contractor`/`contact`)
   feed the certificate auto-actions (see step 7).

   **Example** for `<date>=2026-05-21`:
   ```
   tks_date_time >= '2026-05-20 21:00:00+00:00'
   tks_date_time <  '2026-05-21 21:00:00+00:00'
   ```
   This picks up rows whose UTC timestamp falls inside `[2026-05-20T21:00Z, 2026-05-21T21:00Z)` — exactly the MSK day 21.05.

   String comparison works because UTC timestamps in this column follow lexicographic ISO 8601 order. If a row has a broken `tks_date_time` (e.g. `'2021-13-07'`), it will compare correctly as text but won't fall into any reasonable window; if FunQL still fails, narrow the predicate or pre-filter and record the incident in meta.

   **Pending-строки обязаны быть в выборке** — reconcile.py должен их **видеть**, иначе зависшие оплаты не находятся. Логика (без blacklist'а, он был убран 30.07.2026):
   - Провайдер по тому же `tks_order_id` отдал `succeeded`/`refunded`, а других строк в Озме нет → 🚨 **зависшая оплата**: деньги у провайдера, в учёт не попали (обычно не долетел вебхук). Отдельная секция отчёта + строка в «Что делать».
   - У провайдера денег нет (`failed`, `pending` или строки вообще нет) → брошенная попытка: в матчинг не идёт, попадает в info-строку «Брошенные попытки» с перечислением id.
   - Если у заказа есть и pending-строка, и закрытая (повторная попытка прошла) — матчится закрытая, pending уходит в брошенные.
   - Pending-строки никогда не показываются как `only_in_ozma`.

   **Refund pairs:** a refunded payment lives in Ozma as **two rows** with the same `tks_order_id`. Direction differs:
   - Capture row: `account_to` = acquiring account.
   - Refund row: `account_from` = acquiring account.

   `tks_state` may be `CONFIRMED`/`AUTHORIZED`/`SIGNED` on **both** rows — refund direction is determined by `account_from`/`account_to`, not by state. The reconcile script keys on `(tks_order_id, role)` where role is derived from direction; provider `succeeded` lines up with role=capture and provider `refunded` with role=refund.

   **4b. Contact data for level-3 comparison + certificate dedupe.** Collect the
   union of: every non-empty `customer` id from the transactions above, **plus**
   every non-empty `cert_buyer_id` from rows where `is_certificate_payment = true`
   (the buyer ids feed the certificate dedupe/anti-fraud comparison in step 7).
   Then run two more `mcp__ozma__funql_query` calls over that widened id set and
   save both into `/tmp/reconcile_<date>/ozma_contacts.json` as
   `{people: [...], communication_ways: [...]}`:
   ```funql
   SELECT id, first_name, last_name, patronymic, nickname
   FROM "base"."people" WHERE id IN (<customer_and_buyer_ids>)
   ```
   ```funql
   SELECT contact, type, data
   FROM "base"."communication_ways"
   WHERE is_deleted = false AND contact IN (<customer_and_buyer_ids>)
   ```
   If there are no `customer` ids, write `{"people": [], "communication_ways": []}`
   (or skip the file — reconcile.py treats a missing file as no contact data, and
   every provider contact field then degrades to `missing_in_ozma`).

5. **Run reconcile**:
   ```bash
   python3 ~/.claude/skills/check-transactions/scripts/reconcile.py /tmp/reconcile_<date>/
   ```
   Stdout is the raw Markdown report (levels 1–3 + certificates). **Do NOT dump it
   verbatim** — reformat into the operator worklist below.

   Что в отчёте появилось 30.07.2026 и на что смотреть в первую очередь:
   - `## 🚨 Оплачено у провайдера, в Озме «Ожидается оплата»` — деньги есть, в учёте нет. 🔴 — разбирать, 🟡 — подтверждено меньше часа назад, вебхук мог не успеть (перепроверить позже, руками не проводить).
   - `## ⏳ У провайдера в процессе` — рассрочка в охлаждении или холд. Не расхождение, вернуться при перепроверке дня.
   - `- ⚠️ <канал> не выгружен (...)` — по каналу сверки не было. Скрипт больше НЕ выдаёт по таким каналам «only in ozma»: это пробел в данных, а не расхождение.
   - `ℹ️ Брошенные попытки` — pending без денег у провайдера, шум, но id перечислены.

   ### Формат отчёта для пользователя (обязательный)

   Пиши по-русски, коротко, по пунктам. **Не объясняй, почему провайдер надо
   проверять руками** (не писать «нет токена», «протухла сессия» — это и так
   известно). Структура:

   ```
   ## Сверка платежей за <ДД месяца ГГГГ>

   ### 0) Требует действий сейчас
   <зависшие оплаты 🔴 из секции «Оплачено у провайдера, в Озме Ожидается оплата»:
    Заказ | Кто | Сумма | Канал | что сделать (реплей вебхука / провести руками)>
   <если пусто — «зависших оплат нет»; пункт не выкидывать>
   <сюда же 🟡-свежие и ⏳-в процессе одной строкой: «N ждут подтверждения, перепроверить после закрытия дня»>

   ### 1) Тинькофф (эквайринг) — перепроверить руками
   <таблица оплат за день через Тинькофф-эквайринг (account 6): Заказ | Кто | Сумма | Статус>
   <строки «Ожидается оплата» помечать как вероятную брошенную попытку>

   ### 2) Яндекс.Сплит — перепроверить руками
   <список оплат по Сплиту; если в Озме их нет — так и написать;
    ссылка на консоль https://console.pay.yandex.ru/payments>

   ### 3) CloudPayments (эквайринг + рассрочка) и Mixplat — проверено ✅
   <кратко: кол-во и суммы по каждому каналу; «контакты совпадают» если уровень 3 ок>

   ### 4) Сертификаты
   <кратко статус: сколько авто-погашений, есть ли анти-фрод-флаги>
   ```

   Правила наполнения:
   - **Пункт 0** — всегда первый и всегда есть. Зависшие оплаты 🔴 — единственное,
     где деньги уже у нас, а в выручке их нет; их нельзя утопить в списках ниже.
   - **Пункты 1–2** — это worklist «проверить руками». Туда попадают каналы,
     которые не выгрузились автоматически (Тинькофф-эквайринг, Тинькофф-выписка,
     Сплит). Перечисляй конкретные оплаты из Озмы, чтобы сотруднику было что искать
     в кабинете. Если по каналу оплат в Озме нет — напиши «оплат нет», но всё равно
     оставь пункт (день мог содержать платежи, не попавшие в Озму).
   - **Пункт 3** — каналы, которые сверились автоматически (CloudPayments, Mixplat):
     только итог, без «расхождений» уровня 1/2, если они вызваны невыгруженными
     провайдерами.
   - **Пункт 4** — итог по блоку `## 🎁 Сертификаты` из reconcile.py: сколько комментариев
     записано (шаг 7), какие подарочные сертификаты бумажные (их надо подготовить).
   - **Коротко.** По каналу — количество и сумма, без списка «крупных» оплат. Деталь конкретной
     оплаты — только если с ней что-то не так или нужно действие.
   - **Без внутренней кухни провайдеров**: не писать про дрейф статусов, бэкфиллы, названия
     скриптов, хронику попыток (Сплит не прошёл → оплатила Долями — не интересно). Без «лирики» —
     наблюдений без практической пользы.
   - **`CONFIRMED` и `AUTHORIZED` — оба «оплата прошла»**, без оговорок «проверить, подтвердится ли».
   - **Не прошло в банке (FAILED, отменённые попытки) — не упоминать.** Флагать только обратное:
     в банке прошло, а в Озме «Ожидается оплата» (пункт 0).
   - **«Ожидается оплата» в Озме — не оплата и не дубль.** Брошенные попытки упоминать только
     от **10 000 ₽**. Исключение — рассрочка (`CloudPayments - Рассрочка`): там «Ожидается оплата»
     = ждём одобрения банка, это не мусор. Реальный дубль = два и более `CONFIRMED` на одну заявку.
   - **Нулевые строки (0 ₽, оплата сертификатом) в worklist не выносить.** Если по каналу остались
     только нулевые — «реальных платежей нет».
   - **Служебные карточки не упоминать и не флагать:** Фатт Бэлла (контакт 42) и
     «Шварцер Валентина Михайловна (офис)» (контакт 19379 — с неё идут тестовые оплаты; признак
     теста — бессмысленное `tks_customer_name` при почте `valmat5638@gmail.com`).
   - Сумма ниже цены продукта — сначала проверить `tks_promocode`.

   Сырой Markdown reconcile.py не показывай, если пользователь явно не попросил
   «покажи сырой отчёт».

5b. **Перепроверить предыдущий день** (всегда, без отдельной просьбы). Рассрочки
   подтверждаются вечером и догружаются в Озму позже, поэтому вчерашняя картина
   могла измениться. Прогнать для `<date−1>` шаги 3–4 заново (fetch перезапишет
   `/tmp/reconcile_<date−1>/`) и запустить сокращённый отчёт:
   ```bash
   python3 ~/.claude/skills/check-transactions/scripts/reconcile.py /tmp/reconcile_<date-1>/ --brief
   ```
   `--brief` печатает только шапку, зависшие оплаты, платежи в процессе и «Что делать»
   (и пишет `report_brief.md`). В отчёт пользователю добавить строку вида
   «Перепроверка <ДД.ММ>: зависших N, ждут подтверждения M» — а если появились
   новые 🔴, вынести их в пункт 0 вместе с сегодняшними.

6. **Follow-up**: user asks for details — use `jq` or `Read` on the JSON files in `/tmp/reconcile_<date>/`.

7. **Apply certificate actions** — обязательная часть КАЖДОЙ сверки, в том числе планового
   автозапуска. `reconcile.py` wrote `/tmp/reconcile_<date>/cert_actions.json` and a
   `## 🎁 Сертификаты` report section.

   **Записывать сразу, без подтверждения** (идемпотентно — перед записью перечитать комментарий):
   - **`comments[]`** — «автоматическое использование сертификата». `mcp__ozma__funql_query` the
     current `comment` of `tx_id`. If it already contains the tag (case-insensitive) → skip.
     Otherwise `mcp__ozma__transaction` update `fin.transactions` id=`tx_id`,
     `comment = existing + ", " + tag` when existing is non-empty, else the tag alone.
     Скрипт уже отобрал правильные строки:
     - тег ставится на строку, где **счёт отправителя = Сертификат**, а не на нулевую/платёжную;
     - только если у неё есть **пара** — платёжная строка того же клиента и продукта с
       `used_certificate_amount` = сумме сертификата (сайт сам списал сертификат при оплате);
     - **одиночное погашение** (`single_usages[]` — получатель сам применил сертификат при записи)
       — это НЕ «авто», тег не ставить, даже если хочется.
   - **`gift_comments[]`** — покупка сертификата в подарок (`is_certificate`, `tks_is_gift`, оплачено).
     Тот же порядок записи, тег из `proposed_tag`:
     - `tks_paper_certificate = false` → «серт, не шлем, электронный»;
     - `tks_paper_certificate = true` → «СЕРТ подготовить бумажный и связаться с покупателем» —
       такие отдельно перечислить в отчёте (пункт 4): команде надо подготовить сертификат.

   **Только с подтверждением человека:**
   - **`fio_fixes[]`** («Кэррот»): `mcp__ozma__funql_query` `base.people` id=`person_id`. Only if
     the current name still matches /Кэррот/ → `mcp__ozma__transaction` update `base.people`
     id=`person_id` with `first_name` / `last_name` from the plan.
   - **`merges[]`** (confidence=high only; **confirm each one individually** — destructive):
     `mcp__ozma__run_action` `base/merge_two_contacts` with `{keep_id, dup_id}`
     (survivor = `keep_id`, the lower id; the other is soft-deleted).

   **Только в отчёт, никогда не записывать:** `possible_duplicates[]`, `fraud_flags[]`.
   Анти-фрод = сертификат погасил не покупатель; скрипт флагует только сертификаты **дороже
   1 500 ₽** — мелкие штатно передают друзьям.

   Report applied / skipped / failed counts.

8. **Карточки участников оплат** (по ходу сверки, сразу при появлении — не откладывать в конец):
   - **Кривое ФИО** — не-имя («От Любимых»), дательный падеж («Найденко Климу»), только имя без
     фамилии, перепутанные имя/фамилия. Исправить самому в `base.people` по почте/телефону/
     телеграму. Фамилию-догадку (выведена из почты или ника, не из документа) писать со знаком
     вопроса: `Ярмак?`, имя — обычное. Совсем без зацепок — не выдумывать, спросить. Список правок
     дать в отчёте: ссылка `https://ozma.gogol.school/views/crm/student_form?id=<id>`, было → стало,
     откуда догадка.
   - **Два имени в подарочной оплате** («Женя и Матвей», `tks_is_gift = true`) — это ОДИН
     плательщик (люди думают, что так напишут «от кого» на сертификате). Плательщик — тот, чья почта
     в `tks_email`. Вторую карточку и вторую заявку не заводить.
   - **Мусорные контакты в карточке** (`base.communication_ways`: `tag@mail.ru`, `cooo@mail.ru`,
     `vv@gg.com`, `test@…`, `+7 999 999 99 99`, `111…`) — удалить самому мягко (`is_deleted = true`).
     Правдоподобные адреса не трогать, регистрационный контакт — только если он явный мусор.
     Снимок в самой транзакции (`tks_email` / `tks_phone` / `tks_customer_name`) не править.
     В отчёте перечислить, что удалено.

9. **Маркер «+»** — обязательный последний шаг КАЖДОЙ сверки. Маркер один на всю таблицу:
   найти ВСЕ строки `fin.transactions` с «+» в комментарии и снять с каждой (если кроме «+» ничего
   нет — `comment = null`, иначе убрать «, +»). Затем поставить «+» на последнюю сверенную оплату
   (самая поздняя по `tks_date_time` из охваченных, включая сегодняшние): пустой комментарий → «+»,
   непустой → дописать «, +».

## What this skill does NOT do

- Writes to Ozma ONLY: certificate comments (step 7, automatically), card fixes
  (step 8, automatically), the «+» marker (step 9, automatically), and — after the
  user's confirmation — «Кэррот» ФИО fixes and duplicate merges. Everything else
  (level-1/2/3 discrepancies, anti-fraud flags, possible duplicates) is surfaced
  read-only and resolved by the user.
- Does NOT auto-fix non-certificate discrepancies — only surfaces them.
- Does NOT parse provider responses into context — keep raw JSON in files, use `jq` on demand.

## Level 3 — contact comparison

On orders present on both sides, reconcile.py compares provider contact
(email/phone/name/telegram; name/phone arrive from CP `JsonData` once the site
sends them) against the **union** of Ozma sources: transaction snapshot
(`tks_email`/`tks_phone`/`tks_customer_name`), contact card
(`base.people` first/last/patronymic) and all `communication_ways` of the
contact. A value is OK if found in any source; otherwise it's `mismatch` (Ozma
has a different value) or `missing_in_ozma` (Ozma has none). See the
`## Расхождения контактов (уровень 3)` report section.

## Files in this skill

- `scripts/fetch_payments.py` — async parallel fetch of all 5 OzmaBot endpoints
- `scripts/reconcile.py` — matching + markdown (levels 1–3)
- `scripts/test_contacts.py` — unit tests for level-3 contact comparison
- `references/endpoint-contracts.md` — full endpoint specs
- `references/reconciliation-rules.md` — matching logic + account_id mapping
- `references/status-normalization.md` — status maps
- `.env` — `OZMABOT_URL`, `RECONCILE_API_KEY` (must be created locally, gitignored)

## Setting up the skill

`install.sh` создаёт `.env` автоматически: подтягивает `RECONCILE_API_KEY` из
Notion (🔐 Токены MCP) и пишет `OZMABOT_URL` + `RECONCILE_API_KEY` в
`~/.claude/skills/check-transactions/.env`.

Если ставишь скилл вручную (без install.sh) и `.env` нет — скопируй из примера:
```bash
cp ~/.claude/skills/check-transactions/.env.example ~/.claude/skills/check-transactions/.env
# edit .env, set OZMABOT_URL and RECONCILE_API_KEY (same as in OzmaBot/reconcile/reconcile.env)
```
