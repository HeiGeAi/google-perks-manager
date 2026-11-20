"use strict";

const communityRemark = "谷歌交流群";
const heigeWebsite = "https://www.heigeai.com/";
const heigeWechat = "https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=Mzg5NTg4NzM4OA==#wechat_redirect";
const heigeDouyin = "https://www.douyin.com/user/MS4wLjABAAAASndirSLd4pEnsvTanPmm1OU_WHwTuNcFjfAreF3z0qM";
const releasesUrl = "https://github.com/" + projectRepository + "/releases";

let updateState = { status: "idle" };
let updateOperation = "";
let updateError = "";
let changingLanguage = false;

function formatReleaseVersion(value) {
  return typeof value === "string" && value ? "v" + value.replace(/^v/i, "") : "";
}

function updateActions() {
  if (!desktop?.checkForUpdate) return "";
  const disabled = Boolean(updateOperation || updateState.status === "installing");
  const check = button(t("检查更新", "Check for updates"), "settings-check-update", "compact", "", disabled);
  if (updateState.status === "available" && desktop.downloadUpdate) {
    return button(t("下载更新", "Download update"), "settings-download-update", "primary compact", "", disabled) + check;
  }
  if (updateState.status === "ready" && desktop.installUpdate) {
    return button(t("安装更新", "Install update"), "settings-install-update", "primary compact", "", disabled);
  }
  return check;
}

function updateStatus() {
  if (updateError) return serviceText(updateError, "Could not complete the update. Check the connection and try again.");
  if (updateOperation === "check") return t("正在检查更新…", "Checking for updates…");
  if (updateOperation === "download") return t("正在下载并验证更新…", "Downloading and verifying the update…");
  if (updateOperation === "install" || updateState.status === "installing") {
    return t("正在安装更新，应用即将重新打开。", "Installing the update. The app will reopen shortly.");
  }
  const version = formatReleaseVersion(updateState.version);
  if (updateState.status === "current") return t("当前已是最新版本。", "You're on the latest version.");
  if (updateState.status === "available") return t(`有新版本 ${version}。`, `Version ${version} is available.`);
  if (updateState.status === "ready") return t(`${version} 已下载，可以安装。`, `${version} is downloaded and ready to install.`);
  if (!desktop?.checkForUpdate) return t("在 GitHub 查看发行版本。", "Find published versions on GitHub.");
  return "";
}

function renderPreferences() {
  const options = [
    ["system", t("跟随系统", "System default")],
    ["zh-CN", "简体中文"],
    ["en", "English"],
  ].map(([value, label]) => `<option value="${value}"${value === appInfo.language ? " selected" : ""}>${label}</option>`).join("");
  const version = formatReleaseVersion(appInfo.version || updateState.currentVersion);
  const updateMessage = updateStatus();
  return `<div class="preference-grid">
    <section class="panel preference-card" aria-labelledby="language-title">
      <h2 id="language-title">${t("界面语言", "App language")}</h2>
      <select id="settings-language" aria-labelledby="language-title"${changingLanguage ? " disabled" : ""}>${options}</select>
    </section>
    <section class="panel preference-card" aria-labelledby="update-title">
      <header class="preference-header">
        <h2 id="update-title">${t("软件更新", "Software updates")}</h2>
        ${version ? `<span class="version-label" aria-label="${t("当前版本", "Current version")} ${escapeHtml(version)}">${escapeHtml(version)}</span>` : ""}
      </header>
      <div class="actions">${updateActions()}${externalLink(t("GitHub 发行记录", "GitHub releases"), releasesUrl, "text compact")}</div>
      <p class="settings-status${updateError ? " is-error" : ""}" role="status"${updateMessage ? "" : " hidden"}>${escapeHtml(updateMessage)}</p>
    </section>
  </div>`;
}

function renderSettings() {
  pageTitle();
  pageContent.innerHTML = `<div class="settings-page">
    ${renderPreferences()}
    <section class="panel publisher-card" aria-labelledby="publisher-title">
      <img src="/brand/heige.webp" class="publisher-logo" alt="" width="76" height="76">
      <h2 id="publisher-title">${t("问问黑哥", "Ask Heige")}</h2>
      ${externalLink(t("访问官网", "Visit website"), heigeWebsite, "text compact")}
    </section>
    <div class="settings-grid">
      <section class="panel social-card" aria-labelledby="social-title">
        <h2 id="social-title">${t("关注渠道", "Follow")}</h2>
        <div class="social-row">
          <span class="social-icon icon-wechat" aria-hidden="true"></span>
          <strong>${t("微信公众号", "WeChat Official Account")}</strong>
          ${button(t("扫码关注", "Scan to follow"), "settings-wechat", "compact")}
        </div>
        <div class="social-row">
          <span class="social-icon icon-douyin" aria-hidden="true">♪</span>
          <strong>${t("抖音", "Douyin")}</strong>
          ${externalLink(t("打开主页", "Open profile"), heigeDouyin, "compact")}
        </div>
      </section>
      ${appInfo.hasContactQr ? renderCommunity() : ""}
    </div>
  </div>`;
  const community = document.querySelector(".community-card");
  if (community) community.querySelector("img").decode().catch(() => { community.hidden = true; });
}

