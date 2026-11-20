param([string]$Python = "python", [switch]$SkipInstaller)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$repo = Split-Path $PSScriptRoot
$version = & $Python -c "from google_manager import __version__; print(__version__)"
$work = Join-Path $repo "build/windows"
$app = Join-Path $repo "dist/windows-x64"
New-Item -ItemType Directory -Force $work, $app | Out-Null

Add-Type -AssemblyName System.Drawing
$bitmap = [System.Drawing.Bitmap]::new((Join-Path $repo "macos/AppIcon.png"))
$thumb = [System.Drawing.Bitmap]::new($bitmap, 256, 256)
$stream = [System.IO.MemoryStream]::new()
$thumb.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
$pixels = $stream.ToArray()
$iconPath = Join-Path $work "GooglePerksManager.ico"
$writer = [System.IO.BinaryWriter]::new([System.IO.File]::Create($iconPath))
try {
    $writer.Write([byte[]] @(0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 1, 0, 32, 0))
    $writer.Write([int] $pixels.Length)
    $writer.Write([int] 22)
    $writer.Write($pixels)
} finally {
    $writer.Dispose()
    $stream.Dispose()
    $thumb.Dispose()
    $bitmap.Dispose()
}

$publish = @(
    "publish", (Join-Path $PSScriptRoot "GooglePerksManager.csproj"), "-c", "Release", "-r", "win-x64",
    "--self-contained", "true", "-o", $app, "-p:Version=$version", "-p:DesktopIcon=$iconPath"
)
dotnet @publish

$webStage = Join-Path $work ("web-" + [Guid]::NewGuid())
Copy-Item (Join-Path $repo "web") $webStage -Recurse
try {
    Remove-Item (Join-Path $webStage "brand/wechat-contact.png") -Force -ErrorAction SilentlyContinue
    $bundle = @(
        "--noconfirm", "--log-level", "WARN", "--onedir", "--console", "--name", "GoogleManagerService",
        "--paths", $repo, "--specpath", $work, "--workpath", (Join-Path $work "freeze"), "--distpath", $work,
        "--hidden-import", "google_manager.colab_worker", "--collect-data", "colab_cli",
        "--add-data", "${repo}/benefits.json:.", "--add-data", "${webStage}:web",
        "--add-data", "${repo}/macos/UpdatePublicKey.txt:.",
        "--add-data", "${repo}/recipes/run_job.py:recipes", "--add-data", "${repo}/recipes/breeze.json:recipes",
        "--add-data", "${repo}/recipes/wan22.json:recipes",
        "--exclude-module", "pyarrow", "--exclude-module", "IPython", "--exclude-module", "tkinter",
        (Join-Path $repo "macos/service_entry.py")
    )
    & $Python -m PyInstaller @bundle
    $backend = Join-Path $app "backend"
    if (Test-Path $backend) { Remove-Item $backend -Recurse -Force }
    Move-Item (Join-Path $work "GoogleManagerService") $backend
} finally {
    Remove-Item $webStage -Recurse -Force
}

$notices = Join-Path $app "Notices"
& $Python (Join-Path $repo "macos/notices.py") $notices
Copy-Item (Join-Path $repo "LICENSE") (Join-Path $notices "GooglePerksManager-LICENSE.txt")
if ($SkipInstaller) { return }

$bootstrap = Join-Path $work "MicrosoftEdgeWebview2Setup.exe"
Invoke-WebRequest "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $bootstrap
$signature = Get-AuthenticodeSignature $bootstrap
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
    throw "Microsoft WebView2 bootstrapper signature could not be verified."
}
$compiler = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6/ISCC.exe"
if (!(Test-Path $compiler)) { throw "Install Inno Setup 6 to build the installer." }
$setup = @("/DAppVersion=$version", "/DAppSource=$app", "/DAppIcon=$iconPath",
    "/DBootstrapper=$bootstrap", "/DOutputFolder=$(Join-Path $repo 'dist')", (Join-Path $PSScriptRoot "setup.iss"))
& $compiler @setup
