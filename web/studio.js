"use strict";

const productionKinds = { tts: ["配音", "Voiceover"], video: ["文生视频", "Text to video"], mux: ["音画合成", "Audio and video mix"] };
const jobStates = {
  queued: ["已排队", "Queued", ""], running: ["运行中", "Running", ""], remote: ["Colab 运行中", "Running on Colab", ""],
  check: ["待核对", "Needs verification", "warn"], done: ["已保存", "Saved", "good"], failed: ["失败", "Failed", "bad"],
  interrupted: ["已中断", "Interrupted", "warn"],
};
const creationGPUs = new Set(["L4", "G4", "A100", "H100"]);
let studio = { account: null, jobs: [], error: null };
let studioRequest = 0;

function studioUrl(id = "", suffix = "") {
  return "/api/production/jobs" + (id ? "/" + encodeURIComponent(id) + suffix : "");
}

async function refreshStudio() {
  if (!["studio", "apps"].includes(page) || localStatus !== "ready") return;
  const current = account()?.id || "";
  const request = ++studioRequest;
  try {
    const result = await api(studioUrl() + (current ? "?account=" + encodeURIComponent(current) : ""));
    if (request !== studioRequest || current !== (account()?.id || "")) return;
    if (!Array.isArray(result.jobs)) throw new Error(t("本机任务列表格式有误。", "The local service returned an invalid job list."));
    if (studio.account === current && studio.error === null && JSON.stringify(studio.jobs) === JSON.stringify(result.jobs)) return;
    studio = { account: current, jobs: result.jobs, error: null };
  } catch (error) {
    if (request !== studioRequest || current !== (account()?.id || "")) return;
    studio = { account: current, jobs: studio.account === current ? studio.jobs : [], error: error.message };
  }
  const list = document.querySelector("#studio-jobs");
  if (list) list.innerHTML = renderStudioJobs();
  const deployments = document.querySelector("#deployment-jobs");
  if (deployments) deployments.innerHTML = studio.error ? `<p class="panel-empty inline-error">${escapeHtml(studio.error)}</p>` : renderDeploymentJobs();
}

function activeStudioJobs() {
  return studio.account === (account()?.id || "") ? studio.jobs : [];
}

function renderStudioJobs() {
  if (studio.account !== (account()?.id || "")) return `<p class="job-empty" role="status">${t("正在读取本机任务…", "Loading local jobs…")}</p>`;
  const problem = studio.error ? '<p class="job-empty inline-error" role="alert">' + escapeHtml(studio.error) + '</p>' : "";
  const jobs = studio.jobs.filter(job => job.kind !== "deploy");
  if (!jobs.length) return problem || `<p class="job-empty">${t("提交创作后，进度与作品会出现在这里。", "Production jobs and their outputs will appear here.")}</p>`;
  return problem + jobs.map(job => {
    const [zh, en, kind] = jobStates[job.state] || ["状态待核对", "Check status", ""];
    const name = t(zh, en);
    const target = job.mode === "mac" ? t("本机", "This computer") : job.state === "done"
      ? t("Colab · 已存本机", "Colab · Stored locally") : "Colab · " + job.instance_id;
    const info = job.artifact;
    const detail = info
      ? [info.engine, formatMoney(info.seconds) + t(" 秒", " s"), info.width ? info.width + "×" + info.height : info.sample_rate + " Hz",
         formatMoney(info.size / (1024 * 1024)) + " MB"].filter(Boolean).join(" · ")
      : serviceText(job.phase, name);
    const action = job.state === "done" && info
      ? button(job.kind === "tts" ? t("试听", "Listen") : t("播放", "Play"), "studio-preview", "compact", job.id) +
        (desktop?.revealProductionFile ? button(t("打开文件夹", "Open folder"), "studio-reveal", "compact", job.id) : "") +
        (desktop?.pickDriveFolder ? button(t("转存云盘", "Save to Drive"), "studio-archive", "compact", job.id) : "")
      : job.mode === "colab" && ["remote", "check"].includes(job.state)
        ? button(t("核对并取回", "Verify and collect"), "studio-check", "compact", job.id)
        : "";
    const receipt = job.drive_archive;
    const driveLabel = receipt ? [t("已复制到云盘目录", "Copied to Drive folder"),
      data.accounts.find(profile => profile.id === receipt.account_id)?.name, receipt.project].filter(Boolean).join(" · ") : "";
    return '<article class="job-row">' +
      '<div class="job-title"><strong>' + escapeHtml(job.title) + '</strong><small>' +
      escapeHtml(t(...(productionKinds[job.kind] || ["任务", "Job"])) + " · " + target) + '</small></div>' +
      '<div class="job-status">' + status(name, kind) + '<span>' + escapeHtml(detail) + '</span>' +
      (driveLabel ? '<small class="job-archive">' + escapeHtml(driveLabel) + '</small>' : "") + '</div>' +
      '<div class="job-tools"><small>' + escapeHtml(formatTime(job.updated_at)) + '</small>' +
      (action ? '<div class="job-actions">' + action + '</div>' : "") + '</div></article>';
  }).join("");
}

