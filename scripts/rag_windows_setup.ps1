[CmdletBinding()]
param(
    [switch]$SkipSeed
)

$ErrorActionPreference = 'Stop'

function Stop-Setup {
    param([string]$Message)
    Write-Host ""
    Write-Host "Setup could not continue: $Message" -ForegroundColor Red
    exit 1
}

function Get-PlaintextSecret {
    param([string]$Prompt)

    $secureValue = Read-Host $Prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Get-ProviderKey {
    param([string[]]$Lines)

    foreach ($line in $Lines) {
        if ($line -match '^\s*(OPENAI_API_KEY|GEMINI_API_KEY)\s*=\s*(.*)$') {
            $value = $Matches[2].Trim()
            if ($value.StartsWith('#')) {
                continue
            }
            $value = $value.Trim('"').Trim("'")
            if ($value) {
                return $true
            }
        }
    }
    return $false
}

function Set-EnvValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Value
    )

    $lines = New-Object 'System.Collections.Generic.List[string]'
    foreach ($line in Get-Content -LiteralPath $Path) {
        $lines.Add([string]$line)
    }
    $replaced = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match ("^\s*" + [regex]::Escape($Name) + "\s*=")) {
            $lines[$index] = "$Name=$Value"
            $replaced = $true
            break
        }
    }
    if (-not $replaced) {
        $lines.Add("$Name=$Value")
    }

    [System.IO.File]::WriteAllLines(
        $Path,
        $lines,
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Test-QdrantCollection {
    param([string]$Collection)

    & docker compose exec -T webapp python -c "from backend.core.config import Settings; from qdrant_client import QdrantClient; import sys; settings = Settings(); sys.exit(0 if QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port).collection_exists('$Collection') else 1)" *> $null
    return $LASTEXITCODE -eq 0
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$envPath = Join-Path $repoRoot '.env'
$envExamplePath = Join-Path $repoRoot '.env.example'

Write-Host "Aina-Veris Windows setup" -ForegroundColor Cyan
Write-Host "Repository: $repoRoot"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Setup 'Docker Desktop is required. Install it, start it, and run this installer again.'
}

try {
    & docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose is unavailable.' }
    & docker info | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not running.' }
}
catch {
    Stop-Setup 'Start Docker Desktop, wait until it is running, and run this installer again.'
}

if (-not (Test-Path -LiteralPath $envPath)) {
    if (-not (Test-Path -LiteralPath $envExamplePath)) {
        Stop-Setup '.env.example is missing from this checkout.'
    }
    Copy-Item -LiteralPath $envExamplePath -Destination $envPath
    Write-Host 'Created .env from .env.example.'
}

$envLines = Get-Content -LiteralPath $envPath
if (-not (Get-ProviderKey -Lines $envLines)) {
    Write-Host ''
    Write-Host 'Aina-Veris needs an OpenAI or Gemini API key before it can start.' -ForegroundColor Yellow
    $openAiKey = Get-PlaintextSecret 'Paste your OpenAI API key, or press Enter to use Gemini'
    if ($openAiKey) {
        Set-EnvValue -Path $envPath -Name 'OPENAI_API_KEY' -Value $openAiKey
    }
    else {
        $geminiKey = Get-PlaintextSecret 'Paste your Gemini API key'
        if (-not $geminiKey) {
            Stop-Setup 'No API key was provided. Add OPENAI_API_KEY or GEMINI_API_KEY to .env, then run this installer again.'
        }
        Set-EnvValue -Path $envPath -Name 'GEMINI_API_KEY' -Value $geminiKey
    }
}

Write-Host ''
Write-Host 'Starting Aina-Veris and Qdrant. The first run may take a few minutes...' -ForegroundColor Cyan
& docker compose up -d --build
if ($LASTEXITCODE -ne 0) {
    Stop-Setup 'Docker Compose could not start the services.'
}

if (-not $SkipSeed) {
    $collections = @(
        @{ Name = 'document_index'; Script = 'scripts/seed_qdrant.py' },
        @{ Name = 'document_index_gemini'; Script = 'scripts/seed_qdrant_gemini.py' }
    )

    foreach ($collection in $collections) {
        if (Test-QdrantCollection -Collection $collection.Name) {
            Write-Host "Keeping existing $($collection.Name) data."
            continue
        }

        Write-Host "Seeding $($collection.Name)..." -ForegroundColor Cyan
        & docker compose exec -T webapp python $collection.Script
        if ($LASTEXITCODE -ne 0) {
            Stop-Setup "Could not seed $($collection.Name). Run 'docker compose logs webapp' to inspect the application logs."
        }
    }
}

Write-Host ''
Write-Host 'Setup complete. Opening Aina-Veris at http://localhost:8100' -ForegroundColor Green
Start-Process 'http://localhost:8100'
