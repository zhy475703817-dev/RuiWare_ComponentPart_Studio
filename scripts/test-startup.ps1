$ErrorActionPreference = 'Stop'

# Load the recovery function without starting the real platform.
$source = Join-Path $PSScriptRoot 'start-dev.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$null, [ref]$null)
$function = $ast.Find({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Restart-UnhealthyBackend'
}, $true)
if (-not $function) { throw 'Missing recovery: an unhealthy platform backend must be restartable.' }
Invoke-Expression $function.Extent.Text

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$legacyRoot = Join-Path (Split-Path -Parent $projectRoot) 'RuiWare_ComponentPart_Studio-master'
$legacyPython = Join-Path $legacyRoot '.venv\Scripts\python.exe'
$script:processes = @{}
$script:stopped = @()
function Get-CimInstance {
    param($ClassName, $Filter)
    $processId = [int]($Filter -replace '\D', '')
    return $script:processes[$processId]
}
function taskkill.exe {
    $script:stopped += [int]$args[1]
    $global:LASTEXITCODE = 0
}
function Assert-Rejected($ListenerIds) {
    $script:stopped = @()
    $rejected = $false
    try { Restart-UnhealthyBackend -ListenerIds $ListenerIds -ProjectRoot $projectRoot }
    catch { $rejected = $true }
    if (-not $rejected -or $script:stopped.Count -ne 0) {
        throw 'Unidentified listeners must be rejected without stopping any processes.'
    }
}

# Windows venv launcher -> base Python reloader -> worker holding the port.
$script:processes[101] = [pscustomobject]@{
    ProcessId = 101; ParentProcessId = 100; ExecutablePath = 'C:\Python\python.exe'; CommandLine = 'python -c worker'
}
$script:processes[100] = [pscustomobject]@{
    ProcessId = 100; ParentProcessId = 99; ExecutablePath = 'C:\Python\python.exe'
    CommandLine = '"' + $pythonExe + '" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8020 --reload --reload-dir "' + (Join-Path $projectRoot 'backend') + '"'
}
Restart-UnhealthyBackend -ListenerIds @(101, 100) -ProjectRoot $projectRoot
if ($script:stopped.Count -ne 1 -or $script:stopped[0] -ne 100) {
    throw 'Recovery must identify the project launcher despite its base Python path and stop its tree once.'
}

# Same module from a different project is not ours.
$script:processes[200] = [pscustomobject]@{
    ProcessId = 200; ParentProcessId = 0; ExecutablePath = 'D:\other\.venv\Scripts\python.exe'
    CommandLine = 'python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8020 --reload --reload-dir backend'
}
Assert-Rejected @(200)
Assert-Rejected @(101, 200)
Assert-Rejected @(999)

# A shared base interpreter alone does not identify this project's backend.
$script:processes[200].ExecutablePath = 'C:\Python\python.exe'
$script:processes[200].CommandLine = 'python -m uvicorn backend.app.main:app --port 8020'
Assert-Rejected @(200)

# An older checkout of this same platform may still own 8020 and must be recoverable.
$script:processes[200].CommandLine = '"' + $legacyPython + '" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8020 --reload --reload-dir backend'
Restart-UnhealthyBackend -ListenerIds @(200) -ProjectRoot $projectRoot
if ($script:stopped.Count -ne 1 -or $script:stopped[0] -ne 200) {
    throw 'Recovery must stop a stale backend from an older platform checkout.'
}

# A reloader child may have the same command line as its supervisor; stop the supervisor tree.
$legacyCommand = '"' + $legacyPython + '" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8020 --reload --reload-dir backend'
$script:processes[300] = [pscustomobject]@{
    ProcessId = 300; ParentProcessId = 0; ExecutablePath = 'D:\develop\python\python.exe'; CommandLine = $legacyCommand
}
$script:processes[301] = [pscustomobject]@{
    ProcessId = 301; ParentProcessId = 300; ExecutablePath = 'D:\develop\python\python.exe'; CommandLine = $legacyCommand
}
$script:stopped = @()
Restart-UnhealthyBackend -ListenerIds @(301) -ProjectRoot $projectRoot
if ($script:stopped.Count -ne 1 -or $script:stopped[0] -ne 300) {
    throw 'Recovery must stop the reloader supervisor tree, not just its listening child.'
}

# Another command in this virtual environment is not the platform backend.
$script:processes[200].ExecutablePath = $pythonExe
$script:processes[200].CommandLine = 'python -m http.server 8020'
Assert-Rejected @(200)
$script:processes[200].CommandLine = '"' + $pythonExe + '" -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir backend'
Assert-Rejected @(200)
Write-Host 'Startup recovery checks passed.'
