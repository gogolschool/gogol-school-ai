#!/usr/bin/env python3
"""Tests for pending-order handling in level-1 matching.

Regression guard for the "зависший pending" hole: an order paid at the provider
whose Ozma row is still «Ожидается оплата» (lost webhook) used to be blacklisted
on both sides and never surfaced anywhere in the report.
"""
import unittest

from reconcile import match_level_1, render_markdown

CP_CARDS = 26728


def ozma_row(order_id, state, amount, *, id=1, role="capture", other=29046):
    """Ozma fin.transactions row touching the CP cards acquiring account."""
    row = {
        "id": id,
        "tks_order_id": order_id,
        "amount": float(amount),
        "tks_amount": str(int(amount * 100)),
        "tks_state": state,
        "tks_date_time": "2026-07-29 11:22:21.338766+00:00",
        "tks_customer_name": "Тест Тестов",
    }
    if role == "capture":
        row["account_to"], row["account_from"] = CP_CARDS, other
    else:
        row["account_to"], row["account_from"] = other, CP_CARDS
    return row


def prov_tx(order_id, status, amount, site="cards"):
    return {
        "merchant_payment_id": order_id,
        "status": status,
        "amount_kopecks": int(amount * 100),
        "expected_ozma_account_id": CP_CARDS,
        "site": site,
    }


