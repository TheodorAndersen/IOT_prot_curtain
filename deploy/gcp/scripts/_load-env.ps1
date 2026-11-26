Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

function Get-EnvFilePath {
    return (Join-Path (Get-RepoRoot) ".env")
}

function Import-DeployEnv {
    $envPath = Get-EnvFilePath
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "Missing .env file at $envPath. Copy .env.example to .env and fill in your values first."
    }

    $map = @{}
    foreach ($rawLine in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }
        $eqIdx = $line.IndexOf("=")
        if ($eqIdx -lt 1) {
            continue
        }
        $key = $line.Substring(0, $eqIdx).Trim()
        $val = $line.Substring($eqIdx + 1).Trim()
        if ($val.Length -ge 2) {
            $first = $val[0]
            $last = $val[$val.Length - 1]
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }
        $map[$key] = $val
    }
    return $map
}

function Assert-EnvKeys {
    param(
        [Parameter(Mandatory = $true)][hashtable]$EnvMap,
        [Parameter(Mandatory = $true)][string[]]$Keys
    )
    $missing = @()
    foreach ($k in $Keys) {
        if (-not $EnvMap.ContainsKey($k) -or [string]::IsNullOrWhiteSpace($EnvMap[$k])) {
            $missing += $k
        }
    }
    if ($missing.Count -gt 0) {
        throw ("Missing or empty required keys in .env: " + ($missing -join ", "))
    }
}

function Assert-Gcloud {
    if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
        throw "gcloud CLI is not on PATH. Install Google Cloud SDK and run 'gcloud auth login' first."
    }
}

function Set-EnvKey {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value
    )
    $envPath = Get-EnvFilePath
    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $envPath) {
        foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
            $lines.Add($line)
        }
    }

    $formattedVal = if ($Value -match '\s' -and -not ($Value.StartsWith('"') -and $Value.EndsWith('"'))) {
        "`"$Value`""
    } else {
        $Value
    }
    $newLine = "$Key=$formattedVal"
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $updated = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = $newLine
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        $lines.Add($newLine)
    }
    Set-Content -LiteralPath $envPath -Value $lines -Encoding UTF8
}

function Invoke-Gcloud {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud @Arguments | Out-Host
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if (-not $AllowFailure -and $code -ne 0) {
        throw ("gcloud " + ($Arguments -join " ") + " failed with exit code $code.")
    }
    return [int]$code
}

function Invoke-GcloudSshScript {
    param(
        [Parameter(Mandatory = $true)][string]$VmName,
        [Parameter(Mandatory = $true)][string]$Project,
        [Parameter(Mandatory = $true)][string]$Zone,
        [Parameter(Mandatory = $true)][string]$Script,
        [switch]$AllowFailure
    )
    $lfScript = $Script -replace "`r`n", "`n"
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($lfScript))
    $remoteCmd = "echo $b64 | base64 -d > /tmp/_curtain_cmd.sh && bash /tmp/_curtain_cmd.sh && rm -f /tmp/_curtain_cmd.sh"
    return (Invoke-Gcloud -AllowFailure:$AllowFailure -Arguments @(
        "compute", "ssh", $VmName,
        "--project=$Project",
        "--zone=$Zone",
        "--command=$remoteCmd"
    ))
}

function Invoke-GcloudValue {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & gcloud @Arguments 2>$null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) {
        if ($AllowFailure) {
            return ""
        }
        throw ("gcloud " + ($Arguments -join " ") + " failed with exit code $code.")
    }
    if ($null -eq $raw) {
        return ""
    }
    return (($raw -join "`n").Trim())
}

function Test-GcloudResource {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & gcloud @Arguments *> $null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return ($code -eq 0)
}

function Resolve-StaticIp {
    param(
        [Parameter(Mandatory = $true)][hashtable]$EnvMap
    )
    if ($EnvMap.ContainsKey("GCP_STATIC_IP") -and -not [string]::IsNullOrWhiteSpace($EnvMap["GCP_STATIC_IP"])) {
        $candidate = $EnvMap["GCP_STATIC_IP"].Trim()
        if ($candidate -match '^\d{1,3}(\.\d{1,3}){3}$') {
            return $candidate
        }
    }

    Assert-EnvKeys -EnvMap $EnvMap -Keys @("GCP_PROJECT", "GCP_REGION", "GCP_STATIC_IP_NAME")
    $project = $EnvMap["GCP_PROJECT"]
    $region = $EnvMap["GCP_REGION"]
    $ipName = $EnvMap["GCP_STATIC_IP_NAME"]

    $ip = Invoke-GcloudValue -AllowFailure -Arguments @(
        "compute", "addresses", "describe", $ipName,
        "--project=$project", "--region=$region", "--format=value(address)"
    )
    if ([string]::IsNullOrWhiteSpace($ip)) {
        throw "Could not resolve static IP '$ipName' in $region ($project). Run 01-static-ip.ps1 first."
    }
    $ip = ($ip -split "`r?`n" | Select-Object -First 1).Trim()
    Set-EnvKey -Key "GCP_STATIC_IP" -Value $ip
    $EnvMap["GCP_STATIC_IP"] = $ip
    return $ip
}
