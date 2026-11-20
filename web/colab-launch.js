"use strict";

const launchPurposes = {
  video: { gpu: "L4", title: ["做短视频", "Make a short video"], detail: ["Wan2.2 · 3 秒或 5 秒镜头", "Wan2.2 · 3- or 5-second clips"] },
  tts: { gpu: "L4", title: ["云端配音", "Generate a voiceover"], detail: ["Breeze TTS 2 · 生成 WAV", "Breeze TTS 2 · WAV audio"] },
  notebook: { gpu: "CPU", title: ["笔记本与数据处理", "Run a notebook"], detail: ["运行 Python、整理表格和数据", "Run Python and prepare data"] },
  custom: { title: ["自选算力", "Choose hardware"], detail: ["按显存与用途选择机型", "Compare GPU memory and uses"] },
};

const launchHardware = {
  CPU: [["无 GPU", "No GPU"], ["适合笔记本、Python 脚本和数据处理。", "For notebooks, Python scripts and data preparation."]],
  T4: [["16 GB · 入门 GPU", "16 GB · Entry-level GPU"], ["适合轻量 GPU 实验。本 App 的视频与配音需要 L4、A100、H100 或 G4。", "For light GPU experiments. Studio video and voiceover require L4, A100, H100 or G4."]],
  L4: [["24 GB · 日常创作", "24 GB · Everyday creation"], ["适合本 App 的 Wan2.2 短视频和 Breeze TTS 2 配音，两种任务可用同一台实例。", "For Wan2.2 short clips and Breeze TTS 2 voiceovers. One instance can run either task."]],
  A100: [["40 或 80 GB · 大模型", "40 or 80 GB · Large models"], ["适合更大的模型或训练，也可运行本 App 的视频与配音。显存以实配为准。", "For larger models or training, and also suitable for Studio. GPU memory depends on the assignment."]],
  H100: [["80 GB · 高吞吐", "80 GB · High throughput"], ["适合高吞吐的大模型推理或训练。也可运行本 App 的视频与配音。", "For high-throughput model inference or training. Also suitable for Studio video and voiceover."]],
  G4: [["96 GB 整卡 · 大显存", "96 GB full GPU · High memory"], ["G4 使用 RTX PRO 6000，适合更吃显存的视频和大模型。显存以实配为准。", "G4 uses RTX PRO 6000, for memory-intensive video and large models. Check the assigned GPU memory."]],
};

function suggestedInstanceName(profile, purpose, gpu) {
  const base = purpose === "custom" ? t(`${gpu} 实例`, `${gpu} instance`) : `${t(...launchPurposes[purpose].title)} · ${gpu}`;
  const taken = new Set((profile.observation?.instances || []).map(instance => (instance.name || instance.id).toLowerCase()));
  let name = base;
  for (let number = 2; taken.has(name.toLowerCase()); number++) name = `${base} ${number}`;
  return name;
}

