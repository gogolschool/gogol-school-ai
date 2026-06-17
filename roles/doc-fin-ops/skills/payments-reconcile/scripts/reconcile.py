#!/usr/bin/env python3
"""Reconcile all sources for one day. Reads /tmp/reconcile_<date>/ JSONs, emits markdown.

Usage: reconcile.py /tmp/reconcile_<date>/
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CHANNEL_TO_ACCOUNT = {
    ("tinkoff_acquiring", "ooo"):    6,
    ("tinkoff_acquiring", "ip"):     1570,
    ("cloudpayments",     "cards"):       26728,
    ("cloudpayments",     "installment"): 27017,
    ("cloudpayments",     "dolyame"):     27018,
    ("mixplat",           None):     26729,
    ("yandex_split",      None):     23719,
}
ACCOUNT_TO_LABEL = {v: f"{k[0]}/{k[1] or '_'}" for k, v in CHANNEL_TO_ACCOUNT.items()}
ACCOUNT_IDS = set(CHANNEL_TO_ACCOUNT.values())


OZMA_STATE_MAP = {
    "CONFIRMED": "succeeded", "AUTHORIZED": "succeeded", "SIGNED": "succeeded",
    "COFIRMED": "succeeded",
    "REFUNDED": "refunded", "PARTIAL_REFUNDED": "refunded",
    "FAILED": "failed", "REJECTED": "failed",
    "Ожидается оплата": "pending",
    "ожидается оплата": "pending",
    "Требуется оплата": "pending",
    "требуется оплата": "pending",
}


COUNTERPARTY_MARKERS = {
    "cloudpayments": [("inn", "7705814643"), ("name", "Клаудпейментс")],
    "mixplat":       [("name", "Миксплат"), ("name", "MixPlat")],
    "yandex_split":  [("name", "Яндекс")],
}


# --- Level 3: contact comparison (read-only) -------------------------------
#
# Compare provider contact (name/email/phone/telegram) against Ozma for orders
# present on BOTH sides. Ozma side is the UNION of: transaction snapshot
# (tks_*), contact card (base.people first/last/patronymic) and ALL of the
# contact's communication_ways of the matching type. A provider value is OK if
# it is found in ANY of those; otherwise it's a discrepancy (mismatch when Ozma
# has some value, missing_in_ozma when Ozma has none).


def _ref_id(v):
    """Reference column may serialize as int or as {"id": .., "pun": ..}."""
    if isinstance(v, dict):
        return v.get("id")
    return v


def _norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else None


def _norm_email(s):
    if not s:
        return None
    s = s.strip().lower()
    return s or None


def _norm_telegram(s):
    if not s:
        return None
    s = s.strip().lstrip("@").lower()
    return s or None


_NAME_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def _name_tokens(s):
    if not s:
        return set()
    return {t for t in _NAME_SPLIT.split(s.lower()) if t}


def _parse_jsondata(raw):
    j = raw.get("JsonData")
    if isinstance(j, dict):
        return j
    if isinstance(j, str):
        try:
            v = json.loads(j)
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _ci_get(d, *keys):
    """Case-insensitive top-level lookup; returns first non-empty value."""
    low = {k.lower(): v for k, v in d.items()} if isinstance(d, dict) else {}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def provider_contact(tx) -> dict:
    """Extract {name, email, phone, telegram} from a provider transaction.

    Name/phone/telegram come from CP JsonData (a JSON *string*) going forward;
    only top-level keys are read so receipt Items.Name is never mistaken for the
    buyer. Falls back to the normalized `customer` dict and raw Email/Phone.
    """
    cust = tx.get("customer") or {}
    raw = tx.get("raw") or {}
    jd = _parse_jsondata(raw)

    name = _ci_get(jd, "name")
    if not name:
        parts = [p for p in (_ci_get(jd, "firstName"), _ci_get(jd, "lastName")) if p]
        name = " ".join(parts) if parts else None
    if not name:
        name = cust.get("name")

    email = cust.get("email") or raw.get("Email") or _ci_get(jd, "email")
    phone = cust.get("phone") or raw.get("Phone") or _ci_get(jd, "phone")
    telegram = _ci_get(jd, "tg", "telegram") or cust.get("telegram")

    return {"name": name or None, "email": email or None,
            "phone": phone or None, "telegram": telegram or None}


def _ozma_contact_values(customer_id, people_by_id, comm_by_contact, snap) -> dict:
    emails, phones, tgs, name_toks = set(), set(), set(), set()

    e = _norm_email(snap.get("tks_email"))
    if e:
        emails.add(e)
    p = _norm_phone(snap.get("tks_phone"))
    if p:
        phones.add(p)
    name_toks |= _name_tokens(snap.get("tks_customer_name"))

    per = people_by_id.get(customer_id, {}) if customer_id is not None else {}
    for f in ("first_name", "last_name", "patronymic"):
        name_toks |= _name_tokens(per.get(f))

    for cw in (comm_by_contact.get(customer_id, []) if customer_id is not None else []):
        t, data = cw.get("type"), cw.get("data")
        if t == "Email":
            v = _norm_email(data)
            if v:
                emails.add(v)
        elif t == "Телефон":
            v = _norm_phone(data)
            if v:
                phones.add(v)
        elif t == "Telegram":
            v = _norm_telegram(data)
            if v:
                tgs.add(v)

    return {"emails": emails, "phones": phones, "telegrams": tgs,
            "name_tokens": name_toks}


def compare_contacts(comparable, provider_txs, ozma_txs,
                     people_by_id, comm_by_contact) -> dict:
    """Return {acc: [discrepancy, ...]} for orders in `comparable` (set of
    (account_id, tks_order_id)). Each discrepancy:
    {key, field, category in {mismatch, missing_in_ozma}, provider, ozma}.
    """
    snap_by = {}
    for row in ozma_txs:
        oid = str(row.get("tks_order_id") or "")
        if not oid:
            continue
        for acc in (row.get("account_to"), row.get("account_from")):
            if (acc, oid) in comparable and (acc, oid) not in snap_by:
                snap_by[(acc, oid)] = row

    results = defaultdict(list)
    for tx in provider_txs:
        acc = tx.get("expected_ozma_account_id")
        oid = str(tx.get("merchant_payment_id") or "")
        if (acc, oid) not in comparable:
            continue
        pc = provider_contact(tx)
        row = snap_by.get((acc, oid), {})
        cid = _ref_id(row.get("customer"))
        ov = _ozma_contact_values(cid, people_by_id, comm_by_contact, row)

        for field, pval, oset, normf in (
            ("email", pc["email"], ov["emails"], _norm_email),
            ("phone", pc["phone"], ov["phones"], _norm_phone),
            ("telegram", pc["telegram"], ov["telegrams"], _norm_telegram),
        ):
            if not pval:
                continue
            pv = normf(pval)
            if pv is None:
                continue
            if not oset:
                results[acc].append({"key": oid, "field": field,
                                     "category": "missing_in_ozma",
                                     "provider": pval, "ozma": []})
            elif pv not in oset:
                results[acc].append({"key": oid, "field": field,
                                     "category": "mismatch",
                                     "provider": pval, "ozma": sorted(oset)})

        if pc["name"]:
            pt = _name_tokens(pc["name"])
            ot = ov["name_tokens"]
            if pt:
                if not ot:
                    results[acc].append({"key": oid, "field": "name",
                                         "category": "missing_in_ozma",
                                         "provider": pc["name"], "ozma": []})
                elif not pt <= ot:
                    results[acc].append({"key": oid, "field": "name",
                                         "category": "mismatch",
                                         "provider": pc["name"],
                                         "ozma": sorted(ot)})

    return dict(results)


# --- Certificate auto-actions (Notion cases 1-4) --------------------------

CERT_USAGE_TAG = "автоматическое использование сертификата"
_KARROT_RE = re.compile(r"кэррот", re.IGNORECASE)


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("t", "true", "1", "yes")
    return bool(v)


def is_cert_usage(row) -> bool:
    """A certificate auto-usage row: money drawn from a certificate account."""
    return _as_bool(row.get("is_certificate_payment")) and \
        row.get("account_from_type") == "Сертификат"


def _comment_has_tag(comment, tag) -> bool:
    return tag.lower() in (comment or "").lower()


def build_comment_actions(ozma_txs) -> list:
    """Cert-usage rows whose comment lacks the tag → proposed comment writes."""
    out = []
    for row in ozma_txs:
        if not is_cert_usage(row):
            continue
        cur = row.get("comment")
        if _comment_has_tag(cur, CERT_USAGE_TAG):
            continue
        out.append({
            "tx_id": _ref_id(row.get("id")),
            "account_from_name": row.get("account_from_name"),
            "current_comment": cur,
            "proposed_tag": CERT_USAGE_TAG,
        })
    return out


def provider_name_parts(tx):
    """(first_name, last_name) from provider JsonData; (None, None) if not cleanly split."""
    jd = _parse_jsondata(tx.get("raw") or {})
    first = _ci_get(jd, "firstName")
    last = _ci_get(jd, "lastName")
    if first and last:
        return str(first).strip(), str(last).strip()
    return None, None


def build_fio_fixes(ozma_txs, provider_txs) -> list:
    """«Кэррот Пользователь» rows → proposed first/last-name fixes from provider data."""
    prov_by = {}
    for tx in provider_txs:
        mp = str(tx.get("merchant_payment_id") or "")
        if mp:
            prov_by[(tx.get("expected_ozma_account_id"), mp)] = tx
    out = []
    for row in ozma_txs:
        name = row.get("tks_customer_name") or row.get("account_from_name")
        if not name or not _KARROT_RE.search(name):
            continue
        role_info = _ozma_row_role(row)
        acc = role_info[0] if role_info else None
        tx = prov_by.get((acc, str(row.get("tks_order_id") or "")))
        if not tx:
            continue
        first, last = provider_name_parts(tx)
        if not (first and last):
            continue
        pid = _ref_id(row.get("customer"))
        if pid is None:
            continue
        out.append({
            "person_id": pid,
            "tx_id": _ref_id(row.get("id")),
            "old_name": row.get("tks_customer_name") or name,
            "new_first_name": first,
            "new_last_name": last,
            "source": "provider",
        })
    return out


def _contact_dedup_keys(pid, people_by_id, comm_by_contact):
    """(name_tokens, emails, phones) for a contact id, for duplicate detection."""
    name_toks = set()
    per = people_by_id.get(pid, {})
    for f in ("first_name", "last_name", "patronymic"):
        name_toks |= _name_tokens(per.get(f))
    emails, phones = set(), set()
    for cw in comm_by_contact.get(pid, []):
        t, data = cw.get("type"), cw.get("data")
        if t == "Email":
            v = _norm_email(data)
            if v:
                emails.add(v)
        elif t == "Телефон":
            v = _norm_phone(data)
            if v:
                phones.add(v)
    return name_toks, emails, phones


def build_dedup_and_fraud(ozma_txs, people_by_id, comm_by_contact):
    """For cert-usage rows where payer != certificate buyer:
      high confidence (shared phone/email) -> merge proposal,
      name-only match -> possible duplicate,
      otherwise -> anti-fraud flag.
    Returns (merges, possible_duplicates, fraud_flags)."""
    merges, possible_dups, fraud = [], [], []
    seen_pairs = set()
    for row in ozma_txs:
        if not is_cert_usage(row):
            continue
        payer = _ref_id(row.get("customer"))
        buyer = _ref_id(row.get("cert_buyer_id"))
        if payer is None or buyer is None or payer == buyer:
            continue
        pair = (min(payer, buyer), max(payer, buyer))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        pn, pe, pp = _contact_dedup_keys(payer, people_by_id, comm_by_contact)
        bn, be, bp = _contact_dedup_keys(buyer, people_by_id, comm_by_contact)
        signals = []
        if pe & be:
            signals.append("email")
        if pp & bp:
            signals.append("phone")
        if signals:
            merges.append({"keep_id": pair[0], "dup_id": pair[1],
                           "payer_id": payer, "buyer_id": buyer,
                           "match_signals": signals, "confidence": "high"})
        elif pn and bn and pn == bn:
            possible_dups.append({"payer_id": payer, "buyer_id": buyer,
                                  "match_signals": ["name"], "confidence": "medium"})
        else:
            fraud.append({"tx_id": _ref_id(row.get("id")),
                          "cert_account": _ref_id(row.get("account_from")),
                          "payer_id": payer,
                          "payer_name": " ".join(sorted(pn)) or str(payer),
                          "buyer_id": buyer,
                          "buyer_name": " ".join(sorted(bn)) or str(buyer)})
    return merges, possible_dups, fraud


def build_cert_actions(ozma_txs, provider_txs, people_by_id, comm_by_contact) -> dict:
    """Assemble the full certificate-actions plan."""
    merges, possible_dups, fraud = build_dedup_and_fraud(
        ozma_txs, people_by_id, comm_by_contact)
    return {
        "comments": build_comment_actions(ozma_txs),
        "fio_fixes": build_fio_fixes(ozma_txs, provider_txs),
        "merges": merges,
        "possible_duplicates": possible_dups,
        "fraud_flags": fraud,
    }


def render_cert_section(plan: dict) -> list:
    """Markdown lines for the 🎁 Сертификаты section."""
    c = plan["comments"]
    f = plan["fio_fixes"]
    m = plan["merges"]
    pd = plan["possible_duplicates"]
    fr = plan["fraud_flags"]
    lines = ["", "## 🎁 Сертификаты", ""]
    if not any((c, f, m, pd, fr)):
        lines.append("_Сертификатных действий нет._")
        return lines
    if c:
        lines.append(f"### Комментарии к авто-использованию ({len(c)})")
        for a in c:
            lines.append(f"- tx {a['tx_id']} ({a['account_from_name']}): + «{a['proposed_tag']}»")
    if f:
        lines.append(f"### Правка ФИО «Кэррот» ({len(f)})")
        for a in f:
            lines.append(f"- contact {a['person_id']} (tx {a['tx_id']}): "
                         f"«{a['old_name']}» → {a['new_last_name']} {a['new_first_name']}")
    if m:
        lines.append(f"### Слияния дубликатов — high confidence ({len(m)})")
        for a in m:
            lines.append(f"- keep {a['keep_id']} ← dup {a['dup_id']} "
                         f"(signals: {', '.join(a['match_signals'])})")
    if pd:
        lines.append(f"### Возможные дубликаты — проверить ({len(pd)})")
        for a in pd:
            lines.append(f"- {a['payer_id']} ↔ {a['buyer_id']} (совпало имя)")
    if fr:
        lines.append(f"### ⚠️ Анти-фрод — проверить вручную ({len(fr)})")
        for a in fr:
            lines.append(f"- tx {a['tx_id']}: плательщик {a['payer_name']} ({a['payer_id']}) "
                         f"≠ покупатель {a['buyer_name']} ({a['buyer_id']}), "
                         f"сертификат {a['cert_account']}")
    return lines


def normalize_ozma_state(s):
    if not s:
        return "unknown"
    return OZMA_STATE_MAP.get(s, "unknown")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def collect_provider_txs(dir_: Path) -> List[dict]:
    out = []
    for name in ("cp", "mixplat", "tinkoff_acquiring", "split"):
        p = dir_ / f"{name}.json"
        if not p.exists():
            continue
        body = load_json(p)
        if isinstance(body, dict) and body.get("transactions"):
            for tx in body["transactions"]:
                tx["_source_file"] = name
                out.append(tx)
    return out


def amount_kopecks_from_ozma(row: dict) -> int:
    a = row.get("amount")
    if a is not None:
        return round(float(a) * 100)
    tks = row.get("tks_amount")
    if tks:
        try:
            return int(tks)
        except (TypeError, ValueError):
            return 0
    return 0


def _ozma_row_role(row: dict) -> Optional[Tuple[int, str]]:
    """Determine (acquiring_account_id, role) for an Ozma row.

    `role` is "capture" when the acquiring account is on account_to (money in),
    "refund" when on account_from (money out — refund/chargeback to client).
    Returns None if the row doesn't touch any tracked acquiring account.
    """
    acc_to = row.get("account_to")
    if acc_to in ACCOUNT_IDS:
        return acc_to, "capture"
    acc_from = row.get("account_from")
    if acc_from in ACCOUNT_IDS:
        return acc_from, "refund"
    return None


# Provider unified status → Ozma row role we expect to find for that provider row.
_STATUS_TO_ROLE = {"succeeded": "capture", "refunded": "refund"}
_ROLE_TO_STATUS = {v: k for k, v in _STATUS_TO_ROLE.items()}


def match_level_1(ozma_txs: List[dict], provider_txs: List[dict]) -> Dict:
    """Match provider rows to Ozma rows per acquiring account.

    Rules:
    1. If an order's `tks_order_id` has at least one pending Ozma row
       (tks_state ∈ "Ожидается оплата"/...), the WHOLE order is blacklisted —
       it never appears as only_in_provider or only_in_ozma. The user said:
       "не потерял, просто игнорируй" — incomplete order, both sides skip.
    2. A refund creates a second Ozma row with the SAME `tks_order_id` but
       direction reversed (account_from = acquiring account). `tks_state` may
       still be CONFIRMED/AUTHORIZED on both rows — the role is determined by
       direction, NOT by state.
    3. Provider `succeeded` matches Ozma role=capture; provider `refunded`
       matches Ozma role=refund. Mismatch (provider succeeded but only refund
       row exists in Ozma, or vice-versa) → `status_drift`.
    """
    # Step 1: collect pending tks_order_ids — these are blacklisted everywhere.
    pending_ids: set = set()
    for row in ozma_txs:
        oid = str(row.get("tks_order_id") or "")
        if not oid:
            continue
        if normalize_ozma_state(row.get("tks_state")) == "pending":
            pending_ids.add(oid)

    # Step 2: bucket provider rows by (acc, id, status).
    prov_by_acc: Dict[int, Dict[Tuple[str, str], List[dict]]] = defaultdict(lambda: defaultdict(list))
    prov_ids_by_acc: Dict[int, set] = defaultdict(set)
    for tx in provider_txs:
        acc = tx.get("expected_ozma_account_id")
        mp = str(tx.get("merchant_payment_id") or "")
        if not mp or mp in pending_ids:
            continue
        prov_by_acc[acc][(mp, tx.get("status"))].append(tx)
        prov_ids_by_acc[acc].add(mp)

    # Step 3: bucket Ozma rows by (acc, id, role). Skip pending and blacklisted.
    ozma_by_acc: Dict[int, Dict[Tuple[str, str], List[dict]]] = defaultdict(lambda: defaultdict(list))
    ozma_ids_by_acc: Dict[int, set] = defaultdict(set)
    for row in ozma_txs:
        oid = str(row.get("tks_order_id") or "")
        if not oid or oid in pending_ids:
            continue
        if normalize_ozma_state(row.get("tks_state")) == "pending":
            continue
        role_info = _ozma_row_role(row)
        if not role_info:
            continue
        acc, role = role_info
        ozma_by_acc[acc][(oid, role)].append(row)
        ozma_ids_by_acc[acc].add(oid)

    # Step 4: match per account.
    results = {}
    for acc in sorted(ACCOUNT_IDS):
        prov_buckets = prov_by_acc.get(acc, {})
        ozma_buckets = ozma_by_acc.get(acc, {})

        match: list = []
        status_drift: list = []
        amount_drift: list = []
        only_in_ozma: list = []
        only_in_provider: list = []
        reported_drift: set = set()  # ids already reported once

        # 4a: walk every provider (id, status) bucket.
        for (oid, pstatus), ps in prov_buckets.items():
            expected_role = _STATUS_TO_ROLE.get(pstatus)
            if expected_role is None:
                # provider 'pending'/'failed' — not part of matching; ignore.
                continue
            os_ = ozma_buckets.get((oid, expected_role), [])
            if os_:
                p, o = ps[0], os_[0]
                p_amount = p.get("amount_kopecks", 0)
                o_amount = amount_kopecks_from_ozma(o)
                if abs(p_amount - o_amount) > 0:
                    amount_drift.append({"key": oid, "role": expected_role,
                                          "p_amount": p_amount, "o_amount": o_amount})
                else:
                    match.append({"key": oid, "role": expected_role})
                if len(ps) > 1 or len(os_) > 1:
                    only_in_provider.append({
                        "key": oid, "role": expected_role,
                        "note": f"duplicate rows: provider={len(ps)} ozma={len(os_)}",
                        "amount": p_amount,
                    })
            else:
                # No Ozma row with the expected role. Two cases:
                #  a) Ozma has some other role for this id → status_drift
                #  b) Ozma has nothing for this id → only_in_provider
                ozma_roles_for_id = sorted({r for (oid2, r) in ozma_buckets.keys() if oid2 == oid})
                if ozma_roles_for_id:
                    if oid not in reported_drift:
                        status_drift.append({"key": oid,
                                              "p_status": pstatus,
                                              "o_roles": ",".join(ozma_roles_for_id)})
                        reported_drift.add(oid)
                else:
                    only_in_provider.append({"key": oid,
                                              "role": expected_role,
                                              "status": pstatus,
                                              "amount": ps[0].get("amount_kopecks"),
                                              "site": ps[0].get("site")})

        # 4b: walk every Ozma (id, role) bucket — find those that didn't get a provider match.
        for (oid, orole), os_ in ozma_buckets.items():
            expected_pstatus = _ROLE_TO_STATUS.get(orole)
            if expected_pstatus and (oid, expected_pstatus) in prov_buckets:
                continue  # already handled in 4a
            # If provider has SOME row for this id (different status) — status_drift; otherwise only_in_ozma.
            prov_statuses_for_id = sorted({s for (oid2, s) in prov_buckets.keys() if oid2 == oid})
            if prov_statuses_for_id:
                if oid not in reported_drift:
                    status_drift.append({"key": oid,
                                          "o_role": orole,
                                          "p_statuses": ",".join(prov_statuses_for_id)})
                    reported_drift.add(oid)
            else:
                only_in_ozma.append({"key": oid,
                                      "role": orole,
                                      "amount": amount_kopecks_from_ozma(os_[0]),
                                      "tks_state": os_[0].get("tks_state")})

        results[acc] = {
            "label": ACCOUNT_TO_LABEL.get(acc, str(acc)),
            "provider_count": sum(len(v) for v in prov_buckets.values()),
            "ozma_count": sum(len(v) for v in ozma_buckets.values()),
            "match": match,
            "status_drift": status_drift,
            "amount_drift": amount_drift,
            "only_in_provider": only_in_provider,
            "only_in_ozma": only_in_ozma,
            "pending_blacklisted_count": len(pending_ids & prov_ids_by_acc.get(acc, set())),
        }
    return results


def sum_payouts_by_date(provider_txs: List[dict]) -> Dict[Tuple[int, str], int]:
    sums = defaultdict(int)
    for tx in provider_txs:
        if tx.get("status") != "succeeded":
            continue
        if tx.get("payout_amount_kopecks") is None:
            continue
        key = (tx["expected_ozma_account_id"], tx.get("payout_date"))
        sums[key] += int(tx["payout_amount_kopecks"])
    return sums


def match_level_2(provider_sums: Dict[Tuple[int, str], int],
                   statement_ops: List[dict]) -> Dict:
    out = {}
    for (acc, date), amount in provider_sums.items():
        provider_kind = next((k for k, v in CHANNEL_TO_ACCOUNT.items() if v == acc), None)
        provider_name = provider_kind[0] if provider_kind else None
        markers = COUNTERPARTY_MARKERS.get(provider_name, [])
        cands = []
        for op in statement_ops:
            if op.get("direction") != "credit":
                continue
            ctry = op.get("counterparty") or {}
            if not any(marker_str.lower() in (ctry.get(field) or "").lower()
                       for field, marker_str in markers):
                continue
            cands.append(op)
        match = None
        for c in cands:
            if abs(c["amount_kopecks"] - amount) <= 1:
                match = c
                break
        out[(acc, date)] = {
            "expected_kopecks": amount,
            "candidates": len(cands),
            "matched": match is not None,
            "matched_op_id": match["operation_id"] if match else None,
            "matched_amount": match["amount_kopecks"] if match else None,
        }
    return out


def render_markdown(date: str, ozma: dict, provider_txs: List[dict],
                    level1: Dict, level2: Dict, contacts: Dict, meta: dict) -> str:
    lines = []
    fetched = meta.get("finished_at_utc", "")
    lines.append(f"# Сверка платежей за {date}")
    lines.append("")
    lines.append(f"Собрано: {fetched}")
    src_states = []
    for name, s in meta.get("sources", {}).items():
        mark = "✅" if s.get("ok") else "❌"
        src_states.append(f"{mark} {name}")
    lines.append(f"Источники: {'  '.join(src_states)}")
    if not meta.get("sources", {}).get("ozma", {}).get("ok", True):
        lines.append("⚠️ Озма недоступна — матчинг с заказами невозможен.")

    # Σ summary
    lines.append("")
    lines.append("## Сводка по каналам")
    lines.append("")
    lines.append("| Канал                      | Кол-во | Σ платежей  | Σ payout  | В выписке Tinkoff |")
    lines.append("|----------------------------|--------|-------------|-----------|--------------------|")
    by_label = defaultdict(lambda: {"count": 0, "amount": 0, "payout": 0})
    for tx in provider_txs:
        acc = tx.get("expected_ozma_account_id")
        label = ACCOUNT_TO_LABEL.get(acc, str(acc))
        by_label[label]["count"] += 1
        by_label[label]["amount"] += tx.get("amount_kopecks", 0)
        if tx.get("payout_amount_kopecks") is not None:
            by_label[label]["payout"] += tx["payout_amount_kopecks"]
    for label, agg in sorted(by_label.items()):
        acc = next((v for k, v in CHANNEL_TO_ACCOUNT.items() if f"{k[0]}/{k[1] or '_'}" == label), None)
        l2 = [m for (a, d), m in level2.items() if a == acc]
        l2_mark = "✅" if any(m["matched"] for m in l2) else ("⚠️" if l2 else "—")
        lines.append(f"| {label:26s} | {agg['count']:>6} | {agg['amount']/100:>9.0f} ₽ "
                     f"| {agg['payout']/100:>7.0f} ₽ | {l2_mark} |")

    # Discrepancies
    lines.append("")
    lines.append("## Расхождения уровня 1 (заказ ↔ платёж)")
    lines.append("")
    any_l1 = False
    for acc, r in level1.items():
        if not (r["status_drift"] or r["amount_drift"] or r["only_in_ozma"] or r["only_in_provider"]):
            continue
        any_l1 = True
        lines.append(f"### {r['label']} (account {acc})")
        for d in r["status_drift"]:
            p_side = d.get("p_status") or d.get("p_statuses") or "—"
            o_side = d.get("o_status") or d.get("o_roles") or d.get("o_role") or "—"
            lines.append(f"- \U0001f7e1 status drift `{d['key']}`: provider={p_side} / ozma={o_side}")
        for d in r["amount_drift"]:
            role = f" ({d['role']})" if d.get("role") else ""
            lines.append(f"- \U0001f7e1 amount drift `{d['key']}`{role}: provider={d['p_amount']} / ozma={d['o_amount']}")
        for d in r["only_in_provider"]:
            extra = f" role={d['role']}" if d.get("role") else ""
            note = f" — {d['note']}" if d.get("note") else ""
            lines.append(f"- \U0001f534 only in provider `{d['key']}`:{extra} {d.get('amount', 0)/100:.0f}₽ site={d.get('site')}{note}")
        for d in r["only_in_ozma"]:
            extra = f" role={d['role']}" if d.get("role") else ""
            lines.append(f"- \U0001f534 only in ozma `{d['key']}`:{extra} {d.get('amount', 0)/100:.0f}₽ tks_state={d.get('tks_state')}")
        if r.get("pending_blacklisted_count"):
            lines.append(f"- ℹ️ {r['pending_blacklisted_count']} provider tx(s) blacklisted (matching pending Ozma orders)")
    if not any_l1:
        lines.append("_Все транзакции matched._")

    lines.append("")
    lines.append("## Расхождения уровня 2 (Σ payout ↔ Tinkoff выписка)")
    lines.append("")
    any_l2_problem = False
    for (acc, pdate), m in level2.items():
        if m["matched"]:
            continue
        any_l2_problem = True
        label = ACCOUNT_TO_LABEL.get(acc, str(acc))
        lines.append(f"- \U0001f7e1 {label} payout_date={pdate}: ожидалось {m['expected_kopecks']/100:.0f}₽, "
                     f"кандидатов в выписке: {m['candidates']}")
    if not any_l2_problem:
        lines.append("_Все Σ payout матчатся с выпиской._")

    # Level 3: contacts
    lines.append("")
    lines.append("## Расхождения контактов (уровень 3)")
    lines.append("")
    n_contacts_total = sum(len(v) for v in contacts.values())
    if not n_contacts_total:
        lines.append("_Контакты совпадают._")
    else:
        n_mis = sum(1 for v in contacts.values() for d in v if d["category"] == "mismatch")
        n_missing = sum(1 for v in contacts.values() for d in v if d["category"] == "missing_in_ozma")
        n_orders = len({(acc, d["key"]) for acc, v in contacts.items() for d in v})
        lines.append(f"{n_contacts_total} расхождений: {n_mis} mismatch, "
                     f"{n_missing} missing_in_ozma по {n_orders} заказам")
        for acc in sorted(contacts):
            ds = contacts[acc]
            if not ds:
                continue
            lines.append(f"### {ACCOUNT_TO_LABEL.get(acc, str(acc))} (account {acc})")
            for d in ds:
                if d["category"] == "missing_in_ozma":
                    lines.append(f"- ➕ `{d['key']}` {d['field']}: "
                                 f"provider={d['provider']} / в Ozma пусто")
                else:
                    ozma_str = "{" + ", ".join(str(x) for x in d["ozma"]) + "}"
                    lines.append(f"- \U0001f7e1 `{d['key']}` {d['field']}: "
                                 f"provider={d['provider']} / ozma={ozma_str}")

    # Advisory
    lines.append("")
    lines.append("## Что делать")
    advisory = []
    n_status_drift = sum(len(r["status_drift"]) for r in level1.values())
    n_only_provider = sum(len(r["only_in_provider"]) for r in level1.values())
    n_only_ozma = sum(len(r["only_in_ozma"]) for r in level1.values())
    if n_status_drift:
        advisory.append(f"- Status drift в Озме ({n_status_drift} строк): запустить mixplat_backfill_apply.py — закроется автоматически.")
    if n_only_provider:
        advisory.append(f"- Only in provider ({n_only_provider}): платежи прошли, заказа в Озме нет. Создать заказы руками.")
    if n_only_ozma:
        advisory.append(f"- Only in Ozma ({n_only_ozma}): tks_order_id есть в Озме, провайдер не вернул. Проверить статус через webhook реплей.")
    n_c_missing = sum(1 for v in contacts.values() for d in v if d["category"] == "missing_in_ozma")
    n_c_mismatch = sum(1 for v in contacts.values() for d in v if d["category"] == "mismatch")
    if n_c_missing:
        advisory.append(f"- Контакты missing_in_ozma ({n_c_missing}): у контакта нет этого канала — можно добавить communication_way вручную.")
    if n_c_mismatch:
        advisory.append(f"- Контакты mismatch ({n_c_mismatch}): значение провайдера не найдено среди контактов покупателя — проверить, не другой ли это человек / не сменились ли данные / корректность ФИО.")
    if not advisory:
        advisory.append("- Расхождений нет. ✅")
    lines.extend(advisory)

    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print("Usage: reconcile.py /tmp/reconcile_<date>/", file=sys.stderr)
        sys.exit(2)
    dir_ = Path(sys.argv[1]).resolve()
    if not dir_.is_dir():
        print(f"ERROR: {dir_} not a directory", file=sys.stderr)
        sys.exit(2)
    ozma = load_json(dir_ / "ozma.json") if (dir_ / "ozma.json").exists() else {"transactions": []}
    provider_txs = collect_provider_txs(dir_)
    level1 = match_level_1(ozma.get("transactions", []), provider_txs)

    statement_ops = []
    spath = dir_ / "tinkoff.json"
    if spath.exists():
        body = load_json(spath)
        if isinstance(body, dict):
            statement_ops = body.get("operations", []) or []
    level2 = match_level_2(sum_payouts_by_date(provider_txs), statement_ops)

    # Level 3: contact comparison on orders present on both sides.
    comparable = set()
    for acc, r in level1.items():
        for bucket in ("match", "amount_drift", "status_drift"):
            for d in r.get(bucket, []):
                k = d.get("key")
                if k is not None:
                    comparable.add((acc, str(k)))
    people_by_id, comm_by_contact = {}, defaultdict(list)
    cpath = dir_ / "ozma_contacts.json"
    if cpath.exists():
        cb = load_json(cpath)
        for p in cb.get("people", []):
            pid = _ref_id(p.get("id"))
            if pid is not None:
                people_by_id[pid] = p
        for cw in cb.get("communication_ways", []):
            cid = _ref_id(cw.get("contact"))
            if cid is not None:
                comm_by_contact[cid].append(cw)
    contacts = compare_contacts(comparable, provider_txs, ozma.get("transactions", []),
                                people_by_id, comm_by_contact)

    meta_path = dir_ / "meta.json"
    meta = load_json(meta_path) if meta_path.exists() else {}
    md = render_markdown(ozma.get("date", "?"), ozma, provider_txs, level1, level2, contacts, meta)
    (dir_ / "report.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
