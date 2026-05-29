#!/usr/bin/env python3
"""Fetch payments from all 5 OzmaBot reconcile endpoints in parallel.

Usage: fetch_payments.py YYYY-MM-DD

Writes:
  /tmp/reconcile_<date>/cp.json
  /tmp/reconcile_<date>/tinkoff_acquiring.json
  /tmp/reconcile_<date>/tinkoff.json
  /tmp/reconcile_<date>/mixplat.json
  /tmp/reconcile_<date>/split.json
  /tmp/reconcile_<date>/meta.json

Stdout: ~15-line summary.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SKILL_DIR = Path(__file__).resolve().parent.parent


def load_env() -> Dict[str, str]:
    path = SKILL_DIR / ".env"
    if not path.exists():
        print("ERROR: .env not found in skill dir; copy from .env.example", file=sys.stderr)
        sys.exit(2)
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def fetch_one(url: str, headers: Dict[str, str], timeout: int = 90) -> Tuple[int, dict]:
    req = Request(url, headers=headers)
    start = time.time()
    try:
        with urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        return r.status, data
    except Exception as e:
        # HTTPError gives us body on .read() — try to parse upstream JSON
        body = None
        if hasattr(e, "read"):
            try:
                body = json.load(e)
            except Exception:
                body = None
        if body is None:
            body = {"ok": False, "error": "fetch_exception", "detail": str(e),
                    "took_ms": int((time.time() - start) * 1000)}
        status = getattr(e, "code", 0)
        return status, body


async def fetch_async(url: str, headers: Dict[str, str]) -> Tuple[int, dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_one, url, headers)


async def main(date: str):
    env = load_env()
    base = env["OZMABOT_URL"].rstrip("/")
    headers = {"X-API-Key": env.get("RECONCILE_API_KEY", "")}

    out_dir = Path(f"/tmp/reconcile_{date}")
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = {
        "cp":                 f"{base}/reconcile/cp?{urlencode({'date': date})}",
        "tinkoff_acquiring":  f"{base}/reconcile/tinkoff-acquiring?{urlencode({'date': date})}",
        "tinkoff":            f"{base}/reconcile/tinkoff?{urlencode({'date': date})}",
        "mixplat":            f"{base}/reconcile/mixplat?{urlencode({'date': date})}",
        "split":              f"{base}/reconcile/split?{urlencode({'date': date})}",
    }

    started = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    start_t = time.time()

    results = await asyncio.gather(*(fetch_async(u, headers) for u in targets.values()))

    sources: Dict[str, dict] = {}
    for (name, _), (status, body) in zip(targets.items(), results):
        out_file = out_dir / f"{name}.json"
        out_file.write_text(json.dumps(body, ensure_ascii=False, indent=2))
        if status == 200 and isinstance(body, dict) and "count" in body:
            sources[name] = {"ok": True, "count": body["count"]}
        else:
            sources[name] = {"ok": False,
                              "status": status,
                              "error": body.get("error", f"http_{status}") if isinstance(body, dict) else f"http_{status}",
                              "detail": body.get("detail", "") if isinstance(body, dict) else ""}

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    meta = {
        "date": date,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "took_ms": int((time.time() - start_t) * 1000),
        "skill_version": "0.1.0",
        "sources": sources,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    # Stdout summary
    print(f"Reconcile fetch for {date}:")
    for name, s in sources.items():
        if s.get("ok"):
            print(f"  ✅ {name:20s} count={s['count']}")
        else:
            print(f"  ❌ {name:20s} {s.get('error')}: {s.get('detail')}")
    print(f"Output dir: {out_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: fetch_payments.py YYYY-MM-DD", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(sys.argv[1]))