function startInstance(profile, studioKind = null) {
  const choices = allowedChoices(profile.observation || {}).filter(gpu => Object.hasOwn(launchHardware, gpu));
  if (profile.auth_status !== "connected" || !choices.length) return false;
  const requested = studioKind || "video";
  const initial = choices.includes(launchPurposes[requested].gpu) ? requested : "custom";
  const purposes = Object.entries(launchPurposes).map(([key, purpose]) => {
    const disabled = purpose.gpu && !choices.includes(purpose.gpu);
    const detail = disabled ? t(`${purpose.gpu} 当前不可选`, `${purpose.gpu} is unavailable for this account`) : t(...purpose.detail);
    return `<label class="launch-choice">
      <input type="radio" name="purpose" value="${key}"${key === initial ? " checked" : ""}${disabled ? " disabled" : ""}>
      <span class="launch-choice-copy"><strong>${t(...purpose.title)}</strong><small>${detail}</small></span>
      ${purpose.gpu ? `<span class="launch-chip">${purpose.gpu}</span>` : ""}
    </label>`;
  }).join("");
  const options = choices.map(gpu => `<option value="${gpu}">${gpu} · ${t(...launchHardware[gpu][0])}</option>`).join("");
  const nextJob = (purpose, gpu) => {
    const kind = purpose === "custom" ? studioKind : purpose;
    return creationGPUs.has(gpu) && (kind === "video" || kind === "tts") ? kind : null;
  };

  setDialog(t("启动 Colab 实例", "Start a Colab instance"), `${contextRows([[t("账号", "Account"), profile.name]])}
    <fieldset class="launch-purpose"><legend>${t("这台实例用来做什么？", "What will you use it for?")}</legend>
      <div class="launch-choices">${purposes}</div>
    </fieldset>
    <label class="field" data-custom-gpu hidden>${t("算力型号", "Hardware model")}
      <select name="gpu" aria-describedby="launch-gpu-note" required disabled><option value="">${t("选择型号", "Select a model")}</option>${options}</select>
    </label>
    <div class="launch-notes" aria-live="polite">
      <p id="launch-gpu-note" class="panel-note" hidden></p>
      <p id="launch-next" class="panel-note" hidden></p>
    </div>
    <label class="field">${t("实例名称（选填）", "Instance name (optional)")}
      <input name="name" maxlength="48" autocomplete="off">
    </label>
    <p class="panel-note">${t("实例运行期间可能消耗算力点（CU）。实际型号与费率由 Colab 决定，启动后可查看。", "Running instances can consume compute units (CU). Check the assigned hardware and actual rate on the Colab page after launch.")}</p>
    <p class="launch-progress" role="status">${t("正在向 Colab 申请实例…", "Requesting an instance from Colab…")}</p>`,
  t("启动实例", "Start instance"), async fields => {
    const purpose = fields.get("purpose");
    const gpu = launchPurposes[purpose]?.gpu || fields.get("gpu");
    if (!choices.includes(gpu)) throw new Error(t("请选择可申请的算力型号。", "Choose a hardware model for this account."));
    const name = fields.get("name").trim() || suggestedInstanceName(profile, purpose, gpu);
    const { operation, message } = await change(accountPath(profile, "/instances"), { gpu, name, confirmed: true });
    const kind = nextJob(purpose, gpu);
    if (kind && operation.status === "confirmed") {
      const updated = data.accounts.find(item => item.id === profile.id);
      if (availableInstances(updated).some(item => item.id === operation.instance_id)) {
        location.hash = "studio";
        openStudio(kind, updated, operation.instance_id);
        return false;
      }
      announce(t("实例已启动。请在 Colab 页核对实际分配的型号，创作需要 L4、A100、H100 或 G4。", "The instance started. Check the assigned hardware in Colab. Studio requires L4, A100, H100 or G4."), true);
      return;
    }
    announce(serviceText(message, "Launch request completed. Check the instance status."));
  });

  const form = dialogContent.querySelector("form");
  const { purpose, gpu, name } = form.elements;
  const submit = form.querySelector("button[type=submit]");
  const gpuNote = form.querySelector("#launch-gpu-note");
  const nextNote = form.querySelector("#launch-next");
  function describeSelection() {
    const custom = purpose.value === "custom";
    const model = launchPurposes[purpose.value].gpu || gpu.value;
    const kind = nextJob(purpose.value, model);
    gpu.disabled = !custom;
    gpu.closest("label").hidden = !custom;
    gpuNote.hidden = !model;
    gpuNote.textContent = model ? `${model} · ${t(...launchHardware[model][0])}${t("。", ". ")}${t(...launchHardware[model][1])}` : "";
    nextNote.hidden = !kind;
    nextNote.textContent = kind === "video" ? t("分配成功后打开视频表单。", "Opens the video form once the instance is ready.")
      : t("分配成功后打开配音表单。", "Opens the voiceover form once the instance is ready.");
    name.placeholder = model ? suggestedInstanceName(profile, purpose.value, model) : "";
    submit.textContent = model ? t(`启动 ${model}`, `Start ${model}`) : t("启动实例", "Start instance");
    submit.disabled = !model;
  }
  form.addEventListener("change", describeSelection);
  // Enter in the optional name field should never start a metered instance.
  name.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.isComposing) event.preventDefault();
  });
  describeSelection();
  form.querySelector("[name=purpose]:checked")?.focus();
  return true;
}
