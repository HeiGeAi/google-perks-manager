# 谷歌羊毛管理器

[English](README.en.md) · [GitHub Releases](https://github.com/HeiGeAi/google-perks-manager/releases/latest)

一个独立的 macOS App，按账号管理 Google AI 权益、Colab 实例与算力、云端配音、短视频和本机作品。

## 交流群与个人微信

| 微信扫码进交流群 | 扫码添加黑哥微信 |
| --- | --- |
| <img src="web/brand/wechat-group.png" width="208" alt="谷歌羊毛管理器交流群二维码"> | <img src="web/brand/wechat-personal.png" width="208" alt="黑哥的个人微信二维码"> |
| 2026 年 11 月 27 日前有效 | 好友申请请备注 **谷歌交流群** |

## 安装和语言

下载 Releases 中的 `GooglePerksManager-macOS-arm64.zip`，解压后将“谷歌羊毛管理器”拖进“应用程序”。支持 macOS 14 或更新的 Apple 芯片 Mac。

当前公开包采用临时代码签名和项目更新签名。首次打开从 GitHub 下载的应用时，macOS 可能显示开发者验证提示。[Apple 的首次打开说明](https://support.apple.com/zh-cn/102445)

```bash
open "/Applications/谷歌羊毛管理器.app"
```

默认跟随 macOS 首选语言。到“设置”可选择跟随系统、简体中文或 English。设置还显示当前版本，提供“检查更新”“下载更新”和“安装更新”。安装前会校验发布签名、安装包哈希和应用标识，安装完成会重新打开应用。

## 管理账号、权益和作品

| 页面 | 操作 |
| --- | --- |
| 账号管理 | 点击“连接 Google”，Mac App 会打开官方授权页。完成 Colab CLI 授权后，把 Google 显示的授权码粘回 App。“其他连接方式”支持导入已有的 Colab CLI 授权，或使用可自动接收授权结果的桌面 OAuth 配置。 |
| Colab | 按账号刷新 CU 余额、费率和实例，核对后启动或释放实例。 |
| 权益 | 对照 Google AI Pro、Ultra 5x 和 Ultra 20x 额度。打开官方权益或领取入口，核对后记录当前账号的结果。 |
| 创作 | 在当前账号的 Colab GPU 调用 Breeze TTS 2 和 Wan2.2。在本机合成音画、播放、回收和定位作品。 |

权益的实际资格与到账以 Google 账号显示为准。Breeze TTS 2 自托管权重及产物适用其研究与非商用许可。Colab 的资格、可用实例和费率由 Google 提供。[Google AI 套餐](https://one.google.com/intl/en/about/google-ai-plans/?gl=US&hl=en) · [Google Developer Program 权益](https://developers.google.com/profile/help/benefits?hl=en) · [Colab FAQ](https://research.google.com/colaboratory/faq.html) · [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2)

## 一键接入 Agent

打开 App，在侧栏点“一键接入 Agent”后复制提示词，发给一款能在同一台 Mac 执行本机命令的 Agent。提示词包含当前安装位置。Agent 可以读本机权益目录、领取条件与官方地址，也可以按账号查连接状态、已核对的余额、任务和作品位置。

```bash
app_agent="/Applications/谷歌羊毛管理器.app/Contents/Resources/backend/GoogleManagerService"
"$app_agent" --agent guide --language zh-CN
"$app_agent" --agent state
"$app_agent" --agent benefits --plan pro
```

已有桌面 OAuth 配置时，Agent 可以发起 Colab 连接并把 Google 的授权页面交给本人完成。也可以在 App 的账号管理里直接连接。领取在 Google 官方页面办理，完成后可按真实结果记回 App。启动或释放实例、云端配音和视频等操作有明确的确认参数。本机 CLI 还可以提交音画合成、核对任务和取回作品。

## 构建与发布

准备 Apple 命令行开发工具和 `uv`。构建使用 Apple 芯片 Mac，产物在 `dist/谷歌羊毛管理器.app`。

```bash
python3 -B -m unittest discover -s tests
./macos/build.sh
```

应用沿用旧版的本机账号与任务目录 `~/Library/Application Support/谷歌管理器`。开发测试可以用 `GOOGLE_MANAGER_DATA_DIR` 指定一个权限为 700 的独立目录。当前用户的个人二维码（如有）位于该目录下的 `branding/wechat-contact.png`。公网构建使用应用自带的公开品牌素材，关注入口来自 [问问黑哥官网](https://www.heigeai.com/contact/)。

维护者使用 Python 3.11 或更高版本、OpenSSL 3 和本机发布密钥生成 ZIP、更新清单与独立签名，再把三份文件上传至对应 Git tag 的 GitHub Release。仓库里的 `macos/UpdatePublicKey.txt` 供 App 校验更新。

```bash
python3 scripts/prepare_release.py --output dist/release-v0.7.7 \
  --openssl "$(brew --prefix openssl@3)/bin/openssl" \
  --signing-key "/absolute/path/to/release-ed25519.pem"
```

原创应用源码使用 [MIT 许可证](LICENSE)。品牌素材、依赖和模型仍适用各自的许可。软件包内的 `Contents/Resources/Notices` 收录第三方依赖通知。Google 及相关产品名称由其各自所有者持有。
