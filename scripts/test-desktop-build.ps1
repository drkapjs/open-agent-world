# Exercise real drive mapping/cleanup while replacing only npm's expensive build.
$ErrorActionPreference = 'Stop'
$originalExitCode = $global:LASTEXITCODE
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixture = Join-Path $workspace ('.tmp/desktop build fixture ' + [guid]::NewGuid().ToString('N'))
$originalLocation = (Get-Location).Path
$global:OawDesktopBuildFixture = @{ exitCode = 0; patch = $null; drive = $null }
$savedEnvironment = @{}
foreach ($name in @('UV_CACHE_DIR', 'CARGO_HOME', 'npm_config_cache')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $fixture 'scripts'), (Join-Path $fixture 'desktop'), (Join-Path $fixture '.open-agent-world/desktop-payload') | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'build-desktop.ps1') -Destination (Join-Path $fixture 'scripts/build-desktop.ps1')
    Set-Content -LiteralPath (Join-Path $fixture '.open-agent-world/desktop-payload/source.txt') -Value 'payload fixture'
    function npm.cmd {
        $global:LASTEXITCODE = 0
        if ($args[0] -eq 'ci') { return }
        # PowerShell consumes the -- token when invoking a function shim.
        if (($args[0..2] -join ' ') -ne 'run build --config') { throw 'Missing Tauri config patch' }
        $global:OawDesktopBuildFixture.patch = $args[3]
        $patch = Get-Content -Raw -LiteralPath $global:OawDesktopBuildFixture.patch | ConvertFrom-Json
        $properties = @($patch.bundle.resources.PSObject.Properties)
        if ($properties.Count -ne 2) { throw 'Unexpected resource overrides' }
        $old = $properties | Where-Object Name -eq '../../.open-agent-world/desktop-payload/'
        if (-not $old -or $null -ne $old.Value) { throw 'Long resource key was not removed by merge patch' }
        $alias = $properties | Where-Object Value -eq 'payload/'
        $global:OawDesktopBuildFixture.drive = $alias.Name.TrimEnd('/')
        if ((Get-Content -LiteralPath ($alias.Name + 'source.txt')) -ne 'payload fixture') { throw 'Alias does not point to payload' }
        if (@($patch.PSObject.Properties).Count -ne 1 -or @($patch.bundle.PSObject.Properties).Count -ne 1) { throw 'Patch changed unrelated configuration' }
        $global:LASTEXITCODE = $global:OawDesktopBuildFixture.exitCode
    }
    foreach ($code in @(0, 7)) {
        $global:OawDesktopBuildFixture.exitCode = $code
        $global:OawDesktopBuildFixture.drive = $null
        $global:OawDesktopBuildFixture.patch = $null
        $failure = $null
        try { & (Join-Path $fixture 'scripts/build-desktop.ps1') -SkipFrontend -SkipPayload }
        catch { $failure = $_ }
        if (($code -eq 0) -and $failure) { throw $failure }
        if (($code -ne 0) -and (!$failure -or $failure.Exception.Message -ne 'Desktop installer build failed.')) { throw 'Build failure was not propagated' }
        if (!$global:OawDesktopBuildFixture.drive -or (Test-Path -LiteralPath ($global:OawDesktopBuildFixture.drive + '/source.txt'))) { throw 'Temporary drive was not released' }
        if (Test-Path -LiteralPath $global:OawDesktopBuildFixture.patch) { throw 'Temporary config was not removed' }
        if ((Get-Location).Path -ne $originalLocation) { throw 'Build changed caller location' }
    }
    Write-Host 'PASS: real payload drive, isolated config, failure propagation and cleanup.'
}
finally {
    Remove-Item -LiteralPath Function:\npm.cmd -ErrorAction SilentlyContinue
    Remove-Variable -Name OawDesktopBuildFixture -Scope Global
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name]) }
    $global:LASTEXITCODE = $originalExitCode
    if (Test-Path -LiteralPath $fixture) {
        $resolved = (Resolve-Path -LiteralPath $fixture).Path
        if (-not $resolved.StartsWith($workspace + '\.tmp\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture cleanup path' }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
