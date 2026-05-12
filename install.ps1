# Gogol School AI — установщик ролей для Windows (PowerShell, мульти-роль).
#
# Использование:
#   iwr -useb https://raw.githubusercontent.com/gogolschool/gogol-school-ai/main/install.ps1 -OutFile $env:TEMP\install.ps1
#   & $env:TEMP\install.ps1 -Roles doc-fin-ops,analyst
#
# Доступные роли:
#   doc-fin-ops, client-office-ops, senior-admin, marketing-assistant,
#   smm, brand-pr, product-manager, student-comms, product-assistant, analyst

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string[]]$Roles
)

$ErrorActionPreference = "Stop"

# ─── Константы ─────────────────────────────────────────────────────────────
$RepoUrl           = "https://github.com/gogolschool/gogol-school-ai.git"
$GogolDir          = Join-Path $env:USERPROFILE ".gogol-ai"
$RepoDir           = Join-Path $GogolDir "repo"
$EnvFile           = Join-Path $GogolDir ".env"
$GoogleTokenPath   = Join-Path $GogolDir "google_token.json"
$RolesDir          = Join-Path $env:USERPROFILE "gogol-ai"
$ClaudeDir         = Join-Path $env:USERPROFILE ".claude"
$ClaudeDesktopCfg  = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
$NotionTokensHint  = "Notion -> Токены MCP"

function Write-Ok($msg)   { Write-Host "✓ $msg" -ForegroundColor Green }
function Write-Fail($msg) { Write-Host "✗ $msg" -ForegroundColor Red }
function Write-Warn($msg) { Write-Host "▸ $msg" -ForegroundColor Yellow }
function Write-Step($msg) { Write-Host "`n$msg" -ForegroundColor Cyan }

Write-Host "`n🚀 Установка ролей: $($Roles -join ', ')" -ForegroundColor Magenta

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
if (-not (Test-Cmd "claude" "https://claude.com/download"))    { $missing = $true }
if (-not (Test-Cmd "python" "winget install Python.Python.3.12")) { $missing = $true }
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
    $rDir = Join-Path $RepoDir "roles\$role"
    if (-not (Test-Path $rDir)) { Write-Fail "Роль '$role' не найдена."; exit 1 }
}

# ─── Шаг 1: глобальный shared в ~\.claude\ ────────────────────────────────
Write-Step "📝 Установка общего контекста (~\.claude\)"
New-Item -ItemType Directory -Path "$ClaudeDir\knowledge" -Force | Out-Null

Copy-Item -Path (Join-Path $RepoDir "shared\CLAUDE.md") -Destination (Join-Path $ClaudeDir "CLAUDE.md") -Force
Write-Ok "$ClaudeDir\CLAUDE.md"

Copy-Item -Path (Join-Path $RepoDir "shared\knowledge\*") -Destination "$ClaudeDir\knowledge" -Recurse -Force
Write-Ok "$ClaudeDir\knowledge\"

Get-ChildItem -Path (Join-Path $RepoDir "shared") -Filter "*.md" -File | Where-Object { $_.Name -ne "CLAUDE.md" } | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $ClaudeDir $_.Name) -Force
    Write-Ok "$ClaudeDir\$($_.Name)"
}

# ─── Шаг 2: рабочие папки ролей в ~\gogol-ai\<role>\ ──────────────────────
Write-Step "📁 Установка рабочих папок ролей (~\gogol-ai\<role>\)"
New-Item -ItemType Directory -Path $RolesDir -Force | Out-Null

foreach ($role in $Roles) {
    $src = Join-Path $RepoDir "roles\$role"
    $dst = Join-Path $RolesDir $role
    New-Item -ItemType Directory -Path "$dst\.claude\skills" -Force | Out-Null

    foreach ($f in @("CLAUDE.md","ozma.md","README.md")) {
        $srcFile = Join-Path $src $f
        if (Test-Path $srcFile) {
            Copy-Item -Path $srcFile -Destination (Join-Path $dst $f) -Force
        }
    }
    $skillsSrc = Join-Path $src "skills"
    if (Test-Path $skillsSrc) {
        Copy-Item -Path "$skillsSrc\*" -Destination "$dst\.claude\skills" -Recurse -Force
    }
    Write-Ok "~\gogol-ai\$role\"
}

# ─── Шаг 3: токены и MCP ──────────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) { New-Item -ItemType File -Path $EnvFile -Force | Out-Null }
$Secrets = @{}
Get-Content $EnvFile -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*"?(.*?)"?\s*$') {
        $Secrets[$matches[1]] = $matches[2]
    }
}

function Get-Secret([string]$Name, [string]$Hint, [bool]$Hide = $true) {
    if ($Secrets.ContainsKey($Name) -and $Secrets[$Name]) { return $Secrets[$Name] }
    Write-Host ""; Write-Warn $Name; Write-Host "  $Hint" -ForegroundColor Gray
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

# Объединяем MCP по всем ролям
$mcpNeeded = @{}
foreach ($role in $Roles) {
    $mcpJson = Join-Path $RepoDir "roles\$role\.mcp.json"
    if (-not (Test-Path $mcpJson)) { continue }
    $d = Get-Content $mcpJson -Raw | ConvertFrom-Json
    if ($d.mcpServers) {
        foreach ($p in $d.mcpServers.PSObject.Properties.Name) { $mcpNeeded[$p] = $true }
    }
}

if ($mcpNeeded.Count -eq 0) {
    Write-Warn "У выбранных ролей нет MCP-серверов — пропускаю настройку."
} else {
    Write-Step "🔌 Настройка MCP-серверов (объединённый список по ролям)"
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
        Write-Warn "Нужен google_token.json (сервис-аккаунт Google)"
        Write-Host "  Положи в $GoogleTokenPath и запусти install.ps1 снова." -ForegroundColor Gray
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
            Write-Host "  Подключается вручную: Settings -> Connectors -> Notion" -ForegroundColor Gray
        }
        default { Write-Warn "$name — неизвестный MCP, пропускаю" }
    }
}

Write-Host "`n✅ Готово!" -ForegroundColor Green
Write-Host ""
Write-Host "Установлены роли: $($Roles -join ', ')"
Write-Host ""
Write-Host "Как работать с ролью:"
foreach ($role in $Roles) {
    Write-Host "  cd $RolesDir\$role && claude"
}
Write-Host ""
Write-Host "В этой папке Claude автоматически подхватит CLAUDE.md роли + общий контекст из ~\.claude\"
Write-Host "Скилы роли — '/' в Claude для списка."
Write-Host ""
Write-Host "Перезапусти Claude Desktop после установки."
