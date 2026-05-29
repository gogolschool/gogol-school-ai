#!/usr/bin/env python3
"""Tests for level-3 contact comparison in reconcile.py."""
import unittest

from reconcile import (
    _norm_phone,
    _norm_email,
    _norm_telegram,
    _name_tokens,
    provider_contact,
    compare_contacts,
)


class TestNormalization(unittest.TestCase):
    def test_phone_formats_collapse_to_last10(self):
        for v in ("+79262474166", "79262474166", "89262474166",
                  "8 (926) 247-41-66", "7-926-247-41-66"):
            self.assertEqual(_norm_phone(v), "9262474166")

    def test_phone_empty(self):
        self.assertIsNone(_norm_phone(""))
        self.assertIsNone(_norm_phone(None))
        self.assertIsNone(_norm_phone("   "))

    def test_email_lower_strip(self):
        self.assertEqual(_norm_email("  Ivan@Mail.RU "), "ivan@mail.ru")
        self.assertIsNone(_norm_email(""))
        self.assertIsNone(_norm_email(None))

    def test_telegram_strip_at_lower(self):
        self.assertEqual(_norm_telegram("@Koptehe"), "koptehe")
        self.assertEqual(_norm_telegram("koptehe"), "koptehe")
        self.assertIsNone(_norm_telegram(None))

    def test_name_tokens_order_insensitive(self):
        self.assertEqual(_name_tokens("Иван Петров"), _name_tokens("Петров Иван"))
        self.assertEqual(_name_tokens("  Иван   ПЕТРОВ! "), {"иван", "петров"})
        self.assertEqual(_name_tokens(""), set())
        self.assertEqual(_name_tokens(None), set())


class TestProviderContact(unittest.TestCase):
    def test_email_from_customer(self):
        tx = {"customer": {"email": "a@b.ru", "name": None, "phone": None}, "raw": {}}
        self.assertEqual(provider_contact(tx)["email"], "a@b.ru")

    def test_jsondata_is_parsed_string(self):
        tx = {
            "customer": {"email": None, "name": None, "phone": None},
            "raw": {
                "Email": "x@y.ru",
                "JsonData": '{"name": "Иван Петров", "phone": "+79991234567", '
                            '"tg": "@ivanp", "Items": [{"Name": "OU11"}]}',
            },
        }
        c = provider_contact(tx)
        self.assertEqual(c["name"], "Иван Петров")
        self.assertEqual(c["phone"], "+79991234567")
        self.assertEqual(c["telegram"], "@ivanp")
        self.assertEqual(c["email"], "x@y.ru")

    def test_jsondata_firstname_lastname(self):
        tx = {"customer": {}, "raw": {
            "JsonData": '{"firstName": "Иван", "lastName": "Петров"}'}}
        self.assertEqual(provider_contact(tx)["name"], "Иван Петров")

    def test_items_name_not_used_as_customer_name(self):
        tx = {"customer": {"name": None}, "raw": {
            "JsonData": '{"Items":[{"Name":"OU11-202606"}]}'}}
        self.assertIsNone(provider_contact(tx)["name"])

    def test_broken_jsondata_does_not_crash(self):
        tx = {"customer": {"email": "a@b.ru"}, "raw": {"JsonData": "not json"}}
        self.assertEqual(provider_contact(tx)["email"], "a@b.ru")
        self.assertIsNone(provider_contact(tx)["name"])


def _tx(acc, oid, **raw_or_cust):
    return {
        "expected_ozma_account_id": acc,
        "merchant_payment_id": str(oid),
        "customer": raw_or_cust.get("customer", {}),
        "raw": raw_or_cust.get("raw", {}),
    }


class TestCompareContacts(unittest.TestCase):
    def setUp(self):
        # one matched order 100 on account 26728, customer id 50
        self.comparable = {(26728, "100")}
        self.ozma_txs = [{"tks_order_id": "100", "account_to": 26728, "customer": 50,
                          "tks_email": "old@mail.ru", "tks_phone": "79260000000",
                          "tks_customer_name": "Петров Иван"}]
        self.people = {50: {"first_name": "Иван", "last_name": "Петров",
                            "patronymic": None}}
        self.comm = {50: [
            {"type": "Email", "data": "old@mail.ru"},
            {"type": "Телефон", "data": "+79260000000"},
            {"type": "Telegram", "data": "@ivanp"},
        ]}

    def _run(self, provider_txs):
        return compare_contacts(self.comparable, provider_txs, self.ozma_txs,
                                self.people, self.comm)

    def test_all_match_no_discrepancy(self):
        tx = _tx(26728, 100, customer={"email": "old@mail.ru",
                                        "phone": "89260000000", "name": "Иван Петров"})
        res = self._run([tx])
        self.assertEqual(res.get(26728, []), [])

    def test_email_mismatch(self):
        tx = _tx(26728, 100, customer={"email": "new@mail.ru"})
        d = self._run([tx])[26728]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["field"], "email")
        self.assertEqual(d[0]["category"], "mismatch")

    def test_phone_missing_in_ozma(self):
        # ozma row/contact without any phone
        self.ozma_txs = [{"tks_order_id": "100", "account_to": 26728, "customer": 51,
                          "tks_email": None, "tks_phone": None, "tks_customer_name": None}]
        self.people = {51: {}}
        self.comm = {51: [{"type": "Email", "data": "z@z.ru"}]}
        tx = _tx(26728, 100, customer={"phone": "+79991112233"})
        d = self._run([tx])[26728]
        self.assertEqual(d[0]["field"], "phone")
        self.assertEqual(d[0]["category"], "missing_in_ozma")

    def test_name_token_subset_matches(self):
        # provider sends only first name; ozma has full -> subset match -> ok
        tx = _tx(26728, 100, raw={"JsonData": '{"name":"Иван"}'})
        self.assertEqual(self._run([tx]).get(26728, []), [])

    def test_order_not_comparable_skipped(self):
        tx = _tx(26728, 999, customer={"email": "x@x.ru"})
        self.assertEqual(self._run([tx]).get(26728, []), [])

    def test_empty_provider_field_skipped(self):
        tx = _tx(26728, 100, customer={"email": None, "phone": None, "name": None})
        self.assertEqual(self._run([tx]).get(26728, []), [])


if __name__ == "__main__":
    unittest.main()
