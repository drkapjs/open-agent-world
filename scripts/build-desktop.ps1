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
    # NSIS's compiler cannot open long source paths. Use temporary flat hard
    # links on the same volume, mapping each file to its original installed
    # path. Relative resource keys also avoid Tauri stripping Windows drives.
    # No payload bytes are copied or removed; signing/updater config is retained.
    $payloadRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.open-agent-world/desktop-payload')).Path
    $aliasName = 'nsis-' + [guid]::NewGuid().ToString('N')
    $aliasRoot = Join-Path $projectRoot ".open-agent-world/$aliasName"
    $configPatch = "$aliasRoot.json"
    try {
        New-Item -ItemType Directory -Path $aliasRoot | Out-Null
        $resources = @{ '../../.open-agent-world/desktop-payload/' = $null }
        $index = 0
        Get-ChildItem -LiteralPath $payloadRoot -Recurse -File | ForEach-Object {
            $name = "$index$($_.Extension)"
            New-Item -ItemType HardLink -Path (Join-Path $aliasRoot $name) -Value $_.FullName | Out-Null
            $relative = $_.FullName.Substring($payloadRoot.Length + 1).Replace('\', '/')
            $resources["../../.open-agent-world/$aliasName/$name"] = "payload/$relative"
            $index++
        }
        if ($index -eq 0) { throw 'Cannot package an empty payload.' }
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
        if (Test-Path -LiteralPath $configPatch) { Remove-Item -LiteralPath $configPatch }
        if (Test-Path -LiteralPath $aliasRoot) {
            $resolved = (Resolve-Path -LiteralPath $aliasRoot).Path
            $buildRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.open-agent-world')).Path
            if ($resolved -ne (Join-Path $buildRoot $aliasName)) { throw 'Unsafe NSIS staging cleanup path.' }
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
    Write-Host 'Installer: desktop/src-tauri/target/release/bundle/nsis/'
}
finally { Pop-Location }
