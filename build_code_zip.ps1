param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceRoot = Join-Path $projectRoot "code"
if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "Deployment source directory not found: $sourceRoot"
}

if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path $projectRoot "code_deploy_$timestamp.zip"
}
$absoluteOutput = [System.IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $absoluteOutput) {
    throw "Output already exists; choose a new path: $absoluteOutput"
}

$excludedDirectoryNames = @(
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "runtime_assets",
    "venv"
)

function Test-ExcludedFile {
    param([System.IO.FileInfo]$File)

    $relativePath = $File.FullName.Substring($sourceRoot.Length).TrimStart("\", "/")
    $normalizedPath = $relativePath.Replace([char]0x5C, [char]0x2F)
    $parts = $normalizedPath.Split("/")
    foreach ($part in $parts) {
        if ($excludedDirectoryNames -contains $part) {
            return $true
        }
    }
    if ($relativePath -eq ".env") {
        return $true
    }
    return $File.Extension -in @(".pyc", ".log", ".zip")
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$outputParent = Split-Path -Parent $absoluteOutput
New-Item -ItemType Directory -Force -Path $outputParent | Out-Null
$archive = [System.IO.Compression.ZipFile]::Open(
    $absoluteOutput,
    [System.IO.Compression.ZipArchiveMode]::Create
)
$count = 0
try {
    $files = Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force -File |
        Where-Object { -not (Test-ExcludedFile $_) }
    foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($sourceRoot.Length).TrimStart("\", "/")
        $entryName = $relativePath.Replace("\", "/")
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive,
            $file.FullName,
            $entryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
        $count += 1
    }
}
finally {
    $archive.Dispose()
}

$check = [System.IO.Compression.ZipFile]::OpenRead($absoluteOutput)
try {
    $forbidden = @(
        $check.Entries |
            Where-Object {
                $_.FullName -eq ".env" -or
                $_.FullName.StartsWith("runtime_assets/", [System.StringComparison]::OrdinalIgnoreCase)
            }
    )
    if ($forbidden.Count -gt 0) {
        throw "Protected data was unexpectedly included in the archive."
    }
}
finally {
    $check.Dispose()
}

$sizeMb = [math]::Round((Get-Item -LiteralPath $absoluteOutput).Length / 1MB, 2)
Write-Host "Safe deployment archive created: $absoluteOutput"
Write-Host "Files: $count; Size: $sizeMb MB"
Write-Host ".env and runtime_assets were verified absent."
