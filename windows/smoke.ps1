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
$picture = Join-Path $scratch "picture.mp4"
$sound = Join-Path $scratch "sound.wav"
if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue)) { choco install ffmpeg --yes --no-progress }
ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=blue:s=320x192:r=24:d=1" -c:v libx264 -pix_fmt yuv420p $picture
ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:sample_rate=44100:duration=2" $sound
$reportFile = Join-Path $scratch "windows-smoke.json"
$desktop = Start-Process $app -ArgumentList ('--verify-installation "' + $reportFile + '"') -PassThru
try {
    $discovery = Join-Path $env:GOOGLE_MANAGER_DATA_DIR ".agent-runtime.json"
    for ($attempt = 0; $attempt -lt 100 -and !(Test-Path $discovery); $attempt++) {
        if ($desktop.HasExited) { throw "The installed app exited before starting the service." }
        Start-Sleep -Milliseconds 500
    }
    if (!(Test-Path $discovery)) { throw "The installed service did not become reachable." }
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

    for ($attempt = 0; $attempt -lt 120 -and !(Test-Path $reportFile); $attempt++) {
        if ($desktop.HasExited) { throw "The installed app exited before finishing its native UI checks." }
        Start-Sleep -Milliseconds 500
    }
    if (!(Test-Path $reportFile)) { throw "The installed app did not report its native UI checks." }
    $report = Get-Content $reportFile -Raw | ConvertFrom-Json -AsHashtable
    if (!$report.nativeWebView2 -or !$report.driveProjectDialog) { throw "Failed native UI check: $($report.error) ($($report.reason))." }
    $rows = & $service --agent state | ConvertFrom-Json
    if ($rows.accounts.Count -ne 1 -or $rows.accounts[0].name -ne "Windows test") { throw "The packaged Agent CLI did not read the test profile." }
    $copies = @(Get-ChildItem -File (Join-Path $scratch "Drive fixture/Google Perks Manager/Windows project"))
    if ($copies.Count -ne 1 -or (Get-FileHash $copies[0].FullName).Hash -ne (Get-FileHash $output).Hash) {
        throw "The Drive sync fixture must contain one byte-identical copy."
    }
    Write-Host "Windows native UI and Drive project copy passed."
    $report.agentCli = $true
    $report.driveSyncFolderCopy = $true
    $report.mediaExport = @{ audio = $true; video = $true; seconds = $job.artifact.seconds }
    $report.installerSHA256 = (Get-FileHash $package -Algorithm SHA256).Hash.ToLowerInvariant()
    $report | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $repo "dist/windows-smoke.json")
    Copy-Item (Join-Path $scratch "windows-smoke.png") (Join-Path $repo "dist/windows-smoke.png")
} finally {
    if (Test-Path $env:GOOGLE_MANAGER_STARTUP_LOG) {
        Get-Content $env:GOOGLE_MANAGER_STARTUP_LOG
    }
    if (!$desktop.HasExited) {
        $null = $desktop.CloseMainWindow()
        if (!$desktop.WaitForExit(4000)) { $desktop.Kill($true) }
    }
}
