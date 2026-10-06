import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import b2c_paid_snapshot as bs  # noqa: E402


def test_operations_from_rows():
    rows = [{"id": 12170, "n": 23}, {"id": 12171, "n": 0}]
    assert bs.operations(rows) == [
        {"type": "update", "entity": {"schema": "crm", "name": "actions"}, "id": 12170,
         "entries": {"paid_requests_on_day": 23}},
        {"type": "update", "entity": {"schema": "crm", "name": "actions"}, "id": 12171,
         "entries": {"paid_requests_on_day": 0}},
    ]


def test_operations_skip_null_count():
    assert bs.operations([{"id": 1, "n": None}]) == []


def test_query_has_scope_and_date():
    q = bs.LESSONS_QUERY
    assert "paid_requests_on_day IS NULL" in q
    assert "'Интенсив СПБ'" in q and "'Курс'" in q
    assert "2026-08-31T21:00:00Z" in q
