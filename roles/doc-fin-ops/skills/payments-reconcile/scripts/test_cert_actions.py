#!/usr/bin/env python3
"""Tests for certificate auto-actions in reconcile.py."""
import unittest

from reconcile import (
    is_cert_usage,
    _comment_has_tag,
    CERT_USAGE_TAG,
    build_comment_actions,
    build_fio_fixes,
)


class TestCertUsagePredicate(unittest.TestCase):
    def test_usage_row_detected(self):
        row = {"is_certificate_payment": True, "account_from_type": "Сертификат"}
        self.assertTrue(is_cert_usage(row))

    def test_string_bool_detected(self):
        row = {"is_certificate_payment": "t", "account_from_type": "Сертификат"}
        self.assertTrue(is_cert_usage(row))

    def test_not_usage_when_account_type_differs(self):
        row = {"is_certificate_payment": True, "account_from_type": "Банковский счет"}
        self.assertFalse(is_cert_usage(row))

    def test_not_usage_when_flag_false(self):
        row = {"is_certificate_payment": False, "account_from_type": "Сертификат"}
        self.assertFalse(is_cert_usage(row))

    def test_empty_row(self):
        self.assertFalse(is_cert_usage({}))


class TestCommentHasTag(unittest.TestCase):
    def test_present_case_insensitive(self):
        self.assertTrue(_comment_has_tag("серт, Автоматическое Использование Сертификата", CERT_USAGE_TAG))

    def test_absent(self):
        self.assertFalse(_comment_has_tag("не шлем, бартер", CERT_USAGE_TAG))

    def test_none_comment(self):
        self.assertFalse(_comment_has_tag(None, CERT_USAGE_TAG))


class TestBuildCommentActions(unittest.TestCase):
    def _usage(self, tx_id, comment=None):
        return {"id": tx_id, "is_certificate_payment": True,
                "account_from_type": "Сертификат",
                "account_from_name": "Подарок", "comment": comment}

    def test_proposes_for_empty_comment(self):
        from reconcile import build_comment_actions
        out = build_comment_actions([self._usage(38269, None)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tx_id"], 38269)
        self.assertEqual(out[0]["proposed_tag"], CERT_USAGE_TAG)
        self.assertIsNone(out[0]["current_comment"])

    def test_skips_when_tag_already_present(self):
        from reconcile import build_comment_actions
        out = build_comment_actions([self._usage(1, "автоматическое использование сертификата")])
        self.assertEqual(out, [])

    def test_skips_non_usage_rows(self):
        from reconcile import build_comment_actions
        row = {"id": 2, "is_certificate_payment": False, "account_from_type": "Банковский счет"}
        self.assertEqual(build_comment_actions([row]), [])


class TestBuildFioFixes(unittest.TestCase):
    def _karrot_row(self, oid="100", acc_to=26728, customer=77):
        return {"id": 500, "tks_order_id": oid, "account_to": acc_to,
                "account_from": 999, "customer": customer,
                "tks_customer_name": "Кэррот Пользователь",
                "account_from_name": "Кэррот Пользователь (Банковский счёт)"}

    def _prov(self, oid="100", acc=26728, first="Иван", last="Петров"):
        return {"expected_ozma_account_id": acc, "merchant_payment_id": oid,
                "customer": {}, "raw": {"JsonData":
                    '{"firstName": "%s", "lastName": "%s"}' % (first, last)}}

    def test_fix_built_from_provider_parts(self):
        from reconcile import build_fio_fixes
        out = build_fio_fixes([self._karrot_row()], [self._prov()])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["person_id"], 77)
        self.assertEqual(out[0]["new_first_name"], "Иван")
        self.assertEqual(out[0]["new_last_name"], "Петров")
        self.assertEqual(out[0]["old_name"], "Кэррот Пользователь")

    def test_no_fix_when_not_karrot(self):
        from reconcile import build_fio_fixes
        row = self._karrot_row()
        row["tks_customer_name"] = "Иван Петров"
        row["account_from_name"] = "Иван Петров (Банковский счёт)"
        self.assertEqual(build_fio_fixes([row], [self._prov()]), [])

    def test_no_fix_when_provider_lacks_split_name(self):
        from reconcile import build_fio_fixes
        prov = self._prov()
        prov["raw"] = {"JsonData": '{"name": "Иван Петров"}'}
        self.assertEqual(build_fio_fixes([self._karrot_row()], [prov]), [])

    def test_no_fix_when_no_matching_provider_tx(self):
        from reconcile import build_fio_fixes
        self.assertEqual(build_fio_fixes([self._karrot_row()], []), [])


if __name__ == "__main__":
    unittest.main()
