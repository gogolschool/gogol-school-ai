# Reconciliation rules

## Маппинг канал → `fin.accounts.id`

Используется и endpoint'ом (для каждой `AcquiringTx` проставить `expected_ozma_account_id`), и скиллом (фильтр в FunQL).

| Канал | site/variant | account_id | tks_terminal_key (для справки) |
|---|---|---|---|
| Tinkoff Acquiring | `ooo` | **6** | `1596705727432` |
| Tinkoff Acquiring | `ip` | **1570** | `1610371440398` |
| CloudPayments | `cards` | **26728** | `GSCloudCard` |
| CloudPayments | `installment` | **27017** | `GSCloudInstallment` |
| CloudPayments | `dolyame` | **27018** | `GSCloudDolyame` |
| Mixplat | — | **26729** | `MixPlat` |
| Yandex Split | — | **23719** | `GSYaSplit` |

Маппинг живёт в `reconcile/schemas.py` как dict-константа `CHANNEL_TO_ACCOUNT`, и дублируется в `references/reconciliation-rules.md` для скилла.

## Правила матчинга (`reconcile.py`)

### Группировка по каналу через `expected_ozma_account_id`

Каждая `AcquiringTx` от endpoint'а уже несёт `expected_ozma_account_id`. Скрипт группирует:
- Провайдер-tx по `expected_ozma_account_id`.
- Озма-tx по `account_to` (для зачислений; для refund — по `account_from`).

Дальше матчинг идёт **только внутри одной группы** (один account_id). Это решает edge case «одинаковый InvoiceId на разных каналах»: они разделены группировкой.

### Уровень 1 — «Заказ в Озме ↔ платёж в эквайринге»

**Pending исключён.** Озма-строки в состоянии «Ожидается оплата» / «Требуется оплата» (любой регистр) в сверке не участвуют — это незакрытые заказы, по которым провайдер ещё не подтвердил оплату. Фильтр стоит и в FunQL-запросе, и в `reconcile.py` для безопасности.

**Ключ матчинга:** `(ozma.tks_order_id, normalized_status) ↔ (provider.merchant_payment_id, provider.status)`. Не просто `tks_order_id`, а **пара (id, статус)**.

**Зачем пара:** один `tks_order_id` в Озме может иметь **две строки** — одна на оплату (`tks_state=CONFIRMED/AUTHORIZED/SIGNED` → `succeeded`) и одна на возврат (`tks_state=REFUNDED/PARTIAL_REFUNDED` → `refunded`). Provider тоже возвращает refund как отдельную строку. Пары линкуются 1-к-1 по статусу:
- Provider `succeeded` ↔ Озма-строка `succeeded` для того же `tks_order_id`.
- Provider `refunded` ↔ Озма-строка `refunded` для того же `tks_order_id`.

**Сравнение:**
- Сумма: `round(ozma.amount * 100) == provider.amount_kopecks` (толерантность 0).
- Статус: пары совпадают по нормализованному статусу. Правила нормализации — `references/status-normalization.md`.

**Категории:**
- 🟢 **Match** — оба источника, та же `(id, статус)` пара, суммы сходятся.
- 🟡 **Status drift** — `tks_order_id` есть в обоих, но статусы не пересекаются (например, provider `succeeded`, в Озме только `refunded`).
- 🟡 **Amount drift** — пара `(id, статус)` совпала, но суммы расходятся.
- 🔴 **Only in Ozma** — пары `(id, статус)` нет ни в одном `(id, *)` провайдера.
- 🔴 **Only in provider** — пары нет ни в одном `(id, *)` Озмы.

**Дубль внутри пары** (>1 строка в Озме или у провайдера с одинаковым `(id, статус)`) — фиксируется как `only_in_provider` с пометкой `note: duplicate rows`.

### Уровень 2 — «Эквайринг ↔ зачисление на р/с»

Используется `tinkoff.json` (Tinkoff Business Statement) как источник истины.

