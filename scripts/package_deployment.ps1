param(
    [string]$OutputRoot = "release"
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$output = Join-Path $project $OutputRoot

if (Test-Path -LiteralPath $output) {
    throw "Output already exists: $output. Choose another -OutputRoot or remove it after checking."
}

function Copy-ProjectDir([string]$name) {
    Copy-Item -LiteralPath (Join-Path $project $name) -Destination (Join-Path $server $name) -Recurse
}

$server = Join-Path $output "server"
New-Item -ItemType Directory -Path $server | Out-Null

foreach ($name in @("backend", "frontend", "migrations", "scripts", "docs")) { Copy-ProjectDir $name }
foreach ($name in @("run.py", "requirements.txt", "package.json", "package-lock.json")) {
    Copy-Item -LiteralPath (Join-Path $project $name) -Destination (Join-Path $server $name)
}
Copy-Item -LiteralPath (Join-Path $project "deploy\server\Dockerfile") -Destination (Join-Path $server "Dockerfile")
Copy-Item -LiteralPath (Join-Path $project "deploy\server\docker-compose.yml") -Destination (Join-Path $server "docker-compose.yml")
Copy-Item -LiteralPath (Join-Path $project "deploy\server\Caddyfile") -Destination (Join-Path $server "Caddyfile")
Copy-Item -LiteralPath (Join-Path $project "deploy\server\.env.example") -Destination (Join-Path $server ".env.example")
Copy-Item -LiteralPath (Join-Path $project "deploy\server\README.md") -Destination (Join-Path $server "README.md")

@"
StoryLingo deployment package
Generated: $(Get-Date -Format s)
Server: $server
TTS service is maintained separately at <workspace-root>\TTS

The Server package intentionally excludes local F5 models/runtime/CUDA.
Configure the external TTS service independently of this package.
"@ | Set-Content -LiteralPath (Join-Path $output "README.txt") -Encoding UTF8

Write-Output "Created deployment package: $output"
