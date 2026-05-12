# gogol-school-ai

AI-ассистенты для сотрудников Gogol School: CLAUDE.md, MCP-конфиги, skills (slash-команды) и типовые промпты — по одной папке на роль.

Витрина с описанием каждой роли живёт в Notion: **Дом GOGOL SCHOOL → Команда → 🤖 AI роли**. Этот репо — источник правды, из него ставятся файлы сотруднику на машину.

## Структура

```
roles/
  doc-manager/            ← Менеджер по документообороту
    CLAUDE.md             ← общая личность ассистента
    ozma.md               ← как работать с OzmaDB
    .mcp.json             ← список подключаемых MCP-серверов
    skills/
      bank/SKILL.md       ← /bank — внесение банковской выписки
      newcontract/SKILL.md
  operations-manager/     ← Менеджер по операциям
  head-admin/             ← Главный администратор
  rop/                    ← РОП
  senior-admin/           ← Старший администратор
  marketing-assistant/    ← Ассистент маркетинга
  smm/                    ← СММ
  brand-pr/               ← Бренд-менеджер + PR
  product-manager/        ← Менеджер внутренних проектов
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
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-manager
```

Скрипт скачает нужные файлы роли и положит в `~/.claude/`.

## Обновление

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s <role-slug>
```

Та же команда — install.sh идемпотентный, повторный запуск подтягивает свежую версию.
