# Gogol School AI — установщик ролей для Windows (PowerShell).
#
# Использование:
#   iwr -useb https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.ps1 -OutFile $env:TEMP\install.ps1
#   & $env:TEMP\install.ps1 -Roles doc-fin-ops,analyst
#   & $env:TEMP\install.ps1 -Roles doc-fin-ops,analyst -Folder bella
#
# По умолчанию создаётся одна объединённая рабочая папка ~\gogol-ai\work\
# с CLAUDE.md и skills всех выбранных ролей.

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string[]]$Roles,

    [string]$Folder = "work"
)

$ErrorActionPreference = "Stop"

# ─── Константы ─────────────────────────────────────────────────────────────
$RepoUrl           = "https://github.com/gogolschool/gogol-school-ai.git"
$GogolDir          = Join-Path $env:USERPROFILE ".gogol-ai"
$RepoDir           = Join-Path $GogolDir "repo"
$EnvFile           = Join-Path $GogolDir ".env"
$GoogleTokenPath   = Join-Path $GogolDir "google_token.json"
$RolesDir          = Join-Path $env:USERPROFILE "gogol-ai"
$WorkDir           = Join-Path $RolesDir $Folder
$ClaudeDir         = Join-Path $env:USERPROFILE ".claude"
$ClaudeDesktopCfg  = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
$NotionTokensHint  = "Notion -> Токены MCP"

function Write-Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "✗ $msg" -ForegroundColor Red }
function Write-Warn($msg) { Write-Host "▸ $msg" -ForegroundColor Yellow }
function Write-Step($msg) { Write-Host "`n$msg" -ForegroundColor Cyan }

Write-Host "`n🚀 Установка ролей в ~\gogol-ai\$Folder\: $($Roles -join ', ')" -ForegroundColor Magenta

# ─── Проверка зависимостей ────────────────────────────────────────────────
function Test-Cmd($cmd, $hint) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Write-Ok $cmd; return $true }
    Write-Fail "Не найден: $cmd. $hint"; return $false
}

Write-Step "📋 Проверка зависимостей"
$missing = $false
if (-not (Test-Cmd "node"   "winget install OpenJS.NodeJS")) { $missing = $true }
if (-not (Test-Cmd "npm"    "идёт с node"))                    { $missing = $true }
if (-not (Test-Cmd "git"    "winget install Git.Git"))         { $missing = $true }
if (-not (Test-Cmd "claude" "npm install -g @anthropic-ai/claude-code")) { $missing = $true }
# python в Windows-версии не нужен — JSON парсится нативным ConvertFrom-Json.
if ($missing) { Write-Fail "Установи отсутствующие компоненты."; exit 1 }

# ─── Клонирование/обновление репо ─────────────────────────────────────────
Write-Step "📥 Получение конфигурации"
New-Item -ItemType Directory -Path $GogolDir -Force | Out-Null
if (Test-Path (Join-Path $RepoDir ".git")) {
    git -C $RepoDir pull --quiet
    Write-Ok "Репо обновлён"
} else {
    git clone --quiet $RepoUrl $RepoDir
    Write-Ok "Репо скачан"
}

foreach ($role in $Roles) {
    if (-not (Test-Path (Join-Path $RepoDir "roles\$role"))) {
        Write-Fail "Роль '$role' не найдена."; exit 1
    }
}

# ─── Шаг 1: глобальный shared в ~\.claude\ ────────────────────────────────
Write-Step "📝 Установка общего контекста (~\.claude\)"
New-Item -ItemType Directory -Path "$ClaudeDir\knowledge" -Force | Out-Null

Copy-Item -Path (Join-Path $RepoDir "shared\CLAUDE.md") -Destination (Join-Path $ClaudeDir "CLAUDE.md") -Force
Write-Ok "$ClaudeDir\CLAUDE.md (общий контекст)"

Copy-Item -Path (Join-Path $RepoDir "shared\knowledge\*") -Destination "$ClaudeDir\knowledge" -Recurse -Force
Write-Ok "$ClaudeDir\knowledge\"

Get-ChildItem -Path (Join-Path $RepoDir "shared") -Filter "*.md" -File | Where-Object { $_.Name -ne "CLAUDE.md" } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $ClaudeDir $_.Name) -Force
    Write-Ok "$ClaudeDir\$($_.Name)"
}

# ─── Шаг 2: объединённая рабочая папка ────────────────────────────────────
Write-Step "📁 Сборка рабочей папки $WorkDir"

if (Test-Path "$WorkDir\.claude\skills") { Remove-Item -Recurse -Force "$WorkDir\.claude\skills" }
Remove-Item -Force "$WorkDir\CLAUDE.md","$WorkDir\ozma.md" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path "$WorkDir\.claude\skills" -Force | Out-Null

