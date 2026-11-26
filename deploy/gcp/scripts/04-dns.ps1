param(
    [int]$Ttl = 300
)

. (Join-Path $PSScriptRoot "_load-env.ps1")

Assert-Gcloud
$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @("GCP_PROJECT", "GCP_DNS_ZONE", "DOMAIN")

$project = $cfg["GCP_PROJECT"]
$zoneName = $cfg["GCP_DNS_ZONE"]
$domain = $cfg["DOMAIN"].Trim().TrimEnd(".")
$fqdn = "$domain."
$staticIp = Resolve-StaticIp -EnvMap $cfg

Write-Host "==> Configuring Cloud DNS A record '$fqdn' -> $staticIp in zone '$zoneName' ($project)..."
$existingIp = Invoke-GcloudValue -Arguments @(
    "dns", "record-sets", "list",
    "--project=$project",
    "--zone=$zoneName",
    "--name=$fqdn",
    "--type=A",
    "--format=value(rrdatas[0])"
)
$existingIp = ($existingIp -split "`r?`n" | Select-Object -First 1).Trim()

if ($existingIp -eq $staticIp) {
    Write-Host "DNS A record '$fqdn' already points to $staticIp (TTL=${Ttl}s)."
} elseif (-not [string]::IsNullOrWhiteSpace($existingIp)) {
    Write-Host "Updating DNS A record '$fqdn' from $existingIp to $staticIp..."
    [void](Invoke-Gcloud -Arguments @(
        "dns", "record-sets", "update", $fqdn,
        "--project=$project",
        "--zone=$zoneName",
        "--type=A",
        "--ttl=$Ttl",
        "--rrdatas=$staticIp"
    ))
} else {
    Write-Host "Creating DNS A record '$fqdn' -> $staticIp..."
    [void](Invoke-Gcloud -Arguments @(
        "dns", "record-sets", "create", $fqdn,
        "--project=$project",
        "--zone=$zoneName",
        "--type=A",
        "--ttl=$Ttl",
        "--rrdatas=$staticIp"
    ))
}

Write-Host "`nCloud DNS verification:"
[void](Invoke-Gcloud -Arguments @(
    "dns", "record-sets", "list",
    "--project=$project",
    "--zone=$zoneName",
    "--name=$fqdn",
    "--type=A"
))

Write-Host "`nPublic DNS lookup ($domain):"
try {
    $resolved = Resolve-DnsName -Name $domain -Type A -ErrorAction Stop | Where-Object { $_.QueryType -eq "A" }
    $resolved | Format-Table Name, IPAddress, TTL -AutoSize
} catch {
    Write-Host "DNS not propagated to local resolver yet (check again in ~1-2 minutes)." -ForegroundColor Yellow
}

Write-Host "`nNext: verify bootstrap on the VM, then run .\deploy\gcp\scripts\06-deploy-app.ps1"
