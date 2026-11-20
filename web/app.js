"use strict";

const accountSwitch = document.querySelector("#account-switch");
const indicator = document.querySelector("#local-indicator");
const pageHeader = document.querySelector("#page-header");
const pageContent = document.querySelector("#page-content");
const notice = document.querySelector("#notice");
const dialog = document.querySelector("#dialog");
const dialogContent = document.querySelector("#dialog-content");
const authLabels = {
  disconnected: ["待连接", "Not connected"], connecting: ["等待授权", "Awaiting authorization"], connected: ["已连接", "Connected"],
};
const observationLabels = {
  unverified: ["待刷新", "Refresh needed"], current: ["实时核验", "Live"], cached: ["历史快照", "Last snapshot"], error: ["刷新失败", "Refresh failed"],
};
const benefitLabels = {
  unverified: ["待核验", "Not verified"], available: ["可领取", "Available"], claimed: ["已领取", "Claimed"], not_eligible: ["暂不符合要求", "Not eligible"],
};

let data = { accounts: [], benefits: [], supportedGpus: [] };
let localStatus = "loading";
let selected = localStorage.getItem("google-manager-account");
let page = Object.hasOwn(pageLabels, location.hash.slice(1)) ? location.hash.slice(1) : "colab";
let busy = false;
let dialogSubmit;
let returnFocus;
let oauthWatch;
let activeAuthorization;
let stateVersion = 0;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function button(label, action, style = "", id = "", disabled = false) {
  return `<button type="button" class="button ${style}" data-action="${action}" data-id="${escapeHtml(id)}"${disabled ? " disabled" : ""}>${escapeHtml(label)}</button>`;
}

