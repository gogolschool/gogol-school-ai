# Нормализация статусов

**Provider native → unified `status`:**

| Provider | Native | → unified |
|---|---|---|
| CloudPayments | `Completed` | `succeeded` |
| CloudPayments | `Authorized` (холд) | `pending` |
| CloudPayments | `Declined` | `failed` |
| CloudPayments | `Cancelled` | `failed` |
| CloudPayments | refund-запись | `refunded` |
| Mixplat | `success` | `succeeded` |
| Mixplat | `pending` / `pending_draft` | `pending` |
| Mixplat | `declined` / `error` | `failed` |
| Mixplat | refund (отдельный XML) | `refunded` |
| Tinkoff Acquiring | `NEW` | `pending` |
| Tinkoff Acquiring | `AUTHORIZED` | **`succeeded`** ← подтверждено пользователем |
| Tinkoff Acquiring | `CONFIRMED` | `succeeded` |
| Tinkoff Acquiring | `SIGNED` (Долями) | **`succeeded`** ← подтверждено пользователем |
| Tinkoff Acquiring | `REVERSED` / `REFUNDED` / `PARTIAL_REFUNDED` | `refunded` |
| Tinkoff Acquiring | `REJECTED` / `CANCELED` | `failed` |
| Yandex Split | `CAPTURED` | `succeeded` |
| Yandex Split | `AUTHORIZED` | `pending` |
| Yandex Split | `NEW` / `PROCESSING` | `pending` |
| Yandex Split | `FAILED` | `failed` |
| Yandex Split | `REFUNDED` | `refunded` |
| Tinkoff Statement | `Transaction` | `completed` |
| Tinkoff Statement | `Authorization` | `authorization` |

**Озма `tks_state` → unified (для матчинга):**

| `tks_state` | → unified |
|---|---|
| `CONFIRMED`, `AUTHORIZED`, `SIGNED`, `COFIRMED` (опечатка) | `succeeded` |
| `REFUNDED`, `PARTIAL_REFUNDED` | `refunded` |
| `FAILED`, `REJECTED` | `failed` |
| `Ожидается оплата`, `ожидается оплата`, `Требуется оплата`, `требуется оплата` | `pending` |
| прочее / null | `unknown` |

Сравнение в скилле: `normalize(ozma.tks_state) == normalize(provider.status)` → match.

**Ключевое правило:** AUTHORIZED ≡ CONFIRMED ≡ SIGNED. Если Озма зафиксировала холд `AUTHORIZED`, а провайдер уже `CONFIRMED` — это **не расхождение**, это норма.

## Нормализация дат

- **CP:** `CreatedDateIso` отдаётся UTC — используем.
- **Tinkoff Acquiring:** `Time` поле ISO 8601 с TZ — приводим к UTC.
- **Tinkoff Statement:** ISO 8601 с TZ — приводим к UTC.
- **Mixplat:** `YYYY-MM-DD HH:MM:SS` без TZ, UTC+03:00 (MSK). Конвертация по образцу `mixplat_backfill_dryrun.py:22-27`.
- **Yandex Split:** `createdAt` ISO 8601 UTC — используем.

**Опасный момент в Озме:** `tks_date_time` хранится как **`string`** (не `datetime`), и в БД встречаются битые значения вроде `'2021-13-07'`. При фильтрации FunQL `tks_date_time::date >= '...'::date` падает на таких строках. Решение: дополнить фильтр предикатом, исключающим явно битые записи. Точная форма — на этапе имплементации (вариант: использовать `target_datetime` computed-field из 4.5).
