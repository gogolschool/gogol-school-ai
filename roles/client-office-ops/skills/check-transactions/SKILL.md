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
     customer, tks_email, tks_phone,
     is_certificate, is_certificate_payment, used_certificate_amount, comment
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
   ```
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

   **Pending blacklist:** include pending rows in the query — reconcile.py needs to **see** them to blacklist their `tks_order_id` everywhere. If an order has any «Ожидается оплата» row, both sides ignore it entirely (no `only_in_provider`, no `only_in_ozma`).

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
   Stdout is the final Markdown report. Show it verbatim to the user.

6. **Follow-up**: user asks for details — use `jq` or `Read` on the JSON files in `/tmp/reconcile_<date>/`.

7. **Apply certificate actions** (ONLY on explicit user confirmation). `reconcile.py`
   wrote `/tmp/reconcile_<date>/cert_actions.json` and a `## 🎁 Сертификаты` report
   section. Show that section and ask: «применить? (N комментариев, M ФИО, K слияний)».
   On «да», for each item re-check then write via the OzmaDB MCP:
   - **`comments[]`**: `mcp__ozma__funql_query` the current `comment` of `tx_id`. If it
     already contains «автоматическое использование сертификата» (case-insensitive)
     → skip. Otherwise `mcp__ozma__transaction` update `fin.transactions` id=`tx_id`,
     `comment = existing + ", " + tag` when existing is non-empty, else the tag alone.
   - **`fio_fixes[]`**: `mcp__ozma__funql_query` `base.people` id=`person_id`. Only if the
     current name still matches /Кэррот/ → `mcp__ozma__transaction` update `base.people`
     id=`person_id` with `first_name` / `last_name` from the plan.
   - **`merges[]`** (confidence=high only; **confirm each one individually** — destructive):
     `mcp__ozma__run_action` `base/merge_two_contacts` with `{keep_id, dup_id}`
     (survivor = `keep_id`, the lower id; the other is soft-deleted).
   - **`possible_duplicates[]`** and **`fraud_flags[]`**: report-only, never auto-written.
   Report applied / skipped / failed counts.

## What this skill does NOT do

- Writes to Ozma ONLY for confirmed certificate actions (step 7): auto-usage
  comments, «Кэррот» ФИО fixes, and high-confidence duplicate merges. Everything
  else (level-1/2/3 discrepancies, anti-fraud flags, possible duplicates) is
  surfaced read-only and resolved by the user.
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
