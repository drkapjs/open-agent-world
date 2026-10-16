# Exercise the real Windows preparation function with a disposable Python shim.
# No application process is started/stopped and no packages are downloaded.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'dev.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$definition = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Initialize-BackendEnvironment' }, $true)
Invoke-Expression $definition.Extent.Text

$testBase = [IO.Path]::GetFullPath((Join-Path $projectRoot '.tmp'))
$testRoot = Join-Path $testBase ('dev environment ' + [guid]::NewGuid().ToString('N'))
$venv = Join-Path $testRoot 'backend/.venv'
$python = Join-Path $venv 'Scripts/python.exe'
$extra = Join-Path $testRoot 'extra plugin'
New-Item -ItemType Directory -Force -Path (Split-Path $python), (Join-Path $testRoot 'scripts'), (Join-Path $testRoot 'plugins/example'), $extra | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot 'backend/.venv/Scripts/python.exe') -Destination $python
Copy-Item -LiteralPath (Join-Path $projectRoot 'backend/.venv/pyvenv.cfg') -Destination (Join-Path $venv 'pyvenv.cfg')
Set-Content -LiteralPath (Join-Path $testRoot 'plugins/example/pyproject.toml') -Value '[project]'
@'
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
print('Plugin setup progress must not become part of the returned Python path.')
(root / 'receipt.json').write_text(json.dumps({'python': sys.executable, 'prefix': sys.prefix, 'plugins': sys.argv[1:]}))
sys.exit(4 if (root / 'fail-plugin').exists() else 0)
'@ | Set-Content -LiteralPath (Join-Path $testRoot 'scripts/install-plugins.py') -Encoding utf8
$script:syncExitCode = 0
function uv {
    $script:syncArguments = @($args)
    Write-Output 'uv setup progress'
    $global:LASTEXITCODE = $script:syncExitCode
}

try {
    $actual = Initialize-BackendEnvironment -Root $testRoot -DevelopmentProfile 'fixture-profile' -AdditionalPlugins @($extra)
    if ($actual -isnot [string] -or $actual -ne $python) { throw 'Installer output contaminated the backend executable.' }
    $receipt = Get-Content -Raw -LiteralPath (Join-Path $testRoot 'receipt.json') | ConvertFrom-Json
    if ($receipt.python -ne $python -or $receipt.prefix -ne $venv) { throw 'Setup used a different interpreter from server startup.' }
    if ($receipt.plugins.Count -ne 4 -or $receipt.plugins[0] -ne '--profile' -or $receipt.plugins[1] -ne 'fixture-profile' -or $receipt.plugins[3] -ne $extra) { throw "Profile or plugin paths with spaces were not preserved: $($receipt.plugins | ConvertTo-Json -Compress)" }
    if ($script:syncArguments[0] -ne 'sync' -or $script:syncArguments -contains '--with-editable') { throw 'A temporary uv overlay was requested.' }
    $start = $ast.Find({ param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -eq 'Start-Process' -and $node.Extent.Text -match '-RedirectStandardError \$backendError' }, $true)
    if ($start.Extent.Text -notmatch '-FilePath \$backendPython') { throw 'Backend launch bypasses the prepared interpreter.' }

    $script:syncExitCode = 3
    try {
        Initialize-BackendEnvironment -Root $testRoot | Out-Null
        throw 'Expected host setup failure.'
    } catch {
        if ($_.Exception.Message -ne 'Backend dependency installation failed.') { throw }
    }
    $script:syncExitCode = 0
    New-Item -ItemType File -Path (Join-Path $testRoot 'fail-plugin') | Out-Null
    try {
        Initialize-BackendEnvironment -Root $testRoot | Out-Null
        throw 'Expected plugin setup failure.'
    } catch {
        if ($_.Exception.Message -ne 'Plugin dependency installation failed.') { throw }
    }
    Write-Host 'PASS: persistent interpreter, clean return value, spaced plugin paths, direct server launch, and setup failure propagation.'
} finally {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if ([IO.Path]::GetDirectoryName($resolved) -ne $testBase) { throw 'Test cleanup escaped the temporary directory.' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
