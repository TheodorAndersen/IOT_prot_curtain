param(
    [string]$ApiBaseUrl = "",
    [string]$OutputDir = ""
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $repoRoot "deploy\gcp\scripts\_load-env.ps1")

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker CLI is not on PATH. Start Docker Desktop first."
}

$cfg = Import-DeployEnv

if ([string]::IsNullOrWhiteSpace($ApiBaseUrl)) {
    if ($cfg.ContainsKey("VITE_API_BASE_URL") -and -not [string]::IsNullOrWhiteSpace($cfg["VITE_API_BASE_URL"])) {
        $ApiBaseUrl = $cfg["VITE_API_BASE_URL"].Trim().TrimEnd("/")
    } elseif ($cfg.ContainsKey("DOMAIN") -and -not [string]::IsNullOrWhiteSpace($cfg["DOMAIN"])) {
        $dom = $cfg["DOMAIN"].Trim().TrimEnd(".")
        $ApiBaseUrl = "https://$dom/api"
    } else {
        throw "Set VITE_API_BASE_URL or DOMAIN in .env, or pass -ApiBaseUrl."
    }
} else {
    $ApiBaseUrl = $ApiBaseUrl.Trim().TrimEnd("/")
}

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $repoRoot "dist-mobile"
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$dockerfile = Join-Path $repoRoot "FrontEnd\Dockerfile.android"
$contextDir = Join-Path $repoRoot "FrontEnd"
$apkPath = Join-Path $OutputDir "SmartCurtain-debug.apk"

Write-Host "==> Building Android APK (API base: $ApiBaseUrl)..."
$env:DOCKER_BUILDKIT = "1"
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & docker build `
        -f $dockerfile `
        --target export `
        --output "type=local,dest=$OutputDir" `
        --build-arg "VITE_API_BASE_URL=$ApiBaseUrl" `
        $contextDir | Out-Host
    $code = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prev
}

if ($code -ne 0) {
    throw "Docker Android APK build failed with exit code $code."
}

if (-not (Test-Path -LiteralPath $apkPath)) {
    throw "Build succeeded but APK was not found at $apkPath."
}

$sizeMb = [Math]::Round(((Get-Item -LiteralPath $apkPath).Length / 1MB), 2)
Write-Host ""
Write-Host "Android APK ready:" -ForegroundColor Green
Write-Host "  Path : $apkPath ($sizeMb MB)"
Write-Host "  API  : $ApiBaseUrl"
Write-Host ""
Write-Host "To install on your Android phone:"
Write-Host "  - Quick Share / Google Drive / USB copy SmartCurtain-debug.apk and tap to install"
Write-Host "    (allow 'Install unknown apps' when Android prompts)."
Write-Host "  - Or over USB debugging: adb install -r `"$apkPath`""
