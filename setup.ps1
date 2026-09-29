# Windows x64 one-click installer. Double-click setup.cmd to run this file.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir
$env:UV_CACHE_DIR = Join-Path $ProjectDir '.cache\uv'

function Step([string] $Message) { Write-Host "`n==> $Message" }
function Run-Native([string] $File, [string[]] $Arguments) {
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$File failed with exit code $LASTEXITCODE" }
}
function Find-Ollama {
    $command = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'),
        (Join-Path $env:LOCALAPPDATA 'Ollama\ollama.exe'),
        (Join-Path $env:ProgramFiles 'Ollama\ollama.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}
function Ollama-Ready {
    try {
        $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
        return $true
    } catch { return $false }
}
function Download-And-Run([string] $Url) {
    $installer = Join-Path $env:TEMP ([Guid]::NewGuid().ToString() + '.ps1')
    try {
        Invoke-WebRequest -Uri $Url -OutFile $installer -UseBasicParsing
        # Run vendor installers in a child process: an `exit` in their script
        # must not terminate the rest of this project's setup.
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { throw "Official installer failed: $Url (exit code $LASTEXITCODE)" }
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}

if ([Environment]::Is64BitOperatingSystem -ne $true -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'The Windows one-click installer currently supports x86-64 Windows 10/11. ARM64 support depends on native model wheels.'
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Step 'Preparing uv and Python 3.12'
$uvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
$uv = if ($uvCommand) { $uvCommand.Source } else { $null }
if (-not $uv) {
    $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $uv = $candidate }
}
if (-not $uv) {
    Download-And-Run 'https://astral.sh/uv/install.ps1'
    $uvCommand = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($uvCommand) { $uv = $uvCommand.Source }
    if (-not $uv) {
        $candidate = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $uv = $candidate }
    }
    if (-not $uv) { throw 'uv installed but executable was not found. See https://docs.astral.sh/uv/getting-started/installation/' }
}
Run-Native $uv @('python', 'install', '--no-bin', '3.12')
$python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$marker = Join-Path $ProjectDir '.venv\.media-index-install-path'
$rebuild = -not (Test-Path -LiteralPath $python -PathType Leaf)
if (-not $rebuild) {
    $rebuild = -not (Test-Path -LiteralPath $marker -PathType Leaf)
    if (-not $rebuild) { $rebuild = ((Get-Content -LiteralPath $marker -Raw).Trim() -ne $ProjectDir) }
}
if (-not $rebuild) {
    & $python -c 'import sys; assert sys.version_info[:2] == (3, 12)'
    $rebuild = ($LASTEXITCODE -ne 0)
}
if ($rebuild) {
    Run-Native $uv @('venv', '--clear', '.venv', '--python', '3.12')
    Set-Content -LiteralPath $marker -Value $ProjectDir -Encoding UTF8
}
Run-Native $uv @('pip', 'install', '--python', $python, '-r', 'requirements-common.txt', '-r', 'requirements-portable.txt')
$defaultModel = (& $python setup_support.py suggest-model --backend faster).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to choose a local vision model.' }
if ($env:MEDIA_INDEX_MODEL) { $defaultModel = $env:MEDIA_INDEX_MODEL }
Run-Native $python @('setup_support.py', 'prepare-config', '--default-model', $defaultModel)
Run-Native $python @('setup_support.py', 'link-ffmpeg')

Step 'Preparing Ollama'
$ollama = Find-Ollama
if (-not $ollama) {
    Download-And-Run 'https://ollama.com/install.ps1'
    $ollama = Find-Ollama
    if (-not $ollama) { throw 'Ollama installed but executable was not found. See https://ollama.com/download/windows' }
}
if (-not (Ollama-Ready)) {
    $cache = Join-Path $ProjectDir '.cache'
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $cache 'ollama-serve.log') -RedirectStandardError (Join-Path $cache 'ollama-serve-error.log') | Out-Null
    for ($i = 0; $i -lt 30 -and -not (Ollama-Ready); $i++) { Start-Sleep -Seconds 1 }
}
if (-not (Ollama-Ready)) { throw 'Ollama is not reachable. Start its app and rerun setup.cmd.' }

$model = (& $python setup_support.py model-name).Trim()
if ($LASTEXITCODE -ne 0) { throw 'config.json has no valid vision_model.' }
if ($env:MEDIA_INDEX_SKIP_MODELS -ne '1') {
    Step "Downloading or checking vision model $model (Internet and free disk space required)"
    $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
    $installedNames = @($tags.models | ForEach-Object { $_.name })
    if ($installedNames -notcontains $model) { Run-Native $ollama @('pull', $model) }
    else { Write-Host 'Vision model already available.' }
    Step 'Downloading or checking speech model'
    Run-Native $python @('setup_support.py', 'download-asr', '--backend', 'faster')
} else {
    Write-Warning 'MEDIA_INDEX_SKIP_MODELS=1 skipped model downloads; new media cannot be indexed offline yet.'
}

Step 'Checking the installed environment'
Run-Native $python @('setup_support.py', 'verify', '--backend', 'faster')
Run-Native $python @('media_index.py', 'doctor', '--model', $model)
Write-Host "`nSetup complete. Use .venv\Scripts\python.exe media_index.py index --photo-root C:\Photo --video-root C:\Video"
