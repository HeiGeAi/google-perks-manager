"use strict";

const launchPurposes = {
  h3: { title: ["MiniMax H3 视频", "MiniMax H3 video"], detail: ["部署 ComfyUI 和 H3 视频模型", "Deploy ComfyUI and the H3 video model"] },
  tts: { gpu: "L4", title: ["云端配音", "Generate a voiceover"], detail: ["Breeze TTS 2 · 生成 WAV", "Breeze TTS 2 · WAV audio"] },
  notebook: { gpu: "CPU", title: ["笔记本与数据处理", "Run a notebook"], detail: ["运行 Python、整理表格和数据", "Run Python and prepare data"] },
  custom: { title: ["自选算力", "Choose hardware"], detail: ["CPU、GPU、TPU 和可选系统内存", "CPU, GPU, TPU, and available RAM profiles"] },
};

const launchHardware = {
  CPU: [["无 GPU", "No GPU"], ["适合笔记本、数据处理和小型 Qwen3。", "For notebooks, data preparation and small Qwen3 workloads."]],
  T4: [["16 GB 显存", "16 GB VRAM"], ["H3 低显存试跑需足够系统内存，适合小画幅约 5 秒。", "Experimental H3 preview at a smaller frame size, about 5 seconds, with sufficient system RAM."]],
  L4: [["24 GB 显存", "24 GB VRAM"], ["可做 H3 低显存试跑，适合日常配音和 Wan2.2。", "Experimental H3 preview, and suitable for everyday voiceover and Wan2.2."]],
  A100: [["40 或 80 GB 显存", "40 or 80 GB VRAM"], ["适合 H3。Google 决定分配 40 GB 或 80 GB，两档以实例实测为准。", "Suitable for H3. Google assigns the 40 GB or 80 GB variant. Check measured hardware after launch."]],
  H100: [["80 GB 显存", "80 GB VRAM"], ["H3 高显存选项，可用于生成时保留更多权重。", "High-memory H3 option with room for more model weights."]],
  G4: [["96 GB 显存（整卡）", "96 GB VRAM (full GPU)"], ["RTX PRO 6000 Blackwell，H3 生产首选，历史工作流已在 G4 出片。", "RTX PRO 6000 Blackwell. First choice for H3 production, with a historically verified workflow."]],
  V5E1: [["TPU v5e（单核）", "TPU v5e (one core)"], ["适合兼容 TPU 的 JAX 或 TensorFlow 任务。H3 使用 NVIDIA GPU。", "For TPU-compatible JAX or TensorFlow jobs. H3 uses an NVIDIA GPU."]],
  V6E1: [["TPU v6e（单核）", "TPU v6e (one core)"], ["适合兼容 TPU 的 JAX 或 TensorFlow 任务。H3 使用 NVIDIA GPU。", "For TPU-compatible JAX or TensorFlow jobs. H3 uses an NVIDIA GPU."]],
};

function suggestedInstanceName(profile, purpose, gpu, catalog) {
  const base = purpose === "custom" ? t(`${gpu} 实例`, `${gpu} instance`) : `${t(...catalog[purpose].title)} · ${gpu}`;
  const taken = new Set((profile.observation?.instances || []).map(instance => (instance.name || instance.id).toLowerCase()));
  let name = base;
  for (let number = 2; taken.has(name.toLowerCase()); number++) name = `${base} ${number}`;
  return name;
}

