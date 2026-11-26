param(
    [string]$RuleName = "curtain-allow-http-https",
    [switch]$ConfirmTeardown,
    [switch]$Force
)

. (Join-Path $PSScriptRoot "_load-env.ps1")

$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @(
    "GCP_PROJECT",
    "GCP_REGION",
    "GCP_ZONE",
    "GCP_VM_NAME",
    "GCP_STATIC_IP_NAME",
    "GCP_DNS_ZONE",
    "DOMAIN"
)

$project = $cfg["GCP_PROJECT"]
$region = $cfg["GCP_REGION"]
$zone = $cfg["GCP_ZONE"]
$vmName = $cfg["GCP_VM_NAME"]
$ipName = $cfg["GCP_STATIC_IP_NAME"]
$dnsZone = $cfg["GCP_DNS_ZONE"]
$domain = $cfg["DOMAIN"].Trim().TrimEnd(".")
$fqdn = "$domain."
$rootDomain = if ($cfg.ContainsKey("GCP_ROOT_DOMAIN") -and $cfg["GCP_ROOT_DOMAIN"]) {
    $cfg["GCP_ROOT_DOMAIN"].Trim().TrimEnd(".")
} else {
    "(root domain registration)"
}

Write-Host "========================================================================" -ForegroundColor Yellow
Write-Host " GCP Smart Curtain Teardown Plan" -ForegroundColor Yellow
Write-Host "========================================================================" -ForegroundColor Yellow
Write-Host "Project : $project"
Write-Host ""
Write-Host "WILL DELETE (curtain workload & endpoint):" -ForegroundColor Red
Write-Host "  1. Cloud DNS A record : $fqdn (in zone '$dnsZone')"
Write-Host "  2. Compute Engine VM  : $vmName (zone $zone, plus boot disk)"
Write-Host "  3. VPC Firewall rule  : $RuleName"
Write-Host "  4. Static external IP : $ipName (region $region) + clear GCP_STATIC_IP in .env"
Write-Host ""
Write-Host "WILL PRESERVE (untouched):" -ForegroundColor Green
Write-Host "  - Cloud Domains registration : $rootDomain"
Write-Host "  - Cloud DNS managed zone     : $dnsZone (NS, SOA, and all other records)"
Write-Host "  - GCP project                : $project"
Write-Host "========================================================================" -ForegroundColor Yellow
Write-Host ""

if (-not $ConfirmTeardown) {
    Write-Host "[DRY RUN] No GCP resources were modified. Commands that would run:" -ForegroundColor Cyan
    Write-Host "  1. gcloud dns record-sets delete $fqdn --project=$project --zone=$dnsZone --type=A"
    Write-Host "  2. gcloud compute instances delete $vmName --project=$project --zone=$zone --quiet"
    Write-Host "  3. gcloud compute firewall-rules delete $RuleName --project=$project --quiet"
    Write-Host "  4. gcloud compute addresses delete $ipName --project=$project --region=$region --quiet"
    Write-Host ""
    Write-Host "Re-run with -ConfirmTeardown to actually delete these resources."
    exit 0
}

Assert-Gcloud

if (-not $Force) {
    $reply = Read-Host "Type TEARDOWN to permanently delete the curtain DNS record, VM, firewall rule, and static IP in $project"
    if ($reply -ne "TEARDOWN") {
        Write-Host "Aborted. No resources deleted."
        exit 1
    }
}

Write-Host "`n==> [1/4] Checking Cloud DNS A record '$fqdn' in zone '$dnsZone'..."
$existingDns = Invoke-GcloudValue -AllowFailure -Arguments @(
    "dns", "record-sets", "list",
    "--project=$project", "--zone=$dnsZone",
    "--name=$fqdn", "--type=A", "--format=value(name)"
)
if (-not [string]::IsNullOrWhiteSpace($existingDns)) {
    Write-Host "Deleting DNS A record '$fqdn'..."
    [void](Invoke-Gcloud -Arguments @(
        "dns", "record-sets", "delete", $fqdn,
        "--project=$project", "--zone=$dnsZone", "--type=A", "--quiet"
    ))
} else {
    Write-Host "DNS A record '$fqdn' not found (already deleted)."
}

Write-Host "`n==> [2/4] Checking Compute Engine VM '$vmName' in $zone..."
if (Test-GcloudResource -Arguments @("compute", "instances", "describe", $vmName, "--project=$project", "--zone=$zone")) {
    Write-Host "Deleting VM '$vmName' (this also deletes its boot disk)..."
    [void](Invoke-Gcloud -Arguments @(
        "compute", "instances", "delete", $vmName,
        "--project=$project", "--zone=$zone", "--quiet"
    ))
} else {
    Write-Host "VM '$vmName' not found (already deleted)."
}

Write-Host "`n==> [3/4] Checking VPC firewall rule '$RuleName'..."
if (Test-GcloudResource -Arguments @("compute", "firewall-rules", "describe", $RuleName, "--project=$project")) {
    Write-Host "Deleting firewall rule '$RuleName'..."
    [void](Invoke-Gcloud -Arguments @(
        "compute", "firewall-rules", "delete", $RuleName,
        "--project=$project", "--quiet"
    ))
} else {
    Write-Host "Firewall rule '$RuleName' not found (already deleted)."
}

Write-Host "`n==> [4/4] Checking regional static IP '$ipName' in $region..."
if (Test-GcloudResource -Arguments @("compute", "addresses", "describe", $ipName, "--project=$project", "--region=$region")) {
    Write-Host "Releasing static IP '$ipName'..."
    [void](Invoke-Gcloud -Arguments @(
        "compute", "addresses", "delete", $ipName,
        "--project=$project", "--region=$region", "--quiet"
    ))
} else {
    Write-Host "Static IP '$ipName' not found (already released)."
}

Set-EnvKey -Key "GCP_STATIC_IP" -Value ""
Write-Host "Cleared GCP_STATIC_IP in .env"

Write-Host "`n==> Post-teardown verification ($project):"
Write-Host "--- Compute Engine Instances ---"
[void](Invoke-Gcloud -Arguments @("compute", "instances", "list", "--project=$project"))
Write-Host "--- Regional Static IPs ---"
[void](Invoke-Gcloud -Arguments @("compute", "addresses", "list", "--project=$project"))
Write-Host "--- Curtain Firewall Rules ---"
[void](Invoke-Gcloud -Arguments @("compute", "firewall-rules", "list", "--project=$project", "--filter=name=$RuleName"))
Write-Host "--- Preserved Cloud DNS Zone '$dnsZone' Records ---"
[void](Invoke-Gcloud -Arguments @("dns", "record-sets", "list", "--project=$project", "--zone=$dnsZone"))

Write-Host "`nTeardown complete. Domain registration and DNS zone '$dnsZone' are preserved." -ForegroundColor Green
