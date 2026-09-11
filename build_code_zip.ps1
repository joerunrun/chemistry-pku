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
    $OutputPath = Join-Path $projectRoot "code_deploy.zip"
}
$absoluteOutput = [System.IO.Path]::GetFullPath($OutputPath)
$temporaryOutput = "$absoluteOutput.tmp"

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
if (Test-Path -LiteralPath $temporaryOutput) {
    Remove-Item -LiteralPath $temporaryOutput -Force
}
$archive = [System.IO.Compression.ZipFile]::Open(
    $temporaryOutput,
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

$check = [System.IO.Compression.ZipFile]::OpenRead($temporaryOutput)
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

if (Test-Path -LiteralPath $absoluteOutput) {
    Remove-Item -LiteralPath $absoluteOutput -Force
}
Move-Item -LiteralPath $temporaryOutput -Destination $absoluteOutput

$sizeMb = [math]::Round((Get-Item -LiteralPath $absoluteOutput).Length / 1MB, 2)
Write-Host "Safe deployment archive created: $absoluteOutput"
Write-Host "Files: $count; Size: $sizeMb MB"
Write-Host ".env and runtime_assets were verified absent."
