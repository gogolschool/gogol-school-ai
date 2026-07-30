#!/usr/bin/env python3
"""Tests for day-state awareness: закрытый день, платежи в процессе у провайдера,
свежие подтверждения и каналы, которые не выгрузились.

Мотивация: рассрочки подтверждаются вечером (у CP видно AuthDate/ConfirmDate на
несколько часов позже CreatedDate), поэтому сверка незакрытого дня даёт ложную
картину, а «оплачено у провайдера, в Озме pending» может быть просто гонкой с
вебхуком.
"""
import datetime as dt
import unittest

from reconcile import (
    day_closed_at_utc,
    is_day_closed,
    provider_confirmed_at,
    unavailable_accounts,
    match_level_1,
    render_markdown,
)
from test_pending import ozma_row, prov_tx, CP_CARDS

CP_INSTALLMENT = 27017
TINKOFF_OOO = 6
UTC = dt.timezone.utc


def cp_tx(order_id, status, amount, *, confirm_epoch_ms=None, site="cards",
          acc=CP_CARDS):
    tx = prov_tx(order_id, status, amount, site=site)
    tx["expected_ozma_account_id"] = acc
    tx["provider"] = "cloudpayments"
    tx["datetime_utc"] = "2026-07-29T15:26:55"  # CP отдаёт МСК без смещения
    if confirm_epoch_ms is not None:
        tx["raw"] = {"ConfirmDate": f"/Date({confirm_epoch_ms})/"}
    return tx


class TestDayBoundary(unittest.TestCase):
    def test_msk_day_closes_at_21_utc(self):
        self.assertEqual(day_closed_at_utc("2026-07-29"),
                         dt.datetime(2026, 7, 29, 21, 0, tzinfo=UTC))

    def test_fetch_next_morning_is_closed(self):
        self.assertIs(is_day_closed("2026-07-29", "2026-07-30T10:54:56Z"), True)

    def test_fetch_same_evening_is_not_closed(self):
        # 29.07 18:29 МСК = 15:29 UTC — так сверяла Светлана
        self.assertIs(is_day_closed("2026-07-29", "2026-07-29T15:29:00Z"), False)

    def test_exactly_at_midnight_msk_counts_as_closed(self):
        self.assertIs(is_day_closed("2026-07-29", "2026-07-29T21:00:00Z"), True)

    def test_unknown_fetch_time(self):
        self.assertIsNone(is_day_closed("2026-07-29", None))
        self.assertIsNone(is_day_closed("2026-07-29", "мусор"))


class TestProviderConfirmTime(unittest.TestCase):
    """Epoch-поля CP — единственный однозначный источник времени."""

    def test_cp_confirm_date_epoch(self):
        # /Date(1785343192765)/ = 2026-07-29 16:39:52 UTC (19:39 МСК)
        tx = cp_tx("41823", "succeeded", 89000, confirm_epoch_ms=1785343192765)
        self.assertEqual(provider_confirmed_at(tx),
                         dt.datetime(2026, 7, 29, 16, 39, 52, 765000, tzinfo=UTC))

    def test_cp_falls_back_to_auth_date(self):
        tx = cp_tx("41823", "succeeded", 89000)
        tx["raw"] = {"AuthDate": "/Date(1785342336910)/"}
        self.assertEqual(provider_confirmed_at(tx).hour, 16)

    def test_naive_datetime_utc_is_read_as_msk(self):
        tx = cp_tx("41819", "succeeded", 18000)  # 15:26:55 без смещения = МСК
        self.assertEqual(provider_confirmed_at(tx),
                         dt.datetime(2026, 7, 29, 12, 26, 55, tzinfo=UTC))

    def test_aware_datetime_utc_is_respected(self):
        tx = prov_tx("41834", "succeeded", 2000)
        tx["datetime_utc"] = "2026-07-29T19:22:33+00:00"
        self.assertEqual(provider_confirmed_at(tx),
                         dt.datetime(2026, 7, 29, 19, 22, 33, tzinfo=UTC))

    def test_no_time_at_all(self):
        tx = prov_tx("41819", "succeeded", 18000)
        self.assertIsNone(provider_confirmed_at(tx))


