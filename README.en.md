# Google Perks Manager

[简体中文](README.md) · [GitHub Releases](https://github.com/HeiGeAi/google-perks-manager/releases/latest)

An independent macOS app for Google plan perks, per-account Colab instances and compute units, cloud voice and video jobs, and local output files.

## WeChat community

| Scan to join the group | Scan to add Heige on WeChat |
| --- | --- |
| <img src="web/brand/wechat-group.png" width="208" alt="Google Perks Manager WeChat group QR code"> | <img src="web/brand/wechat-personal.png" width="208" alt="Heige's personal WeChat QR code"> |
| Valid before November 27, 2026 | Include **谷歌交流群** in your friend request |

## Install

Download `GooglePerksManager-macOS-arm64.zip`, extract it, and move `谷歌羊毛管理器.app` to Applications. The app supports Apple silicon Macs running macOS 14 or later.

Public builds currently use ad hoc code signing and an upstream update signature. macOS can show a developer verification notice when you first open an app downloaded from GitHub. [Apple’s first-launch guidance](https://support.apple.com/en-us/102445)

The app follows the preferred macOS language by default. Use **Settings → Language** to pick English, Simplified Chinese, or the system language. Settings also shows the current version and offers **Check for updates**, **Download update**, and **Install update**. The app verifies the release signature and archive before installation, then opens the new version.

## Main views

| View | What you can do |
| --- | --- |
| Accounts | Add each Google account and connect an approved Desktop OAuth client, or import the local authorization from the official Colab CLI. Complete Google’s consent in the browser. |
| Colab | Check compute units, hourly usage, and running instances. Choose the account and runtime before starting or stopping an instance. |
| Perks | Compare Google AI Pro, Ultra 5x and Ultra 20x, find official redemption links, and record the result for each account. |
| Studio | Run Breeze TTS 2 or Wan2.2 on a selected Colab GPU, combine audio and video on the Mac, and find or play completed files. |

Google determines actual benefit eligibility, Colab availability, and usage. The Breeze TTS 2 weights and self-hosted outputs carry research and non-commercial terms. [Google AI plans](https://one.google.com/intl/en/about/google-ai-plans/?gl=US&hl=en) · [Developer Program perks](https://developers.google.com/profile/help/benefits?hl=en) · [Colab FAQ](https://research.google.com/colaboratory/faq.html) · [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2)

## Connect an agent

Choose **Connect an Agent** in the sidebar and copy the prompt into an agent with a local terminal on the same Mac. The prompt includes the executable path. The local interface can read the perk catalog and official claim steps, report the current account and Colab status, start the Google authorization flow, and work with jobs and output files.

```bash
app_agent="/Applications/谷歌羊毛管理器.app/Contents/Resources/backend/GoogleManagerService"
"$app_agent" --agent guide --language en
"$app_agent" --agent state
"$app_agent" --agent benefits --plan pro
```

Google account consent and perk redemption are completed on Google’s own pages. The agent can guide that process and record the verified result in the app. Commands that change accounts or may spend compute units require explicit user confirmation. The agent can also combine existing files, check jobs, and retrieve outputs.

## Build

Install Apple’s Command Line Tools and `uv` on an Apple silicon Mac.

```bash
python3 -B -m unittest discover -s tests
./macos/build.sh
```

The app bundle is written to `dist/谷歌羊毛管理器.app`. Existing data lives in `~/Library/Application Support/谷歌管理器`. To test with independent data, set `GOOGLE_MANAGER_DATA_DIR` to a directory owned by the current user with mode 700. Optional personal branding images stay in this local data directory.

The original app source is available under the [MIT license](LICENSE). Branding, dependencies, and model weights retain their respective licenses. The distributed bundle contains dependency notices in `Contents/Resources/Notices`.