function startInstance(profile, requested = "h3") {
  const choices = allowedChoices(profile.observation || {}).filter(gpu => Object.hasOwn(launchHardware, gpu));
  if (profile.auth_status !== "connected" || !choices.length) return false;

  const catalog = { ...launchPurposes };
  if (!Object.hasOwn(catalog, requested) && data.projects[requested]) {
    const name = data.projects[requested].name;
    catalog[requested] = { title: [name, name], detail: ["启动后部署所选项目", "Deploy after launching the instance"] };
  }
  const forPurpose = key => {
    const project = data.projects[key];
    if (project) return project.gpus.filter(gpu => choices.includes(gpu));
    const model = catalog[key].gpu;
    return model ? choices.filter(gpu => gpu === model) : choices;
  };
  const enabled = key => (key !== "h3" || Boolean(data.projects.h3)) && forPurpose(key).length > 0;
  const initial = enabled(requested) ? requested : "custom";
  const purposes = Object.entries(catalog).map(([key, purpose]) => {
    const canSelect = enabled(key);
    return `<label class="launch-choice">
      <input type="radio" name="purpose" value="${key}"${key === initial ? " checked" : ""}${canSelect ? "" : " disabled"}>
      <span class="launch-choice-copy"><strong>${t(...purpose.title)}</strong>
        <small>${canSelect ? t(...purpose.detail) : t("当前账号暂无匹配型号", "No matching hardware for this account")}</small></span>
      ${purpose.gpu ? `<span class="launch-chip">${purpose.gpu}</span>` : ""}
    </label>`;
  }).join("");

  setDialog(t("启动 Colab 实例", "Start a Colab instance"), `${contextRows([[t("账号", "Account"), profile.name]])}
    <fieldset class="launch-purpose"><legend>${t("选择用途", "Choose a workload")}</legend>
      <div class="launch-choices">${purposes}</div>
    </fieldset>
    <div class="launch-choices launch-settings">
      <label class="field">${t("算力型号", "Hardware model")}<select name="gpu" aria-describedby="launch-gpu-note" required></select></label>
      <label class="field">${t("系统内存（RAM）", "System memory (RAM)")}<select name="memory"></select></label>
    </div>
    <div class="launch-notes" aria-live="polite">
      <p id="launch-gpu-note" class="panel-note"></p><p id="launch-next" class="panel-note" hidden></p>
    </div>
    ${legacyNodesField(true)}
    <label class="field">${t("实例名称（选填）", "Instance name (optional)")}<input name="name" maxlength="48" autocomplete="off"></label>
    <p class="panel-note">${t("实际分配及费率由 Colab 决定。系统内存选项不改变显卡显存。", "Colab determines the actual hardware and rate. The system RAM option does not change GPU memory.")}</p>
    <p class="launch-progress" role="status">${t("正在向 Colab 申请实例…", "Requesting an instance from Colab…")}</p>`,
  t("启动实例", "Start instance"), async fields => {
    const purpose = fields.get("purpose");
    const gpu = fields.get("gpu");
    if (!forPurpose(purpose).includes(gpu)) throw new Error(t("请选择可申请的算力型号。", "Choose a hardware model for this account."));
    const high = fields.get("memory") === "high";
    const name = fields.get("name").trim() || suggestedInstanceName(profile, purpose, gpu, catalog);
    const { operation, message } = await change(accountPath(profile, "/instances"), { gpu, high_mem: high, name, confirmed: true });
    const current = () => data.accounts.find(item => item.id === profile.id);
    const allocated = () => current()?.observation?.instances?.some(row => row.id === operation.instance_id);
    if (operation.status === "pending" && (data.projects[purpose] || purpose === "tts")) {
      for (let attempt = 0; attempt < 3 && !allocated(); attempt++) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        try { await change(accountPath(profile, "/refresh"), {}); }
        catch { break; }
      }
    }
    if (allocated()) {
      const updated = current();
      if (data.projects[purpose]) {
        if (!projectInstances(updated, purpose).some(row => row.id === operation.instance_id)) {
          announce(t("实例已启动。实际机房或型号与项目不匹配，请在 Colab 页核对。", "The instance started. Check its assigned region and hardware in Colab before deploying."), true);
          return;
        }
        try {
          await queueDeployment(updated, operation.instance_id, purpose, fields.get("legacy_nodes") === "on");
          location.hash = "apps";
        } catch (error) {
          // A failure after allocation must not invite a second paid launch.
          announce(t("实例已启动，部署提交失败。", "The instance started, but deployment could not be submitted.") + " " + error.message, true);
        }
        return;
      }
      if (purpose === "tts" && availableInstances(updated).some(row => row.id === operation.instance_id)) {
        location.hash = "studio";
        openStudio("tts", updated, operation.instance_id);
        return false;
      }
    }
    announce(serviceText(message, "Launch request completed. Check the instance status."));
  });

  const form = dialogContent.querySelector("form");
  const { purpose, gpu, memory, name } = form.elements;
  const legacy = form.querySelector("[data-legacy-nodes]");
  const submit = form.querySelector("button[type=submit]");
  const gpuNote = form.querySelector("#launch-gpu-note");
  const nextNote = form.querySelector("#launch-next");
  let gpuChoices;
  function updateChoices() {
    const key = purpose.value;
    gpuChoices = forPurpose(key);
    const project = data.projects[key];
    const recommended = project?.recommended || catalog[key].gpu;
    const pick = key === "custom" ? "" : (gpuChoices.includes(recommended) ? recommended : gpuChoices[0]);
    gpu.replaceChildren(new Option(t("选择型号", "Select a model"), ""));
    for (const model of gpuChoices) gpu.append(new Option(`${model} · ${t(...launchHardware[model][0])}`, model));
    gpu.value = pick || "";
    legacy.hidden = key !== "h3";
    legacy.querySelector("input").disabled = legacy.hidden;
    describeSelection();
  }
  function describeSelection() {
    const key = purpose.value;
    const model = gpu.value;
    const project = data.projects[key];
    const high = data.highRam.includes(model);
    const prior = memory.value;
    memory.replaceChildren(new Option(high ? t("标准", "Standard") : t("由 Colab 分配", "Assigned by Colab"), "standard"));
    if (high) {
      memory.append(new Option(t("高内存", "High RAM"), "high"));
      memory.value = key === "h3" ? "high" : (prior || "standard");
    }
    memory.disabled = !high;
    gpuNote.hidden = !model;
    gpuNote.textContent = model ? `${model} · ${t(...launchHardware[model][0])}${t("。", ". ")}${t(...launchHardware[model][1])}` : "";
    nextNote.hidden = !project && key !== "tts";
    nextNote.textContent = key === "h3"
      ? t("分配后核对机房再部署。首次需约 42.47 GB 权重，保留原 G4 工作流。", "Checks the assigned region before deployment. The original G4 workflow uses about 42.47 GB of weights.")
      : project ? t("分配成功后直接部署。", "Deploys as soon as the instance is ready.") : t("分配成功后打开配音表单。", "Opens the voiceover form when ready.");
    name.placeholder = model ? suggestedInstanceName(profile, key, model, catalog) : "";
    submit.textContent = model ? (project ? t(`启动 ${model} 并部署`, `Start ${model} & deploy`) : t(`启动 ${model}`, `Start ${model}`))
      : t("启动实例", "Start instance");
    submit.disabled = !model;
  }
  form.addEventListener("change", event => {
    if (event.target.name === "purpose") updateChoices();
    else if (event.target === gpu) describeSelection();
  });
  name.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.isComposing) event.preventDefault();
  });
  updateChoices();
  form.querySelector("[name=purpose]:checked")?.focus();
  return true;
}