# CLAUDE.md: склейка всех ролей
$claudeMdParts = @()
$claudeMdParts += "# Рабочая папка: $Folder"
$claudeMdParts += ""
$claudeMdParts += "Активные роли: **$($Roles -join ', ')**"
$claudeMdParts += ""
$claudeMdParts += "Общий контекст про Gogol School — в ``~\.claude\CLAUDE.md`` (загружается автоматически). Ниже — специфика выбранных ролей."
$claudeMdParts += ""
foreach ($role in $Roles) {
    $rMd = Join-Path $RepoDir "roles\$role\CLAUDE.md"
    if (Test-Path $rMd) {
        $claudeMdParts += "---"
        $claudeMdParts += ""
        $claudeMdParts += "# === Роль: $role ==="
        $claudeMdParts += ""
        $claudeMdParts += (Get-Content $rMd -Raw)
        $claudeMdParts += ""
    }
}
($claudeMdParts -join "`n") | Set-Content -Path (Join-Path $WorkDir "CLAUDE.md") -NoNewline
Write-Ok "$WorkDir\CLAUDE.md (склейка $($Roles.Count) ролей)"

# ozma.md: склейка
$ozmaSrcs = @()
foreach ($role in $Roles) {
    $o = Join-Path $RepoDir "roles\$role\ozma.md"
    if (Test-Path $o) { $ozmaSrcs += @{role=$role; path=$o} }
}
if ($ozmaSrcs.Count -eq 1) {
    Copy-Item -Path $ozmaSrcs[0].path -Destination (Join-Path $WorkDir "ozma.md") -Force
    Write-Ok "$WorkDir\ozma.md"
} elseif ($ozmaSrcs.Count -gt 1) {
    $parts = @("# Контекст по OzmaDB (объединённый из ролей)", "")
    foreach ($s in $ozmaSrcs) {
        $parts += "---"
        $parts += "## === Роль: $($s.role) ==="
        $parts += ""
        $parts += (Get-Content $s.path -Raw)
        $parts += ""
    }
    ($parts -join "`n") | Set-Content -Path (Join-Path $WorkDir "ozma.md") -NoNewline
    Write-Ok "$WorkDir\ozma.md (склейка $($ozmaSrcs.Count) ролей)"
}

# Skills: объединение из всех ролей
$skillTotal = 0
foreach ($role in $Roles) {
    $src = Join-Path $RepoDir "roles\$role\skills"
    if (-not (Test-Path $src)) { continue }
    Get-ChildItem -Path $src -Directory | ForEach-Object {
        $dst = Join-Path "$WorkDir\.claude\skills" $_.Name
        Copy-Item -Path $_.FullName -Destination $dst -Recurse -Force
        $skillTotal++
    }
}
Write-Ok "$WorkDir\.claude\skills\ ($skillTotal скилов)"

# ─── Шаг 3: токены и MCP ──────────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile -Force | Out-Null }
$Secrets = @{}
Get-Content $EnvFile -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*"?(.*?)"?\s*$') { $Secrets[$matches[1]] = $matches[2] }
}

