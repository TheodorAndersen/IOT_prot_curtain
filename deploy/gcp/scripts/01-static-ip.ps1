. (Join-Path $PSScriptRoot "_load-env.ps1")

Assert-Gcloud
$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @("GCP_PROJECT", "GCP_REGION", "GCP_STATIC_IP_NAME")

$project = $cfg["GCP_PROJECT"]
$region = $cfg["GCP_REGION"]
$ipName = $cfg["GCP_STATIC_IP_NAME"]

Write-Host "==> Checking regional static IP '$ipName' in $region ($project)..."
if (Test-GcloudResource -Arguments @("compute", "addresses", "describe", $ipName, "--project=$project", "--region=$region")) {
    Write-Host "Static IP '$ipName' already exists."
} else {
    Write-Host "Creating regional static IP '$ipName'..."
    [void](Invoke-Gcloud -Arguments @(
        "compute", "addresses", "create", $ipName,
        "--project=$project",
        "--region=$region",
        "--network-tier=PREMIUM"
    ))
}

$ipRaw = Invoke-GcloudValue -Arguments @(
    "compute", "addresses", "describe", $ipName,
    "--project=$project", "--region=$region", "--format=value(address)"
)
$ip = ($ipRaw -split "`r?`n" | Select-Object -First 1).Trim()
if ([string]::IsNullOrWhiteSpace($ip)) {
    throw "Static IP '$ipName' was created/found but returned an empty address."
}

Set-EnvKey -Key "GCP_STATIC_IP" -Value $ip
Write-Host "Updated GCP_STATIC_IP=$ip in .env"

Write-Host "`nVerification output:"
[void](Invoke-Gcloud -Arguments @(
    "compute", "addresses", "describe", $ipName,
    "--project=$project",
    "--region=$region",
    "--format=table(name,address,region,status,addressType,networkTier)"
))

Write-Host "`nNext: run .\deploy\gcp\scripts\02-firewall.ps1"
