#!/usr/bin/env bash
# Gogol School AI — установщик роли.
#
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s <role>
#
# Доступные роли:
#   doc-fin-ops, client-office-ops, senior-admin, marketing-assistant,
#   smm, brand-pr, product-manager, student-comms, product-assistant

set -euo pipefail

# ─── Константы ──────────────────────────────────────────────────────────────
REPO_URL="https://github.com/gogolschool/gogol-school-ai.git"
REPO_DIR="$HOME/.gogol-ai/repo"
ENV_FILE="$HOME/.gogol-ai/.env"
GOOGLE_TOKEN_PATH="$HOME/.gogol-ai/google_token.json"
CLAUDE_DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
NOTION_TOKENS_HINT="Notion → 🔐 Токены MCP"

# ─── Цвета для вывода ───────────────────────────────────────────────────────
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
blue()   { printf "\033[34m%s\033[0m\n" "$*"; }
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }

# ─── Аргументы ──────────────────────────────────────────────────────────────
ROLE="${1:-}"
if [[ -z "$ROLE" ]]; then
  red "Не указана роль."
  echo "Использование: bash install.sh <role>"
  echo "Доступные: doc-fin-ops, client-office-ops, senior-admin, marketing-assistant,"
  echo "           smm, brand-pr, product-manager, student-comms, product-assistant"
  exit 1
fi

bold "🚀 Установка AI-ассистента для роли: $ROLE"
echo

# ─── Проверка зависимостей ─────────────────────────────────────────────────
check_dep() {
  local cmd="$1"
  local hint="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    red "✗ Не найден: $cmd"
    echo "  $hint"
    return 1
  fi
  green "✓ $cmd установлен"
}

bold "📋 Проверка зависимостей"
MISSING=0
check_dep node "Установи Node.js: brew install node" || MISSING=1
check_dep npm "Идёт с node" || MISSING=1
check_dep git "brew install git" || MISSING=1
check_dep claude "Установи Claude Code: см. https://claude.com/download" || MISSING=1
if (( MISSING )); then
  red "Установи отсутствующие компоненты и запусти снова."
  exit 1
fi
echo

# ─── Клонирование/обновление репозитория ───────────────────────────────────
bold "📥 Получение конфигурации роли"
mkdir -p "$HOME/.gogol-ai"
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" pull --quiet
  green "✓ Конфигурация обновлена"
else
  git clone --quiet "$REPO_URL" "$REPO_DIR"
  green "✓ Конфигурация скачана"
fi
echo

ROLE_DIR="$REPO_DIR/roles/$ROLE"
if [[ ! -d "$ROLE_DIR" ]]; then
  red "Роль '$ROLE' не найдена в репозитории."
  exit 1
fi

# ─── Копирование CLAUDE.md и skills ────────────────────────────────────────
bold "📝 Установка CLAUDE.md и skills"
mkdir -p "$HOME/.claude/skills"

# Склеиваем shared + role в один ~/.claude/CLAUDE.md
{
  cat "$REPO_DIR/shared/CLAUDE.md"
  echo
  echo "---"
  echo
  cat "$ROLE_DIR/CLAUDE.md" 2>/dev/null || true
} > "$HOME/.claude/CLAUDE.md"
green "✓ ~/.claude/CLAUDE.md обновлён"

# Копируем shared/knowledge целиком (на него ссылаются)
mkdir -p "$HOME/.claude/knowledge"
cp -R "$REPO_DIR/shared/knowledge/." "$HOME/.claude/knowledge/"
green "✓ ~/.claude/knowledge/ обновлён"

# Копируем ozma.md рядом, если есть
if [[ -f "$ROLE_DIR/ozma.md" ]]; then
  cp "$ROLE_DIR/ozma.md" "$HOME/.claude/ozma.md"
  green "✓ ~/.claude/ozma.md обновлён"
fi

# Копируем skills роли
if [[ -d "$ROLE_DIR/skills" ]]; then
  cp -R "$ROLE_DIR/skills/." "$HOME/.claude/skills/"
  green "✓ ~/.claude/skills/ обновлены"
fi
echo

# ─── Загрузка кэша секретов ────────────────────────────────────────────────
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
# shellcheck disable=SC1090
source "$ENV_FILE"

# ─── Запрос секрета у пользователя (с кешем) ───────────────────────────────
ask_secret() {
  local var_name="$1"
  local hint="$2"
  local hide="${3:-yes}"  # yes = скрытый ввод
  local current="${!var_name:-}"
  if [[ -n "$current" ]]; then
    return 0  # уже есть в ~/.gogol-ai/.env
  fi
  echo
  yellow "▸ $var_name"
  echo "  $hint"
  if [[ "$hide" == "yes" ]]; then
    read -r -s -p "  значение: " value
    echo
  else
    read -r -p "  значение: " value
  fi
  printf "%s=%q\n" "$var_name" "$value" >> "$ENV_FILE"
  export "$var_name=$value"
}