class TestStuckPending(unittest.TestCase):
    """Provider confirmed the payment, Ozma row is still pending → must surface."""

    def setUp(self):
        self.ozma = [ozma_row("41819", "Ожидается оплата", 18000, id=39191)]
        self.prov = [prov_tx("41819", "succeeded", 18000)]
        self.res = match_level_1(self.ozma, self.prov)[CP_CARDS]

    def test_reported_as_stuck_pending(self):
        self.assertEqual(len(self.res["stuck_pending"]), 1)
        d = self.res["stuck_pending"][0]
        self.assertEqual(d["key"], "41819")
        self.assertEqual(d["p_status"], "succeeded")
        self.assertEqual(d["p_amount"], 1800000)
        self.assertEqual(d["o_amount"], 1800000)
        self.assertEqual(d["ozma_tx_id"], 39191)
        self.assertEqual(d["tks_state"], "Ожидается оплата")

    def test_not_double_counted_elsewhere(self):
        self.assertEqual(self.res["only_in_provider"], [])
        self.assertEqual(self.res["only_in_ozma"], [])
        self.assertEqual(self.res["match"], [])
        self.assertEqual(self.res["pending_ignored"], [])

    def test_amount_mismatch_is_visible(self):
        res = match_level_1([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                            [prov_tx("41819", "succeeded", 17000)])[CP_CARDS]
        d = res["stuck_pending"][0]
        self.assertEqual((d["p_amount"], d["o_amount"]), (1700000, 1800000))

    def test_refund_against_pending_row_also_surfaces(self):
        res = match_level_1([ozma_row("41819", "Требуется оплата", 18000, id=39191)],
                            [prov_tx("41819", "refunded", 18000)])[CP_CARDS]
        self.assertEqual(len(res["stuck_pending"]), 1)
        self.assertEqual(res["stuck_pending"][0]["p_status"], "refunded")


class TestAbandonedAttempts(unittest.TestCase):
    """Pending rows with no provider money behind them stay out of the noise."""

    def test_provider_failed_only(self):
        res = match_level_1([ozma_row("41822", "Ожидается оплата", 89000, id=39200)],
                            [prov_tx("41822", "failed", 89000)])[CP_CARDS]
        self.assertEqual(res["stuck_pending"], [])
        self.assertEqual(res["only_in_ozma"], [])
        self.assertEqual(res["only_in_provider"], [])
        self.assertEqual(res["pending_ignored"], ["41822"])

    def test_no_provider_row_at_all(self):
        res = match_level_1([ozma_row("41822", "Ожидается оплата", 89000, id=39200)],
                            [])[CP_CARDS]
        self.assertEqual(res["stuck_pending"], [])
        self.assertEqual(res["only_in_ozma"], [])
        self.assertEqual(res["pending_ignored"], ["41822"])

    def test_provider_still_pending(self):
        res = match_level_1([ozma_row("41822", "Ожидается оплата", 89000, id=39200)],
                            [prov_tx("41822", "pending", 89000)])[CP_CARDS]
        self.assertEqual(res["stuck_pending"], [])
        self.assertEqual(res["pending_ignored"], ["41822"])


class TestRetryAfterPendingAttempt(unittest.TestCase):
    """First attempt hung as pending, second one went through — plain match."""

    def setUp(self):
        self.res = match_level_1(
            [ozma_row("41830", "Ожидается оплата", 7400, id=39301),
             ozma_row("41830", "CONFIRMED", 7400, id=39302)],
            [prov_tx("41830", "succeeded", 7400)])[CP_CARDS]

    def test_matched_against_confirmed_row(self):
        self.assertEqual(len(self.res["match"]), 1)
        self.assertEqual(self.res["match"][0]["key"], "41830")

    def test_pending_row_is_only_informational(self):
        self.assertEqual(self.res["stuck_pending"], [])
        self.assertEqual(self.res["pending_ignored"], ["41830"])
        self.assertEqual(self.res["only_in_provider"], [])
        self.assertEqual(self.res["only_in_ozma"], [])


class TestNonPendingBehaviourUnchanged(unittest.TestCase):
    """Regression guard: the pending rework must not touch the normal paths."""

    def test_plain_match(self):
        res = match_level_1([ozma_row("41814", "CONFIRMED", 1500, id=1)],
                            [prov_tx("41814", "succeeded", 1500)])[CP_CARDS]
        self.assertEqual(len(res["match"]), 1)
        self.assertEqual(res["stuck_pending"], [])
        self.assertEqual(res["pending_ignored"], [])

    def test_only_in_provider_without_pending_row(self):
        res = match_level_1([], [prov_tx("99887", "succeeded", 250)])[CP_CARDS]
        self.assertEqual(len(res["only_in_provider"]), 1)
        self.assertEqual(res["stuck_pending"], [])

    def test_only_in_ozma(self):
        res = match_level_1([ozma_row("41756", "CONFIRMED", 32000, id=2)], [])[CP_CARDS]
        self.assertEqual(len(res["only_in_ozma"]), 1)

    def test_refund_pair(self):
        res = match_level_1(
            [ozma_row("41800", "CONFIRMED", 5000, id=3, role="capture"),
             ozma_row("41800", "CONFIRMED", 5000, id=4, role="refund")],
            [prov_tx("41800", "succeeded", 5000), prov_tx("41800", "refunded", 5000)])[CP_CARDS]
        self.assertEqual(len(res["match"]), 2)
        self.assertEqual(res["status_drift"], [])

    def test_amount_drift(self):
        res = match_level_1([ozma_row("41815", "CONFIRMED", 1500, id=5)],
                            [prov_tx("41815", "succeeded", 1400)])[CP_CARDS]
        self.assertEqual(len(res["amount_drift"]), 1)

    def test_status_drift(self):
        res = match_level_1([ozma_row("41816", "CONFIRMED", 1260, id=6, role="refund")],
                            [prov_tx("41816", "succeeded", 1260)])[CP_CARDS]
        self.assertEqual(len(res["status_drift"]), 1)


class TestReferenceColumnsAsDicts(unittest.TestCase):
    """OzmaDB MCP serializes reference columns as {"id": .., "pun": ..}."""

    def test_dict_refs_still_resolve_to_an_account(self):
        row = ozma_row("41814", "CONFIRMED", 1500, id=7)
        row["account_to"] = {"id": CP_CARDS, "pun": "Cloud Payments"}
        row["account_from"] = {"id": 29046, "pun": "Тест Тестов"}
        res = match_level_1([row], [prov_tx("41814", "succeeded", 1500)])[CP_CARDS]
        self.assertEqual(len(res["match"]), 1)

    def test_dict_refs_on_pending_row_surface_stuck_payment(self):
        row = ozma_row("41819", "Ожидается оплата", 18000, id=39191)
        row["account_to"] = {"id": CP_CARDS, "pun": "Cloud Payments"}
        res = match_level_1([row], [prov_tx("41819", "succeeded", 18000)])[CP_CARDS]
        self.assertEqual(len(res["stuck_pending"]), 1)


class TestReportWiring(unittest.TestCase):
    """The findings must actually reach the markdown, not just the dict."""

    def render(self, ozma_rows, prov_rows):
        level1 = match_level_1(ozma_rows, prov_rows)
        return render_markdown("2026-07-29", {"transactions": ozma_rows}, prov_rows,
                               level1, {}, {}, {"sources": {}})

    def test_stuck_order_is_in_the_report(self):
        md = self.render([ozma_row("41819", "Ожидается оплата", 18000, id=39191)],
                         [prov_tx("41819", "succeeded", 18000)])
        self.assertIn("Оплачено у провайдера, в Озме «Ожидается оплата»", md)
        self.assertIn("`41819`", md)
        self.assertIn("39191", md)
        self.assertIn("Зависшие оплаты (1)", md)  # «Что делать»

    def test_abandoned_attempts_are_listed_not_dropped(self):
        md = self.render([ozma_row("41822", "Ожидается оплата", 89000, id=39200)],
                         [prov_tx("41822", "failed", 89000)])
        self.assertIn("_Зависших оплат нет._", md)
        self.assertIn("Брошенные попытки", md)
        self.assertIn("`41822`", md)

    def test_clean_day_says_so(self):
        md = self.render([ozma_row("41814", "CONFIRMED", 1500, id=1)],
                         [prov_tx("41814", "succeeded", 1500)])
        self.assertIn("_Зависших оплат нет._", md)
        self.assertIn("_Все транзакции matched._", md)
        self.assertNotIn("Брошенные попытки", md)


if __name__ == "__main__":
    unittest.main()
