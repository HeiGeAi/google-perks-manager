"use strict";

function agentPrompt() {
  const command = appInfo.agentCommand
    ? appInfo.agentShell === "powershell"
      ? "& '" + appInfo.agentCommand.replaceAll("'", "''") + "' --agent"
      : "'" + appInfo.agentCommand.replaceAll("'", "'\\''") + "' --agent"
    : "python3 -m google_manager.agent";
  const guide = command + " guide --language " + appInfo.effectiveLanguage;
  return t(
`请用谷歌羊毛管理器协助我查看和操作本机的 Google 权益、Colab 算力和创作任务。请先在本机终端执行以下命令，读取可用命令、参数和结果格式。

${guide}

按最新指南调用命令。App 保持打开时，可读取本机账号的授权状态、实例、余额和观察时间。先确认我选的账号，区分已授权、存在实例和最近一次核对的余额。需要重新核对时，请刷新目标账号。

我要看权益时，请按我提供的会员档位读取目录和单项权益，列出官方领取地址、额度、资格和核对步骤。先确认官方页面使用的账号。Google 登录、授权、付款、订阅及 Cloud Billing 变更请交给我在官方页面完成。只有在我确认结果后才记录领取状态。

如果账号尚未接入 Colab，请先说明官方 OAuth 或已有 Colab CLI 授权的本机接入方式。经我明确同意后再打开授权流程。凭证由本机工具读取，请把令牌、Cookie、客户端密钥和授权链接保留在本机。

申请或释放实例、部署应用、运行会消耗 CU 的创作任务前，请让我确认账号、实例、费用或文件风险。按指南选择兼容实例部署 MiniMax H3 或其他项目。生成视频时优先指定 MiniMax H3。核对任务进度，取回作品并给出实际保存路径。合成任务在本机执行。请用实际返回值报告结果，余额未知时标明待核对。`,
`Use Google Perks Manager to help me review and manage Google perks, Colab compute, and production jobs on this computer. First run the command below in a local terminal to read the current commands, arguments, and response format.

${guide}

Follow the guide you receive. Keep the app open for live account, instance, balance, and job queries. Confirm which account I mean and distinguish Google authorization, running instances, and the last verified balance. Refresh that account when a live check is needed.

For perks, use the membership tier I specify. Read the catalog and selected perk, then show the official redemption link, allowance, eligibility, and verification steps. Verify the account on the official page. Let me complete Google sign-in, consent, payment, subscriptions, and Cloud Billing changes there. Record redemption status only after I confirm the result.

If Colab is disconnected, explain the official OAuth and existing local Colab CLI authorization options. Open the consent flow only after I confirm. Keep credentials local, including tokens, cookies, client secrets, and authorization URLs.

Before starting or releasing an instance, deploying an app, or running a CU-consuming job, ask me to confirm the account, instance, costs, or file risks. Use the guide to select a compatible runtime and deploy MiniMax H3 or another project. Prefer MiniMax H3 for new video jobs. Check progress, collect the result, and report its saved path. Audio and video mixing runs locally. Base your report on returned results and mark any unknown balance as unverified.`);
}

function renderAgent() {
  pageTitle();
  pageContent.innerHTML = `<section class="panel agent-panel" aria-labelledby="agent-prompt-title">
    <header class="panel-header">
      <div>
        <h2 id="agent-prompt-title">${t("接入提示词", "Connection prompt")}</h2>
        <p class="panel-note">${appInfo.agentCommand
          ? t("把提示词发给能在本机运行命令的 Agent。", "Give this prompt to an agent that can run commands on this computer.")
          : t("在源码目录使用提示词中的命令。", "Run the command in the prompt from the source checkout.")}</p>
      </div>
      ${button(t("复制提示词", "Copy prompt"), "agent-copy", "primary compact")}
    </header>
    <textarea id="agent-prompt" readonly rows="14" spellcheck="false" aria-labelledby="agent-prompt-title">${escapeHtml(agentPrompt())}</textarea>
    <p id="agent-copy-status" class="copy-status" role="status" hidden></p>
  </section>`;
}

async function copyAgentPrompt(control) {
  const field = document.querySelector("#agent-prompt");
  const message = document.querySelector("#agent-copy-status");
  if (!field || !message) return;
  control.disabled = true;
  try {
    if (desktop?.copyAgentPrompt) {
      if (await desktop.copyAgentPrompt.postMessage(field.value) !== true) throw new Error();
    } else {
      await navigator.clipboard.writeText(field.value);
    }
    message.textContent = t("提示词已复制", "Prompt copied");
  } catch {
    field.focus();
    field.select();
    message.textContent = t("提示词已选中，请使用复制快捷键。", "Prompt selected. Use your copy shortcut.");
  } finally {
    message.hidden = false;
    control.disabled = false;
  }
}
