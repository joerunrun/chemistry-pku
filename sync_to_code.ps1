param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployRoot = Join-Path $projectRoot "code"
$docsDirectoryName = "MD" + [char]0x8BF4 + [char]0x660E

if (-not (Test-Path -LiteralPath $deployRoot -PathType Container)) {
    throw "Deployment directory not found: $deployRoot"
}

$deployItems = @(
    "pipeline",
    "CV_detection2",
    "deploy",
    $docsDirectoryName,
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "README.md",
    "ali_client.py",
    "client_factory.py",
    "gemini_client.py",
    "image.png",
    "omni_client.py",
    "openrouter_client.py",
    "qiniu_client.py",
    "env.example"
)

$excludedDirectoryNames = @(
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "runtime_assets",
    "venv"
)

function Test-ExcludedPath {
    param([string]$Path)

    $parts = $Path -split "[\\/]"
    foreach ($part in $parts) {
        if ($excludedDirectoryNames -contains $part) {
            return $true
        }
    }
    return $Path.EndsWith(".pyc", [System.StringComparison]::OrdinalIgnoreCase)
}

function Sync-Directory {
    param(
        [string]$Source,
        [string]$Destination
    )

    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path
    $files = Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force -File |
        Where-Object { -not (Test-ExcludedPath $_.FullName) }

    foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($sourceRoot.Length).TrimStart("\", "/")
        $targetPath = Join-Path $Destination $relativePath
        Write-Host "$relativePath -> $targetPath"

        if ($Apply) {
            $targetParent = Split-Path -Parent $targetPath
            New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $targetPath -Force
        }
    }
}

foreach ($item in $deployItems) {
    $sourcePath = Join-Path $projectRoot $item
    $targetPath = Join-Path $deployRoot $item

    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Source item not found: $sourcePath"
    }

    if (Test-Path -LiteralPath $sourcePath -PathType Container) {
        Sync-Directory -Source $sourcePath -Destination $targetPath
        continue
    }

    Write-Host "$item -> $targetPath"
    if ($Apply) {
        Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
    }
}

if ($Apply) {
    Write-Host "Sync complete. No files were deleted. .env and runtime_assets were not copied."
} else {
    Write-Host "Preview only. Nothing was written. Run: powershell -NoProfile -ExecutionPolicy Bypass -File .\sync_to_code.ps1 -Apply"
}