function availableInstances(profile, engine) {
  if (engine === "MiniMax H3") return projectInstances(profile, "h3");
  if (profile?.auth_status !== "connected") return [];
  const items = profile.observation?.instances || [];
  return items.filter(i => creationGPUs.has(i.gpu));
}

function studioTarget(profile, engine) {
  const instances = availableInstances(profile, engine);
  if (instances.length) return escapeHtml(profile.name) + ' <span class="studio-target-count">' + t(instances.length + " 台可选", instances.length + " available") + '</span>';
  if (profile?.auth_status === "connected") return t("新建时可直接启动 Colab 实例", "Start a Colab instance when you begin");
  return t("连接 Google 后选择实例", "Connect Google to select an instance");
}

function renderStudio(profile) {
  pageTitle();
  const modules = [
    ["video", t("MiniMax H3 视频", "MiniMax H3 video"), t("生成带立体声的视频，或在 ComfyUI 继续图生视频与接续工作流。", "Generate video with stereo audio or continue image-to-video and multi-shot workflows in ComfyUI."), t("新建 H3 视频", "New H3 video"), studioTarget(profile, "MiniMax H3")],
    ["tts", t("Breeze TTS 2 配音", "Breeze TTS 2 voiceover"), t("在 Colab GPU 上生成 WAV。", "Generate WAV audio on a Colab GPU."), t("新建配音", "New voiceover"), studioTarget(profile)],
    ["mux", t("音画合成", "Audio and video mix"), t("选择视频和一段配音，导出带声音的 MP4。", "Combine a video and a voiceover into an MP4."), t("新建合成", "New mix"), t("本机", "This computer")],
  ];
  return '<div class="layout-stack studio-page"><div class="studio-modules">' +
    modules.map(([key, title, caption, cta, target]) =>
      '<section class="panel studio-module studio-' + key + '"><div class="studio-module-title">' +
      '<i class="studio-icon studio-icon-' + key + '" aria-hidden="true"></i><h2>' + title + '</h2></div>' +
      '<p>' + caption + '</p><small>' + target + '</small><div class="actions">' +
      button(cta, "studio-launch", key === "video" ? "primary compact" : "compact", key, busy) +
      (key === "video" ? button(t("Wan2.2 备选", "Wan2.2 alternative"), "studio-wan", "text compact", "", busy) : "") + '</div></section>'
    ).join("") +
    '</div><section class="panel studio-ledger"><div class="panel-header"><h2>' +
    t("任务与作品", "Jobs and outputs") + '</h2><span class="panel-note">' +
    t("本机作品 · 跨账号可见", "Local outputs · Visible across accounts") +
    '</span></div><div id="studio-jobs">' + renderStudioJobs() + '</div></section></div>';
}

function studioContext(profile, kind, preferredInstance, engine) {
  const items = availableInstances(profile, engine);
  const options = items.map(item => '<option value="' + escapeHtml(item.id) + '"' + (item.id === preferredInstance ? ' selected' : '') + '>' +
    escapeHtml([item.name || item.id, item.gpu, item.region].filter(Boolean).join(" · ")) + '</option>').join("");
  if (!options) {
    return `<p class="panel-note">${t('请在 <a href="#colab" data-action="studio-colab">Colab</a> 页刷新或启动实例。', 'Refresh or start an instance in <a href="#colab" data-action="studio-colab">Colab</a>.')}</p>`;
  }
  return contextRows([[t("账号", "Account"), profile.name]]) +
    '<label class="field">' + t("Colab 实例", "Colab instance") + '<select name="instance_id">'  + options + '</select></label>' +
    (engine === "MiniMax H3" ? `<p class="panel-note">${t("首次约 42.47 GB 权重，已校验的缓存会复用。低显存实例使用小画幅、约 5 秒试跑。", "The first run needs about 42.47 GB of weights. Verified caches are reused. Low-memory instances use a smaller frame size and a roughly 5-second preview.")}</p>`
      : kind === "video" ? `<p class="panel-note">${t("首次在此实例生成需下载约 18.15 GB 模型文件。", "The first run on this instance downloads about 18.15 GB of model files.")}</p>` :
      `<p class="panel-note">${t("首次准备约 7.68 GB 模型文件。Breeze TTS 2 自托管适用于研究和非商用。", "The first run downloads about 7.68 GB of model files. Self-hosted Breeze TTS 2 is licensed for research and non-commercial use.")}</p>`) +
    `<label class="confirmation"><input type="checkbox" name="cloud_confirm"><span>${t("确认在此实例运行，按 Google 实际费率消耗 CU。", "I confirm this instance and accept CU charges at Google’s current rate.")}</span></label>`;
}

