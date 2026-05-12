# Gogol School AI — установщик роли (Windows / PowerShell).
#
# Использование:
#   iwr -useb https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.ps1 -OutFile $env:TEMP\install.ps1
#   & $env:TEMP\install.ps1 -Role doc-fin-ops
#
# Доступные роли:
#   doc-fin-ops, client-office-ops, senior-admin, marketing-assistant,
#   smm, brand-pr, product-manager, student-comms, product-assistant, analyst

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Role
)

$ErrorActionPreference = "Stop"

# ─── Константы ─────────────────────────────────────────────────────────────
$RepoUrl           = "https://github.com/gogolschool/gogol-school-ai.git"
$GogolDir          = Join-Path $env:USERPROFILE ".gogol-ai"
$RepoDir           = Join-Path $GogolDir "repo"
$EnvFile           = Join-Path $GogolDir ".env"
$GoogleTokenPath   = Join-Path $GogolDir "google_token.json"
$ClaudeDir         = Join-Path $env:USERPROFILE ".claude"
$ClaudeDesktopCfg  = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
$NotionTokensHint  = "Notion -> Токены MCP"

# ─── Цвета ─────────────────────────────────────────────────────────────────
function Write-Ok($msg)    { Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Fail($msg)  { Write-Host "✗ $msg" -ForegroundColor Red }
function Write-Warn($msg)  { Write-Host "▸ $msg" -ForegroundColor Yellow }
function Write-Step($msg)  { Write-Host "`n$msg" -ForegroundColor Cyan }

Write-Host "`n🚀 Установка AI-ассистента для роли: $Role" -ForegroundColor Magenta
Write-Host ""

# ─── Проверка зависимостей ─────────────────────────────────────────────────
function Test-Command($cmd, $hint) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-Ok "$cmd установлен"
        return $true
    }
    Write-Fail "Не найден: $cmd. $hint"
    return $false
}

Write-Step "📋 Проверка зависимостей"
$missing = $false
if (-not (Test-Command "node"   "Установи Node.js: winget install OpenJS.NodeJS")) { $missing = $true }
if (-not (Test-Command "npm"    "Идёт с node"))                                     { $missing = $true }
if (-not (Test-Command "git"    "winget install Git.Git"))                          { $missing = $true }
if (-not (Test-Command "claude" "Установи Claude Code: https://claude.com/download")) { $missing = $true }
if (-not (Test-Command "python" "Установи Python: winget install Python.Python.3.12")) { $missing = $true }
if ($missing) {
    Write-Fail "Установи отсутствующие компоненты и запусти снова."
    exit 1
}

# ─── Клонирование/обновление репо ──────────────────────────────────────────
Write-Step "📥 Получение конфигурации роли"
New-Item -ItemType Directory -Path $GogolDir -Force | Out-Null
if (Test-Path (Join-Path $RepoDir ".git")) {
    git -C $RepoDir pull --quiet
    Write-Ok "Конфигурация обновлена"
} else {
    git clone --quiet $RepoUrl $RepoDir
    Write-Ok "Конфигурация скачана"
}

$RoleDir = Join-Path $RepoDir "roles\$Role"
if (-not (Test-Path $RoleDir)) {
    Write-Fail "Роль '$Role' не найдена в репозитории."
    exit 1
}

# ─── Копирование CLAUDE.md и skills ────────────────────────────────────────
Write-Step "📝 Установка CLAUDE.md и skills"
New-Item -ItemType Directory -Path "$ClaudeDir\skills" -Force | Out-Null
New-Item -ItemType Directory -Path "$ClaudeDir\knowledge" -Force | Out-Null

$sharedClaudeMd = Join-Path $RepoDir "shared\CLAUDE.md"
$roleClaudeMd   = Join-Path $RoleDir "CLAUDE.md"
$combined       = Join-Path $ClaudeDir "CLAUDE.md"

$content = Get-Content $sharedClaudeMd -Raw
if (Test-Path $roleClaudeMd) {
    $content += "`n`n---`n`n" + (Get-Content $roleClaudeMd -Raw)
}
Set-Content -Path $combined -Value $content -NoNewline
Write-Ok "$combined обновлён"

Copy-Item -Path (Join-Path $RepoDir "shared\knowledge\*") -Destination "$ClaudeDir\knowledge" -Recurse -Force
Write-Ok "$ClaudeDir\knowledge\ обновлён"

# Копируем дополнительные shared-файлы (marketing.md и т.п.), кроме CLAUDE.md
Get-ChildItem -Path (Join-Path $RepoDir "shared") -Filter "*.md" -File | Where-Object { $_.Name -ne "CLAUDE.md" } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $ClaudeDir $_.Name) -Force
    Write-Ok "$ClaudeDir\$($_.Name) обновлён"
}