Для каждого acquiring-канала (CP, Mixplat, Yandex Split, Tinkoff Acquiring):
1. Берём провайдер-tx за день со `status='succeeded'`.
2. Группируем по `payout_date` (где есть) или агрегатно за день (где нет).
3. Суммируем `payout_amount_kopecks` (где есть) или `amount_kopecks` (где нет, например Yandex Split).
4. Ищем в `tinkoff.json operations[]` строку с `direction='credit'`:
   - Для CP — `counterparty.inn` или `name` содержит CloudPayments-маркер (точное значение — open question 14.3).
   - Для Mixplat — `counterparty.name` содержит «Миксплат».
   - Для Yandex Split — `counterparty.name` содержит «Яндекс».
   - Для Tinkoff Acquiring — внутрибанковский перевод (источник = тот же Tinkoff), может либо приходить как single operation, либо вообще не отражаться отдельно. Open question 14.4.
5. Толерантность ±1 копейка.

**Если payout_date в Tinkoff отличается от ожидаемого:** не считаем расхождением, если в пределах ±3 рабочих дня (T+1 типично, но бывает дольше при выходных).

### Уровень 3 — Σ-сводка (всегда в шапке)

```markdown
| Канал                       | Кол-во | Σ платежей  | Σ payout  | В Tinkoff-выписке         |
|-----------------------------|--------|-------------|-----------|---------------------------|
| Tinkoff Acquiring           | 48     | 612 000 ₽   |     —     | (внутрибанк, см. detail)  |
|   ↳ ООО                     | 45     | 580 000 ₽   |           |                           |
|   ↳ ИП                      |  3     |  32 000 ₽   |           |                           |
| CloudPayments               | 37     | 525 400 ₽   | 509 638 ₽ | ✅ 509 638 ₽ от 7705814643 |
|   ↳ cards                   | 25     | 312 000 ₽   | 302 640 ₽ |                           |
|   ↳ installment             |  8     | 145 000 ₽   | 140 650 ₽ |                           |
|   ↳ dolyame                 |  4     |  68 400 ₽   |  66 348 ₽ |                           |
| Mixplat                     | 28     | 245 000 ₽   | 230 300 ₽ | ✅ 230 300 ₽ от «Миксплат» |
| Yandex Split                | 12     |  84 000 ₽   |     —     | ⚠️ ищи руками             |
| Озма (все каналы)           | 125    | 1 466 400 ₽ |     —     |                           |
```

### Толерантности

- **Уровень 1 (заказ ↔ платёж):** точное равенство в копейках. Расхождение даже на 1 копейку — 🟡.
- **Уровень 2 (Σ payout ↔ зачисление):** ±1 копейка (банковские округления).
- **Дата payout vs зачисление:** ±3 рабочих дня.

### Edge cases

| Случай | Поведение |
|---|---|
| Дубль в провайдере (2 CP-tx с одним `InvoiceId`, обе succeeded) | 🔴 «Duplicate provider tx», помечаем |
| Refund | Отдельная строка со `status=refunded`. В Озме либо `tks_state=REFUNDED`/`PARTIAL_REFUNDED`, либо вторая запись |
| CP `Authorized` (холд) | `status=pending`, не в `Σ payout`. Info-блок «N транзакций в холде» |
| Mixplat-реестр для сегодня `null` | 502 от endpoint, в отчёте: «Реестр за сегодня не сформирован» |
| Provider tx с `expected_ozma_account_id` не совпал ни с одной Озма-tx через `tks_order_id` | 🔴 «Only in provider» |
| Озма-tx с account_to в нашем списке, но провайдер не вернул | 🔴 «Only in Ozma» |
| `tks_date_time` битый (формат `2021-13-07`) в `fin.transactions` | Игнорируем такие строки, в отчёте: «N строк с битой датой пропущено» |
| Несколько `payment_type` для одной tks в Озме | Используем `account_to/from` как primary, `payment_type` для cross-check |
| Mixplat refund в реестре | merchant_payment_id отсутствует — не матчится в L1, появится как "only in provider"; Σ в L2 корректна |

### Блок «Что делать дальше»

```markdown
## Что делать
- Status drift в Озме (3 строки): запустить `mixplat_backfill_apply.py` — закроется автоматически.
- Only in CP cards (1): InvoiceId=99887, 250₽. Заказа в Озме нет. Найти клиента, создать заказ руками.
- 2 транзакции в CP-холде (status=pending) — не пойдут в выплату, пока не confirmed/cancelled.
- Yandex Split за день: 84 000 ₽ — найти в Tinkoff-выписке зачисление от «Яндекс» руками.
```
