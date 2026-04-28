$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = 8787
$ApiUrl = "http://127.0.0.1:$Port"
$FrontendDir = Join-Path $RepoRoot "web"
$FrontendSrcDir = Join-Path $FrontendDir "src"
$FrontendDistDir = Join-Path $RepoRoot "src\autoteam\web\dist"
$FrontendDistIndex = Join-Path $FrontendDistDir "index.html"
$AutoteamExe = Join-Path $RepoRoot ".venv\Scripts\autoteam.exe"
$StdoutLog = Join-Path $RepoRoot "autoteam-api.run.log"
$StderrLog = Join-Path $RepoRoot "autoteam-api.run.err.log"

function Write-Step {
    param([string]$Message)
    Write-Host "[AutoTeam] $Message"
}

function Test-CommandExists {
    param([string]$Name)
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-BackendDeps {
    if (Test-Path $AutoteamExe) {
        return
    }

    if (-not (Test-CommandExists "uv")) {
        throw "uv was not found in PATH. Install uv first."
    }

    Write-Step "Python environment missing, running uv sync..."
    & uv sync
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed."
    }

    if (-not (Test-Path $AutoteamExe)) {
        throw "autoteam executable was not created under .venv\Scripts."
    }
}

function Ensure-PlaywrightBrowser {
    if (-not (Test-CommandExists "uv")) {
        throw "uv was not found in PATH. Install uv first."
    }

    $browserRoot = Join-Path $env:USERPROFILE "AppData\Local\ms-playwright"
    $chromiumInstalled = (Test-Path $browserRoot) -and (
        Get-ChildItem -Path $browserRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "chromium-*" } |
        Select-Object -First 1
    )

    if ($chromiumInstalled) {
        return
    }

    Write-Step "Playwright Chromium missing, installing..."
    & uv run playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        throw "playwright install chromium failed."
    }
}

function Get-LatestWriteTime {
    param([string[]]$Paths)

    $latest = Get-Date "2000-01-01"
    foreach ($path in $Paths) {
        if (-not (Test-Path $path)) {
            continue
        }
        $items = Get-ChildItem -Path $path -Recurse -File -ErrorAction SilentlyContinue
        foreach ($item in $items) {
            if ($item.LastWriteTime -gt $latest) {
                $latest = $item.LastWriteTime
            }
        }
    }
    return $latest
}

function Test-FrontendBuildNeeded {
    if (-not (Test-Path $FrontendDistIndex)) {
        return $true
    }

    $sourceLatest = Get-LatestWriteTime @(
        (Join-Path $FrontendDir "src"),
        (Join-Path $FrontendDir "package.json"),
        (Join-Path $FrontendDir "package-lock.json"),
        (Join-Path $FrontendDir "vite.config.js")
    )
    $distLatest = Get-LatestWriteTime @($FrontendDistDir)
    return $sourceLatest -gt $distLatest
}

function Ensure-FrontendBuild {
    if (-not (Test-CommandExists "npm")) {
        throw "npm was not found in PATH. Install Node.js first."
    }

    $NodeModulesDir = Join-Path $FrontendDir "node_modules"
    if (-not (Test-Path $NodeModulesDir)) {
        Write-Step "web\node_modules missing, running npm install..."
        Push-Location $FrontendDir
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed."
            }
        }
        finally {
            Pop-Location
        }
    }

    if (-not (Test-FrontendBuildNeeded)) {
        Write-Step "Frontend build is up to date."
        return
    }

    Write-Step "Building frontend..."
    Push-Location $FrontendDir
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "npm run build failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Stop-ExistingServer {
    $processIds = New-Object System.Collections.Generic.HashSet[int]

    $listeners = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listeners) {
        foreach ($listener in $listeners) {
            [void]$processIds.Add([int]$listener.OwningProcess)
        }
    }

    $apiProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like '*autoteam.exe* api*'
        }

    foreach ($process in $apiProcesses) {
        [void]$processIds.Add([int]$process.ProcessId)
    }

    if ($processIds.Count -eq 0) {
        Write-Step "No existing AutoTeam API process found."
        return
    }

    foreach ($processId in $processIds) {
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
            Write-Step "Stopping process $($process.ProcessName) ($processId)..."
            Stop-Process -Id $processId -Force -ErrorAction Stop
        }
        catch {
            Write-Step "Failed to stop process ${processId}: $($_.Exception.Message)"
        }
    }

    Start-Sleep -Seconds 1
}

function Start-AutoteamServer {
    if (Test-Path $StdoutLog) {
        Remove-Item -LiteralPath $StdoutLog -Force
    }
    if (Test-Path $StderrLog) {
        Remove-Item -LiteralPath $StderrLog -Force
    }

    Write-Step "Starting AutoTeam API on port $Port..."
    $process = Start-Process `
        -FilePath $AutoteamExe `
        -ArgumentList @("api", "--host", "0.0.0.0", "--port", "$Port") `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru

    return $process
}

function Wait-ForServerReady {
    param([int]$TimeoutSeconds = 25)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri "$ApiUrl/api/setup/status" -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
        }
        Start-Sleep -Milliseconds 700
    }

    return $false
}

try {
    Write-Step "Repo root: $RepoRoot"
    Ensure-BackendDeps
    Ensure-PlaywrightBrowser
    Ensure-FrontendBuild
    Stop-ExistingServer
    $process = Start-AutoteamServer

    if (-not (Wait-ForServerReady)) {
        throw "Server did not become ready in time. Check autoteam-api.run.err.log."
    }

    Write-Step "Server is ready: $ApiUrl"
    Write-Step "PID: $($process.Id)"
    Write-Step "Logs: $StdoutLog"
    Start-Process $ApiUrl | Out-Null
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
