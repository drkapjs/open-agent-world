[CmdletBinding()]
param([switch]$SkipFrontend, [switch]$SkipPayload)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $env:UV_CACHE_DIR = Join-Path $projectRoot '.uv-cache'
    $env:CARGO_HOME = Join-Path $projectRoot '.open-agent-world/cargo'
    $env:npm_config_cache = Join-Path $projectRoot '.open-agent-world/npm-cache'
    if (-not $SkipFrontend) {
        & npm.cmd --prefix frontend run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not $SkipPayload) {
        & (Join-Path $projectRoot 'backend/.venv/Scripts/python.exe') scripts/package-backend.py
        if ($LASTEXITCODE -ne 0) { throw 'Backend packaging failed.' }
    }
    # NSIS's compiler opens source files through MAX_PATH-limited APIs. Alias
    # the payload root itself (not the checkout) without changing resource IDs
    # or installed paths. A temporary Tauri merge patch preserves signing and
    # updater configuration and removes the original long resource key.
    $payloadRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.open-agent-world/desktop-payload')).Path
    $payloadDrive = $null
    foreach ($letter in 'ZYXWVUTSRQP'.ToCharArray()) {
        if (-not (Get-PSDrive -Name $letter -ErrorAction SilentlyContinue)) {
            & subst.exe "${letter}:" $payloadRoot
            if ($LASTEXITCODE -ne 0) { throw 'Cannot create the temporary payload drive.' }
            $payloadDrive = "${letter}:"
            break
        }
    }
    if (-not $payloadDrive) { throw 'No free drive letter for the NSIS payload.' }
    $configPatch = Join-Path $projectRoot ('.open-agent-world/desktop-config-' + [guid]::NewGuid().ToString('N') + '.json')
    try {
        $resources = @{ '../../.open-agent-world/desktop-payload/' = $null }
        $resources["$payloadDrive/"] = 'payload/'
        $patch = @{ bundle = @{ resources = $resources } } | ConvertTo-Json -Depth 4
        [IO.File]::WriteAllText($configPatch, $patch, [Text.UTF8Encoding]::new($false))
        Push-Location (Join-Path $projectRoot 'desktop')
        try {
            & npm.cmd ci --ignore-scripts --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
            & npm.cmd run build -- --config $configPatch
            if ($LASTEXITCODE -ne 0) { throw 'Desktop installer build failed.' }
        }
        finally { Pop-Location }
    }
    finally {
        & subst.exe $payloadDrive /d
        if ($LASTEXITCODE -ne 0) { throw "Cannot remove temporary payload drive $payloadDrive" }
        if (Test-Path -LiteralPath $configPatch) { Remove-Item -LiteralPath $configPatch }
    }
    Write-Host 'Installer: desktop/src-tauri/target/release/bundle/nsis/'
}
finally { Pop-Location }
