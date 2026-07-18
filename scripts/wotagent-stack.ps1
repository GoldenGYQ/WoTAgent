[CmdletBinding()]
param(
    [ValidateSet("start", "start-safe", "remote", "private", "status", "stop")]
    [string]$Action = "remote"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $repoRoot "wotagent-prototype"
$simulatorDir = Join-Path $repoRoot "wot-device-simulator"
$frontendDir = Join-Path $repoRoot "frontend\wotagent-v2-console"
$runtimeDir = Join-Path $env:LOCALAPPDATA "WoTAgent\stack"
$logDir = Join-Path $runtimeDir "logs"

$services = @{
    simulator = @{
        Executable = Join-Path $simulatorDir ".venv\Scripts\wot-sim-api.exe"
        Arguments = @()
        WorkingDirectory = $simulatorDir
        Port = 18080
        HealthUrl = "http://127.0.0.1:18080/"
    }
    backend = @{
        Executable = Join-Path $backendDir ".venv\Scripts\wotagent.exe"
        Arguments = @("api", "--host", "127.0.0.1", "--port", "8000")
        WorkingDirectory = $backendDir
        Port = 8000
        HealthUrl = "http://127.0.0.1:8000/api/health"
    }
    monitor = @{
        Executable = Join-Path $backendDir ".venv\Scripts\wotagent.exe"
        Arguments = @("monitor", "--interval", "2")
        WorkingDirectory = $backendDir
        Port = $null
        HealthUrl = $null
    }
    frontend = @{
        Executable = "npm.cmd"
        Arguments = @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5174", "--strictPort")
        WorkingDirectory = $frontendDir
        Port = 5174
        HealthUrl = "http://127.0.0.1:5174/"
    }
}

function Write-Step {
    param([string]$Message)
    Write-Host "[WoTAgent] $Message" -ForegroundColor Cyan
}

function Get-PidFile {
    param([string]$Name)
    return Join-Path $runtimeDir "$Name.pid"
}

function Get-TrackedProcess {
    param([string]$Name)
    $pidFile = Get-PidFile $Name
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return $null
    }

    $savedPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
    if (-not $savedPid) {
        return $null
    }

    $process = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    return $process
}

function Get-PortOwner {
    param([int]$Port)
    return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Test-HttpEndpoint {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpEndpoint $Url) {
            Write-Step "$Name is ready: $Url"
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not become healthy within $TimeoutSeconds seconds. Check $logDir."
}

function Assert-Dependencies {
    foreach ($name in @("simulator", "backend", "monitor")) {
        $executable = $services[$name].Executable
        if (-not (Test-Path -LiteralPath $executable)) {
            throw "Missing $executable. Run the existing dependency setup for $name first."
        }
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "npm.cmd was not found. Install Node.js LTS first."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "node_modules"))) {
        throw "Frontend dependencies are missing. Run npm install in $frontendDir first."
    }
}

function Start-StackService {
    param([string]$Name)

    $definition = $services[$Name]
    $tracked = Get-TrackedProcess $Name
    if ($tracked) {
        Write-Step "$Name is already managed (PID $($tracked.Id))."
        return
    }

    if ($definition.Port) {
        $listener = Get-PortOwner $definition.Port
        if ($listener) {
            throw "Port $($definition.Port) is already owned by PID $($listener.OwningProcess). Stop that process or run stop-wotagent.bat if it belongs to this stack."
        }
    }

    $stdout = Join-Path $logDir "$Name.stdout.log"
    $stderr = Join-Path $logDir "$Name.stderr.log"
    $startParameters = @{
        FilePath = $definition.Executable
        WorkingDirectory = $definition.WorkingDirectory
        WindowStyle = "Hidden"
        RedirectStandardOutput = $stdout
        RedirectStandardError = $stderr
        PassThru = $true
    }
    if ($definition.Arguments.Count -gt 0) {
        $startParameters.ArgumentList = $definition.Arguments
    }
    $process = Start-Process @startParameters
    Set-Content -LiteralPath (Get-PidFile $Name) -Value $process.Id -Encoding ASCII
    Write-Step "Started $name (PID $($process.Id))."

    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        throw "$Name exited immediately with code $($process.ExitCode). Check $stderr."
    }
    if ($definition.HealthUrl) {
        Wait-HttpEndpoint -Name $Name -Url $definition.HealthUrl
    }
}