# ─── Чтение списка MCP роли ────────────────────────────────────────────────
MCP_JSON="$ROLE_DIR/.mcp.json"
if [[ ! -f "$MCP_JSON" ]]; then
  yellow "Для роли не задан список MCP — пропускаю шаг настройки серверов."
  exit 0
fi
# Список ключей mcpServers через python (предустановлен на macOS)
MCP_LIST=$(python3 -c "import json,sys; d=json.load(open('$MCP_JSON')); print('\n'.join((d.get('mcpServers') or {}).keys()))")

if [[ -z "$MCP_LIST" ]]; then
  yellow "Список MCP пуст — пропускаю настройку серверов."
  exit 0
fi

bold "🔌 Настройка MCP-серверов"
echo "Откуда брать токены: $NOTION_TOKENS_HINT"
echo

# ─── Регистрация MCP-сервера (в Claude Desktop + Claude Code) ──────────────
# Аргументы: имя_сервера, JSON-фрагмент конфигурации для Desktop
register_mcp_desktop() {
  local name="$1"
  local cfg="$2"
  mkdir -p "$(dirname "$CLAUDE_DESKTOP_CFG")"
  if [[ ! -f "$CLAUDE_DESKTOP_CFG" ]]; then
    echo '{"mcpServers":{}}' > "$CLAUDE_DESKTOP_CFG"
  fi
  # Подменяем/добавляем сервер через python (jq может не быть)
  python3 - "$CLAUDE_DESKTOP_CFG" "$name" "$cfg" <<'PYEOF'
import json, sys
path, name, cfg = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
data.setdefault("mcpServers", {})[name] = json.loads(cfg)
json.dump(data, open(path, "w"), indent=2, ensure_ascii=False)
PYEOF
}

# Аргументы: имя_сервера, остальные args для `claude mcp add ... -- npx -y mcp-remote URL ...`
register_mcp_code() {
  local name="$1"
  shift
  # Удалить, если уже был, чтобы не дублировать
  claude mcp remove --scope user "$name" >/dev/null 2>&1 || true
  claude mcp add --scope user "$name" -- "$@"
}

