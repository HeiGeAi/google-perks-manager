# Google Perks Manager

[简体中文](README.md) · [GitHub Releases](https://github.com/HeiGeAi/google-perks-manager/releases/latest)

An independent desktop app for Google plan perks, per-account Colab instances and compute units, cloud voice and video jobs, and local output files.

## WeChat community

| Scan to join the group | Scan to add Heige on WeChat |
| --- | --- |
| <img src="web/brand/wechat-group.png" width="208" alt="Google Perks Manager WeChat group QR code"> | <img src="web/brand/wechat-personal.png" width="208" alt="Heige's personal WeChat QR code"> |
| Valid before November 27, 2026 | Include **谷歌交流群** in your friend request |

## Install

| System | Install |
| --- | --- |
| macOS 14 or newer, Apple silicon | Download `GooglePerksManager-macOS-arm64.zip`, extract it, and move `谷歌羊毛管理器.app` to Applications. |
| Windows 10 22H2 or Windows 11, x64 | Run `GooglePerksManager-Windows-x64-Setup.exe`. Setup installs Microsoft Edge WebView2 when needed. |

macOS can show a developer verification notice when you first open an app downloaded from GitHub. The project signs its release manifest. Check the release page for the SHA-256 of the Windows installer. [Apple’s first-launch guidance](https://support.apple.com/en-us/102445)

The app follows the system language by default. Use **Settings → Language** to pick English, Simplified Chinese, or the system language. Use **Check for updates**, **Download update**, and **Install update** to apply releases. The app verifies the signed release manifest and installer before opening the installation flow.

## Main views

| View | What you can do |
| --- | --- |
| Accounts | Select Connect to Google to open Google’s consent page. Paste the code Google shows after approving the official Colab CLI connection. Under Other ways to connect, you can import an existing Colab CLI grant or use a desktop OAuth client for automatic callback. |
| Colab | Check compute units, hourly usage, and running instances. Choose the account and runtime before starting or stopping an instance. |
| Perks | Compare Google AI Pro, Ultra 5x and Ultra 20x, check student and trial restrictions, and record results for each account. |
| Studio | Run Breeze TTS 2 or Wan2.2 on a selected Colab GPU, combine audio and video locally, and find or play completed files. |

Google determines actual benefit eligibility, Colab availability, and usage. The Breeze TTS 2 weights and self-hosted outputs carry research and non-commercial terms. [Google AI plans](https://one.google.com/intl/en/about/google-ai-plans/?gl=US&hl=en) · [Developer Program perks](https://developers.google.com/profile/help/benefits?hl=en) · [Colab FAQ](https://research.google.com/colaboratory/faq.html) · [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2)

## Connect an agent

Choose **Connect an Agent** in the sidebar and copy the prompt into an agent with a terminal on the same computer. The prompt includes the executable path. The local interface can read the perk catalog and official claim steps, report the current account and Colab status, start the Google authorization flow, and work with jobs and output files.

```bash
app_agent="/Applications/谷歌羊毛管理器.app/Contents/Resources/backend/GoogleManagerService"
"$app_agent" --agent guide --language en
"$app_agent" --agent state
"$app_agent" --agent benefits --plan pro
```

On Windows, the default installation supports PowerShell commands.

```powershell
$appAgent = Join-Path $env:LOCALAPPDATA 'Programs\GooglePerksManager\backend\GoogleManagerService.exe'
& $appAgent --agent guide --language en
& $appAgent --agent state
```

Google account consent and perk redemption are completed on Google’s own pages. The agent can guide that process and record the verified result in the app. Commands that change accounts or may spend compute units require explicit user confirmation. The agent can also combine existing files, check jobs, and retrieve outputs.

## Build

Install Apple’s Command Line Tools and `uv` on an Apple silicon Mac.

```bash
python3 -B -m unittest discover -s tests
./macos/build.sh
```

On Windows, install Python 3.13, the .NET 10 SDK, PowerShell 7, and Inno Setup 6.

```powershell
python -m pip install -r windows/requirements.txt
python -B -m unittest discover -s tests
./windows/build.ps1
```

Builds go under `dist`. Account authorizations and outputs are stored in `~/Library/Application Support/谷歌管理器` on macOS and `%LOCALAPPDATA%\HeiGeAi\GooglePerksManager` on Windows. Set `GOOGLE_MANAGER_DATA_DIR` to a private user directory to test with separate data.

The original app source is available under the [MIT license](LICENSE). Branding, dependencies, and model weights retain their respective licenses. Distributed builds contain dependency notices in `Notices`.