function Start-Stack {
    param([bool]$EnableEventConsumer)

    Assert-Dependencies
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null

    $oldConsumer = $env:ELDERCARE_EVENT_CONSUMER_ENABLED
    $oldMcpUrl = $env:MCP_SERVER_URL
    try {
        $env:ELDERCARE_EVENT_CONSUMER_ENABLED = if ($EnableEventConsumer) { "true" } else { "false" }
        $env:MCP_SERVER_URL = "http://127.0.0.1:18080/mcp"

        Start-StackService "simulator"
        Start-StackService "backend"
        Start-StackService "monitor"
        Start-StackService "frontend"
    }
    finally {
        $env:ELDERCARE_EVENT_CONSUMER_ENABLED = $oldConsumer
        $env:MCP_SERVER_URL = $oldMcpUrl
    }

    Write-Host ""
    Write-Host "Local console: http://127.0.0.1:5174/" -ForegroundColor Green
    Write-Host "Logs: $logDir"
    if (-not $EnableEventConsumer) {
        Write-Host "Safe mode: Monitor events are collected but not submitted to the LLM." -ForegroundColor Yellow
    }
}

function Stop-StackService {
    param([string]$Name)
    $process = Get-TrackedProcess $Name
    if (-not $process) {
        Write-Step "$Name is not managed by this script."
        return
    }

    & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
    Remove-Item -LiteralPath (Get-PidFile $Name) -Force -ErrorAction SilentlyContinue
    Write-Step "Stopped $name."
}

function Find-Tailscale {
    $command = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $installed = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path -LiteralPath $installed) {
        return $installed
    }
    return $null
}

function Install-Tailscale {
    Write-Step "Tailscale is not installed. Starting the official winget package installer."
    Write-Host "Windows may ask for administrator approval. Complete that prompt to continue." -ForegroundColor Yellow
    $arguments = @(
        "install",
        "--id", "Tailscale.Tailscale",
        "--exact",
        "--accept-package-agreements",
        "--accept-source-agreements"
    )
    $installer = Start-Process -FilePath "winget.exe" -ArgumentList $arguments -Wait -PassThru
    if ($installer.ExitCode -ne 0) {
        throw "Tailscale installation failed with exit code $($installer.ExitCode)."
    }

    $tailscale = Find-Tailscale
    if (-not $tailscale) {
        throw "Tailscale installation completed but tailscale.exe was not found. Reopen the terminal and run this script again."
    }
    return $tailscale
}

function Enable-PrivateRemoteAccess {
    $tailscale = Find-Tailscale
    if (-not $tailscale) {
        $tailscale = Install-Tailscale
    }

    $status = $null
    try {
        $status = (& $tailscale status --json 2>$null | ConvertFrom-Json)
    }
    catch {
        $status = $null
    }

    if (-not $status -or $status.BackendState -ne "Running") {
        Write-Step "Tailscale login is required. Follow the browser sign-in prompt."
        & $tailscale up
        if ($LASTEXITCODE -ne 0) {
            throw "Tailscale could not connect. Complete login, then rerun start-wotagent-remote.bat."
        }
        $status = (& $tailscale status --json | ConvertFrom-Json)
    }

    & $tailscale serve --bg http://127.0.0.1:5174
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale Serve could not publish the local frontend."
    }

    $dnsName = [string]$status.Self.DNSName
    $remoteUrl = if ($dnsName) { "https://$($dnsName.TrimEnd('.'))/" } else { "Run 'tailscale serve status' to see the URL." }
    Set-Content -LiteralPath (Join-Path $runtimeDir "remote-url.txt") -Value $remoteUrl -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $runtimeDir "tailscale-serve.enabled") -Value "true" -Encoding ASCII

    Write-Host ""
    Write-Host "Private phone URL: $remoteUrl" -ForegroundColor Green
    Write-Host "The phone must run Tailscale and join the same tailnet." -ForegroundColor Yellow
    & $tailscale serve status
}

function Disable-PrivateRemoteAccess {
    $marker = Join-Path $runtimeDir "tailscale-serve.enabled"
    if (-not (Test-Path -LiteralPath $marker)) {
        return
    }
    $tailscale = Find-Tailscale
    if ($tailscale) {
        & $tailscale serve reset 2>$null | Out-Null
    }
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $runtimeDir "remote-url.txt") -Force -ErrorAction SilentlyContinue
}

function Find-Cloudflared {
    $command = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $installed = Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe"
    if (Test-Path -LiteralPath $installed) {
        return $installed
    }
    return $null
}

function Get-QuickTunnelUrl {
    param([string[]]$LogFiles)
    foreach ($logFile in $LogFiles) {
        if (-not (Test-Path -LiteralPath $logFile)) {
            continue
        }
        $content = Get-Content -LiteralPath $logFile -Raw -ErrorAction SilentlyContinue
        if (-not $content) {
            continue
        }
        $match = [regex]::Match($content, "https://[a-z0-9-]+\.trycloudflare\.com")
        if ($match.Success) {
            return "$($match.Value)/"
        }
    }
    return $null
}