function studioFileInput(kind) {
  const isVideo = kind === "video";
  const choices = activeStudioJobs().filter(j => j.artifact && (isVideo ? j.kind !== "tts" : j.kind === "tts"));
  const label = isVideo ? t("视频画面", "Video") : t("配音音轨", "Voiceover audio");
  const picker = desktop?.pickMediaFile ? button(t("选择文件…", "Choose file…"), "studio-pick-file", "compact", kind) : "";
  return '<label class="field">' + label + '<select name="' + kind + '_job"><option value="">' + t("从本机文件选择", "Choose a local file") + '</option>' +
    choices.map(item => '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.title) + '</option>').join("") +
    '</select></label><div class="field studio-file-group" data-file-group="' + kind + '">' +
    '<label for="studio-' + kind + '">' + t("文件路径", "File path") + '</label><div class="path-picker"><input id="studio-' + kind +
    '" name="' + kind + '_path" type="text" maxlength="1024" autocomplete="off" required>' + picker + '</div></div>';
}

function syncStudioDialog(form) {
  const cloud = form.querySelector("[data-cloud-fields]");
  if (cloud) {
    const enabled = form.elements.mode.value === "colab";
    cloud.hidden = !enabled;
    for (const control of cloud.querySelectorAll("input, select")) {
      control.disabled = !enabled;
      control.required = enabled;
    }
  }
  for (const group of form.querySelectorAll("[data-file-group]")) {
    const fromLibrary = !!form.elements[group.dataset.fileGroup + "_job"].value;
    group.hidden = fromLibrary;
    group.querySelector("input").required = !fromLibrary;
  }
}

function openStudio(kind, profile, preferredInstance, engine = kind === "video" ? "MiniMax H3" : "") {
  if (!Object.hasOwn(productionKinds, kind)) return;
  if (kind !== "mux" && !availableInstances(profile, engine).length) {
    if (profile && startInstance(profile, kind === "video" ? (engine === "MiniMax H3" ? "h3" : "wan") : "tts")) return;
    if (kind === "video" && engine === "MiniMax H3") return openDeployment("h3", profile);
    const engine = kind === "tts" ? "Breeze TTS 2" : "Wan2.2";
    setDialog(t("选择 Colab GPU", "Choose a Colab GPU"), `<p>${engine} ${t("需要 L4、G4、A100 或 H100 实例。", "requires an L4, G4, A100, or H100 instance.")}</p>
      <div class="actions"><a class="button primary" href="#colab" data-action="studio-colab">${t("管理 Colab 实例", "Manage Colab instances")}</a></div>`);
    return;
  }
  const jobId = crypto.randomUUID().replaceAll("-", "");
  const title = `<label class="field">${t("作品名称", "Output title")}<input name="title" maxlength="80" required autocomplete="off"></label>`;
  let fields;
  if (kind === "tts") {
    fields = '<input type="hidden" name="mode" value="colab">' +
      `<label class="field">${t("配音文稿", "Voiceover script")}<textarea name="text" maxlength="600" rows="4" required></textarea></label>` +
      `<label class="field">${t("音色和语气（选填）", "Voice and tone (optional)")}<textarea name="instruction" maxlength="400" rows="2"></textarea></label>` +
      '<div data-cloud-fields>' + studioContext(profile, kind, preferredInstance, engine) + '</div>';
  } else if (kind === "video") {
    const seconds = engine === "MiniMax H3" ? [5, 10, 15] : [5, 3];
    fields = '<input type="hidden" name="mode" value="colab">' +
      `<label class="field">${t("画面描述", "Scene description")}<textarea name="text" maxlength="1800" rows="4" required></textarea></label>` +
      `<div class="studio-form-grid"><label class="field">${t("画幅", "Aspect ratio")}<select name="ratio"><option value="landscape">${t("横屏", "Landscape")}</option>
      <option value="portrait">${t("竖屏", "Portrait")}</option></select></label><label class="field">${t("时长", "Duration")}<select name="seconds">
      ${seconds.map(value => `<option value="${value}">${t(`约 ${value} 秒`, `About ${value} seconds`)}</option>`).join("")}</select></label></div>` +
      '<div data-cloud-fields>' + studioContext(profile, kind, preferredInstance, engine) + '</div>';
  } else {
    fields = '<input type="hidden" name="mode" value="mac">' + studioFileInput("video") + studioFileInput("audio") +
      `<p class="panel-note">${t("画面按配音时长循环或裁短，成片保存为 MP4。", "The video loops or trims to the length of the voiceover and saves as MP4.")}</p>`;
  }
  const seed = crypto.getRandomValues(new Uint32Array(1))[0];
  const dialogLabels = {
    tts: [t("新建配音", "New voiceover"), t("开始配音", "Generate voiceover")],
    video: [t(`新建 ${engine} 视频`, `New ${engine} video`), t("开始生成", "Generate video")],
    mux: [t("新建音画合成", "New audio and video mix"), t("开始合成", "Mix audio and video")],
  };
  setDialog(dialogLabels[kind][0], title + fields, dialogLabels[kind][1], async form => {
    const mode = form.get("mode");
    const body = { id: jobId, title: form.get("title"), kind, mode };
    if (kind === "mux") {
      for (const type of ["video", "audio"]) {
        body[type + "_job"] = form.get(type + "_job");
        body[type + "_path"] = form.get(type + "_path");
      }
    } else {
      body.text = form.get("text");
      if (kind === "tts") body.instruction = form.get("instruction");
      if (kind === "video") Object.assign(body, { engine, ratio: form.get("ratio"), seconds: Number(form.get("seconds")), seed });
    }
    if (mode === "colab") Object.assign(body, {
      account_id: profile?.id, instance_id: form.get("instance_id"), confirmed: form.get("cloud_confirm") === "on",
    });
    await api(studioUrl(), body);
    await refreshStudio();
  });
  const form = dialogContent.querySelector("form");
  syncStudioDialog(form);
  form.addEventListener("change", () => syncStudioDialog(form));
}

async function pickStudioFile(kind) {
  const field = document.getElementById("studio-" + kind);
  if (!field || !desktop?.pickMediaFile) return;
  try {
    const path = await desktop.pickMediaFile.postMessage(kind);
    if (path && field.isConnected) field.value = path;
  } catch (error) {
    const message = dialogContent.querySelector(".dialog-problem");
    if (message) {
      message.hidden = false;
      message.textContent = serviceText(error.message, "Could not open the file picker.") || t("无法打开文件选择器。", "Could not open the file picker.");
    }
  }
}

function previewStudio(id) {
  const job = activeStudioJobs().find(row => row.id === id && row.artifact);
  if (!job) return;
  const url = studioUrl(id, "/media");
  const player = job.kind === "tts" ? '<audio controls preload="metadata" src="' + url + '"></audio>'
    : '<video controls playsinline preload="metadata" src="' + url + '"></video>';
  const save = desktop?.revealProductionFile
    ? button(t("打开文件夹", "Open folder"), "studio-reveal", "compact", id)
    : '<a class="button compact" href="' + url + '" download="' + escapeHtml(job.artifact.name) + '">' + t("下载作品", "Download output") + '</a>';
  setDialog(job.title, '<div class="studio-player">' + player + '</div><p class="panel-note">' +
    escapeHtml(formatMoney(job.artifact.seconds) + t(" 秒 · SHA-256 已记录", " s · SHA-256 recorded")) + '</p><div class="actions">' + save + '</div>');
}

async function studioAction(action, id, profile) {
  try {
    if (action === "studio-launch") openStudio(id, profile);
    else if (action === "studio-wan") openStudio("video", profile, undefined, "Wan2.2");
    else if (action === "studio-preview") previewStudio(id);
    else if (action === "studio-pick-file") await pickStudioFile(id);
    else if (action === "studio-archive") await openStudioArchive(id, profile);
    else if (action === "studio-reveal") {
      const job = activeStudioJobs().find(row => row.id === id && row.artifact);
      if (job) await desktop?.revealProductionFile?.postMessage({ id, name: job.artifact.name });
    } else if (action === "studio-check") {
      await api(studioUrl(id, "/refresh"), {});
      await refreshStudio();
    } else if (action === "studio-colab") dialog.close();
  } catch (error) { announce(serviceText(error.message), true); }
}

setInterval(() => {
  if (!document.hidden) refreshStudio();
}, 2200);
