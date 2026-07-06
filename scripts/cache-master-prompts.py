#!/usr/bin/env python3
"""Кэш мастер-промптов Gogol School из Notion в локальные файлы.

Зачем: скиллы в рантайме тянут мастер-промпт из Notion. Если Notion лёг или
протух токен — агент остаётся без инструкции. Этот скрипт складывает копию
каждого мастер-промпта рядом со скиллом (`references/master-prompt.cache.md`),
чтобы был фолбэк (правило — в shared/CLAUDE.md, раздел «Мастер-промпты»).

Что делает:
  - обходит roles/*/skills/*/SKILL.md;
  - вытаскивает id Notion-страниц, помеченных как мастер-промпт;
  - тянет их содержимое через Notion API и рендерит в текст;
  - пишет roles/<role>/skills/<skill>/references/master-prompt.cache.md.

Кэш-файлы в git НЕ коммитятся (см. .gitignore) — у каждой машины свой,
регенерируется по требованию. Запускать периодически / перед известным
простоем Notion / после правки промптов.

Использование:
    NOTION_TOKEN=ntn_xxx python3 scripts/cache-master-prompts.py [--dry-run]

На РФ-сервере, где Notion доступен только через прокси, задать прокси в env:
    HTTPS_PROXY=http://127.0.0.1:1087 NOTION_TOKEN=... python3 scripts/cache-master-prompts.py
(urllib сам подхватывает HTTP(S)_PROXY из окружения.)
"""
import os
import re
import sys
import json
import time
import urllib.request
import urllib.error

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"
DRY_RUN = "--dry-run" in sys.argv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_GLOB_ROOT = os.path.join(REPO, "roles")

# id страницы = 32 hex-символа (с дефисами или без)
ID_RE = re.compile(r"([0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
# строки SKILL.md, где id — это мастер-промпт (а не связанная инструкция/таблица)
MASTER_HINT_RE = re.compile(r"мастер[- ]?промпт|мастер[- ]?промт|master[- ]?prompt|prompt library", re.I)


def _dash(uid):
    """Привести 32-hex к каноничному uuid с дефисами."""
    h = uid.replace("-", "").lower()
    return "%s-%s-%s-%s-%s" % (h[0:8], h[8:12], h[12:16], h[16:20], h[20:32])


def notion_api(method, path):
    req = urllib.request.Request(
        API + path, method=method,
        headers={"Authorization": "Bearer " + NOTION_TOKEN,
                 "Notion-Version": NOTION_VERSION,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def rich_text(rt):
    return "".join(x.get("plain_text", "") for x in (rt or []))


def render_blocks(block_id, depth=0):
    """Рекурсивно рендерит дерево блоков в markdown-подобный текст."""
    out = []
    cursor = None
    while True:
        path = "/blocks/%s/children?page_size=100" % block_id
        if cursor:
            path += "&start_cursor=" + cursor
        data = notion_api("GET", path)
        for b in data.get("results", []):
            out.append(render_block(b, depth))
            if b.get("has_children") and b["type"] not in ("column_list", "column"):
                out.append(render_blocks(b["id"], depth + 1))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        time.sleep(0.2)  # мягкий троттлинг под rate-limit Notion (3 rps)
    return "\n".join(x for x in out if x is not None)


def render_block(b, depth):
    t = b.get("type", "")
    d = b.get(t, {})
    pad = "  " * depth
    txt = rich_text(d.get("rich_text"))
    if t == "paragraph":
        return pad + txt if txt else ""
    if t in ("heading_1", "heading_2", "heading_3"):
        hashes = "#" * int(t[-1])
        return "\n%s %s" % (hashes, txt)
    if t == "bulleted_list_item":
        return pad + "- " + txt
    if t == "numbered_list_item":
        return pad + "1. " + txt
    if t == "to_do":
        mark = "x" if d.get("checked") else " "
        return "%s- [%s] %s" % (pad, mark, txt)
    if t == "toggle":
        return pad + "▸ " + txt
    if t == "quote":
        return pad + "> " + txt
    if t == "callout":
        icon = (d.get("icon") or {}).get("emoji", "")
        return "%s%s %s" % (pad, icon, txt)
    if t == "code":
        lang = d.get("language", "")
        return "```%s\n%s\n```" % (lang, txt)
    if t == "divider":
        return "---"
    if t == "child_page":
        return pad + "📄 " + d.get("title", "")
    if txt:
        return pad + txt
    return None


def find_master_ids(skill_md_path):
    """Вернуть уникальные id мастер-промптов из SKILL.md (по строкам с хинтом)."""
    ids = []
    seen = set()
    with open(skill_md_path, encoding="utf-8") as f:
        for line in f:
            if not MASTER_HINT_RE.search(line):
                continue
            for m in ID_RE.findall(line):
                canon = _dash(m)
                if canon not in seen:
                    seen.add(canon)
                    ids.append(canon)
    return ids


def main():
    if not NOTION_TOKEN and not DRY_RUN:
        print("NOTION_TOKEN не задан — нечем ходить в Notion. "
              "Запусти: NOTION_TOKEN=ntn_... python3 scripts/cache-master-prompts.py", file=sys.stderr)
        sys.exit(1)

    total, ok, empty, failed = 0, 0, 0, 0
    for role in sorted(os.listdir(SKILLS_GLOB_ROOT)):
        skills_dir = os.path.join(SKILLS_GLOB_ROOT, role, "skills")
        if not os.path.isdir(skills_dir):
            continue
        for skill in sorted(os.listdir(skills_dir)):
            skill_md = os.path.join(skills_dir, skill, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            ids = find_master_ids(skill_md)
            if not ids:
                continue
            total += 1
            if DRY_RUN:
                print("• %s/%s → %s" % (role, skill, ", ".join(ids)))
                continue
            parts = []
            for pid in ids:
                try:
                    body = render_blocks(pid).strip()
                    if body:
                        parts.append("<!-- notion page %s -->\n\n%s" % (pid, body))
                except urllib.error.HTTPError as e:
                    print("  ! %s/%s %s → HTTP %s" % (role, skill, pid, e.code), file=sys.stderr)
                    failed += 1
                except Exception as e:
                    print("  ! %s/%s %s → %s" % (role, skill, pid, e), file=sys.stderr)
                    failed += 1
            if not parts:
                empty += 1
                continue
            ref_dir = os.path.join(skills_dir, skill, "references")
            os.makedirs(ref_dir, exist_ok=True)
            header = ("<!-- АВТО-КЭШ мастер-промпта из Notion. Не редактировать руками. -->\n"
                      "<!-- Регенерация: scripts/cache-master-prompts.py. Может быть неактуальным — "
                      "источник правды всегда в Notion. -->\n\n")
            with open(os.path.join(ref_dir, "master-prompt.cache.md"), "w", encoding="utf-8") as f:
                f.write(header + "\n\n---\n\n".join(parts) + "\n")
            ok += 1
            print("✓ %s/%s (%d стр.)" % (role, skill, len(ids)))

    if DRY_RUN:
        print("\n[dry-run] скиллов с мастер-промптами: %d" % total)
    else:
        print("\nГотово: закэшировано %d, пусто %d, ошибок %d (из %d скиллов)"
              % (ok, empty, failed, total))


if __name__ == "__main__":
    main()
