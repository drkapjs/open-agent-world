[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Push-Location $projectRoot
try {
    foreach ($tool in @("uv", "node", "npm.cmd")) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            throw "Missing prerequisite: $tool. Install uv and Node.js 24 or newer (including npm)."
        }
    }
    & node -e 'process.exit(parseInt(process.versions.node, 10) >= 24 ? 0 : 1)'
    if ($LASTEXITCODE -ne 0) { throw "Node.js 24 or newer is required." }

    $uvArguments = @(
        "sync", "--project", "backend", "--dev",
        "--extra", "adk",
        "--extra", "litellm"
    )
    & uv @uvArguments
    if ($LASTEXITCODE -ne 0) { throw "Python environment setup failed." }

    & npm.cmd --prefix frontend ci --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "Frontend environment setup failed." }
    & npm.cmd --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw "Production frontend build failed." }
}
finally {
    Pop-Location
}
