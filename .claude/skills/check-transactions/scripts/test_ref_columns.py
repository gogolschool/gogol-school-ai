#!/usr/bin/env python3
"""Reference columns (account_to/account_from) may arrive as {"id": ..} dicts.

The OzmaDB MCP serializes reference columns as {"id": .., "pun": ..}, so
ozma.json saved straight from the SKILL.md query has dicts where the older
fixtures had bare ints. Both forms must work everywhere — a raw `row.get(...)`
on such a column blows up with "unhashable type: 'dict'" the moment the value
is used as a set element or inside a tuple key.
"""
import unittest

from reconcile import _ozma_row_role, compare_contacts


def _ref(v, as_dict):
    return {"id": v, "pun": "..."} if as_dict else v


def _ozma_row(as_dict, acc_to=26728, acc_from=999):
    return {"id": 1, "tks_order_id": "100", "tks_state": "CONFIRMED",
            "account_to": _ref(acc_to, as_dict), "account_from": _ref(acc_from, as_dict),
            "customer": _ref(50, as_dict), "amount": 1500.0,
            "tks_email": "old@mail.ru", "tks_phone": "79260000000",
            "tks_customer_name": "Петров Иван"}


def _provider_tx(email="old@mail.ru"):
    return {"expected_ozma_account_id": 26728, "merchant_payment_id": "100",
            "status": "succeeded", "amount_kopecks": 150000,
            "customer": {"email": email}}


class TestOzmaRowRole(unittest.TestCase):
    def test_capture_with_int_refs(self):
        self.assertEqual(_ozma_row_role(_ozma_row(False)), (26728, "capture"))

    def test_capture_with_dict_refs(self):
        self.assertEqual(_ozma_row_role(_ozma_row(True)), (26728, "capture"))

    def test_refund_direction_with_dict_refs(self):
        row = _ozma_row(True, acc_to=999, acc_from=26728)
        self.assertEqual(_ozma_row_role(row), (26728, "refund"))

    def test_untracked_account_returns_none(self):
        self.assertIsNone(_ozma_row_role(_ozma_row(True, acc_to=999, acc_from=888)))


class TestCompareContactsWithDictRefs(unittest.TestCase):
    def setUp(self):
        self.comparable = {(26728, "100")}
        self.people = {50: {"first_name": "Иван", "last_name": "Петров",
                            "patronymic": None}}
        self.comm = {50: [{"type": "Email", "data": "old@mail.ru"},
                          {"type": "Телефон", "data": "+79260000000"}]}

    def _run(self, as_dict, provider_txs):
        return compare_contacts(self.comparable, provider_txs, [_ozma_row(as_dict)],
                                self.people, self.comm)

    def test_dict_refs_do_not_crash_and_match(self):
        self.assertEqual(self._run(True, [_provider_tx()]).get(26728, []), [])

    def test_dict_refs_still_detect_mismatch(self):
        d = self._run(True, [_provider_tx(email="new@mail.ru")])[26728]
        self.assertEqual((d[0]["field"], d[0]["category"]), ("email", "mismatch"))

    def test_int_and_dict_refs_agree(self):
        tx = [_provider_tx(email="new@mail.ru")]
        self.assertEqual(self._run(False, tx), self._run(True, tx))


if __name__ == "__main__":
    unittest.main()
