param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$startedProcesses = @()

function Get-JsonResponse([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 2 } catch { return $null }
}

function Wait-Ready([string]$Url, [System.Diagnostics.Process]$Process) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Get-JsonResponse $Url) { return }
        if ($Process.HasExited) { throw "Service exited before ready. Check logs in $runtimeDir" }
        Start-Sleep -Seconds 1
    }
    throw "Service did not become ready: $Url. Check logs in $runtimeDir"
}

function Restart-UnhealthyBackend([int[]]$ListenerIds, [string]$PythonExe) {
    $roots = @()
    $projectPythonPattern = '(?i)(?:^|\s)"?' + [regex]::Escape($PythonExe) + '"?(?=\s|$)'
    foreach ($listenerId in $ListenerIds) {
        $processId = $listenerId
        $root = $null
        # Windows' venv launcher and Uvicorn reload workers may own different PIDs.
        for ($depth = 0; $processId -gt 0 -and $depth -lt 16; $depth++) {
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
            if (-not $process) { break }
            $commandLine = [string]$process.CommandLine
            $isProjectPython = $process.ExecutablePath -eq $PythonExe -or $commandLine -match $projectPythonPattern
            $isComponentBackend = $commandLine -match '(?:^|\s)-m\s+uvicorn\s+backend\.app\.main:app(?:\s|$)' -and
                $commandLine -match '(?:^|\s)--port\s+8020(?:\s|$)'
            if ($isProjectPython -and $isComponentBackend) {
                $root = $process.ProcessId
                break
            }
            $processId = $process.ParentProcessId
        }
        if (-not $root) {
            throw "Port 8020 is occupied by an unidentified service (PID $listenerId). No processes were stopped. Check .runtime/backend.err.log if a previous backend failed."
        }
        $roots += $root
    }
    # Verify every listener before stopping anything; never stop other projects.
    foreach ($root in ($roots | Select-Object -Unique)) {
        Write-Host "Restarting unresponsive component backend (PID $root)..."
        & taskkill.exe /PID $root /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to stop the unresponsive component backend (PID $root)." }
    }
}

try {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js 24 or newer first.' }
    $nodeVersion = (& node --version).TrimStart('v')
    $nodeMajor = [int]($nodeVersion.Split('.')[0])
    if ($nodeMajor -lt 24) { throw 'Node.js 24 or newer is required.' }

    $pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
        else { throw 'Install Python 3.12 or newer first.' }
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create the project Python environment.' }
    }
    & $pythonExe -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required. Recreate .venv with that version.' }

    $requirements = Join-Path $projectRoot 'backend\requirements.txt'
    $requirementsHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $stamp = Join-Path $projectRoot '.venv\component-requirements.sha256'
    $installedHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
    if ($installedHash -ne $requirementsHash) {
        & $pythonExe -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        Set-Content -LiteralPath $stamp -Value $requirementsHash -Encoding ascii
    }
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'node_modules\vite\bin\vite.js'))) {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }

    $healthUrl = 'http://127.0.0.1:8020/api/health'
    $health = Get-JsonResponse $healthUrl
    if ($health.service -ne 'component-template-api') {
        $listeners = @(Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -gt 0) {
            Restart-UnhealthyBackend -ListenerIds @($listeners.OwningProcess | Select-Object -Unique) -PythonExe $pythonExe
        }
        $api = Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8020', '--reload', '--reload-dir', 'backend') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'backend.out.log') -RedirectStandardError (Join-Path $runtimeDir 'backend.err.log') -PassThru
        $startedProcesses += $api
        Wait-Ready $healthUrl $api
        if ((Get-JsonResponse $healthUrl).service -ne 'component-template-api') { throw 'Backend health identity is invalid.' }
    }

    $frontendUrl = 'http://127.0.0.1:5180/'
    if (Get-NetTCPConnection -LocalPort 5180 -State Listen -ErrorAction SilentlyContinue) {
        $page = Invoke-WebRequest -UseBasicParsing -Uri $frontendUrl -TimeoutSec 3
        $forwardedHealth = Get-JsonResponse ($frontendUrl + 'api/health')
        if ($page.Content -notmatch '/src/main.tsx' -or $forwardedHealth.service -ne 'component-template-api') {
            throw 'Port 5180 is occupied by another service. Stop that service before starting this platform.'
        }
    } else {
        $nodeExe = (Get-Command node).Source
        $web = Start-Process -FilePath $nodeExe -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5180', '--strictPort') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'frontend.out.log') -RedirectStandardError (Join-Path $runtimeDir 'frontend.err.log') -PassThru
        $startedProcesses += $web
        Wait-Ready ($frontendUrl + 'api/health') $web
    }

    Write-Host "Component workbench: $frontendUrl"
    Write-Host 'FastAPI docs: http://127.0.0.1:8020/docs'
    Write-Host 'MCP (Streamable HTTP): http://127.0.0.1:8020/mcp'
    Write-Host "Service logs: $runtimeDir"
    if (-not $NoBrowser) { Start-Process $frontendUrl }
} catch {
    # Only clean up processes launched by this invocation; never kill unrelated listeners.
    foreach ($started in $startedProcesses) {
        if (-not $started.HasExited) { & taskkill.exe /PID $started.Id /T /F | Out-Null }
    }
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
