# Контракты endpoints

Все шесть endpoints требуют `X-API-Key: <RECONCILE_API_KEY>` header (кроме `/health`).

**Если credentials провайдера отсутствуют в env** — endpoint возвращает `503 not_configured` с явным сообщением «`<provider>`: credentials missing in env, set X, Y». Это позволяет deploy без блокеров: пока Tinkoff паролей нет, Tinkoff endpoints возвращают 503, остальные работают.

## `GET /reconcile/cp`

**Query params:**
- `date` (required) — `YYYY-MM-DD` в MSK.
- `site` (optional) — `cards` / `installment` / `dolyame`. Без параметра — все три параллельно.
- `include_refunds` (optional, default `true`).

**Что делает:**
1. Читает `CP_SITE_{i}_*` из env (цикл до первой дырки). Имена сайтов фиксированы: `cards` / `installment` / `dolyame` (подтверждено пользователем).
2. Для каждого сайта (или одного, если `?site=`) — параллельно `POST https://api.cloudpayments.ru/payments/list` с Basic Auth и `{"Date": date, "TimeZone": "MSK"}`.
3. Если `include_refunds=true` — `POST /payments/list/refunds`.
4. Нормализует CP-формат в `AcquiringTx`: сумма ÷ 100 → копейки, `expected_ozma_account_id` берётся из 4.5 по `site`.

**Response 200:**

```json
{
  "provider": "cloudpayments",
  "date": "2026-05-20",
  "tz": "MSK",
  "fetched_at_utc": "2026-05-21T07:00:12Z",
  "sites": {
    "cards":       { "ok": true,  "count": 25, "took_ms": 1100, "expected_ozma_account_id": 26728 },
    "installment": { "ok": true,  "count":  8, "took_ms":  900, "expected_ozma_account_id": 27017 },
    "dolyame":     { "ok": true,  "count":  4, "took_ms":  920, "expected_ozma_account_id": 27018 }
  },
  "count": 37,
  "transactions": [
    {
      "provider": "cloudpayments",
      "site": "cards",
      "expected_ozma_account_id": 26728,
      "provider_transaction_id": "987654321",
      "merchant_payment_id": "12345",
      "datetime_utc": "2026-05-20T11:30:00Z",
      "amount_kopecks": 250000,
      "currency": "RUB",
      "status": "succeeded",
      "payout_date": "2026-05-21",
      "payout_amount_kopecks": 242500,
      "customer": {"name": "Иванов И.И.", "email": "...", "phone": "..."},
      "description": "Оплата продукта X",
      "raw": { /* полный CP-ответ */ }
    }
  ]
}
```

## `GET /reconcile/tinkoff-acquiring`

**Query params:**
- `date` (required) — `YYYY-MM-DD` в MSK.
- `terminal` (optional) — `ooo` / `ip`. Без параметра — оба.

**Что делает:**
1. Читает `TINKOFF_ACQUIRING_OOO_TERMINAL_KEY` + `TINKOFF_ACQUIRING_OOO_PASSWORD`, аналогично для IP. Если хотя бы одна пара отсутствует — для этого терминала вернётся `not_configured`.
2. **Tinkoff Acquiring не имеет публичного "list payments by date"** в стандартном API (только per-payment `GetState`). Нужно либо:
   - **Вариант A (preferred):** использовать `GetReceipt`/`GetTransactionHistory`-аналоги, если они доступны в новом personal cabinet API. На этапе имплементации уточнить через https://www.tbank.ru/kassa/dev/payments/.
   - **Вариант B (fallback):** на нашей стороне сразу после авторизации в эквайринге записывать `PaymentId` в Озму (в `account_from` / отдельное поле), и потом для сверки итерироваться по нашим PaymentId через `GetState`. По образцу Mixplat backfill.
3. Нормализует в `AcquiringTx` со статусной таблицей 4.3.

**Open question 14.7** — закрепить, как именно ходим: A или B. Если A не существует — B с обвязкой «лог PaymentId» (нужен новый код в OzmaBot core).

**Response 200:** та же схема, что у CP, с полем `terminals` вместо `sites`:

```json
{
  "provider": "tinkoff_acquiring",
  "date": "2026-05-20",
  "fetched_at_utc": "2026-05-21T07:00:12Z",
  "terminals": {
    "ooo": { "ok": true, "count": 45, "expected_ozma_account_id": 6 },
    "ip":  { "ok": true, "count":  3, "expected_ozma_account_id": 1570 }
  },
  "count": 48,
  "transactions": [
    {
      "provider": "tinkoff_acquiring",
      "site": "ooo",
      "expected_ozma_account_id": 6,
      "provider_transaction_id": "PAYMENT_ID_FROM_TINKOFF",
      "merchant_payment_id": "OrderId_из_запроса",
      "datetime_utc": "2026-05-20T11:30:00Z",
      "amount_kopecks": 250000,
      "currency": "RUB",
      "status": "succeeded",
      "payout_date": "2026-05-21",
      "payout_amount_kopecks": null,
      "customer": {"email": "..."},
      "description": "...",
      "raw": { /* полный T-Acquiring ответ */ }
    }
  ]
}
```

## `GET /reconcile/tinkoff` (Business Statement)

**Query params:**
- `date` (required) — `YYYY-MM-DD` в MSK. На бэкенде → `from=YYYY-MM-DDT00:00:00+03:00`, `to=YYYY-MM-(DD+1)T00:00:00+03:00`.
- `direction` (optional, `credit` | `debit` | `all`, default `credit`).

