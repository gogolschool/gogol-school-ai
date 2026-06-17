---
name: check-transactions
description: Daily payments reconciliation for Gogol School — fetches transactions from CloudPayments / Tinkoff Acquiring / Tinkoff Statement / Mixplat / Yandex Split for a given day, queries fin.transactions in Ozma, and produces a Markdown discrepancy report. Use when user mentions "сверка платежей", "сверить за день/вчера/дату", "проверь транзакции", "reconcile payments", or asks to compare provider data with Ozma.
---

# check-transactions

## When to use

User says one of:
- «сверь за вчера», «сверь 20 мая», «reconcile yesterday», «сверка платежей за дату», «проверь транзакции за …»
- «покажи отчёт за вчера» (reads cached report)

## How

1. **Parse target date** from the user's message. Russian relative dates: "вчера" = today−1, "сегодня" = today. ISO: `YYYY-MM-DD`. Russian month names: «20 мая» = `2026-05-20` (year = current). If ambiguous, ASK before fetching.

2. **Determine intent:**
   - "сверь" / "проверь" / "reconcile" / `--force` → always fetch
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
     account_to, account_to=>name as account_to_name,
     account_from, payment_type, tks_customer_name, tks_description,
     customer, tks_email, tks_phone
   FROM "fin"."transactions"
   WHERE tks_date_time >= '<prev_date> 21:00:00+00:00'
     AND tks_date_time <  '<date> 21:00:00+00:00'
     AND (account_to IN (6, 1570, 26728, 27017, 27018, 26729, 23719)
          OR account_from IN (6, 1570, 26728, 27017, 27018, 26729, 23719))
     AND is_deleted = false
   ```
   Save returned rows to `/tmp/reconcile_<date>/ozma.json` as `{date, count, fetched_at_utc, transactions: [...]}`.
   (`customer`, `tks_email`, `tks_phone` feed the level-3 contact comparison; `customer` may serialize as int or `{id,...}` — reconcile.py handles both.)

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

   **4b. Contact data for level-3 comparison.** Collect the non-empty `customer`
   ids from the transactions above, then run two more `mcp__ozma__funql_query`
   calls and save both into `/tmp/reconcile_<date>/ozma_contacts.json` as
   `{people: [...], communication_ways: [...]}`:
   ```funql
   SELECT id, first_name, last_name, patronymic, nickname
   FROM "base"."people" WHERE id IN (<customer_ids>)
   ```
   ```funql
   SELECT contact, type, data
   FROM "base"."communication_ways"
   WHERE is_deleted = false AND contact IN (<customer_ids>)
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

## What this skill does NOT do

- Does NOT write to Ozma (read-only). Contact discrepancies (level 3) are only
  surfaced in the report with a recommendation — the user resolves them manually.
- Does NOT auto-fix discrepancies — only surfaces them.
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

If `.env` does not exist, copy from `.env.example` and fill in:
```bash
cp ~/.claude/skills/check-transactions/.env.example ~/.claude/skills/check-transactions/.env
# edit .env, set OZMABOT_URL and RECONCILE_API_KEY (same as in OzmaBot/reconcile/reconcile.env)
```