$ozmaMd = Join-Path $RoleDir "ozma.md"
if (Test-Path $ozmaMd) {
    Copy-Item -Path $ozmaMd -Destination "$ClaudeDir\ozma.md" -Force
    Write-Ok "$ClaudeDir\ozma.md обновлён"
}

$skillsSrc = Join-Path $RoleDir "skills"
if (Test-Path $skillsSrc) {
    Copy-Item -Path "$skillsSrc\*" -Destination "$ClaudeDir\skills" -Recurse -Force
    Write-Ok "$ClaudeDir\skills\ обновлены"
}

# ─── Загрузка кэша секретов ────────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile -Force | Out-Null }
$Secrets = @{}
Get-Content $EnvFile -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*"?(.*?)"?\s*$') {
        $Secrets[$matches[1]] = $matches[2]
    }
}

function Get-Secret([string]$Name, [string]$Hint, [bool]$Hide = $true) {
    if ($Secrets.ContainsKey($Name) -and $Secrets[$Name]) {
        return $Secrets[$Name]
    }
    Write-Host ""
    Write-Warn $Name
    Write-Host "  $Hint" -ForegroundColor Gray
    if ($Hide) {
        $secure = Read-Host "  значение" -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $value = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    } else {
        $value = Read-Host "  значение"
    }
    $Secrets[$Name] = $value
    "`n$Name=`"$value`"" | Add-Content -Path $EnvFile
    return $value
}

# ─── Чтение списка MCP роли ────────────────────────────────────────────────
$McpJsonPath = Join-Path $RoleDir ".mcp.json"
if (-not (Test-Path $McpJsonPath)) {
    Write-Warn "Для роли не задан список MCP — пропускаю шаг настройки серверов."
    exit 0
}
$mcpData = Get-Content $McpJsonPath -Raw | ConvertFrom-Json
$mcpNames = @()
if ($mcpData.mcpServers) {
    $mcpNames = $mcpData.mcpServers.PSObject.Properties.Name
}

if ($mcpNames.Count -eq 0) {
    Write-Warn "Список MCP пуст — пропускаю настройку серверов."
    exit 0
}

Write-Step "🔌 Настройка MCP-серверов"
Write-Host "Откуда брать токены: $NotionTokensHint" -ForegroundColor Gray

# ─── Регистрация MCP в Claude Desktop ──────────────────────────────────────
function Register-DesktopMcp([string]$Name, [hashtable]$Config) {
    $dir = Split-Path $ClaudeDesktopCfg -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (-not (Test-Path $ClaudeDesktopCfg)) {
        '{"mcpServers":{}}' | Set-Content $ClaudeDesktopCfg
    }
    $cfg = Get-Content $ClaudeDesktopCfg -Raw | ConvertFrom-Json
    if (-not $cfg.mcpServers) { $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force }
    $cfg.mcpServers | Add-Member -NotePropertyName $Name -NotePropertyValue $Config -Force
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $ClaudeDesktopCfg
}

function Register-CodeMcp([string]$Name, [string[]]$Args) {
    & claude mcp remove --scope user $Name 2>$null | Out-Null
    & claude mcp add --scope user $Name -- @Args
}

# ─── Установка MCP ─────────────────────────────────────────────────────────
function Install-Ozma {
    $bearer        = Get-Secret "OZMA_BEARER"        "Bearer-токен Ozma MCP (из $NotionTokensHint)"
    $clientSecret  = Get-Secret "OZMA_CLIENT_SECRET" "Client Secret Ozma (из $NotionTokensHint)"
    $username      = Get-Secret "OZMA_USERNAME"      "Твой логин в Ozma (email)" $false
    $password      = Get-Secret "OZMA_PASSWORD"      "Твой пароль в Ozma"

    $url = "https://ozmamcp.gogol.school/mcp"
    $args = @(
        "-y","mcp-remote",$url,
        "--header","Authorization: Bearer $bearer",
        "--header","X-Ozma-URL: https://ozma.gogol.school/api/",
        "--header","X-Ozma-Auth-URL: https://ozma.gogol.school/auth/realms/ozma/protocol/openid-connect/token",
        "--header","X-Ozma-Client-ID: ozmadb",
        "--header","X-Ozma-Client-Secret: $clientSecret",
        "--header","X-Ozma-Username: $username",
        "--header","X-Ozma-Password: $password"
    )
    Register-DesktopMcp "ozma" @{ command = "npx"; args = $args }
    Register-CodeMcp "ozma" (@("npx") + $args)
    Write-Ok "ozma подключён"
}

function Install-GogolSite {
    $bearer = Get-Secret "SITE_BEARER" "Bearer-токен сайта (gogol-site-remote, из $NotionTokensHint)"
    $url = "https://ozma.gogol.school/site_mcp/mcp"
    $args = @("-y","mcp-remote",$url,"--header","Authorization: Bearer $bearer")
    Register-DesktopMcp "gogol-site-remote" @{ command = "npx"; args = $args }
    Register-CodeMcp "gogol-site-remote" (@("npx") + $args)
    Write-Ok "gogol-site-remote подключён"
}

function Install-Unisender {
    $token = Get-Secret "UNISENDER_TOKEN" "Токен unisender (из $NotionTokensHint)"
    $url = "http://ozma.gogol.school:8002/mcp/?token=$token"
    $args = @("-y","mcp-remote",$url,"--allow-http")
    Register-DesktopMcp "unisender" @{ command = "npx"; args = $args }
    Register-CodeMcp "unisender" (@("npx") + $args)
    Write-Ok "unisender подключён"
}

function Install-Telegram {
    $bearer = Get-Secret "TELEGRAM_BEARER" "Bearer-токен Telegram MCP (из $NotionTokensHint)"
    $url = "http://ozma.gogol.school:8001/mcp"
    $args = @("-y","mcp-remote",$url,"--allow-http","--header","Authorization: Bearer $bearer")
    Register-DesktopMcp "telegram_ozma_mcp" @{ command = "npx"; args = $args }
    Register-CodeMcp "telegram_ozma_mcp" (@("npx") + $args)
    Write-Ok "telegram_ozma_mcp подключён"
}

$script:GoogleInstalled = $false
function Install-Google {
    if ($script:GoogleInstalled) { return }
    if (-not (Test-Path $GoogleTokenPath)) {
        Write-Warn "Нужен google_token.json (сервис-аккаунт Google)"
        Write-Host "  Инструкция: $NotionTokensHint -> раздел Google Sheets/Docs" -ForegroundColor Gray
        Write-Host "  Положи файл в $GoogleTokenPath и запусти install.ps1 снова." -ForegroundColor Gray
        Write-Warn "Пропускаю google_sheets и google_docs."
        return
    }
    $email = Get-Secret "GOOGLE_SERVICE_ACCOUNT_EMAIL" "Email сервис-аккаунта (xxx@yyy.iam.gserviceaccount.com)" $false

    Register-DesktopMcp "google_sheets" @{
        command = "npx"; args = @("-y","mcp-google-sheets")
        env = @{ GOOGLE_SHEETS_CLIENT_ID = $email; TOKEN_PATH = $GoogleTokenPath }
    }
    Register-DesktopMcp "google_docs" @{
        command = "npx"; args = @("-y","@a-bonus/google-docs-mcp")
        env = @{ GOOGLE_CLIENT_ID = $email; TOKEN_PATH = $GoogleTokenPath }
    }

    $env:GOOGLE_SHEETS_CLIENT_ID = $email
    $env:TOKEN_PATH = $GoogleTokenPath
    & claude mcp remove --scope user google_sheets 2>$null | Out-Null
    & claude mcp add --scope user google_sheets -- npx -y mcp-google-sheets

    $env:GOOGLE_CLIENT_ID = $email
    & claude mcp remove --scope user google_docs 2>$null | Out-Null
    & claude mcp add --scope user google_docs -- npx -y "@a-bonus/google-docs-mcp"

    Write-Ok "google_sheets и google_docs подключены"
    $script:GoogleInstalled = $true
}

foreach ($name in $mcpNames) {
    switch -Regex ($name) {
        '^ozma$'                                 { Install-Ozma }
        '^(gogol-school-site|gogol-site-remote)$' { Install-GogolSite }
        '^unisender$'                            { Install-Unisender }
        '^(telegram|telegram_ozma_mcp)$'         { Install-Telegram }
        '^(google-sheets|google-drive|google_sheets|google_docs)$' { Install-Google }
        '^notion$' {
            Write-Warn "notion — встроенная интеграция Claude Desktop"
            Write-Host "  Подключается вручную: Claude Desktop -> Settings -> Connectors -> Notion" -ForegroundColor Gray
        }
        default {
            Write-Warn "$name — неизвестный MCP, пропускаю"
        }
    }
}

Write-Host ""
Write-Host "✅ Готово!" -ForegroundColor Green
Write-Host ""
Write-Host "Перезапусти Claude Desktop и Claude Code."
Write-Host "Проверь: в Claude Code напиши '/' — должны появиться скилы роли."
Write-Host ""
Write-Host "Секреты сохранены в: $EnvFile"
Write-Host "Конфиг роли: $RoleDir"
Write-Host "Обновить позже: запусти install.ps1 снова."
