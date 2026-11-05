# photos2show local setup for Windows: uv + app, Ollama, models matching this computer, .env, UI.
# Run: powershell -ExecutionPolicy Bypass -File setup.ps1
# Safe to run again: installed parts are skipped, models already pulled are only checked.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$OllamaUrl = "http://localhost:11434"
$Context = 32768  # the slideshow step sends one line per photo, see docs/configuration.md#local-models

function Ask($question, $default) {
    $answer = Read-Host "$question [$default]"
    if ($answer) { $answer } else { $default }
}

function Run {  # a program's failure does not stop PowerShell by itself
    & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE) { throw "$($args -join ' ') failed (exit code $LASTEXITCODE)" }
}

function OllamaUp {
    try { Invoke-RestMethod "$OllamaUrl/api/version" -TimeoutSec 3 | Out-Null; $true } catch { $false }
}

# --- uv and the app -------------------------------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager) ..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Write-Host "Installing photos2show ..."
Run uv sync --quiet

# --- Ollama -------------------------------------------------------------------------------------
$env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"  # where the installer puts it; not on Path until a new shell
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama (runs the AI models on this computer) is not installed."
    if ((Ask "Install it automatically (a) or install it yourself (m)?" "m") -eq "a") {  # nothing installed unasked
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host "winget is missing. Download Ollama from https://ollama.com/download, then run setup.ps1 again."
            exit 1
        }
        Run winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
    } else {
        Write-Host "Install Ollama from https://ollama.com/download, then run setup.ps1 again."
        exit 0
    }
}
if (-not (OllamaUp)) {
    Write-Host "Starting the Ollama server ..."
    Start-Process ollama -ArgumentList serve -WindowStyle Hidden
    foreach ($i in 1..30) { if (OllamaUp) { break }; Start-Sleep 1 }
    if (-not (OllamaUp)) { Write-Host "Ollama does not answer at $OllamaUrl."; exit 1 }
}

# --- Hardware -> recommended pair (keep in sync with *Recommended pairs* in docs/configuration.md) ---
$ramGb = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$vramGb = 0; $gpu = "none"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $mib = (nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits) | ForEach-Object { [int]$_ } |
        Sort-Object | Select-Object -Last 1
    if ($mib) { $gpu = "NVIDIA"; $vramGb = [math]::Round($mib / 1024) }
}
if ($vramGb -eq 0) {  # AMD and others: the registry has the full size (WMI's AdapterRAM stops at 4 GB)
    $class = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*"
    foreach ($card in Get-ItemProperty $class -ErrorAction SilentlyContinue) {
        $size = $card."HardwareInformation.qwMemorySize"
        if ($size -is [byte[]]) { $size = [BitConverter]::ToUInt64($size, 0) }
        if ($size -and [math]::Round($size / 1GB) -gt $vramGb) { $gpu = $card.DriverDesc; $vramGb = [math]::Round($size / 1GB) }
    }
}
if     ($vramGb -ge 22) { $analysis = "qwen3-vl:30b-a3b-instruct"; $slideshow = "qwen3:30b-instruct" }
elseif ($vramGb -ge 15) { $analysis = "qwen3-vl:8b-instruct";      $slideshow = "gpt-oss:20b" }
elseif ($vramGb -ge 11) { $analysis = "qwen3-vl:8b-instruct";      $slideshow = "gemma4:12b" }
elseif ($vramGb -ge 7)  { $analysis = "gemma4:e4b";                $slideshow = "gemma4:e4b" }
else                    { $analysis = "gemma4:e2b";                $slideshow = "qwen3:4b-instruct" }

Write-Host ""
Write-Host "This computer: $ramGb GB RAM, GPU: $gpu$(if ($vramGb) { " with $vramGb GB" })."
if ($vramGb -lt 7) { Write-Host "No GPU with 7 GB or more found: the AI runs on the CPU, about a minute per photo." }
Write-Host "Press Enter to keep a suggestion or type another Ollama model (see docs/configuration.md#local-models)."
$analysis = Ask "Analysis model (describes each photo, needs vision)" $analysis
$slideshow = Ask "Slideshow model (chooses the photos, text only)" $slideshow

# --- Models ---------------------------------------------------------------------------------------
Run ollama pull $analysis
if ($slideshow -ne $analysis) { Run ollama pull $slideshow }
# Ollama's default context is too small for the slideshow list; this copy shares the downloaded files.
$modelfile = New-TemporaryFile
Set-Content $modelfile.FullName "FROM $slideshow`nPARAMETER num_ctx $Context" -Encoding ascii
Run ollama create photos2show-slideshow -f $modelfile.FullName
Remove-Item $modelfile.FullName

# --- .env ---------------------------------------------------------------------------------------
$write = $true
if (Test-Path .env) { $write = (Ask "Replace the existing .env (it is kept as .env.bak)? y/n" "y") -eq "y" }
if ($write) {
    if (Test-Path .env) { Copy-Item .env .env.bak -Force }
    Set-Content .env -Encoding ascii @"
# Written by setup.ps1; see docs/configuration.md
OPENAI_BASE_URL=$OllamaUrl/v1
OPENAI_API_KEY=
MODEL=$analysis
ANALYSIS_MODEL=$analysis
# $slideshow with a $Context-token context (made by setup.ps1)
SLIDESHOW_MODEL=photos2show-slideshow
AI_CONCURRENCY=1
AI_THINKING=on
"@
    Write-Host "Wrote .env. Server, key, models or Thinking chosen in Settings still win over it; set them back to default there."
}

Write-Host "Starting photos2show (stop with Ctrl+C; next time: uv run photos2show-web) ..."
uv run photos2show-web
