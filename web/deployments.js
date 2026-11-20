"use strict";

const projectNotes = {
  h3: ["用 ComfyUI 生成带声音的视频。原 G4 工作流和 4 个旧节点可选。", "Generate video with stereo audio in ComfyUI. The original G4 workflow and four legacy nodes are available."],
  comfy: ["部署工作台，在浏览器导入自己的工作流。", "Open a private workspace and import your own workflows."],
  wan: ["准备 Wan2.2 5B 模型和 ComfyUI，适合轻量短片。", "Prepare Wan2.2 5B and ComfyUI for lightweight video."],
  breeze: ["准备配音权重和依赖，从创作页生成 WAV。", "Prepare the voiceover weights and dependencies. Generate WAV audio in Studio."],
  qwen: ["用 CPU 部署小型 Qwen3，对话和接口调试。", "Deploy a small Qwen3 assistant on CPU for chat and API testing."],
};

function hardwareSummary(hardware) {
  const gib = mib => new Intl.NumberFormat(appInfo.effectiveLanguage, { maximumFractionDigits: 1 }).format(mib / 1024) + " GiB";
  const devices = hardware.gpus?.map(gpu => gpu.name + " · " + t("显存", "VRAM") + " " + gib(gpu.vram_mib)) || [];
  return [...devices, t("系统内存", "System RAM") + " " + gib(hardware.ram_mib),
    t("磁盘可用", "Disk free") + " " + gib(hardware.free_disk_mib)].join(" · ");
}

function projectInstances(profile, project) {
  if (profile?.auth_status !== "connected") return [];
  return (profile.observation?.instances || []).filter(row => data.projects[project]?.gpus.includes(row.gpu) &&
    (project !== "h3" || data.h3Regions.includes(row.region)));
}

function legacyNodesField(hidden = false) {
  return `<label class="confirmation" data-legacy-nodes${hidden ? " hidden" : ""}><input type="checkbox" name="legacy_nodes"${hidden ? " disabled" : ""}>
    <span>${t("安装旧工作流的 4 个节点（接续、降噪、音频、放大）", "Install four legacy nodes for continuation, noise, audio and upscaling")}</span></label>`;
}

function queueDeployment(profile, instanceId, project, legacyNodes = false) {
  return change(studioUrl(), {
    id: crypto.randomUUID().replaceAll("-", ""), kind: "deploy", mode: "colab",
    project, account_id: profile.id, instance_id: instanceId, legacy_nodes: legacyNodes, confirmed: true,
  });
}

function openDeployment(project, profile) {
  const config = data.projects[project];
  if (!config) return;
  if (profile?.auth_status !== "connected") {
    setDialog(config.name, `<p>${t("请先连接要使用的 Google 账号。", "Connect the Google account you want to use first.")}</p>
      <a class="button primary" href="#accounts" data-action="studio-colab">${t("账号管理", "Accounts")}</a>`);
    return;
  }
  const items = projectInstances(profile, project);
  if (!items.length) {
    startInstance(profile, project);
    return;
  }
  const options = items.map(instance => `<option value="${escapeHtml(instance.id)}">${escapeHtml([instance.name, instance.gpu, instance.region].filter(Boolean).join(" · "))}</option>`).join("");
  setDialog(config.name, contextRows([[t("账号", "Account"), profile.name]]) +
    `<label class="field">${t("部署到实例", "Deploy to instance")}<select name="instance_id">${options}</select></label>
    <p class="panel-note">${t("部署会在所选实例下载并校验模型。实例运行期间按 Colab 实际费率计费。", "Deployment downloads and verifies models on the selected instance. Actual Colab rates apply while the instance runs.")}</p>
    ${project === "h3" ? legacyNodesField() : ""}
    <div class="actions">${externalLink(t("查看许可", "View license"), config.license, "text compact")}</div>`,
  t("开始部署", "Deploy"), async fields => {
    await queueDeployment(profile, fields.get("instance_id"), project, fields.get("legacy_nodes") === "on");
    location.hash = "apps";
    await refreshStudio();
  });
}