function Enable-QuickTunnel {
    $cloudflared = Find-Cloudflared
    if (-not $cloudflared) {
        throw "cloudflared.exe was not found. Install Cloudflare cloudflared first."
    }

    $remoteFile = Join-Path $runtimeDir "remote-url.txt"
    $stdout = Join-Path $logDir "cloudflared.stdout.log"
    $stderr = Join-Path $logDir "cloudflared.stderr.log"
    $tracked = Get-TrackedProcess "cloudflared"
    if ($tracked -and (Test-Path -LiteralPath $remoteFile)) {
        Write-Host "Public phone URL: $(Get-Content -LiteralPath $remoteFile)" -ForegroundColor Green
        return
    }
    if ($tracked) {
        $existingUrl = Get-QuickTunnelUrl -LogFiles @($stdout, $stderr)
        if ($existingUrl) {
            Set-Content -LiteralPath $remoteFile -Value $existingUrl -Encoding UTF8
            Write-Host "Public phone URL: $existingUrl" -ForegroundColor Green
            return
        }
        throw "A managed cloudflared process is running without a published URL. Run stop-wotagent.bat and retry."
    }

    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    $process = Start-Process `
        -FilePath $cloudflared `
        -ArgumentList @("tunnel", "--url", "http://127.0.0.1:5174", "--no-autoupdate") `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru
    Set-Content -LiteralPath (Get-PidFile "cloudflared") -Value $process.Id -Encoding ASCII

    $deadline = (Get-Date).AddSeconds(45)
    $remoteUrl = $null
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) {
            throw "cloudflared exited with code $($process.ExitCode). Check $stderr."
        }
        $remoteUrl = Get-QuickTunnelUrl -LogFiles @($stdout, $stderr)
        if ($remoteUrl) {
            break
        }
        Start-Sleep -Milliseconds 500
        $process.Refresh()
    }

    if (-not $remoteUrl) {
        throw "Cloudflare Quick Tunnel did not return a URL within 45 seconds. Check $stderr."
    }

    Set-Content -LiteralPath $remoteFile -Value $remoteUrl -Encoding UTF8
    Write-Host ""
    Write-Host "Public phone URL: $remoteUrl" -ForegroundColor Green
    Write-Host "Temporary public tunnel: run stop-wotagent.bat immediately after testing." -ForegroundColor Yellow
}

function Disable-QuickTunnel {
    $process = Get-TrackedProcess "cloudflared"
    if ($process) {
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        Remove-Item -LiteralPath (Get-PidFile "cloudflared") -Force -ErrorAction SilentlyContinue
        Write-Step "Stopped Cloudflare Quick Tunnel."
    }
    Remove-Item -LiteralPath (Join-Path $runtimeDir "remote-url.txt") -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    Write-Host "WoTAgent stack status" -ForegroundColor Cyan
    foreach ($name in @("simulator", "backend", "monitor", "frontend")) {
        $definition = $services[$name]
        $tracked = Get-TrackedProcess $name
        $healthy = if ($definition.HealthUrl) { Test-HttpEndpoint $definition.HealthUrl } else { [bool]$tracked }
        $pidText = if ($tracked) { "PID $($tracked.Id)" } else { "unmanaged/stopped" }
        $healthText = if ($healthy) { "ready" } else { "not ready" }
        Write-Host ("{0,-10} {1,-20} {2}" -f $name, $pidText, $healthText)
    }

    $remoteFile = Join-Path $runtimeDir "remote-url.txt"
    if (Test-Path -LiteralPath $remoteFile) {
        $remoteUrl = Get-Content -LiteralPath $remoteFile
        $quickTunnel = Get-TrackedProcess "cloudflared"
        if ($remoteUrl -match "trycloudflare\.com" -and -not $quickTunnel) {
            Write-Host "remote     stale record (cloudflared is stopped)"
        }
        else {
            Write-Host "remote     $remoteUrl"
        }
    }
    else {
        Write-Host "remote     disabled"
    }
    Write-Host "logs       $logDir"
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

switch ($Action) {
    "start" {
        Start-Stack -EnableEventConsumer $true
    }
    "start-safe" {
        Start-Stack -EnableEventConsumer $false
    }
    "remote" {
        Start-Stack -EnableEventConsumer $true
        Enable-QuickTunnel
    }
    "private" {
        Start-Stack -EnableEventConsumer $true
        Enable-PrivateRemoteAccess
    }
    "status" {
        Show-Status
    }
    "stop" {
        Disable-QuickTunnel
        Disable-PrivateRemoteAccess
        foreach ($name in @("frontend", "monitor", "backend", "simulator")) {
            Stop-StackService $name
        }
        Write-Step "Stack stopped."
    }
}
