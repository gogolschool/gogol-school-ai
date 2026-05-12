# gogol-school-ai

AI-ассистенты для сотрудников Gogol School: CLAUDE.md, MCP-конфиги, skills (slash-команды) и типовые промпты — по одной папке на роль.

Витрина с описанием каждой роли живёт в Notion: **Дом GOGOL SCHOOL → Команда → 🤖 AI роли**. Этот репо — источник правды, из него ставятся файлы сотруднику на машину.

## Структура

```
shared/
  CLAUDE.md               ← общий контекст про Gogol School (читают все роли)
  knowledge/
    systems.md            ← обзор Ozma/Bitrix/Notion/Telegram/Sheets
    database.md           ← схема OzmaDB

roles/
  doc-fin-ops/            ← Документооборот + финансы + операции
    CLAUDE.md             ← только специфика роли (общее — в shared/)
    ozma.md               ← как работать с OzmaDB
    .mcp.json             ← список подключаемых MCP-серверов
    skills/
      bank/SKILL.md       ← /bank — внесение банковской выписки
      newcontract/SKILL.md
  client-office-ops/      ← Операционный менеджер
  senior-admin/           ← Администратор
  marketing-assistant/    ← Менеджер по маркетингу
  smm/                    ← СММ
  brand-pr/               ← Бренд-менеджер + PR
  product-manager/        ← Менеджер программ
  student-comms/          ← Менеджер по общению со студентами
  product-assistant/      ← Помощник менеджера продукта

install.sh                ← скрипт установки для сотрудника
```

## Установка ассистента (для сотрудника)

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s <role-slug>
```

Например, для менеджера по документообороту:

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops
```

Скрипт скачает нужные файлы роли и положит в `~/.claude/`.

## Обновление

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s <role-slug>
```

Та же команда — install.sh идемпотентный, повторный запуск подтягивает свежую версию.