function renderDeploymentJobs() {
  const deployments = activeStudioJobs().filter(job => job.kind === "deploy");
  if (!deployments.length) return `<p class="panel-empty">${t("这个账号暂无部署记录。", "No deployments for this account yet.")}</p>`;
  const instances = account()?.observation?.instances || [];
  return deployments.map(job => {
    const row = instances.find(instance => instance.id === job.instance_id);
    const [zh, en, color] = jobStates[job.state] || ["待核验", "Check status", ""];
    const state = job.state === "done" && row ? status(t("已部署", "Deployed"), "good") : job.state === "done"
      ? status(t("实例待核验", "Instance not listed"), "warn") : status(t(zh, en), color);
    const action = job.state === "done" && row
      ? job.project === "breeze" ? button(t("去配音", "Open voiceover"), "deploy-use", "compact", job.id)
      : button(job.project === "qwen" ? t("打开对话", "Open chat") : t("打开 ComfyUI", "Open ComfyUI"), "deploy-open", "compact", job.id)
        + (job.project === "h3" ? button(t("新建 H3 视频", "New H3 video"), "deploy-use", "text compact", job.id) : "")
      : job.state === "check" ? button(t("核对部署", "Verify deployment"), "studio-check", "compact", job.id) : "";
    const metadata = job.hardware ? hardwareSummary(job.hardware) : serviceText(job.phase);
    return `<div class="deployment-row">
      <div><strong>${escapeHtml(job.title)}</strong><p class="secondary">${escapeHtml(row?.name || job.instance_id)}</p></div>
      <div>${state}<p class="panel-note">${escapeHtml(metadata)}</p></div><div class="actions">${action}</div>
    </div>`;
  }).join("");
}

function renderDeployments(profile) {
  pageTitle();
  const projects = Object.entries(data.projects);
  const cards = projects.map(([id, project]) => `<section class="panel deployment-card${id === "h3" ? " is-featured" : ""}">
    <h2>${escapeHtml(project.name)}</h2><p class="panel-note">${t(...projectNotes[id])}</p>
    <p class="deployment-gpus">${escapeHtml(project.gpus.join(" · "))}</p>
    <div class="actions">${button(t("部署", "Deploy"), "deploy-project", id === "h3" ? "primary compact" : "compact", id, busy)}
      ${externalLink(t("许可", "License"), project.license, "text compact")}</div>
  </section>`).join("");
  return `<div class="layout-stack">
    <div class="deployment-grid" aria-label="${t(`${projects.length} 个可部署项目`, `${projects.length} deployable projects`)}">${cards}</div>
    <section class="panel"><div class="panel-header"><h2>${t("部署记录", "Deployments")}</h2><span class="panel-note">${escapeHtml(profile?.name || "")}</span></div>
      <div id="deployment-jobs">${renderDeploymentJobs()}</div></section>
  </div>`;
}

async function deploymentAction(action, id, profile) {
  if (action === "deploy-project") return openDeployment(id, profile);
  const job = activeStudioJobs().find(row => row.id === id && row.kind === "deploy" && row.state === "done");
  if (!job || profile?.id !== job.account_id) return;
  if (action === "deploy-use") {
    location.hash = "studio";
    return openStudio(job.project === "breeze" ? "tts" : "video", profile, job.instance_id, "MiniMax H3");
  }
  if (action !== "deploy-open") return;
  busy = true;
  announce(t("正在建立私有应用通道…", "Opening the private application connection…"));
  render();
  try {
    const { port } = await api(studioUrl(job.id, "/open"), {});
    if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error(t("无效的应用端口。", "Invalid application port."));
    if (desktop?.openPrivateEditor) {
      if (!(await desktop.openPrivateEditor.postMessage(port))) throw new Error(t("无法打开浏览器。", "Could not open the browser."));
    } else {
      window.open(`http://127.0.0.1:${port}/`, "_blank", "noopener,noreferrer");
    }
    announce("");
  } catch (error) { announce(error.message, true); }
  finally { busy = false; render(); }
}