function Get-Secret([string]$Name, [string]$Hint, [bool]$Hide = $true) {
    if ($Secrets.ContainsKey($Name) -and $Secrets[$Name]) { return $Secrets[$Name] }
    Write-Host ""; Write-Warn $Name; Write-Host "  $Hint" -ForegroundColor Gray
    if ($Hide) {
        $secure = Read-Host "  значение" -AsSecureString
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $value = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    } else { $value = Read-Host "  значение" }
    $Secrets[$Name] = $value
    "`n$Name=`"$value`"" | Add-Content -Path $EnvFile
    return $value
}

# ─── Опциональный авто-фетч общих токенов из Notion ───────────────────────
# Использует Claude Code CLI + Notion MCP. Токены НИКОГДА не уходят в git:
# Notion -> claude CLI -> переменные -> .env (только на этой машине).
$NotionTokensPageUrl = "https://www.notion.so/35e612c762af8159910dce94293341af"

function Fetch-TokensFromNotion {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { return $false }

    $prompt = @"
Сходи в Notion по URL $NotionTokensPageUrl. На этой странице ('Токены MCP') найди значения переменных в кодовых блоках. Выведи строго в формате (одна переменная на строку, без пробелов вокруг =, без кавычек, без markdown, без любого другого текста):

OZMA_BEARER=значение
OZMA_CLIENT_SECRET=значение
SITE_BEARER=значение
UNISENDER_TOKEN=значение
TELEGRAM_BEARER=значение

Если какого-то значения нет (или это плейсхолдер TODO) — пропусти его строку. Никаких пояснений, только эти строки.
"@

    Write-Warn "Запрашиваю токены через Claude Code + Notion MCP (~10-30 сек)..."
    $output = $null
    try {
        $output = & claude -p $prompt --output-format text 2>$null
    } catch { return $false }
    if (-not $output) { return $false }

    $found = 0
    $allowedNames = @("OZMA_BEARER","OZMA_CLIENT_SECRET","SITE_BEARER","UNISENDER_TOKEN","TELEGRAM_BEARER")
    foreach ($line in ($output -split "`n")) {
        if ($line -match '^([A-Z_]+)=(.+)$') {
            $n = $matches[1].Trim()
            $v = $matches[2].Trim()
            if ($allowedNames -notcontains $n) { continue }
            if ($v -eq "значение" -or $v -like "*TODO*") { continue }
            if ($Secrets.ContainsKey($n) -and $Secrets[$n]) { continue }
            $Secrets[$n] = $v
            "`n$n=`"$v`"" | Add-Content -Path $EnvFile
            Write-Ok "$n (из Notion)"
            $found++
        }
    }
    return ($found -gt 0)
}

$needGeneral = $false
foreach ($v in @("OZMA_BEARER","OZMA_CLIENT_SECRET","SITE_BEARER","UNISENDER_TOKEN","TELEGRAM_BEARER")) {
    if (-not ($Secrets.ContainsKey($v) -and $Secrets[$v])) { $needGeneral = $true; break }
}
if ($needGeneral -and (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Warn "Можно подтянуть общие токены MCP из Notion автоматически."
    Write-Host "  Нужен Claude Code с подключённым Notion MCP." -ForegroundColor Gray
    $ans = Read-Host "  Подтянуть из Notion? [Y/n]"
    if ($ans -notmatch '^[Nn]') {
        if (Fetch-TokensFromNotion) {
            Write-Ok "Токены сохранены в $EnvFile."
        } else {
            Write-Warn "Не получилось (нет доступа к Notion или Notion MCP не подключён). Спрошу вручную."
        }
    }
}

# Объединение MCP по всем ролям
$mcpNeeded = @{}
foreach ($role in $Roles) {
    $mcpJson = Join-Path $RepoDir "roles\$role\.mcp.json"
    if (-not (Test-Path $mcpJson)) {
        Write-Warn "  нет $mcpJson — пропускаю роль $role в MCP-фазе"
        continue
    }
    try {
        $d = Get-Content $mcpJson -Raw | ConvertFrom-Json
    } catch {
        Write-Fail "  Не смог разобрать $mcpJson — $($_.Exception.Message)"
        continue
    }
    if ($d.mcpServers) {
        foreach ($p in $d.mcpServers.PSObject.Properties.Name) { $mcpNeeded[$p] = $true }
    }
}

if ($mcpNeeded.Count -eq 0) {
    Write-Warn "У выбранных ролей нет MCP-серверов."
} else {
    $names = ($mcpNeeded.Keys -join ' ')
    Write-Step "🔌 Настройка MCP-серверов ($($mcpNeeded.Count) шт.): $names"
    Write-Host "Токены брать из: $NotionTokensHint" -ForegroundColor Gray
}

function Register-DesktopMcp([string]$Name, [hashtable]$Config) {
    $dir = Split-Path $ClaudeDesktopCfg -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (-not (Test-Path $ClaudeDesktopCfg)) { '{"mcpServers":{}}' | Set-Content $ClaudeDesktopCfg }
    $cfg = Get-Content $ClaudeDesktopCfg -Raw | ConvertFrom-Json
    if (-not $cfg.mcpServers) { $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force }
    $cfg.mcpServers | Add-Member -NotePropertyName $Name -NotePropertyValue $Config -Force
    $cfg | ConvertTo-Json -Depth 10 | Set-Content $ClaudeDesktopCfg
}

function Register-CodeMcp([string]$Name, [string[]]$Args) {
    & claude mcp remove --scope user $Name 2>$null | Out-Null
    & claude mcp add --scope user $Name -- @Args
}

function Install-Ozma {
    $bearer       = Get-Secret "OZMA_BEARER"        "Bearer-токен Ozma MCP"
    $clientSecret = Get-Secret "OZMA_CLIENT_SECRET" "Client Secret Ozma"
    $username     = Get-Secret "OZMA_USERNAME"      "Твой логин в Ozma" $false
    $password     = Get-Secret "OZMA_PASSWORD"      "Твой пароль в Ozma"
    $url = "https://ozmamcp.gogol.school/mcp"
    $args = @("-y","mcp-remote",$url,
        "--header","Authorization: Bearer $bearer",
        "--header","X-Ozma-URL: https://ozma.gogol.school/api/",
        "--header","X-Ozma-Auth-URL: https://ozma.gogol.school/auth/realms/ozma/protocol/openid-connect/token",
        "--header","X-Ozma-Client-ID: ozmadb",
        "--header","X-Ozma-Client-Secret: $clientSecret",
        "--header","X-Ozma-Username: $username",
        "--header","X-Ozma-Password: $password")
    Register-DesktopMcp "ozma" @{ command="npx"; args=$args }
    Register-CodeMcp "ozma" (@("npx") + $args)
    Write-Ok "ozma"
}
function Install-GogolSite {
    $bearer = Get-Secret "SITE_BEARER" "Bearer-токен gogol-site-remote"
    $url = "https://ozma.gogol.school/site_mcp/mcp"
    $args = @("-y","mcp-remote",$url,"--header","Authorization: Bearer $bearer")
    Register-DesktopMcp "gogol-site-remote" @{ command="npx"; args=$args }
    Register-CodeMcp "gogol-site-remote" (@("npx") + $args)
    Write-Ok "gogol-site-remote"
}
function Install-Unisender {
    $token = Get-Secret "UNISENDER_TOKEN" "Токен unisender"
    $url = "http://ozma.gogol.school:8002/mcp/?token=$token"
    $args = @("-y","mcp-remote",$url,"--allow-http")
    Register-DesktopMcp "unisender" @{ command="npx"; args=$args }
    Register-CodeMcp "unisender" (@("npx") + $args)
    Write-Ok "unisender"
}
function Install-Telegram {
    $bearer = Get-Secret "TELEGRAM_BEARER" "Bearer-токен Telegram MCP"
    $url = "http://ozma.gogol.school:8001/mcp"
    $args = @("-y","mcp-remote",$url,"--allow-http","--header","Authorization: Bearer $bearer")
    Register-DesktopMcp "telegram_ozma_mcp" @{ command="npx"; args=$args }
    Register-CodeMcp "telegram_ozma_mcp" (@("npx") + $args)
    Write-Ok "telegram_ozma_mcp"
}
$script:GoogleInstalled = $false
function Install-Google {
    if ($script:GoogleInstalled) { return }
    if (-not (Test-Path $GoogleTokenPath)) {
        Write-Warn "Нужен google_token.json"
        Write-Host "  Положи в $GoogleTokenPath и запусти снова." -ForegroundColor Gray
        Write-Warn "Пропускаю google_sheets и google_docs."
        return
    }
    $email = Get-Secret "GOOGLE_SERVICE_ACCOUNT_EMAIL" "Email сервис-аккаунта" $false
    Register-DesktopMcp "google_sheets" @{
        command="npx"; args=@("-y","mcp-google-sheets")
        env=@{ GOOGLE_SHEETS_CLIENT_ID=$email; TOKEN_PATH=$GoogleTokenPath }
    }
    Register-DesktopMcp "google_docs" @{
        command="npx"; args=@("-y","@a-bonus/google-docs-mcp")
        env=@{ GOOGLE_CLIENT_ID=$email; TOKEN_PATH=$GoogleTokenPath }
    }
    $env:GOOGLE_SHEETS_CLIENT_ID = $email; $env:TOKEN_PATH = $GoogleTokenPath
    & claude mcp remove --scope user google_sheets 2>$null | Out-Null
    & claude mcp add --scope user google_sheets -- npx -y mcp-google-sheets
    $env:GOOGLE_CLIENT_ID = $email
    & claude mcp remove --scope user google_docs 2>$null | Out-Null
    & claude mcp add --scope user google_docs -- npx -y "@a-bonus/google-docs-mcp"
    Write-Ok "google_sheets + google_docs"
    $script:GoogleInstalled = $true
}

foreach ($name in $mcpNeeded.Keys) {
    switch -Regex ($name) {
        '^ozma$'                                                   { Install-Ozma }
        '^(gogol-school-site|gogol-site-remote)$'                  { Install-GogolSite }
        '^unisender$'                                              { Install-Unisender }
        '^(telegram|telegram_ozma_mcp)$'                           { Install-Telegram }
        '^(google-sheets|google-drive|google_sheets|google_docs)$' { Install-Google }
        '^notion$' {
            Write-Warn "notion — встроен в Claude Desktop"
            Write-Host "  Подключи вручную: Settings -> Connectors -> Notion" -ForegroundColor Gray
        }
        default { Write-Warn "$name — неизвестный MCP, пропускаю" }
    }
}

Write-Host "`n✅ Готово!" -ForegroundColor Green
Write-Host ""
Write-Host "Рабочая папка: $WorkDir"
Write-Host "Активные роли: $($Roles -join ', ')"
Write-Host ""
Write-Host "Как работать:"
Write-Host "  cd $WorkDir"
Write-Host "  claude"
Write-Host ""
Write-Host "Добавить ещё роль: запусти эту же команду со всем списком ролей."
Write-Host "Перезапусти Claude Desktop после установки."