function externalLink(label, value, style = "text") {
  const url = safeUrl(value);
  return url ? `<a class="button ${style}" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} <span aria-hidden="true">↗</span></a>` : "";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat(appInfo.effectiveLanguage, {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).format(date);
}

function formatMoney(value) {
  return typeof value === "number" && Number.isFinite(value) ? new Intl.NumberFormat(appInfo.effectiveLanguage, { maximumFractionDigits: 3 }).format(value) : "";
}

function account() {
  return data.accounts.find(item => item.id === selected);
}

function selectAccount(id) {
  selected = id || null;
  if (selected) localStorage.setItem("google-manager-account", selected);
  else localStorage.removeItem("google-manager-account");
  desktop?.setAccountSelection?.postMessage(selected).catch(() => {});
}

function applyState(state) {
  const next = {
    accounts: state.accounts,
    benefits: state.benefits,
    supportedGpus: Array.isArray(state.supported_gpus) ? state.supported_gpus : [],
  };
  const changed = localStatus !== "ready" || JSON.stringify(data) !== JSON.stringify(next);
  data = next;
  if (!account()) selectAccount(data.accounts[0]?.id);
  localStatus = "ready";
  return changed;
}

function announce(message, error = false) {
  notice.textContent = message || "";
  notice.hidden = !message;
  notice.classList.toggle("is-error", error);
}

async function api(path, body) {
  const options = { credentials: "same-origin", cache: "no-store", headers: { "Accept-Language": appInfo.effectiveLanguage } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error(t("本机服务连接失败。请确认谷歌羊毛管理器正在运行。", "Cannot reach the local service. Make sure Google Perks Manager is running."));
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(serviceText(payload.error) || t(`请求失败（HTTP ${response.status}）。`, `Request failed (HTTP ${response.status}).`));
  return payload;
}

async function readState() {
  const version = ++stateVersion;
  const result = await api("/api/state");
  if (!Array.isArray(result.accounts) || !Array.isArray(result.benefits)) throw new Error(t("本机工作区返回的数据格式有误。", "The local workspace returned an invalid response."));
  return version === stateVersion && applyState(result);
}

async function load() {
  try {
    await readState();
    announce("");
  } catch (error) {
    localStatus = "error";
    announce(error.message, true);
  }
  render();
}

async function change(path, fields) {
  const result = await api(path, fields);
  if (Array.isArray(result.accounts) && Array.isArray(result.benefits)) {
    stateVersion++;
    applyState(result);
  }
  else await readState();
  return result;
}

function accountPath(profile, suffix = "") {
  return `/api/accounts/${encodeURIComponent(profile.id)}${suffix}`;
}

function identity(profile) {
  const letter = Array.from(profile.name || "G")[0];
  return `<div class="account-identity"><span class="avatar" aria-hidden="true">${escapeHtml(letter)}</span><strong>${escapeHtml(profile.name)}</strong></div>`;
}

function status(text, kind = "") {
  return `<span class="status ${kind}">${escapeHtml(text)}</span>`;
}

function authStatus(profile) {
  return status(t(...(authLabels[profile.auth_status] || authLabels.disconnected)), profile.auth_status === "connected" ? "good" : "");
}

function renderAccountSwitch() {
  const items = data.accounts.map(profile => {
    const letter = Array.from(profile.name || "G")[0];
    return `<button type="button" class="account-item" data-action="select-account" data-id="${escapeHtml(profile.id)}" aria-pressed="${profile.id === selected}">
      <span class="avatar" aria-hidden="true">${escapeHtml(letter)}</span>
      <strong>${escapeHtml(profile.name)}</strong><span class="account-dot" aria-hidden="true"></span>
    </button>`;
  });
  const options = data.accounts.map(profile => `<option value="${escapeHtml(profile.id)}"${profile.id === selected ? " selected" : ""}>${escapeHtml(profile.name)}</option>`).join("");
  const addLabel = t("新增 Google 账号", "Add Google account");
  const add = `<button type="button" class="switch-add" data-action="add-account" aria-label="${addLabel}" title="${addLabel}">＋</button>`;
  accountSwitch.innerHTML = `
    <div class="switch-heading"><span>${t("Google 账号", "Google accounts")}</span>${add}</div>
    ${items.length ? `<div class="account-list">${items.join("")}</div>` : `<div class="switch-empty">${t("尚未添加账号", "No accounts yet")}</div>`}
    ${options ? `<div class="mobile-switch"><select aria-label="${t("切换 Google 账号", "Switch Google account")}" id="mobile-account">${options}</select>${add}</div>` : ""}`;
  indicator.className = `local-indicator${localStatus === "ready" ? " is-ready" : localStatus === "error" ? " is-error" : ""}`;
  indicator.textContent = t(...({
    loading: ["正在连接本机", "Connecting to local service"], ready: ["本机服务已连接", "Local service connected"],
    error: ["本机服务连接异常", "Local service unavailable"],
  }[localStatus]));
}

function pageTitle(actions = "") {
  pageHeader.innerHTML = `<h1>${pageLabel(page)}</h1>${actions ? `<div class="page-header-actions">${actions}</div>` : ""}`;
}

function onboarding(title, description, label, action, id) {
  return `<section class="panel signature" aria-labelledby="onboard-title">
    <div class="signature-content"><h2 id="onboard-title">${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p><div class="actions">${button(label, action, "primary", id)}</div></div>
    <img class="signature-icon" src="/brand/app-icon.png" alt="" width="256" height="256">
  </section>`;
}

function benefitPreview() {
  if (!data.benefits.length) return "";
  const links = data.benefits.slice(0, 3).map(benefit => `<a href="#benefits">${escapeHtml(localizedBenefit(benefit).title)} <span aria-hidden="true">›</span></a>`);
  return `<section class="panel"><div class="panel-header"><h2>${t("Google 权益", "Google perks")}</h2><a class="button text" href="#benefits">${t("打开权益目录", "Browse perks")} <span aria-hidden="true">›</span></a></div><div class="benefit-preview">${links.join("")}</div></section>`;
}

function hasObservation(observation) {
  return Boolean(formatTime(observation.checked_at) || observation.status === "current" || observation.status === "cached");
}

function allowedChoices(observation) {
  return Array.isArray(observation.eligible_gpus) && observation.eligible_gpus.length
    ? observation.eligible_gpus : data.supportedGpus;
}

function metric(name, value, unit) {
  return `<div class="metric"><dt>${escapeHtml(name)}</dt><dd>${value === "" ? `<strong class="unknown">${t("待核验", "Not verified")}</strong>` : `<strong>${escapeHtml(value)}</strong><small>${escapeHtml(unit)}</small>`}</dd></div>`;
}

function instanceTable(profile, observation, instances) {
  const current = observation.status === "current" && profile.auth_status === "connected";
  const canStop = profile.auth_status === "connected" && hasObservation(observation);
  const rows = instances.map(instance => {
    const name = instance.name || instance.id;
    const id = instance.name && instance.id !== instance.name ? `<span class="instance-id secondary">${escapeHtml(instance.id)}</span>` : "";
    const stop = button(t("停止", "Stop"), "stop-instance", "danger compact", instance.id, !canStop || busy);
    return `<tr><td><strong>${escapeHtml(name)}</strong>${id}</td><td>${escapeHtml(instance.gpu || t("类型待核验", "GPU unknown"))}</td><td>${escapeHtml(instance.region || t("地域待核验", "Region unknown"))}</td><td>${stop}</td></tr>`;
  });
  return `<section class="panel" aria-labelledby="instance-title"><div class="panel-header"><h2 id="instance-title">${t("实例", "Instances")}</h2>${canStop && rows.length && !current ? `<span class="panel-note">${t("释放前会重新核对实例 ID", "The instance ID is checked again before release")}</span>` : ""}</div>
    ${rows.length ? `<table class="table" aria-labelledby="instance-title"><thead><tr><th scope="col">${t("实例", "Instance")}</th><th scope="col">${t("算力", "GPU")}</th><th scope="col">${t("地域", "Region")}</th><th scope="col">${t("操作", "Actions")}</th></tr></thead><tbody>${rows.join("")}</tbody></table>` : `<p class="panel-empty">${current ? t("当前账号没有运行中的实例。", "This account has no running instances.") : hasObservation(observation) ? t("上次读回没有运行中的实例。", "No running instances in the last snapshot.") : t("等待账号连接并刷新实例。", "Connect an account and refresh instances.")}</p>`}
  </section>`;
}

function renderColab(profile) {
  if (!profile) {
    pageTitle(`<a class="button" href="#studio">${t("开始创作", "Open Studio")}</a>`);
    return `<div class="layout-stack">${onboarding(t("添加 Google 账号", "Add a Google account"), t("连接后即可读取该账号的算力余额、计费速率和 Colab 实例。", "Connect an account to view its compute balance, usage rate, and Colab instances."), t("添加账号", "Add account"), "add-account")}${benefitPreview()}</div>`;
  }
  const observation = profile.observation || {};
  const instances = Array.isArray(observation.instances) ? observation.instances : [];
  const choices = allowedChoices(observation);
  const connected = profile.auth_status === "connected";
  const current = connected && observation.status === "current";
  const actions = connected
    ? button(t("刷新实例", "Refresh instances"), "refresh-account", "", profile.id, busy) + button(t("启动实例", "Start instance"), "start-instance", "primary", profile.id, busy || !choices.length)
    : button(profile.auth_status === "connecting" ? t("继续连接 Google", "Continue with Google") : t("连接 Google", "Connect to Google"), "connect-account", "primary", profile.id, busy);
  pageTitle(actions);
  const checkedAt = formatTime(observation.checked_at);
  const evidence = status(t(...(connected ? observationLabels[observation.status] || observationLabels.unverified : authLabels[profile.auth_status] || authLabels.disconnected)),
    current ? "good" : observation.status === "error" ? "bad" : "");
  const summary = `<section class="panel summary" aria-label="${t("当前账号的算力状态", "Compute status for the current account")}">
    <div class="summary-top">${identity(profile)}<div class="evidence">${evidence}${checkedAt ? `<span>${t("核验于", "Checked")} ${escapeHtml(checkedAt)}</span>` : ""}</div></div>
    <dl class="summary-metrics">${metric(t("算力余额", "Compute balance"), formatMoney(observation.balance_cu), "CU")}${metric(t("计费速率", "Usage rate"), formatMoney(observation.hourly_cu), t("CU/小时", "CU/hour"))}${metric(t("运行中实例", "Running instances"), hasObservation(observation) ? formatMoney(instances.length) : "", t("台", ""))}</dl>
  </section>`;
  const error = profile.auth_error || observation.error;
  const problem = error ? `<p class="inline-error" role="status">${escapeHtml(serviceText(error))}</p>` : "";
  const pending = profile.operation_pending ? `<p class="panel-note">${t("上次启动结果待核验，请先刷新实例。", "Refresh instances to verify the previous launch attempt.")}</p>` : "";
  return `<div class="layout-stack">${summary}${problem}${pending}${instanceTable(profile, observation, instances)}</div>`;
}

function renderAccounts() {
  pageTitle(button(t("添加账号", "Add account"), "add-account", "primary", "", busy));
  if (!data.accounts.length) return `<div class="panel state-panel">${t("尚未添加 Google 账号。", "No Google accounts added yet.")}</div>`;
  const rows = data.accounts.map(profile => {
    const connected = profile.auth_status === "connected";
    const authAction = connected
      ? button(t("刷新实例", "Refresh instances"), "refresh-account", "compact", profile.id, busy)
      : button(t("连接 Google", "Connect to Google"), "connect-account", "primary compact", profile.id, busy);
    const rename = button(t("改名", "Rename"), "rename-account", "text compact", profile.id, busy);
    const disconnect = profile.auth_status !== "disconnected" ? button(t("解绑", "Disconnect"), "disconnect-account", "text compact", profile.id, busy) : "";
    const options = connected ? "" : button(t("其他连接方式", "Other ways to connect"), "connect-options", "text compact", profile.id, busy);
    const error = profile.auth_error ? `<p class="inline-error account-error" role="status">${escapeHtml(serviceText(profile.auth_error))}</p>` : "";
    return `<div class="account-row">${identity(profile)}<div class="actions">${authStatus(profile)}${authAction}${options}${button(t("查看 Colab", "View Colab"), "view-account", "text compact", profile.id)}${rename}${disconnect}</div>${error}</div>`;
  });
  return `<section class="panel"><div class="panel-header"><h2>${t("Google 账号", "Google accounts")}</h2><span class="panel-note">${t(`${data.accounts.length} 个`, String(data.accounts.length))}</span></div>${rows.join("")}</section>`;
}

function render() {
  for (const link of document.querySelectorAll("[data-page]")) {
    if (link.dataset.page === page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  renderAccountSwitch();
  pageContent.setAttribute("aria-busy", String(busy || localStatus === "loading"));
  if (page === "settings") {
    pageContent.setAttribute("aria-busy", "false");
    renderSettings();
    return;
  }
  if (page === "agent") {
    pageContent.setAttribute("aria-busy", "false");
    renderAgent();
    return;
  }
  if (localStatus !== "ready") {
    pageTitle(localStatus === "error" ? button(t("重试", "Retry"), "retry", "primary") : "");
    pageContent.innerHTML = `<div class="panel state-panel">${localStatus === "error" ? t("无法读取本机工作区。", "Could not load the local workspace.") : t("正在加载本机工作区…", "Loading the local workspace…")}</div>`;
    return;
  }
  const profile = account();
  if (page === "benefits") pageContent.innerHTML = renderBenefits(profile);
  else if (page === "accounts") pageContent.innerHTML = renderAccounts();
  else if (page === "studio") {
    pageContent.innerHTML = renderStudio(profile);
    refreshStudio();
  }
  else pageContent.innerHTML = renderColab(profile);
}

function setDialog(title, body, submitLabel, handler, submitStyle = "primary") {
  dialogSubmit = handler;
  const actions = submitLabel ? `<div class="dialog-actions"><button type="button" class="button" data-action="close-dialog">${t("取消", "Cancel")}</button><button type="submit" class="button ${submitStyle}">${escapeHtml(submitLabel)}</button></div>` : "";
  dialogContent.innerHTML = `<div class="dialog-header"><h2 id="dialog-title">${escapeHtml(title)}</h2><button type="button" class="dialog-close" data-action="close-dialog" aria-label="${t("关闭", "Close")}">×</button></div>
    <form class="dialog-body"><p class="inline-error dialog-problem" role="alert" hidden></p>${body}${actions}</form>`;
  if (!dialog.open) {
    returnFocus = document.activeElement;
    dialog.showModal();
  }
  dialogContent.querySelector("input, select, a")?.focus();
}

function contextRows(entries) {
  return `<dl class="dialog-context">${entries.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}</dl>`;
}

function addAccount() {
  setDialog(t("添加 Google 账号", "Add a Google account"), `
    <label class="field">${t("账号别名", "Account name")}<input name="name" placeholder="${t("例如，工作账号", "Example: Work")}" maxlength="48" required></label>
    <label class="field">${t("Google 邮箱", "Google email")}<input name="email" type="email" autocomplete="email" maxlength="254" required></label>`,
  t("添加账号", "Add account"), async fields => {
    const email = fields.get("email").trim();
    await change("/api/accounts", { name: fields.get("name").trim(), email });
    const added = data.accounts.find(item => item.email.toLowerCase() === email.toLowerCase());
    if (added) selectAccount(added.id);
    announce(t("已添加账号。连接 Google 后可刷新 Colab 实例。", "Account added. Connect it to Google to refresh Colab instances."));
  });
}

function renameAccount(profile) {
  setDialog(t("修改账号别名", "Rename account"), `
    <label class="field">${t("账号别名", "Account name")}<input name="name" value="${escapeHtml(profile.name)}" maxlength="48" required></label>`,
  t("保存别名", "Save name"), async fields => {
    await change(accountPath(profile, "/rename"), { name: fields.get("name").trim() });
    announce(t("已保存账号别名。", "Account name saved."));
  });
}

function disconnectAccount(profile) {
  setDialog(t("移除本机授权", "Disconnect local account"), `${contextRows([[t("Google 邮箱", "Google email"), profile.email]])}
    <p class="panel-note">${t("已启动的实例由 Colab 继续管理。需要停止实例时，请先到 Colab 操作。", "Running instances stay in Colab. To stop an instance, release it in Colab before disconnecting.")}</p>`,
  t("移除授权", "Disconnect"), async () => {
    await change(accountPath(profile, "/disconnect"), {});
    announce(t("已移除这个账号在本机的授权。", "Account disconnected on this Mac."));
  }, "danger");
}

function watchConnection(id) {
  clearTimeout(oauthWatch);
  const authorization = activeAuthorization;
  let remaining = 241;
  const tick = async () => {
    if (activeAuthorization !== authorization) return;
    if (!busy) {
      try {
        if (await readState()) render();
        if (activeAuthorization !== authorization) return;
        const profile = data.accounts.find(item => item.id === id);
        if (profile?.auth_status === "connected") {
          activeAuthorization = null;
          announce(t("Google 账号已连接。", "Google account connected."));
          if (dialog.open && dialog.querySelector("[data-oauth-wait]")) dialog.close();
          return;
        }
        if (profile?.auth_status !== "connecting") {
          showAuthProblem(serviceText(profile?.auth_error) || t("授权未完成。请重新连接 Google。", "Authorization is incomplete. Connect to Google again."));
          const actions = dialogContent.querySelector("[data-oauth-actions]");
          if (actions) actions.innerHTML = button(t("重新授权", "Authorize again"), "connect-account", "compact", id);
          const submit = dialogContent.querySelector("[type=submit]");
          if (submit) submit.disabled = true;
          return;
        }
      } catch {
        // The current page still shows its last successful state.
      }
    }
    if (--remaining > 0) oauthWatch = setTimeout(tick, 2500);
  };
  oauthWatch = setTimeout(tick, 1500);
}

async function openAccountAuthorization(profile, path = "") {
  const result = await change(accountPath(profile, "/connect"), { client_config_path: path });
  const candidate = safeUrl(result.open_url);
  const url = candidate && new URL(candidate);
  if (!url || url.host !== "accounts.google.com" || url.hash
    || !["/o/oauth2/auth", "/o/oauth2/v2/auth"].includes(url.pathname)) {
    throw new Error(t("授权网址校验失败。请重新连接 Google。", "The authorization URL could not be verified. Connect to Google again."));
  }
  activeAuthorization = { profile, attempt: result.attempt };
  const opened = await desktop?.openGoogleAuthorization?.postMessage(url.href).catch(() => false);
  const usesCode = result.flow === "cli";
  const instruction = usesCode
    ? t("在浏览器选择上方账号并同意授权，然后把 Google 页面显示的授权码粘贴到这里。", "Choose the account shown above and approve access in your browser. Paste the code Google displays here.")
    : t("在浏览器选择上方账号并同意授权。完成后，这里会自动更新。", "Choose the account shown above and approve access in your browser. The app will update automatically.");
  const context = `<div data-oauth-wait>${contextRows([[t("Google 邮箱", "Google email"), profile.email]])}</div>`;
  const links = `<div class="actions" data-oauth-actions>${externalLink(opened ? t("重新打开 Google 页面", "Reopen Google") : t("打开 Google 授权页", "Open Google authorization"), url.href, opened ? "text compact" : "compact")}</div>`;
  const title = t("连接 Google", "Connect to Google");
  if (usesCode) {
    setDialog(title, `${context}<p class="panel-note">${instruction}</p>
      <label class="field">${t("Google 授权码", "Google authorization code")}
        <input name="code" type="password" autocomplete="off" maxlength="4096" required></label>
      ${links}`, t("完成连接", "Connect account"), async fields => {
      await change(accountPath(profile, "/connect/code"), { attempt: result.attempt, code: fields.get("code").trim() });
      activeAuthorization = null;
      announce(t("Google 账号已连接。", "Google account connected."));
    });
  } else {
    setDialog(title, `${context}<p class="panel-note">${instruction}</p>${links}`);
  }
  watchConnection(profile.id);
}

async function connectAccount(profile) {
  busy = true;
  announce(t("正在打开 Google 授权…", "Opening Google authorization…"));
  render();
  try {
    await openAccountAuthorization(profile);
    announce("");
  } catch (error) {
    announce(error.message, true);
  } finally {
    busy = false;
    render();
  }
}

function connectionOptions(profile) {
  const picker = desktop?.chooseOAuthClient
    ? button(t("选择文件…", "Choose file…"), "choose-oauth-client", "compact")
    : "";
  setDialog(t("其他连接方式", "Other ways to connect"), `${contextRows([[t("Google 邮箱", "Google email"), profile.email]])}
    <div class="actions">${button(t("导入已有的 Colab CLI 授权", "Import existing Colab CLI authorization"), "import-cli", "", profile.id)}</div>
    <div class="field"><label for="client-config-path">${t("桌面 OAuth 配置文件", "Desktop OAuth client file")}</label>
      <div class="path-picker"><input id="client-config-path" name="client_config_path" placeholder="${t("选择 OAuth 客户端 JSON", "Choose an OAuth client JSON file")}" autocomplete="off" maxlength="1024" required>${picker}</div></div>`,
  t("使用桌面配置", "Connect with client file"), async fields => {
    await openAccountAuthorization(profile, fields.get("client_config_path").trim());
    return false;
  });
}

function importCli(profile) {
  const picker = desktop?.chooseCliToken ? button(t("选择文件…", "Choose file…"), "choose-oauth-client", "compact", "cli") : "";
  setDialog(t("连接已授权的 Colab CLI", "Link an authorized Colab CLI"), `${contextRows([[t("Google 邮箱", "Google email"), profile.email]])}
    <div class="field"><label for="client-config-path">${t("CLI 授权文件路径", "CLI authorization file path")}</label>
      <div class="path-picker"><input id="client-config-path" name="client_config_path"
        value="~/.config/colab-cli/token.json" maxlength="1024" autocomplete="off" required>${picker}</div>
      <small>${t("Google 会核对邮箱是否与档案一致，授权保存在此账号的本机档案中。", "Google checks that the email matches. Authorization stays in the local profile.")}</small>
    </div>`, t("核对并连接", "Verify and connect"), async fields => {
    const result = await change(accountPath(profile, "/import-cli"), { token_file: fields.get("client_config_path").trim() });
    announce(serviceText(result.message, "Colab CLI authorization imported."));
  });
}

async function chooseOAuthClient(control) {
  const field = dialogContent.querySelector("[name=client_config_path]");
  if (!field || !desktop?.chooseOAuthClient) return;
  control.disabled = true;
  try {
    const handler = control.dataset.id === "cli" ? desktop.chooseCliToken : desktop.chooseOAuthClient;
    const path = await handler.postMessage(null);
    if (typeof path === "string" && path && field.isConnected) {
      field.value = path;
      field.focus();
    }
  } catch {
    if (!field.isConnected) return;
    const problem = dialogContent.querySelector(".dialog-problem");
    problem.textContent = t("无法打开文件选择器。请在上方输入配置文件路径。", "Could not open the file picker. Enter the configuration file path above.");
    problem.hidden = false;
  } finally {
    control.disabled = false;
  }
}

function stopInstance(profile, instanceId) {
  const observation = profile.observation || {};
  if (profile.auth_status !== "connected" || !hasObservation(observation)) return;
  const instance = observation.instances?.find(item => item.id === instanceId);
  if (!instance) return;
  setDialog(t("停止 Colab 实例", "Stop a Colab instance"), `${contextRows([[t("Google 邮箱", "Google email"), profile.email], [t("实例 ID", "Instance ID"), instance.id], [t("算力", "GPU"), instance.gpu || t("待核验", "Not verified")]])}
    <label class="confirmation"><input type="checkbox" name="confirmed" required><span>${t("我已保存所需文件，确认释放上方实例。", "I saved the files I need and confirm release of this instance.")}</span></label>
    <details class="steps"><summary>${t("任务状态无法核对时", "If job status cannot be verified")}</summary>
      <label class="confirmation"><input type="checkbox" name="discard_jobs"><span>${t("确认放弃待核对任务中尚未取回的作品。", "Discard uncollected outputs from jobs whose status is unknown.")}</span></label>
    </details>`,
  t("确认停止", "Stop instance"), async fields => {
    const result = await change(accountPath(profile, "/instances/stop"), {
      instance_id: instance.id, confirmed: fields.get("confirmed") === "on", discard_jobs: fields.get("discard_jobs") === "on",
    });
    announce(serviceText(result.message, "Stop request completed. Check the instance status.") || t("停止请求已完成。请查看当前账号的实例状态。", "Stop request completed. Check the instance status."));
  }, "danger");
}

function recordBenefit(profile, id) {
  const benefit = data.benefits.find(item => item.id === id);
  if (!benefit) return;
  const record = profile.benefit_status?.[benefit.id];
  const options = Object.entries(benefitLabels).map(([value, label]) => `<option value="${value}"${record?.status === value ? " selected" : ""}>${escapeHtml(t(...label))}</option>`).join("");
  setDialog(t("记录权益核验结果", "Record verified perk status"), `${contextRows([[t("Google 邮箱", "Google email"), profile.email], [t("权益", "Perk"), localizedBenefit(benefit).title]])}
    <label class="field">${t("在 Google 核对的状态", "Status verified in Google")}<select name="status">${options}</select></label>
    <label class="field">${t("备注（选填）", "Note (optional)")}<textarea name="note" maxlength="500" rows="3">${escapeHtml(record?.note)}</textarea></label>`,
  t("保存记录", "Save record"), async fields => {
    await change(accountPath(profile, `/benefits/${encodeURIComponent(benefit.id)}`), { status: fields.get("status"), note: fields.get("note").trim() });
    announce(t("已保存当前账号的本人核验记录。", "Verification record saved for this account."));
  });
}

async function refreshAccount(profile) {
  busy = true;
  announce(t(`正在刷新 ${profile.name} 的 Colab 实例…`, `Refreshing Colab instances for ${profile.name}…`));
  render();
  try {
    const result = await change(accountPath(profile, "/refresh"), {});
    const observation = data.accounts.find(item => item.id === profile.id)?.observation || {};
    announce(serviceText(result.message, `Refreshed instance and compute status for ${profile.name}.`) || serviceText(observation.error) || t(`已刷新 ${profile.name} 的实例和算力状态。`, `Refreshed instance and compute status for ${profile.name}.`), observation.status === "error" || observation.status === "cached");
  } catch (error) {
    await readState().catch(() => {});
    announce(error.message, true);
  } finally {
    busy = false;
    render();
  }
}

function showAuthProblem(message) {
  if (dialog.open && dialog.querySelector("[data-oauth-wait]")) {
    const problem = dialogContent.querySelector(".dialog-problem");
    problem.textContent = message;
    problem.hidden = false;
  }
}

document.addEventListener("click", event => {
  const control = event.target.closest("[data-action]");
  if (!control || control.disabled) return;
  const { action, id } = control.dataset;
  const profile = data.accounts.find(item => item.id === id);
  if (action === "retry") load();
  else if (action === "close-dialog" && !busy) dialog.close();
  else if (action.startsWith("settings-") && !busy) settingsAction(action, control);
  else if (action === "agent-copy" && page === "agent") copyAgentPrompt(control);
  else if (action === "select-account" || action === "view-account") {
    if (!profile) return;
    selectAccount(profile.id);
    announce("");
    if (action === "view-account") location.hash = "colab";
    render();
  } else if (!busy && localStatus === "ready") {
    if (action.startsWith("studio-")) studioAction(action, id, account());
    else if (action === "add-account") addAccount();
    else if (action === "rename-account" && profile) renameAccount(profile);
    else if (action === "import-cli" && profile) importCli(profile);
    else if (action === "disconnect-account" && profile) disconnectAccount(profile);
    else if (action === "connect-account" && profile) connectAccount(profile);
    else if (action === "connect-options" && profile) connectionOptions(profile);
    else if (action === "choose-oauth-client") chooseOAuthClient(control);
    else if (action === "refresh-account" && profile) refreshAccount(profile);
    else if (action === "start-instance" && profile) startInstance(profile);
    else if (action === "stop-instance" && account()) stopInstance(account(), id);
    else if (action === "filter-benefits" && page === "benefits") filterBenefits(id);
    else if (action === "record-benefit" && account()) recordBenefit(account(), id);
  }
});

pageContent.addEventListener("change", event => {
  if (event.target.id === "settings-language") changeSettingsLanguage(event.target);
});

accountSwitch.addEventListener("change", event => {
  if (event.target.id === "mobile-account") {
    selectAccount(event.target.value);
    announce("");
    render();
    document.querySelector("#mobile-account")?.focus();
  }
});

dialog.addEventListener("submit", async event => {
  event.preventDefault();
  if (!dialogSubmit || busy) return;
  const form = event.target;
  const fields = new FormData(form);
  const problem = form.querySelector(".dialog-problem");
  problem.hidden = true;
  const controls = [...dialogContent.querySelectorAll("button, input, textarea, select")].filter(control => !control.disabled);
  controls.forEach(control => { control.disabled = true; });
  busy = true;
  dialog.setAttribute("aria-busy", "true");
  try {
    if (await dialogSubmit(fields) !== false) dialog.close();
  } catch (error) {
    problem.textContent = error.message;
    problem.hidden = false;
    await readState().catch(() => {});
  } finally {
    busy = false;
    dialog.removeAttribute("aria-busy");
    controls.forEach(control => { control.disabled = false; });
    render();
  }
});

dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
dialog.addEventListener("close", () => {
  for (const media of dialog.querySelectorAll("audio, video")) media.pause();
  clearTimeout(oauthWatch);
  if (activeAuthorization) {
    const { profile, attempt } = activeAuthorization;
    activeAuthorization = null;
    change(accountPath(profile, "/connect/cancel"), { attempt }).then(render).catch(() => {});
  }
  dialogSubmit = null;
  if (returnFocus?.isConnected) returnFocus.focus();
});
window.addEventListener("hashchange", () => {
  const key = location.hash.slice(1);
  if (!Object.hasOwn(pageLabels, key)) return;
  page = key;
  announce("");
  render();
});

async function readLocalChanges() {
  if (busy || localStatus !== "ready" || document.hidden) return;
  try {
    if (await readState()) render();
  } catch {
    // Interactive refresh still shows the actual error.
  }
}

document.addEventListener("visibilitychange", readLocalChanges);
setInterval(readLocalChanges, 15000);

async function initialize() {
  const metadata = Promise.all([initializeAppInfo(), loadBenefitTranslations()]);
  if (desktop?.getAccountSelection) {
    try {
      selected = await desktop.getAccountSelection.postMessage(null);
    } catch {
      // Use the current window's selection if the desktop preference is unavailable.
    }
  }
  await metadata;
  await load();
}

initialize();
