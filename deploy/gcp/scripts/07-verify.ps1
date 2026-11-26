. (Join-Path $PSScriptRoot "_load-env.ps1")

Assert-Gcloud
$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @("GCP_PROJECT", "GCP_ZONE", "GCP_VM_NAME", "DEPLOY_APP_DIR", "DOMAIN")

$project = $cfg["GCP_PROJECT"]
$zone = $cfg["GCP_ZONE"]
$vmName = $cfg["GCP_VM_NAME"]
$appDir = $cfg["DEPLOY_APP_DIR"]
$domain = $cfg["DOMAIN"].Trim().TrimEnd(".")
$staticIp = Resolve-StaticIp -EnvMap $cfg

Write-Host "==> [1/3] Checking DNS ($domain == $staticIp)..."
$resolved = Resolve-DnsName -Name $domain -Type A -ErrorAction Stop | Where-Object { $_.QueryType -eq "A" }
$resolved | Format-Table Name, IPAddress, TTL -AutoSize
if (($resolved | ForEach-Object { $_.IPAddress }) -notcontains $staticIp) {
    throw "DNS mismatch: expected $staticIp for $domain"
}

Write-Host "==> [2/3] Checking VM resources, containers, published ports, and systemd..."
$vmCheckCmd = @"
set -euo pipefail
echo "--- Memory & Swap ---"
free -h
echo "--- Docker Compose Services ---"
cd "$appDir"
sudo docker compose -f docker-compose.yml -f deploy/gcp/docker-compose.prod.yml ps
echo "--- Systemd Auto-Start ---"
systemctl is-enabled curtain-backend.service
"@
[void](Invoke-GcloudSshScript -VmName $vmName -Project $project -Zone $zone -Script $vmCheckCmd)

Write-Host "`n==> [3/3] Checking HTTPS endpoints on https://$domain..."
$health = Invoke-RestMethod -Uri "https://$domain/health" -TimeoutSec 15
Write-Host ("  /health    -> " + ($health | ConvertTo-Json -Compress))

$docsResp = Invoke-WebRequest -Uri "https://$domain/api/docs" -UseBasicParsing -TimeoutSec 15
Write-Host ("  /api/docs  -> HTTP " + $docsResp.StatusCode)

$spaResp = Invoke-WebRequest -Uri "https://$domain/" -UseBasicParsing -TimeoutSec 15
Write-Host ("  /          -> HTTP " + $spaResp.StatusCode)

Write-Host "`nAll verification checks passed for https://$domain" -ForegroundColor Green
