# ─────────────────────────────────────────────────────────────────────────────
# SecretaryAI — One-Command Full-Stack Installer (Windows PowerShell)
#
# Usage (run as Administrator or standard user):
#   iwr -useb https://get.secretaryai.com/install.ps1 | iex
#   .\install.ps1
#   .\install.ps1 -Version 1.2.0
# ─────────────────────────────────────────────────────────────────────────────
param(
    [string]$Version    = $env:SECRETARY_VERSION ?? "latest",
    [string]$InstallDir = $env:SECRETARY_INSTALL_DIR ?? "$env:USERPROFILE\SecretaryAI"
)

$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Log  ($msg) { Write-Host "  -> $msg" -ForegroundColor Cyan }
function Ok   ($msg) { Write-Host "  + $msg"  -ForegroundColor Green }
function Warn ($msg) { Write-Host "  ! $msg"  -ForegroundColor Yellow }
function Fail ($msg) { Write-Host "  x $msg"  -ForegroundColor Red; exit 1 }

function Prompt-Required($label) {
    $val = ""
    while ([string]::IsNullOrWhiteSpace($val)) {
        $val = Read-Host "  $label"
        if ([string]::IsNullOrWhiteSpace($val)) { Write-Host "  (required — cannot be empty)" }
    }
    return $val
}
function Prompt-Optional($label) {
    return Read-Host "  $label (optional, press Enter to skip)"
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  =====================================" -ForegroundColor White
Write-Host "       SecretaryAI Installer           " -ForegroundColor White
Write-Host "  =====================================" -ForegroundColor White
Write-Host ""

# ── Check Docker ──────────────────────────────────────────────────────────────
Log "Checking prerequisites..."

try {
    $dockerVersion = docker --version 2>&1
    Ok "Docker found: $dockerVersion"
} catch {
    Fail "Docker is not installed. Download it from https://docs.docker.com/desktop/windows/"
}

try {
    docker info 2>&1 | Out-Null
    Ok "Docker daemon is running"
} catch {
    Fail "Docker Desktop is not running. Start Docker Desktop and try again."
}

$ComposeCmd = $null
try {
    docker compose version 2>&1 | Out-Null
    $ComposeCmd = "docker compose"
    Ok "Docker Compose v2 found"
} catch {
    try {
        docker-compose version 2>&1 | Out-Null
        $ComposeCmd = "docker-compose"
        Ok "Docker Compose v1 found"
    } catch {
        Fail "Docker Compose not found. It ships with Docker Desktop — try reinstalling."
    }
}

# ── Install directory ─────────────────────────────────────────────────────────
Log "Installing to $InstallDir ..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir

# ── Download compose file ─────────────────────────────────────────────────────
$ComposeUrl   = "https://github.com/secretaryai/secretaryai/releases/latest/download/docker-compose.prod.yml"
$EnvExampleUrl = "https://github.com/secretaryai/secretaryai/releases/latest/download/.env.example"

if (Test-Path "docker-compose.prod.yml") {
    Warn "docker-compose.prod.yml already exists — updating."
}
Log "Downloading docker-compose.prod.yml ..."
try {
    Invoke-WebRequest -Uri $ComposeUrl -OutFile "docker-compose.prod.yml" -UseBasicParsing -ErrorAction Stop
} catch {
    Fail "Failed to download docker-compose.prod.yml — check your internet connection. Error: $_"
}
Ok "docker-compose.prod.yml downloaded"

# ── Set up .env ───────────────────────────────────────────────────────────────
if (Test-Path ".env") {
    Warn ".env already exists — skipping prompt (delete it to reconfigure)."
} else {
    Log "Downloading .env template ..."
    try {
        Invoke-WebRequest -Uri $EnvExampleUrl -OutFile ".env.example" -UseBasicParsing -ErrorAction Stop
    } catch {
        Fail "Failed to download .env.example — check your internet connection. Error: $_"
    }

    Write-Host ""
    Write-Host "  Configure SecretaryAI" -ForegroundColor White
    Write-Host "  Press Enter to skip optional values."
    Write-Host ""

    # Auto-generate a cryptographically random 64-char hex secret key
    $rng = [Security.Cryptography.RNGCryptoServiceProvider]::Create()
    $secretBytes = New-Object Byte[] 32
    $rng.GetBytes($secretBytes)
    $SecretKey = ($secretBytes | ForEach-Object { $_.ToString("x2") }) -join ""
    if ($SecretKey.Length -ne 64) { Fail "Secret key generation failed — unexpected length: $($SecretKey.Length)" }

    Write-Host "  ---- Required ----------------------------------"
    $AnthropicKey     = Prompt-Required "Anthropic API key (sk-ant-...)"
    $SupabaseUrl      = Prompt-Required "Supabase project URL (https://xxx.supabase.co)"
    $SupabaseAnon     = Prompt-Required "Supabase anon key"
    $SupabaseService  = Prompt-Required "Supabase service role key"
    $DatabaseUrl      = Prompt-Required "Postgres connection string (postgresql://postgres:...)"

    Write-Host ""
    Write-Host "  ---- Optional integrations ---------------------"
    $ConductorKey     = Prompt-Optional "Conductor API key (QuickBooks Desktop)"
    $IntuitClientId   = Prompt-Optional "Intuit client ID (QuickBooks Online)"
    $IntuitSecret     = Prompt-Optional "Intuit client secret (QuickBooks Online)"
    $SendgridKey      = Prompt-Optional "SendGrid API key (email reports)"
    $GoogleClientId   = Prompt-Optional "Google client ID (Gmail/Sheets)"
    $GoogleSecret     = Prompt-Optional "Google client secret"
    $OpenAiKey        = Prompt-Optional "OpenAI API key (voice input)"

    # Read template and replace placeholders
    $env_content = Get-Content ".env.example" -Raw

    $replacements = @{
        "SECRET_KEY=replace-me-with-a-random-64-char-hex-string" = "SECRET_KEY=$SecretKey"
        "ANTHROPIC_API_KEY=sk-ant-api03-..."  = "ANTHROPIC_API_KEY=$AnthropicKey"
        "SUPABASE_URL=https://your-project-id.supabase.co" = "SUPABASE_URL=$SupabaseUrl"
        "SUPABASE_ANON_KEY=eyJhbGci..."       = "SUPABASE_ANON_KEY=$SupabaseAnon"
        "SUPABASE_SERVICE_ROLE_KEY=eyJhbGci..." = "SUPABASE_SERVICE_ROLE_KEY=$SupabaseService"
        "DATABASE_URL=postgresql://postgres:<password>@db.your-project-id.supabase.co:5432/postgres" = "DATABASE_URL=$DatabaseUrl"
        "CONDUCTOR_API_KEY="     = "CONDUCTOR_API_KEY=$ConductorKey"
        "INTUIT_CLIENT_ID="      = "INTUIT_CLIENT_ID=$IntuitClientId"
        "INTUIT_CLIENT_SECRET="  = "INTUIT_CLIENT_SECRET=$IntuitSecret"
        "SENDGRID_API_KEY=SG...." = "SENDGRID_API_KEY=$SendgridKey"
        "GOOGLE_CLIENT_ID="      = "GOOGLE_CLIENT_ID=$GoogleClientId"
        "GOOGLE_CLIENT_SECRET="  = "GOOGLE_CLIENT_SECRET=$GoogleSecret"
        "OPENAI_API_KEY="        = "OPENAI_API_KEY=$OpenAiKey"
    }

    foreach ($old in $replacements.Keys) {
        $env_content = $env_content.Replace($old, $replacements[$old])
    }

    Set-Content -Path ".env" -Value $env_content -Encoding UTF8
    Remove-Item ".env.example" -Force
    Ok ".env configured"
}

# ── Pull images ───────────────────────────────────────────────────────────────
Write-Host ""
Log "Pulling Docker images (this may take a few minutes on first run)..."
$env:VERSION = $Version
Invoke-Expression "$ComposeCmd -f docker-compose.prod.yml pull"
Ok "Images pulled"

# ── Start services ────────────────────────────────────────────────────────────
Log "Starting SecretaryAI..."
Invoke-Expression "$ComposeCmd -f docker-compose.prod.yml up -d"
Ok "Services started"

# ── Health check ──────────────────────────────────────────────────────────────
Log "Waiting for API to be ready..."
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { Ok "API is healthy"; $ready = $true; break }
    } catch { }
    Start-Sleep 2
}
if (-not $ready) {
    Write-Host ""
    Warn "API did not become healthy in time. Recent logs:"
    Invoke-Expression "$ComposeCmd -f docker-compose.prod.yml logs --tail=20 api" 2>&1 | Write-Host
    Write-Host ""
    Fail "Startup failed. Fix the issue above then run: $ComposeCmd -f docker-compose.prod.yml up -d"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  =====================================" -ForegroundColor Green
Write-Host "   SecretaryAI is running!" -ForegroundColor Green
Write-Host "  =====================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Web UI:   http://localhost" -ForegroundColor Cyan
Write-Host "  API docs: http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Manage:   cd $InstallDir"
Write-Host "  Logs:     $ComposeCmd -f docker-compose.prod.yml logs -f"
Write-Host "  Stop:     $ComposeCmd -f docker-compose.prod.yml down"
Write-Host "  Update:   iwr -useb https://get.secretaryai.com/install.ps1 | iex"
Write-Host ""
