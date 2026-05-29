# Менеджер по документообороту

Продажи, договоры, банковские выписки. Работает в OzmaDB + Bitrix.

## Файлы

- **[CLAUDE.md](CLAUDE.md)** — личность ассистента, обязанности, правила
- **[ozma.md](ozma.md)** — контекст по OzmaDB под эту роль (схемы, views, actions)
- **[.mcp.json](.mcp.json)** — список MCP-серверов, которые нужны роли
- **`skills/`** — slash-команды:
  - **[`/bank`](skills/bank/SKILL.md)** — внесение банковской выписки в `fin.economics`
  - **[`/newcontract`](skills/newcontract/SKILL.md)** — генерация договора оказания услуг
  - **[`payments-reconcile`](skills/payments-reconcile/SKILL.md)** — ежедневная сверка платежей (CloudPayments / Tinkoff / Mixplat / Yandex Split) с `fin.transactions` в Ozma

## Установка (для сотрудника)

```bash
curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s doc-fin-ops
```

> install.sh — пока заглушка, реальная установка появится после того, как пилот этой роли пройдёт первый круг использования.

## Витрина в Notion

[🤖 AI роли → Менеджер по документообороту](https://www.notion.so/35e612c762af811d8b79c5c0e8b6a2bf)