# ─── Конфиги по каждому MCP ────────────────────────────────────────────────
setup_ozma() {
  ask_secret OZMA_BEARER "Bearer-токен Ozma MCP (общий, из $NOTION_TOKENS_HINT)"
  ask_secret OZMA_CLIENT_SECRET "Client Secret Ozma (из $NOTION_TOKENS_HINT)"
  ask_secret OZMA_USERNAME "Твой логин в Ozma (email)" no
  ask_secret OZMA_PASSWORD "Твой пароль в Ozma"

  local url="https://ozmamcp.gogol.school/mcp"
  local cfg
  cfg=$(python3 -c "
import json
print(json.dumps({
  'command': 'npx',
  'args': [
    '-y','mcp-remote', '$url',
    '--header', 'Authorization: Bearer $OZMA_BEARER',
    '--header', 'X-Ozma-URL: https://ozma.gogol.school/api/',
    '--header', 'X-Ozma-Auth-URL: https://ozma.gogol.school/auth/realms/ozma/protocol/openid-connect/token',
    '--header', 'X-Ozma-Client-ID: ozmadb',
    '--header', 'X-Ozma-Client-Secret: $OZMA_CLIENT_SECRET',
    '--header', 'X-Ozma-Username: $OZMA_USERNAME',
    '--header', 'X-Ozma-Password: $OZMA_PASSWORD',
  ]
}))
")
  register_mcp_desktop "ozma" "$cfg"
  register_mcp_code "ozma" npx -y mcp-remote "$url" \
    --header "Authorization: Bearer $OZMA_BEARER" \
    --header "X-Ozma-URL: https://ozma.gogol.school/api/" \
    --header "X-Ozma-Auth-URL: https://ozma.gogol.school/auth/realms/ozma/protocol/openid-connect/token" \
    --header "X-Ozma-Client-ID: ozmadb" \
    --header "X-Ozma-Client-Secret: $OZMA_CLIENT_SECRET" \
    --header "X-Ozma-Username: $OZMA_USERNAME" \
    --header "X-Ozma-Password: $OZMA_PASSWORD"
  green "✓ ozma подключён"
}

setup_gogol_site() {
  ask_secret SITE_BEARER "Bearer-токен сайта (gogol-site-remote, из $NOTION_TOKENS_HINT)"
  local url="https://ozma.gogol.school/site_mcp/mcp"
  local cfg
  cfg=$(python3 -c "
import json
print(json.dumps({
  'command':'npx',
  'args':['-y','mcp-remote','$url','--header','Authorization: Bearer $SITE_BEARER']
}))
")
  register_mcp_desktop "gogol-site-remote" "$cfg"
  register_mcp_code "gogol-site-remote" npx -y mcp-remote "$url" \
    --header "Authorization: Bearer $SITE_BEARER"
  green "✓ gogol-site-remote подключён"
}

setup_unisender() {
  ask_secret UNISENDER_TOKEN "Токен unisender (из $NOTION_TOKENS_HINT)"
  local url="http://ozma.gogol.school:8002/mcp/?token=$UNISENDER_TOKEN"
  local cfg
  cfg=$(python3 -c "
import json
print(json.dumps({
  'command':'npx',
  'args':['-y','mcp-remote','$url','--allow-http']
}))
")
  register_mcp_desktop "unisender" "$cfg"
  register_mcp_code "unisender" npx -y mcp-remote "$url" --allow-http
  green "✓ unisender подключён"
}

setup_telegram() {
  ask_secret TELEGRAM_BEARER "Bearer-токен Telegram MCP (из $NOTION_TOKENS_HINT)"
  local url="http://ozma.gogol.school:8001/mcp"
  local cfg
  cfg=$(python3 -c "
import json
print(json.dumps({
  'command':'npx',
  'args':['-y','mcp-remote','$url','--allow-http','--header','Authorization: Bearer $TELEGRAM_BEARER']
}))
")
  register_mcp_desktop "telegram_ozma_mcp" "$cfg"
  register_mcp_code "telegram_ozma_mcp" npx -y mcp-remote "$url" --allow-http \
    --header "Authorization: Bearer $TELEGRAM_BEARER"
  green "✓ telegram_ozma_mcp подключён"
}

setup_google() {
  # Один JSON-файл сервис-аккаунта работает и для Docs, и для Sheets
  if [[ ! -f "$GOOGLE_TOKEN_PATH" ]]; then
    yellow "▸ Нужен google_token.json (сервис-аккаунт Google)"
    echo "  Создай по инструкции: $NOTION_TOKENS_HINT → раздел Google Sheets/Docs"
    echo "  Когда файл скачан — положи его в $GOOGLE_TOKEN_PATH и запусти install.sh снова."
    yellow "  Пропускаю google_sheets и google_docs."
    return 0
  fi
  ask_secret GOOGLE_SERVICE_ACCOUNT_EMAIL \
    "Email сервис-аккаунта (выглядит как xxx@yyy.iam.gserviceaccount.com)" no

  local sheets_cfg docs_cfg
  sheets_cfg=$(python3 -c "
import json
print(json.dumps({
  'command':'npx',
  'args':['-y','mcp-google-sheets'],
  'env':{'GOOGLE_SHEETS_CLIENT_ID':'$GOOGLE_SERVICE_ACCOUNT_EMAIL','TOKEN_PATH':'$GOOGLE_TOKEN_PATH'}
}))
")
  docs_cfg=$(python3 -c "
import json
print(json.dumps({
  'command':'npx',
  'args':['-y','@a-bonus/google-docs-mcp'],
  'env':{'GOOGLE_CLIENT_ID':'$GOOGLE_SERVICE_ACCOUNT_EMAIL','TOKEN_PATH':'$GOOGLE_TOKEN_PATH'}
}))
")
  register_mcp_desktop "google_sheets" "$sheets_cfg"
  register_mcp_desktop "google_docs" "$docs_cfg"

  GOOGLE_SHEETS_CLIENT_ID="$GOOGLE_SERVICE_ACCOUNT_EMAIL" \
  TOKEN_PATH="$GOOGLE_TOKEN_PATH" \
    claude mcp add --scope user google_sheets -- npx -y mcp-google-sheets || true
  GOOGLE_CLIENT_ID="$GOOGLE_SERVICE_ACCOUNT_EMAIL" \
  TOKEN_PATH="$GOOGLE_TOKEN_PATH" \
    claude mcp add --scope user google_docs -- npx -y @a-bonus/google-docs-mcp || true

  green "✓ google_sheets и google_docs подключены"
}

# ─── Маппинг имён в .mcp.json к функциям установки ────────────────────────
while IFS= read -r mcp_name; do
  case "$mcp_name" in
    ozma)              setup_ozma ;;
    gogol-school-site|gogol-site-remote)
                       setup_gogol_site ;;
    unisender)         setup_unisender ;;
    telegram|telegram_ozma_mcp)
                       setup_telegram ;;
    google-sheets|google-drive|google_sheets|google_docs)
                       # Установим Google один раз, даже если в .mcp.json упомянуты оба ключа
                       if [[ -z "${GOOGLE_INSTALLED:-}" ]]; then
                         setup_google
                         GOOGLE_INSTALLED=1
                       fi
                       ;;
    notion)
      yellow "▸ notion — встроенная интеграция Claude Desktop"
      echo "   Подключается вручную: Claude Desktop → Settings → Connectors → Notion"
      ;;
    *)
      yellow "▸ $mcp_name — неизвестный MCP, пропускаю"
      ;;
  esac
done <<< "$MCP_LIST"

echo
bold "✅ Готово!"
echo
echo "Перезапусти Claude Desktop (Cmd+Q → открыть снова) и Claude Code."
echo "Проверь: в Claude Code напиши '/' — должны появиться скилы роли."
echo
echo "Секреты сохранены в: $ENV_FILE (только ты имеешь доступ)"
echo "Конфиг роли: $ROLE_DIR"
echo "Обновить позже: запусти эту же команду повторно."
