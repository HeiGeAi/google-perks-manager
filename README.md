# 谷歌羊毛管理器

[English](README.en.md) · [GitHub Releases](https://github.com/HeiGeAi/google-perks-manager/releases/latest)

一个独立的桌面 App，按账号管理 Google AI 权益、Colab 实例与算力、云端配音、短视频和本机作品。

## 真机界面与演示

以下是 **Mac 真机实拍**。画面来自 Apple 芯片 Mac 上运行的正式版 v0.9.0，账号、算力和作品均为演示数据。

https://github.com/user-attachments/assets/da51b224-ed1f-4b49-9867-86d0279f1a79

[下载原版 MP4（35 秒）](https://github.com/HeiGeAi/google-perks-manager/releases/download/v0.9.0/GooglePerksManager-demo.mp4)

| 多账号管理 | Colab 实例与算力 |
| --- | --- |
| [<img src="docs/media/v0.9.0/accounts.jpg" width="440" alt="账号管理页，两个演示 Google 账号以备注名显示">](docs/media/v0.9.0/accounts.jpg) | [<img src="docs/media/v0.9.0/colab.jpg" width="440" alt="Colab 页显示演示账号的余额、费率与 GPU 实例">](docs/media/v0.9.0/colab.jpg) |
| 以备注识别账号，查看连接状态并快速切换。 | 按账号查看算力余额、当前费率和 GPU 实例。 |

| Pro 与 Ultra 权益 | 配音、视频和本机作品 |
| --- | --- |
| [<img src="docs/media/v0.9.0/perks.jpg" width="440" alt="权益页并列显示 Pro、Ultra 5x 和 Ultra 20x 档位及学生优惠说明">](docs/media/v0.9.0/perks.jpg) | [<img src="docs/media/v0.9.0/studio.jpg" width="440" alt="创作页展示 Colab 配音、文生视频、音画合成和作品操作">](docs/media/v0.9.0/studio.jpg) |
| 对照档位额度和学生优惠，打开官方领取及核验入口。 | 云端配音、短视频和音画合成集中在创作页。本机作品可跨账号查看、播放及打开文件夹。 |

| 按项目转存云盘 | 一键接入 Agent |
| --- | --- |
| [<img src="docs/media/v0.9.0/drive.jpg" width="440" alt="转存云盘弹窗，使用演示账号选择项目文件夹">](docs/media/v0.9.0/drive.jpg) | [<img src="docs/media/v0.9.0/agent.jpg" width="440" alt="Agent 接入页面及可复制的本机提示词">](docs/media/v0.9.0/agent.jpg) |
| 选目标账号和项目，将作品复制到 Google Drive 同步目录。 | 复制提示词，接入能在同一台电脑运行命令的 Agent。 |

## 交流群与个人微信

| 微信扫码进交流群 | 扫码添加黑哥微信 |
| --- | --- |
| <img src="web/brand/wechat-group.png" width="208" alt="谷歌羊毛管理器交流群二维码"> | <img src="web/brand/wechat-personal.png" width="208" alt="黑哥的个人微信二维码"> |
| 2026 年 11 月 27 日前有效 | 好友申请请备注 **谷歌交流群** |

## 安装和语言

| 系统 | 下载及安装 |
| --- | --- |
| macOS 14 或更新版本，Apple 芯片 | 下载 `GooglePerksManager-macOS-arm64.zip`，解压后将“谷歌羊毛管理器”拖进“应用程序”。 |
| Windows x64，系统内部版本 19041 或更新 | 下载 `GooglePerksManager-Windows-x64-Setup.exe`，按向导安装。需要时安装器会补装 Microsoft Edge WebView2。 |

首次打开从 GitHub 下载的 macOS 版时，系统可能显示开发者验证提示。Windows 发行包使用项目的更新签名，可对照发行页的 SHA-256 校验。 [Apple 的首次打开说明](https://support.apple.com/zh-cn/102445)

```bash
open "/Applications/谷歌羊毛管理器.app"
```

默认跟随系统语言。到“设置”可选择跟随系统、简体中文或 English。设置还显示当前版本，提供“检查更新”“下载更新”和“安装更新”。App 在启动安装前校验发布签名、安装包哈希和应用标识。Windows 会打开安装向导。

## 管理账号、权益和作品

| 页面 | 操作 |
| --- | --- |
| 账号管理 | 点击“连接 Google”打开官方授权页。完成 Colab CLI 授权后，把 Google 显示的授权码粘回 App。“其他连接方式”支持导入已有的 Colab CLI 授权，或使用可自动接收授权结果的桌面 OAuth 配置。 |
| Colab | 按账号查看 CU 余额、费率和实例。按短视频、配音或数据处理用途启动推荐机型，也可自选算力。 |
| 权益 | 对照 Google AI Pro、Ultra 5x 和 Ultra 20x 额度，查看学生版及试用限制。打开官方权益或领取入口，核对后记录当前账号的结果。 |
| 创作 | 在当前账号的 Colab GPU 调用 Breeze TTS 2 和 Wan2.2。保存到本机的作品可跨账号播放、打开文件夹或用于音画合成。 |

权益的实际资格与到账以 Google 账号显示为准。Breeze TTS 2 自托管权重及产物适用其研究与非商用许可。Colab 的资格、可用实例和费率由 Google 提供。[Google AI 套餐](https://one.google.com/intl/en/about/google-ai-plans/?gl=US&hl=en) · [Google Developer Program 权益](https://developers.google.com/profile/help/benefits?hl=en) · [Colab FAQ](https://research.google.com/colaboratory/faq.html) · [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2)

## 转存到 Google Drive

在创作页的作品行点击“转存云盘”。首次使用时，在 Google Drive 桌面版登录目标账号，并为它选择一次“我的云端硬盘”或同步目录。之后可选已有项目，也可输入新项目名。点击“一键转存”，App 会在同步目录的 **Google Perks Manager** 下按项目归档，保留本机原件。同一作品重复转存到同一项目会复用已校验的副本。云端同步进度可在 Google Drive 桌面版里查看。 [Google Drive 桌面版](https://support.google.com/drive/answer/10838124?hl=zh-Hans)

## 一键接入 Agent

打开 App，在侧栏点“一键接入 Agent”后复制提示词，发给一款能在同一台电脑运行命令的 Agent。提示词会填入当前安装的命令路径。Agent 可以读本机权益目录、领取条件与官方地址，也可以按账号查连接状态、已核对的余额、任务和作品位置。

```bash
app_agent="/Applications/谷歌羊毛管理器.app/Contents/Resources/backend/GoogleManagerService"
"$app_agent" --agent guide --language zh-CN
"$app_agent" --agent state
"$app_agent" --agent benefits --plan pro
```

Windows PowerShell 的默认安装路径示例。

```powershell
$appAgent = Join-Path $env:LOCALAPPDATA 'Programs\GooglePerksManager\backend\GoogleManagerService.exe'
& $appAgent --agent guide --language zh-CN
& $appAgent --agent state
```

已有桌面 OAuth 配置时，Agent 可以发起 Colab 连接并把 Google 的授权页面交给本人完成。也可以在 App 的账号管理里直接连接。领取在 Google 官方页面办理，完成后可按真实结果记回 App。启动或释放实例、云端配音和视频等操作有明确的确认参数。本机 CLI 还可以提交音画合成、核对任务和取回作品。

## 构建与发布

macOS 构建使用 Apple 芯片 Mac、Apple 命令行开发工具和 `uv`，产物在 `dist/谷歌羊毛管理器.app`。

```bash
python3 -B -m unittest discover -s tests
./macos/build.sh
```

Windows 构建需要 Python 3.13、.NET 10 SDK、PowerShell 7 和 Inno Setup 6。安装器在 `dist/GooglePerksManager-Windows-x64-Setup.exe`。

```powershell
python -m pip install -r windows/requirements.txt
python -B -m unittest discover -s tests
./windows/build.ps1
```

账号授权和作品存放在本机。macOS 沿用 `~/Library/Application Support/谷歌管理器`，Windows 使用 `%LOCALAPPDATA%\HeiGeAi\GooglePerksManager`。可用 `GOOGLE_MANAGER_DATA_DIR` 指定当前用户专用的独立目录。

维护者可下载 Windows 构建工作流通过安装验收的安装器，再用 Python 3.11 或更高版本、OpenSSL 3 和本机发布密钥生成跨平台发行包与签名。仓库里的 `macos/UpdatePublicKey.txt` 供两端 App 校验更新。

```bash
python3 scripts/prepare_release.py --output dist/release-v0.8.0 \
  --windows-installer dist/GooglePerksManager-Windows-x64-Setup.exe \
  --openssl "$(brew --prefix openssl@3)/bin/openssl" \
  --signing-key "/absolute/path/to/release-ed25519.pem"
```

原创应用源码使用 [MIT 许可证](LICENSE)。品牌素材、依赖和模型适用各自的许可。安装包内的 `Notices` 目录收录第三方依赖通知。Google 及相关产品名称由其各自所有者持有。
