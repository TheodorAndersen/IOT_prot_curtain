param(
    [switch]$SkipDnsCheck
)

. (Join-Path $PSScriptRoot "_load-env.ps1")

Assert-Gcloud
$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @(
    "GCP_PROJECT",
    "GCP_ZONE",
    "GCP_VM_NAME",
    "DEPLOY_REPO_URL",
    "DEPLOY_APP_DIR",
    "DOMAIN",
    "CERTBOT_EMAIL",
    "POSTGRES_PASSWORD"
)

if ($cfg["CERTBOT_EMAIL"] -eq "you@example.com") {
    throw "CERTBOT_EMAIL in .env is still a placeholder. Set your real email first."
}

$project = $cfg["GCP_PROJECT"]
$zone = $cfg["GCP_ZONE"]
$vmName = $cfg["GCP_VM_NAME"]
$repoUrl = $cfg["DEPLOY_REPO_URL"]
$appDir = $cfg["DEPLOY_APP_DIR"]
$domain = $cfg["DOMAIN"].Trim().TrimEnd(".")
$staticIp = Resolve-StaticIp -EnvMap $cfg

if (-not $SkipDnsCheck) {
    Write-Host "==> Checking DNS A record for $domain matches $staticIp..."
    try {
        $ips = @(Resolve-DnsName -Name $domain -Type A -ErrorAction Stop | Where-Object { $_.QueryType -eq "A" } | ForEach-Object { $_.IPAddress })
    } catch {
        throw "DNS lookup for $domain failed. Run 04-dns.ps1 and wait for DNS propagation, or pass -SkipDnsCheck."
    }
    if ($ips -notcontains $staticIp) {
        throw "DNS for $domain resolved to [$($ips -join ', ')] instead of $staticIp. Wait for propagation before issuing Let's Encrypt cert."
    }
    Write-Host "DNS OK: $domain -> $staticIp"
}

Write-Host "==> Verifying VM bootstrap on '$vmName'..."
$checkBootstrapCmd = "test -f /var/log/curtain-bootstrap.done && docker compose version && swapon --show"
$bootCode = Invoke-GcloudSshScript -AllowFailure -VmName $vmName -Project $project -Zone $zone -Script $checkBootstrapCmd
if ($bootCode -ne 0) {
    throw "VM bootstrap not ready yet. Inspect /var/log/curtain-bootstrap.log on the VM and re-run once complete."
}

Write-Host "==> Cloning/updating repo at $appDir..."
$repoPrepCmd = @"
set -euo pipefail
if [[ ! -d "$appDir/.git" ]]; then
  sudo mkdir -p "$appDir"
  sudo chown "`$(id -un):`$(id -gn)" "$appDir"
  git clone "$repoUrl" "$appDir"
else
  cd "$appDir"
  git pull --ff-only
fi
"@
[void](Invoke-GcloudSshScript -VmName $vmName -Project $project -Zone $zone -Script $repoPrepCmd)

Write-Host "==> Uploading .env securely to $appDir/.env..."
$localEnv = Get-EnvFilePath
[void](Invoke-Gcloud -Arguments @(
    "compute", "scp", $localEnv, "${vmName}:/tmp/curtain.env.tmp",
    "--project=$project", "--zone=$zone"
))

$envInstallCmd = @"
set -euo pipefail
sed -i 's/\r$//' /tmp/curtain.env.tmp
mv /tmp/curtain.env.tmp "$appDir/.env"
chmod 600 "$appDir/.env"
"@
[void](Invoke-GcloudSshScript -VmName $vmName -Project $project -Zone $zone -Script $envInstallCmd)

Write-Host "==> Starting/updating production stack & TLS on '$vmName'..."
$deployCmd = @"
set -euo pipefail
cd "$appDir"
chmod +x deploy/gcp/init-letsencrypt.sh
if sudo docker compose -f docker-compose.yml -f deploy/gcp/docker-compose.prod.yml run --rm --no-deps --entrypoint sh certbot -c "test -s /etc/letsencrypt/live/$domain/fullchain.pem" >/dev/null 2>&1; then
  echo "Existing TLS certificate found for $domain; rebuilding and restarting services..."
  sudo docker compose -f docker-compose.yml -f deploy/gcp/docker-compose.prod.yml up -d --build
else
  echo "No TLS certificate found for $domain yet; running init-letsencrypt.sh..."
  sudo ./deploy/gcp/init-letsencrypt.sh
fi
sudo cp deploy/gcp/curtain-backend.service /etc/systemd/system/curtain-backend.service
sudo sed -i "s|/opt/iot_prot_curtain|$appDir|g" /etc/systemd/system/curtain-backend.service
sudo systemctl daemon-reload
sudo systemctl enable curtain-backend.service
"@
[void](Invoke-GcloudSshScript -VmName $vmName -Project $project -Zone $zone -Script $deployCmd)

Write-Host "`nDeployment complete. Run .\deploy\gcp\scripts\07-verify.ps1 to verify end-to-end."