**Что делает:**
1. `GET https://business.tbank.ru/openapi/api/v1/statement?accountNumber=...&from=...&to=...&withBalances=true&limit=1000`
2. Header `Authorization: Bearer <TINKOFF_TOKEN>`.
3. `nextCursor` → дополнительные запросы до полного исчерпания.
4. Фильтр по `direction`.
5. Rate limit 20 RPS, retry на 429 с `Retry-After`.

**Response 200:**

```json
{
  "provider": "tinkoff_statement",
  "date": "2026-05-20",
  "account_number": "40702810XXXXXXXXXXXX",
  "balances": { "opening_kopecks": 12345600, "closing_kopecks": 14567800 },
  "fetched_at_utc": "2026-05-21T07:00:12Z",
  "count": 18,
  "operations": [
    {
      "provider": "tinkoff_statement",
      "operation_id": "abc-123-def",
      "datetime_utc": "2026-05-20T08:15:00Z",
      "amount_kopecks": 242500,
      "currency": "RUB",
      "direction": "credit",
      "status": "completed",
      "counterparty": {
        "name": "ООО \"Клаудпейментс\"",
        "inn": "7705814643",
        "kpp": "...",
        "bik": "...",
        "account": "..."
      },
      "purpose": "Зачисление по реестру № 1234 от 20.05.2026",
      "raw": { /* полная operation */ }
    }
  ]
}
```

## `GET /reconcile/mixplat`

**Query params:**
- `date` (required) — `YYYY-MM-DD` в MSK.

**Что делает:**
1. `POST https://api.mixplat.com/get_register` с `{api_version:3, company_id:26695, period:<date>, signature:md5(...)}` (раздел 7.6 docs.mixplat.dev).
2. Получает `payment_register_download_url` + `refund_register_download_url`. Если `null` (реестр не готов) — polling 5 сек × 6 раз. После 30 сек — 502 `register_not_ready`.
3. Скачивает оба XML, парсит `xml.etree.ElementTree`.
4. Нормализует в `AcquiringTx`:
   - `payout_amount_kopecks ← amount_merchant`
   - `payout_date ← period` (день из запроса)
   - `site ← null`
   - `expected_ozma_account_id ← 26729`

**Response 200:**

```json
{
  "provider": "mixplat",
  "date": "2026-05-20",
  "fetched_at_utc": "2026-05-21T07:00:12Z",
  "register": {
    "payment_register_id": 12345,
    "refund_register_id":  6789
  },
  "expected_ozma_account_id": 26729,
  "count": 28,
  "transactions": [ /* AcquiringTx, см. 4.1 */ ]
}
```

## `GET /reconcile/split`

**Query params:**
- `date` (required) — `YYYY-MM-DD` в MSK. На бэкенде → `createdGte=...T00:00:00Z`, `createdLt=...T00:00:00Z` (имена параметров уточнить по актуальной доке Yandex Pay).

**Что делает:**
1. `GET https://pay.yandex.ru/api/merchant/v1/orders?createdGte=...&createdLt=...&paymentMethod=SPLIT&limit=100`
2. Header `Authorization: Api-Key <YANDEX_PAY_API_KEY>`, `X-Request-Id: <uuid>`.
3. Пагинация по `cursor` если есть (уточнить в актуальной доке).
4. Нормализует в `AcquiringTx`:
   - `expected_ozma_account_id ← 23719`
   - `payout_date ← null` (нет в публичной доке — будет open question при first зачислении)
   - `payout_amount_kopecks ← null` (то же)

**Response 200:** та же схема, что у Mixplat:

```json
{
  "provider": "yandex_split",
  "date": "2026-05-20",
  "fetched_at_utc": "2026-05-21T07:00:12Z",
  "expected_ozma_account_id": 23719,
  "count": 12,
  "transactions": [ /* AcquiringTx */ ]
}
```

## `GET /reconcile/health`

**Без auth-header.** Дёшевый health-check.

**Response 200:**

```json
{
  "ok": true,
  "providers": {
    "cp":                 { "ok": true,  "sites_configured": ["cards","installment","dolyame"] },
    "tinkoff_acquiring":  { "ok": false, "configured_terminals": [] },
    "tinkoff_statement":  { "ok": false, "reason": "TINKOFF_TOKEN missing" },
    "mixplat":            { "ok": true,  "company_id": 26695 },
    "yandex_split":       { "ok": false, "reason": "YANDEX_PAY_API_KEY missing" }
  },
  "env_status": "partial",   // "ok" | "partial" | "missing_critical"
  "time_msk": "2026-05-21T10:00:12+03:00"
}
```

`env_status="partial"` означает: запустить можно, часть endpoints вернёт `503 not_configured`. Это нормально на этапе постепенного выпуска токенов.

## Унифицированные ошибки

```json
{ "error": "<kind>", "detail": "<...>", "provider_status": <opt>, "attempt_count": <opt> }
```

| HTTP | `error` | Когда |
|---|---|---|
| 400 | `bad_request` | невалидный `date` или другие params |
| 401 | `unauthorized` | нет/неверный `X-API-Key` |
| 502 | `upstream_error` | провайдер ответил не 2xx после всех retry |
| 502 | `register_not_ready` | Mixplat: `download_url` оставался null дольше 30 сек |
| 503 | `not_configured` | credentials отсутствуют в env |
| 500 | `internal_error` | unhandled exception |
