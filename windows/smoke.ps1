$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$repo = Split-Path $PSScriptRoot
$scratch = Join-Path $env:TEMP ("gm-windows-smoke-" + [Guid]::NewGuid())
New-Item -ItemType Directory $scratch | Out-Null
$package = Join-Path $repo "dist/GooglePerksManager-Windows-x64-Setup.exe"
$installation = Join-Path $scratch "Application"
$installer = Start-Process $package -ArgumentList ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + $installation + '"') -Wait -PassThru
if ($installer.ExitCode -ne 0) { throw "Installer exited with $($installer.ExitCode)." }
Write-Host "Windows setup completed."

$app = Join-Path $installation "GooglePerksManager.exe"
$service = Join-Path $installation "backend/GoogleManagerService.exe"
$env:GOOGLE_MANAGER_DATA_DIR = Join-Path $scratch "LocalData"
$env:PYTHONUTF8 = "1"
$env:GOOGLE_MANAGER_STARTUP_LOG = Join-Path $scratch "startup.log"
$guide = & $service --agent guide | ConvertFrom-Json
if (!$guide.commands.state) { throw "The installed service could not run its Agent CLI." }
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=$port"
$desktop = Start-Process $app -PassThru
try {
    $debug = "http://127.0.0.1:$port"
    $ready = $false
    for ($attempt = 0; $attempt -lt 24; $attempt++) {
        if ($desktop.HasExited) { throw "The installed app exited before displaying a window." }
        try {
            $metadata = Invoke-RestMethod "$debug/json/version" -TimeoutSec 1 -NoProxy
            if ($metadata.webSocketDebuggerUrl) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (!$ready) { throw "The installed native window did not initialize WebView2." }
    Write-Host "Windows native WebView2 is reachable."

    npm install --prefix $scratch --no-audit --no-fund playwright-core@1.65.1
    $env:GM_PLAYWRIGHT = Join-Path $scratch "node_modules/playwright-core"
    node (Join-Path $PSScriptRoot "smoke.cjs") $debug $repo

    $rows = & $service --agent state | ConvertFrom-Json
    if ($rows.accounts.Count -ne 1 -or $rows.accounts[0].name -ne "Windows test") { throw "The packaged Agent CLI did not read the test profile." }
    $picture = Join-Path $scratch "picture.mp4"
    $sound = Join-Path $scratch "sound.wav"
    if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue)) { choco install ffmpeg --yes --no-progress }
    ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=blue:s=320x192:r=24:d=1" -c:v libx264 -pix_fmt yuv420p $picture
    ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=44100:duration=2" $sound
    $job = (& $service --agent mux --title "Windows test mix" --video-path $picture --audio-path $sound | ConvertFrom-Json).job
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        $job = (& $service --agent job $job.id | ConvertFrom-Json).job
        if ($job.state -in @("done", "failed", "interrupted")) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($job.state -ne "done") { throw "The Windows media engine did not finish the fixture. State: $($job.state). Phase: $($job.phase)." }
    $output = Join-Path $env:GOOGLE_MANAGER_DATA_DIR "production/$($job.id)/output.mp4"
    $probe = ffprobe -v error -show_entries stream=codec_type -of json $output | ConvertFrom-Json
    if ($probe.streams.codec_type -notcontains "audio" -or $probe.streams.codec_type -notcontains "video") {
        throw "The Windows export must contain both audio and video."
    }
    $reportFile = Join-Path $repo "dist/windows-smoke.json"
    $report = Get-Content $reportFile -Raw | ConvertFrom-Json -AsHashtable
    $report.agentCli = $true
    $report.mediaExport = @{ audio = $true; video = $true; seconds = $job.artifact.seconds }
    $report.installerSHA256 = (Get-FileHash $package -Algorithm SHA256).Hash.ToLowerInvariant()
    $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 $reportFile
} finally {
    if (Test-Path $env:GOOGLE_MANAGER_STARTUP_LOG) {
        Get-Content $env:GOOGLE_MANAGER_STARTUP_LOG
    }
    if (!$desktop.HasExited) {
        $null = $desktop.CloseMainWindow()
        if (!$desktop.WaitForExit(4000)) { $desktop.Kill($true) }
    }
}