class TestFreshConfirmation(unittest.TestCase):
    """Подтверждение за минуты до выгрузки — вебхук мог просто не успеть."""

    def stuck(self, confirm_ms, fetched):
        res = match_level_1([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                            [cp_tx("41819", "succeeded", 18000, confirm_epoch_ms=confirm_ms)],
                            fetched_at_utc=fetched)
        return res[CP_CARDS]["stuck_pending"][0]

    def test_confirmed_minutes_before_fetch_is_fresh(self):
        # подтверждено 16:39:52 UTC, выгрузка 16:50 UTC
        d = self.stuck(1785343192765, "2026-07-29T16:50:00Z")
        self.assertTrue(d["fresh"])

    def test_confirmed_hours_before_fetch_is_not_fresh(self):
        d = self.stuck(1785343192765, "2026-07-30T10:54:56Z")
        self.assertFalse(d["fresh"])

    def test_unknown_confirm_time_is_not_fresh(self):
        res = match_level_1([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                            [prov_tx("41819", "succeeded", 18000)],
                            fetched_at_utc="2026-07-30T10:54:56Z")
        self.assertFalse(res[CP_CARDS]["stuck_pending"][0]["fresh"])

    def test_without_fetch_time_nothing_is_fresh(self):
        res = match_level_1([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                            [cp_tx("41819", "succeeded", 18000, confirm_epoch_ms=1785343192765)])
        self.assertFalse(res[CP_CARDS]["stuck_pending"][0]["fresh"])


class TestProviderInFlight(unittest.TestCase):
    """Рассрочка в процессе: у провайдера pending/холд, в Озме «Ожидается оплата»."""

    def setUp(self):
        self.res = match_level_1(
            [ozma_row("41823", "Ожидается оплата", 89000, id=39195)],
            [cp_tx("41823", "pending", 89000, site="installment", acc=CP_CARDS)])[CP_CARDS]

    def test_listed_as_in_flight(self):
        self.assertEqual(len(self.res["provider_pending"]), 1)
        d = self.res["provider_pending"][0]
        self.assertEqual(d["key"], "41823")
        self.assertEqual(d["amount"], 8900000)
        self.assertTrue(d["ozma_pending"])

    def test_not_an_abandoned_attempt(self):
        self.assertEqual(self.res["pending_ignored"], [])
        self.assertEqual(self.res["stuck_pending"], [])
        self.assertEqual(self.res["only_in_provider"], [])
        self.assertEqual(self.res["only_in_ozma"], [])

    def test_in_flight_without_ozma_row(self):
        res = match_level_1([], [cp_tx("41823", "pending", 89000)])[CP_CARDS]
        self.assertEqual(len(res["provider_pending"]), 1)
        self.assertFalse(res["provider_pending"][0]["ozma_pending"])

    def test_failed_provider_row_is_not_in_flight(self):
        res = match_level_1([ozma_row("41822", "Ожидается оплата", 89000, id=39194)],
                            [prov_tx("41822", "failed", 89000)])[CP_CARDS]
        self.assertEqual(res["provider_pending"], [])
        self.assertEqual(res["pending_ignored"], ["41822"])


class TestUnavailableSources(unittest.TestCase):
    META = {"sources": {"cp": {"ok": True},
                        "tinkoff_acquiring": {"ok": False, "detail": "no terminal pairs"},
                        "tinkoff": {"ok": False, "detail": "TINKOFF_TOKEN missing"},
                        "mixplat": {"ok": True},
                        "split": {"ok": False, "detail": "session expired"}}}

    def test_accounts_of_failed_acquiring_sources(self):
        self.assertEqual(unavailable_accounts(self.META), {6, 1570, 23719})

    def test_statement_source_has_no_level1_accounts(self):
        meta = {"sources": {"tinkoff": {"ok": False}}}
        self.assertEqual(unavailable_accounts(meta), set())

    def test_all_ok(self):
        meta = {"sources": {"cp": {"ok": True}, "mixplat": {"ok": True}}}
        self.assertEqual(unavailable_accounts(meta), set())

    def test_missing_meta(self):
        self.assertEqual(unavailable_accounts({}), set())


class TestRenderDayState(unittest.TestCase):
    def render(self, ozma_rows, prov_rows, meta, **kw):
        level1 = match_level_1(ozma_rows, prov_rows,
                              fetched_at_utc=meta.get("finished_at_utc"))
        return render_markdown("2026-07-29", {"transactions": ozma_rows}, prov_rows,
                               level1, {}, {}, meta, **kw)

    def test_open_day_is_flagged(self):
        md = self.render([], [], {"finished_at_utc": "2026-07-29T15:29:00Z", "sources": {}})
        self.assertIn("День не закрыт", md)
        self.assertIn("рассрочк", md)

    def test_closed_day_has_no_warning(self):
        md = self.render([], [], {"finished_at_utc": "2026-07-30T10:54:56Z", "sources": {}})
        self.assertNotIn("День не закрыт", md)

    def test_fresh_stuck_is_yellow_not_red(self):
        md = self.render([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                         [cp_tx("41819", "succeeded", 18000, confirm_epoch_ms=1785343192765)],
                         {"finished_at_utc": "2026-07-29T16:50:00Z", "sources": {}})
        stuck_line = next(l for l in md.splitlines() if "`41819`" in l and "18000₽" in l)
        self.assertIn("\U0001f7e1", stuck_line)
        self.assertNotIn("\U0001f534", stuck_line)
        self.assertIn("вебхук мог не успеть", stuck_line)

    def test_stale_stuck_is_red(self):
        md = self.render([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                         [cp_tx("41819", "succeeded", 18000, confirm_epoch_ms=1785343192765)],
                         {"finished_at_utc": "2026-07-30T10:54:56Z", "sources": {}})
        stuck_line = next(l for l in md.splitlines() if "`41819`" in l and "18000₽" in l)
        self.assertIn("\U0001f534", stuck_line)

    def test_in_flight_section(self):
        md = self.render([ozma_row("41823", "Ожидается оплата", 89000, id=39195)],
                         [cp_tx("41823", "pending", 89000, site="installment")],
                         {"finished_at_utc": "2026-07-30T10:54:56Z", "sources": {}})
        self.assertIn("У провайдера в процессе", md)
        self.assertIn("`41823`", md)

    def test_unavailable_channel_is_not_reported_as_only_in_ozma(self):
        row = ozma_row("41828", "CONFIRMED", 7400, id=39200)
        row["account_to"] = TINKOFF_OOO
        meta = {"finished_at_utc": "2026-07-30T10:54:56Z",
                "sources": {"tinkoff_acquiring": {"ok": False, "detail": "no terminal pairs"}}}
        md = self.render([row], [], meta)
        self.assertNotIn("only in ozma `41828`", md)
        self.assertIn("не выгружен", md)
        self.assertIn("no terminal pairs", md)
        self.assertIn("1 стр", md)

    def test_pending_rows_of_unavailable_channel_are_counted_not_dropped(self):
        rows = []
        for oid, state in (("41828", "CONFIRMED"), ("41829", "Ожидается оплата")):
            row = ozma_row(oid, state, 7400, id=39200)
            row["account_to"] = TINKOFF_OOO
            rows.append(row)
        meta = {"finished_at_utc": "2026-07-30T10:54:56Z",
                "sources": {"tinkoff_acquiring": {"ok": False, "detail": "no terminal pairs"}}}
        md = self.render(rows, [], meta)
        self.assertIn("2 стр", md)
        # брошенной попыткой pending по невыгруженному каналу не считаем
        self.assertNotIn("Брошенные попытки", md)

    def test_brief_mode_keeps_only_actionable_sections(self):
        md = self.render([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                         [prov_tx("41819", "succeeded", 18000)],
                         {"finished_at_utc": "2026-07-30T10:54:56Z", "sources": {}},
                         brief=True)
        self.assertIn("Оплачено у провайдера", md)
        self.assertIn("У провайдера в процессе", md)
        self.assertIn("`41819`", md)
        self.assertNotIn("Расхождения уровня 2", md)
        self.assertNotIn("Расхождения контактов", md)


if __name__ == "__main__":
    unittest.main()
