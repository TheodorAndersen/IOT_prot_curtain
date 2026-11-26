param(
    [int]$DiskSizeGb = 20,
    [string]$DiskType = "pd-standard"
)

. (Join-Path $PSScriptRoot "_load-env.ps1")

Assert-Gcloud
$cfg = Import-DeployEnv
Assert-EnvKeys -EnvMap $cfg -Keys @(
    "GCP_PROJECT",
    "GCP_REGION",
    "GCP_ZONE",
    "GCP_VM_NAME",
    "GCP_MACHINE_TYPE",
    "GCP_NETWORK_TAG",
    "GCP_STATIC_IP_NAME"
)

$project = $cfg["GCP_PROJECT"]
$zone = $cfg["GCP_ZONE"]
$vmName = $cfg["GCP_VM_NAME"]
$machineType = $cfg["GCP_MACHINE_TYPE"]
$tag = $cfg["GCP_NETWORK_TAG"]
$staticIp = Resolve-StaticIp -EnvMap $cfg

$bootstrapScript = Join-Path $PSScriptRoot "vm-bootstrap.sh"
if (-not (Test-Path -LiteralPath $bootstrapScript)) {
    throw "Missing startup script: $bootstrapScript"
}

Write-Host "==> Checking Compute Engine VM '$vmName' in $zone ($project)..."
if (Test-GcloudResource -Arguments @("compute", "instances", "describe", $vmName, "--project=$project", "--zone=$zone")) {
    Write-Host "VM '$vmName' already exists."
} else {
    Write-Host "Creating VM '$vmName' ($machineType, static IP $staticIp, Ubuntu 24.04 LTS, ${DiskSizeGb}GB $DiskType)..."
    [void](Invoke-Gcloud -Arguments @(
        "compute", "instances", "create", $vmName,
        "--project=$project",
        "--zone=$zone",
        "--machine-type=$machineType",
        "--image-family=ubuntu-2404-lts-amd64",
        "--image-project=ubuntu-os-cloud",
        "--boot-disk-size=${DiskSizeGb}GB",
        "--boot-disk-type=$DiskType",
        "--address=$staticIp",
        "--tags=$tag",
        "--metadata-from-file=startup-script=$bootstrapScript"
    ))
}

Write-Host "`nVerification output:"
[void](Invoke-Gcloud -Arguments @(
    "compute", "instances", "describe", $vmName,
    "--project=$project",
    "--zone=$zone",
    "--format=table(name,zone.basename(),machineType.basename(),status,networkInterfaces[0].accessConfigs[0].natIP,tags.items)"
))

Write-Host "`nVerify bootstrap (swap + Docker) completes cleanly (~1-2 min after first boot):"
Write-Host "  gcloud compute ssh $vmName --project=$project --zone=$zone --command='test -f /var/log/curtain-bootstrap.done && swapon --show && docker compose version'"
Write-Host "`nNext: run .\deploy\gcp\scripts\04-dns.ps1"