function personalQr(label) {
  return `<span class="personal-qr"><img src="/brand/wechat-contact.png" width="972" height="1444" alt="${escapeHtml(label)}"></span>`;
}

function renderCommunity() {
  return `<section class="panel community-card" aria-labelledby="community-title">
    <h2 id="community-title">${t("谷歌交流群", "Google community")}</h2>
    <button type="button" class="community-qr-button" data-action="settings-enlarge" aria-label="${t("放大个人微信二维码", "Enlarge the personal WeChat QR code")}">
      ${personalQr(t("微信扫码添加黑哥为好友", "Scan to add Heige on WeChat"))}
    </button>
    <p>${t("微信扫码添加黑哥，好友申请请备注", "Scan to add Heige on WeChat and include the note")} <strong>${communityRemark}</strong>${t("。", ".")}</p>
    <div class="community-actions">
      ${button(t("复制备注", "Copy note"), "settings-copy", "compact")}
      <span class="copy-status" role="status"></span>
    </div>
  </section>`;
}

async function copyCommunityRemark(control) {
  const statusText = control.closest(".community-actions").querySelector(".copy-status");
  statusText.textContent = "";
  try {
    if (desktop?.copyCommunityRemark) {
      if (await desktop.copyCommunityRemark.postMessage(null) !== true) throw new Error();
    } else {
      await navigator.clipboard.writeText(communityRemark);
    }
    statusText.textContent = t("已复制", "Copied");
  } catch {
    statusText.textContent = t("复制失败", "Could not copy");
  }
}

async function changeSettingsLanguage(control) {
  if (changingLanguage) return;
  const focused = document.activeElement === control;
  changingLanguage = true;
  control.disabled = true;
  try {
    await chooseLanguage(control.value);
    announce("");
  } catch (error) {
    announce(serviceText(error.message, "Could not save the language setting."), true);
  } finally {
    changingLanguage = false;
    render();
    if (focused && page === "settings") document.querySelector("#settings-language")?.focus();
  }
}

async function runUpdate(operation) {
  if (updateOperation) return;
  const handler = {
    check: desktop?.checkForUpdate,
    download: updateState.status === "available" && desktop?.downloadUpdate,
    install: updateState.status === "ready" && desktop?.installUpdate,
  }[operation];
  if (!handler) return;
  updateOperation = operation;
  updateError = "";
  renderSettings();
  try {
    const result = await handler.postMessage(null);
    const allowed = { check: ["current", "available"], download: ["ready"], install: ["installing", "cancelled"] };
    if (!allowed[operation].includes(result?.status)) {
      throw new Error(t("更新状态无法读取。请重新检查更新。", "Update status is unavailable. Check for updates again."));
    }
    if (result.status !== "cancelled") updateState = { ...updateState, ...result };
  } catch (error) {
    updateError = error?.message || t("无法完成更新。请稍后重试。", "Could not complete the update. Try again later.");
  } finally {
    updateOperation = "";
    if (page === "settings") renderSettings();
  }
}

function settingsAction(action, control) {
  if (action === "settings-wechat") {
    setDialog(t("关注问问黑哥", "Follow Ask Heige"), `<div class="qr-dialog">
      <img class="official-qr" src="/brand/wechat-official.png" width="640" height="640" alt="${t("问问黑哥微信公众号二维码", "Ask Heige WeChat Official Account QR code")}">
      ${externalLink(t("打开公众号", "Open on WeChat"), heigeWechat, "text compact")}
    </div>`);
  } else if (action === "settings-enlarge" && appInfo.hasContactQr) {
    setDialog(t("黑哥的个人微信", "Heige on WeChat"), `<div class="qr-dialog">
      ${personalQr(t("黑哥个人微信二维码", "Heige’s personal WeChat QR code"))}
      <p>${t("好友申请请备注", "Include this note in your friend request")} <strong>${communityRemark}</strong></p>
      <div class="community-actions">${button(t("复制备注", "Copy note"), "settings-copy", "compact")}
        <span class="copy-status" role="status"></span>
      </div>
    </div>`);
  } else if (action === "settings-copy") {
    copyCommunityRemark(control);
  } else if (action === "settings-check-update") {
    runUpdate("check");
  } else if (action === "settings-download-update") {
    runUpdate("download");
  } else if (action === "settings-install-update") {
    runUpdate("install");
  }
}
