#!/usr/bin/env python3
"""Tests for certificate auto-actions in reconcile.py."""
import unittest

from reconcile import (
    is_cert_usage,
    _comment_has_tag,
    CERT_USAGE_TAG,
    build_comment_actions,
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


if __name__ == "__main__":
    unittest.main()
