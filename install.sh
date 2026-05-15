#!/usr/bin/env bash
# Gogol School AI — установщик ролей.
#
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh | bash -s <role1> [role2]...
#   curl -fsSL ... | bash -s -- --folder <name> <role1> [role2]...
#
# По умолчанию создаётся одна объединённая рабочая папка ~/gogol-ai/work/
# с CLAUDE.md и skills всех выбранных ролей. Сотрудник работает оттуда.
#
# Доступные роли:
#   doc-fin-ops, client-office-ops, senior-admin, marketing-assistant,
#   smm, brand-pr, product-manager, student-comms, product-assistant, analyst

set -euo pipefail

# ─── Проверка версии bash ───────────────────────────────────────────────────
# Скрипт использует ассоциативные массивы (declare -A) — нужен bash 4+.
# На macOS системный /bin/bash — 3.2, поэтому проверяем и подсказываем фикс.
if (( BASH_VERSINFO[0] < 4 )); then
  printf "\033[31m✗ Нужен bash 4 или новее (у тебя %s).\033[0m\n" "$BASH_VERSION" >&2
  if [[ "$(uname)" == "Darwin" ]]; then
    cat >&2 <<'EOF'

На macOS системный bash — 3.2. Поставь новый через Homebrew и перезапусти:

  brew install bash

  curl -fsSL https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.sh -o /tmp/install.sh
  /opt/homebrew/bin/bash /tmp/install.sh <role>     # Apple Silicon
  /usr/local/bin/bash    /tmp/install.sh <role>     # Intel Mac

EOF
  fi
  exit 1
fi

# ─── Константы ──────────────────────────────────────────────────────────────
REPO_URL="https://github.com/gogolschool/gogol-school-ai.git"
GOGOL_DIR="$HOME/.gogol-ai"
REPO_DIR="$GOGOL_DIR/repo"
ENV_FILE="$GOGOL_DIR/.env"
GOOGLE_TOKEN_PATH="$GOGOL_DIR/google_token.json"
ROLES_DIR="$HOME/gogol-ai"
CLAUDE_DIR="$HOME/.claude"
CLAUDE_DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
NOTION_TOKENS_HINT="Notion → 🔐 Токены MCP"
FOLDER_NAME="work"

# ─── Цвета ──────────────────────────────────────────────────────────────────
red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
bold()   { printf "\033[1m%s\033[0m\n" "$*"; }

# ─── Парсинг аргументов ─────────────────────────────────────────────────────
ROLES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --folder)
      FOLDER_NAME="$2"; shift 2 ;;
    --help|-h)
      echo "Использование: bash install.sh [--folder <name>] <role1> [role2]..."
      echo "По умолчанию папка: ~/gogol-ai/work/"
      exit 0 ;;
    *)
      ROLES+=("$1"); shift ;;
  esac
done

