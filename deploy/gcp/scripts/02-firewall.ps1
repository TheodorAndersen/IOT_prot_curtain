param(
    [string]$RuleName = "curtain-allow-http-https"
)

. (Join-Path $PSScriptRoot "_load-env.ps1")

Assert-Gcloud
$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @("GCP_PROJECT", "GCP_NETWORK_TAG")

$project = $cfg["GCP_PROJECT"]
$tag = $cfg["GCP_NETWORK_TAG"]

Write-Host "==> Checking VPC firewall rule '$RuleName' in $project..."
if (Test-GcloudResource -Arguments @("compute", "firewall-rules", "describe", $RuleName, "--project=$project")) {
    Write-Host "Firewall rule '$RuleName' already exists (keeping Postgres 5432 and backend 8000 closed)."
} else {
    Write-Host "Creating firewall rule '$RuleName' (allow tcp:80,tcp:443 to tag '$tag')..."
    [void](Invoke-Gcloud -Arguments @(
        "compute", "firewall-rules", "create", $RuleName,
        "--project=$project",
        "--allow=tcp:80,tcp:443",
        "--target-tags=$tag",
        "--source-ranges=0.0.0.0/0",
        "--description=Allow HTTP/HTTPS traffic to IoT curtain Nginx proxy"
    ))
}

Write-Host "`nVerification output:"
[void](Invoke-Gcloud -Arguments @(
    "compute", "firewall-rules", "describe", $RuleName,
    "--project=$project",
    "--format=table(name,direction,allowed,sourceRanges,targetTags)"
))

Write-Host "`nNext: run .\deploy\gcp\scripts\03-vm.ps1"