if [[ ${#ROLES[@]} -eq 0 ]]; then
  red "Не указано ни одной роли."
  echo "Использование: bash install.sh [--folder <name>] <role1> [role2]..."
  echo "Доступные: doc-fin-ops, client-office-ops, senior-admin, marketing-assistant,"
  echo "           smm, brand-pr, product-manager, student-comms, product-assistant, analyst"
  exit 1
fi

WORK_DIR="$ROLES_DIR/$FOLDER_NAME"

bold "🚀 Установка ролей в ~/gogol-ai/$FOLDER_NAME/: ${ROLES[*]}"
echo

# ─── Проверка зависимостей ─────────────────────────────────────────────────
check_dep() {
  if ! command -v "$1" >/dev/null 2>&1; then
    red "✗ Не найден: $1"; echo "  $2"; return 1
  fi
  green "✓ $1"
}

bold "📋 Проверка зависимостей"
MISSING=0
check_dep node "brew install node  (или скачай .pkg с https://nodejs.org)" || MISSING=1
check_dep npm "идёт с node" || MISSING=1
check_dep git "brew install git" || MISSING=1
check_dep python3 "brew install python  (или скачай .pkg с https://python.org)" || MISSING=1
check_dep claude "npm install -g @anthropic-ai/claude-code  (нужны права sudo, если node из .pkg)" || MISSING=1
(( MISSING )) && { red "Установи отсутствующие компоненты."; exit 1; }
echo

# ─── Клонирование/обновление репо ──────────────────────────────────────────
bold "📥 Получение конфигурации"
mkdir -p "$GOGOL_DIR"
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" pull --quiet
  green "✓ Репо обновлён"
else
  git clone --quiet "$REPO_URL" "$REPO_DIR"
  green "✓ Репо скачан"
fi
echo

# Проверим, что роли существуют
for role in "${ROLES[@]}"; do
  if [[ ! -d "$REPO_DIR/roles/$role" ]]; then
    red "Роль '$role' не найдена."; exit 1
  fi
done

# ─── Шаг 1: глобальный shared в ~/.claude/ ─────────────────────────────────
bold "📝 Установка общего контекста (~/.claude/)"
mkdir -p "$CLAUDE_DIR/knowledge"

cp "$REPO_DIR/shared/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"
green "✓ ~/.claude/CLAUDE.md (общий контекст)"

cp -R "$REPO_DIR/shared/knowledge/." "$CLAUDE_DIR/knowledge/"
green "✓ ~/.claude/knowledge/"

for shared_file in "$REPO_DIR/shared"/*.md; do
  base=$(basename "$shared_file")
  [[ "$base" == "CLAUDE.md" ]] && continue
  cp "$shared_file" "$CLAUDE_DIR/$base"
  green "✓ ~/.claude/$base"
done
echo

# ─── Шаг 2: объединённая рабочая папка ─────────────────────────────────────
bold "📁 Сборка рабочей папки ~/gogol-ai/$FOLDER_NAME/"

# Чистим только то, что генерируется (CLAUDE.md, ozma.md) —
# не трогаем личные файлы пользователя, если он что-то туда положил.
# Скилы раскатываются глобально (~/.claude/skills/), а не в WORK_DIR,
# чтобы /<команда> подхватывалась из любой папки.
rm -f "$WORK_DIR/CLAUDE.md" "$WORK_DIR/ozma.md"
mkdir -p "$WORK_DIR"

# ── CLAUDE.md: склейка всех CLAUDE.md выбранных ролей ──
{
  echo "# Рабочая папка: $FOLDER_NAME"
  echo
  echo "Активные роли: **${ROLES[*]}**"
  echo
  echo "Общий контекст про Gogol School — в \`~/.claude/CLAUDE.md\` (загружается автоматически). Ниже — специфика выбранных ролей."
  echo
  for role in "${ROLES[@]}"; do
    role_md="$REPO_DIR/roles/$role/CLAUDE.md"
    if [[ -f "$role_md" ]]; then
      echo "---"
      echo
      echo "# === Роль: $role ==="
      echo
      cat "$role_md"
      echo
    fi
  done
} > "$WORK_DIR/CLAUDE.md"
green "✓ $WORK_DIR/CLAUDE.md (склейка ${#ROLES[@]} ролей)"

# ── ozma.md: склейка, если несколько; иначе копия ──
# Используем x=$((x+1)) вместо ((x++)) — последний при x=0 возвращает exit 1
# и под set -e тихо убивает скрипт.
ozma_count=0
for role in "${ROLES[@]}"; do
  if [[ -f "$REPO_DIR/roles/$role/ozma.md" ]]; then
    ozma_count=$((ozma_count + 1))
  fi
done
if (( ozma_count == 1 )); then
  for role in "${ROLES[@]}"; do
    [[ -f "$REPO_DIR/roles/$role/ozma.md" ]] && cp "$REPO_DIR/roles/$role/ozma.md" "$WORK_DIR/ozma.md"
  done
  green "✓ $WORK_DIR/ozma.md"
elif (( ozma_count > 1 )); then
  {
    echo "# Контекст по OzmaDB (объединённый из ролей)"
    echo
    for role in "${ROLES[@]}"; do
      ozma_md="$REPO_DIR/roles/$role/ozma.md"
      if [[ -f "$ozma_md" ]]; then
        echo "---"
        echo "## === Роль: $role ==="
        echo
        cat "$ozma_md"
        echo
      fi
    done
  } > "$WORK_DIR/ozma.md"
  green "✓ $WORK_DIR/ozma.md (склейка из $ozma_count ролей)"
fi

# ── skills: копируем все из всех ролей в ~/.claude/skills/ ──
# Глобальная установка — /<команда> работает из любой папки.
# x=$((x+1)) вместо ((x++)) — см. комментарий выше.
mkdir -p "$CLAUDE_DIR/skills"
skill_total=0
for role in "${ROLES[@]}"; do
  src="$REPO_DIR/roles/$role/skills"
  [[ ! -d "$src" ]] && continue
  for skill_dir in "$src"/*/; do
    [[ ! -d "$skill_dir" ]] && continue
    skill_name=$(basename "$skill_dir")
    rm -rf "$CLAUDE_DIR/skills/$skill_name"
    cp -R "$skill_dir" "$CLAUDE_DIR/skills/$skill_name"
    skill_total=$((skill_total + 1))
  done
done
green "✓ ~/.claude/skills/ ($skill_total скилов, доступны из любой папки)"
echo

# ─── Шаг 3: токены и MCP ───────────────────────────────────────────────────
touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
# shellcheck disable=SC1090
source "$ENV_FILE"

ask_secret() {
  local var_name="$1" hint="$2" hide="${3:-yes}"
  local current="${!var_name:-}"
  [[ -n "$current" ]] && return 0
  echo; yellow "▸ $var_name"; echo "  $hint"
  if [[ "$hide" == "yes" ]]; then
    read -r -s -p "  значение: " value; echo
  else
    read -r -p "  значение: " value
  fi
  printf "%s=%q\n" "$var_name" "$value" >> "$ENV_FILE"
  export "$var_name=$value"
}

# ─── Опциональный авто-фетч общих токенов из Notion ────────────────────────
# Использует Claude Code CLI + подключённый Notion MCP. Если у пользователя
# нет доступа к Notion-странице 🔐 Токены MCP — fetch провалится и мы
# тихо упадём в ручной ввод. Токены НИКОГДА не уходят в git: они идут
# Notion → claude CLI → переменные bash → ~/.gogol-ai/.env (chmod 600).
NOTION_TOKENS_PAGE_URL="https://www.notion.so/35e612c762af8159910dce94293341af"

fetch_tokens_from_notion() {
  command -v claude >/dev/null 2>&1 || return 1

  local prompt="Сходи в Notion по URL ${NOTION_TOKENS_PAGE_URL}. На этой странице ('🔐 Токены MCP') найди значения переменных в кодовых блоках. Выведи строго в формате (одна переменная на строку, без пробелов вокруг =, без кавычек, без markdown, без любого другого текста):

OZMA_BEARER=значение
OZMA_CLIENT_SECRET=значение
SITE_BEARER=значение
UNISENDER_TOKEN=значение
TELEGRAM_BEARER=значение

Если какого-то значения нет (или это плейсхолдер TODO) — пропусти его строку. Никаких пояснений, только эти строки."

  yellow "  Запрашиваю токены через Claude Code + Notion MCP (это займёт ~10-30 сек)..."
  local output
  if ! output=$(claude -p "$prompt" --output-format text 2>/dev/null); then
    return 1
  fi

  local found=0 n v
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^([A-Z_]+)=(.+)$ ]]; then
      n="${BASH_REMATCH[1]}"; v="${BASH_REMATCH[2]}"
      # Игнорируем чужие/мусорные переменные
      case "$n" in
        OZMA_BEARER|OZMA_CLIENT_SECRET|SITE_BEARER|UNISENDER_TOKEN|TELEGRAM_BEARER) ;;
        *) continue ;;
      esac
      # Trim whitespace
      v="${v#"${v%%[![:space:]]*}"}"
      v="${v%"${v##*[![:space:]]}"}"
      # Skip placeholders
      [[ "$v" == "значение" || "$v" == *"TODO"* ]] && continue
      # Skip if уже в кэше
      [[ -n "${!n:-}" ]] && continue
      printf "%s=%q\n" "$n" "$v" >> "$ENV_FILE"
      export "$n=$v"
      green "  ✓ $n (из Notion)"
      found=$((found + 1))
    fi
  done <<< "$output"

  (( found > 0 ))
}

# Спрашиваем: подтянуть автоматически или нет.
# Только если каких-то общих токенов ещё нет в кэше.
need_general=0
for v in OZMA_BEARER OZMA_CLIENT_SECRET SITE_BEARER UNISENDER_TOKEN TELEGRAM_BEARER; do
  [[ -z "${!v:-}" ]] && need_general=1
done

if (( need_general )) && command -v claude >/dev/null 2>&1; then
  echo
  yellow "▸ Можно подтянуть общие токены MCP из Notion автоматически."
  echo "  Нужен Claude Code с подключённым Notion MCP (Settings → Connectors → Notion)."
  echo "  Если откажешься — спрошу токены вручную."
  read -r -p "  Подтянуть из Notion? [Y/n]: " ans
  if [[ ! "$ans" =~ ^[Nn] ]]; then
    if fetch_tokens_from_notion; then
      green "  Токены сохранены в $ENV_FILE."
    else
      yellow "  Не получилось (нет доступа к Notion или Notion MCP не подключён). Спрошу вручную."
    fi
  fi
fi

declare -A MCP_NEEDED
for role in "${ROLES[@]}"; do
  mcp_json="$REPO_DIR/roles/$role/.mcp.json"
  if [[ ! -f "$mcp_json" ]]; then
    yellow "  ⚠ нет $mcp_json — пропускаю роль $role в MCP-фазе"
    continue
  fi
  # Парсим список MCP-серверов из роли. Если python падает — показываем ошибку.
  parsed_output=""
  if ! parsed_output=$(python3 -c "
import json, sys
try:
    d = json.load(open('$mcp_json'))
    print('\n'.join((d.get('mcpServers') or {}).keys()))
except Exception as e:
    print(f'PARSE_ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1); then
    red "  ✗ Не смог разобрать $mcp_json:"
    echo "    $parsed_output"
    continue
  fi
  while IFS= read -r name; do
    [[ -n "$name" ]] && MCP_NEEDED["$name"]=1
  done <<< "$parsed_output"
done

if [[ ${#MCP_NEEDED[@]} -eq 0 ]]; then
  yellow "У выбранных ролей нет MCP-серверов."
else
  bold "🔌 Настройка MCP-серверов (${#MCP_NEEDED[@]} шт.): ${!MCP_NEEDED[*]}"
  echo "Токены брать из: $NOTION_TOKENS_HINT"
fi

register_mcp_desktop() {
  local name="$1" cfg="$2"
  mkdir -p "$(dirname "$CLAUDE_DESKTOP_CFG")"
  [[ ! -f "$CLAUDE_DESKTOP_CFG" ]] && echo '{"mcpServers":{}}' > "$CLAUDE_DESKTOP_CFG"
  python3 - "$CLAUDE_DESKTOP_CFG" "$name" "$cfg" <<'PYEOF'
import json, sys
path, name, cfg = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
data.setdefault("mcpServers", {})[name] = json.loads(cfg)
json.dump(data, open(path, "w"), indent=2, ensure_ascii=False)
PYEOF
}
register_mcp_code() {
  local name="$1"; shift
  claude mcp remove --scope user "$name" >/dev/null 2>&1 || true
  claude mcp add --scope user "$name" -- "$@"
}

setup_ozma() {
  ask_secret OZMA_BEARER "Bearer-токен Ozma MCP (из $NOTION_TOKENS_HINT)"
  ask_secret OZMA_CLIENT_SECRET "Client Secret Ozma (из $NOTION_TOKENS_HINT)"
  ask_secret OZMA_USERNAME "Твой логин в Ozma (email)" no
  ask_secret OZMA_PASSWORD "Твой пароль в Ozma"
  local url="https://ozmamcp.gogol.school/mcp"
  local cfg=$(python3 -c "
import json
print(json.dumps({'command':'npx','args':['-y','mcp-remote','$url',
  '--header','Authorization: Bearer $OZMA_BEARER',
  '--header','X-Ozma-URL: https://ozma.gogol.school/api/',
  '--header','X-Ozma-Auth-URL: https://ozma.gogol.school/auth/realms/ozma/protocol/openid-connect/token',
  '--header','X-Ozma-Client-ID: ozmadb',
  '--header','X-Ozma-Client-Secret: $OZMA_CLIENT_SECRET',
  '--header','X-Ozma-Username: $OZMA_USERNAME',
  '--header','X-Ozma-Password: $OZMA_PASSWORD']}))")
  register_mcp_desktop "ozma" "$cfg"
  register_mcp_code "ozma" npx -y mcp-remote "$url" \
    --header "Authorization: Bearer $OZMA_BEARER" \
    --header "X-Ozma-URL: https://ozma.gogol.school/api/" \
    --header "X-Ozma-Auth-URL: https://ozma.gogol.school/auth/realms/ozma/protocol/openid-connect/token" \
    --header "X-Ozma-Client-ID: ozmadb" \
    --header "X-Ozma-Client-Secret: $OZMA_CLIENT_SECRET" \
    --header "X-Ozma-Username: $OZMA_USERNAME" \
    --header "X-Ozma-Password: $OZMA_PASSWORD"
  green "✓ ozma"
}
setup_gogol_site() {
  ask_secret SITE_BEARER "Bearer-токен gogol-site-remote"
  local url="https://ozma.gogol.school/site_mcp/mcp"
  local cfg=$(python3 -c "
import json
print(json.dumps({'command':'npx','args':['-y','mcp-remote','$url','--header','Authorization: Bearer $SITE_BEARER']}))")
  register_mcp_desktop "gogol-site-remote" "$cfg"
  register_mcp_code "gogol-site-remote" npx -y mcp-remote "$url" \
    --header "Authorization: Bearer $SITE_BEARER"
  green "✓ gogol-site-remote"
}
setup_unisender() {
  ask_secret UNISENDER_TOKEN "Токен unisender"
  local url="http://ozma.gogol.school:8002/mcp/?token=$UNISENDER_TOKEN"
  local cfg=$(python3 -c "
import json
print(json.dumps({'command':'npx','args':['-y','mcp-remote','$url','--allow-http']}))")
  register_mcp_desktop "unisender" "$cfg"
  register_mcp_code "unisender" npx -y mcp-remote "$url" --allow-http
  green "✓ unisender"
}
setup_telegram() {
  ask_secret TELEGRAM_BEARER "Bearer-токен Telegram MCP"
  local url="http://ozma.gogol.school:8001/mcp"
  local cfg=$(python3 -c "
import json
print(json.dumps({'command':'npx','args':['-y','mcp-remote','$url','--allow-http','--header','Authorization: Bearer $TELEGRAM_BEARER']}))")
  register_mcp_desktop "telegram_ozma_mcp" "$cfg"
  register_mcp_code "telegram_ozma_mcp" npx -y mcp-remote "$url" --allow-http \
    --header "Authorization: Bearer $TELEGRAM_BEARER"
  green "✓ telegram_ozma_mcp"
}
GOOGLE_INSTALLED=""
setup_google() {
  [[ -n "$GOOGLE_INSTALLED" ]] && return 0
  if [[ ! -f "$GOOGLE_TOKEN_PATH" ]]; then
    yellow "▸ Нужен google_token.json"
    echo "  Положи в $GOOGLE_TOKEN_PATH и запусти снова."
    yellow "  Пропускаю google_sheets и google_docs."
    return 0
  fi
  ask_secret GOOGLE_SERVICE_ACCOUNT_EMAIL "Email сервис-аккаунта" no
  local sheets_cfg=$(python3 -c "
import json
print(json.dumps({'command':'npx','args':['-y','mcp-google-sheets'],
  'env':{'GOOGLE_SHEETS_CLIENT_ID':'$GOOGLE_SERVICE_ACCOUNT_EMAIL','TOKEN_PATH':'$GOOGLE_TOKEN_PATH'}}))")
  local docs_cfg=$(python3 -c "
import json
print(json.dumps({'command':'npx','args':['-y','@a-bonus/google-docs-mcp'],
  'env':{'GOOGLE_CLIENT_ID':'$GOOGLE_SERVICE_ACCOUNT_EMAIL','TOKEN_PATH':'$GOOGLE_TOKEN_PATH'}}))")
  register_mcp_desktop "google_sheets" "$sheets_cfg"
  register_mcp_desktop "google_docs" "$docs_cfg"
  GOOGLE_SHEETS_CLIENT_ID="$GOOGLE_SERVICE_ACCOUNT_EMAIL" TOKEN_PATH="$GOOGLE_TOKEN_PATH" \
    claude mcp add --scope user google_sheets -- npx -y mcp-google-sheets || true
  GOOGLE_CLIENT_ID="$GOOGLE_SERVICE_ACCOUNT_EMAIL" TOKEN_PATH="$GOOGLE_TOKEN_PATH" \
    claude mcp add --scope user google_docs -- npx -y @a-bonus/google-docs-mcp || true
  green "✓ google_sheets + google_docs"
  GOOGLE_INSTALLED=1
}

for mcp_name in "${!MCP_NEEDED[@]}"; do
  case "$mcp_name" in
    ozma)                                          setup_ozma ;;
    gogol-school-site|gogol-site-remote)           setup_gogol_site ;;
    unisender)                                     setup_unisender ;;
    telegram|telegram_ozma_mcp)                    setup_telegram ;;
    google-sheets|google-drive|google_sheets|google_docs) setup_google ;;
    notion)
      yellow "▸ notion — встроен в Claude Desktop"
      echo "  Подключи вручную: Claude Desktop → Settings → Connectors → Notion" ;;
    *) yellow "▸ $mcp_name — неизвестный MCP, пропускаю" ;;
  esac
done

echo
bold "✅ Готово!"
echo
echo "Рабочая папка: ~/gogol-ai/$FOLDER_NAME/"
echo "Активные роли: ${ROLES[*]}"
echo
echo "Как работать:"
echo "  cd ~/gogol-ai/$FOLDER_NAME"
echo "  claude"
echo
echo "Claude увидит CLAUDE.md со всеми твоими ролями + общий контекст из ~/.claude/."
echo "Скилы установлены глобально в ~/.claude/skills/ и работают из любой папки."
echo "Доступные скилы — '/' в чате."
echo
echo "Добавить ещё роль: запусти эту же команду со всем списком ролей."
echo "Пример: bash install.sh doc-fin-ops analyst marketing-assistant"
echo
echo "Перезапусти Claude Desktop после установки (Cmd+Q → открыть)."
