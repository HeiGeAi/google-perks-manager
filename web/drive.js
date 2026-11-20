"use strict";

const driveDesktopHelp = "https://support.google.com/drive/answer/10838124";

function driveAccountPath(id) {
  return "/api/drive/accounts/" + encodeURIComponent(id);
}

async function openStudioArchive(id, current) {
  const job = activeStudioJobs().find(row => row.id === id && row.state === "done" && row.artifact);
  if (!job || !desktop?.pickDriveFolder) return;
  const profile = current || data.accounts[0];
  if (!profile) {
    setDialog(t("转存云盘", "Save to Drive"),
      '<p>' + t("添加 Google 账号后，即可为它选择同步目录。", "Add a Google account to choose its sync folder.") + '</p>' +
      '<div class="actions">' + button(t("添加账号", "Add account"), "add-account", "primary") + '</div>');
    return;
  }
  await loadDriveDialog(job, profile);
}

function driveDialogError(form, error) {
  if (!form.isConnected || !dialog.open) return;
  const problem = form.querySelector(".dialog-problem");
  problem.hidden = false;
  problem.textContent = error.message;
}

async function loadDriveDialog(job, profile) {
  setDialog(t("转存云盘", "Save to Drive"),
    '<p class="panel-note" role="status">' + t("正在读取同步目录…", "Reading sync folders…") + '</p>');
  const loading = dialogContent.querySelector("form");
  try {
    const config = await api(driveAccountPath(profile.id));
    if (loading.isConnected && dialog.open) renderDriveDialog(job, profile, config);
  } catch (error) {
    if (loading.isConnected && dialog.open) {
      loading.querySelector('[role="status"]').remove();
      driveDialogError(loading, error);
    }
  }
}

function renderDriveDialog(job, profile, config) {
  const accountField = '<label class="field">' + t("目标 Google 账号", "Destination Google account") + '<select name="drive_account">' +
    data.accounts.map(row => '<option value="' + escapeHtml(row.id) + '"' + (row.id === profile.id ? ' selected' : '') + '>' +
      escapeHtml(row.name) + '</option>').join("") + '</select></label>';
  const ready = config.ready;
  const project = config.projects.includes(config.last_project) ? config.last_project : config.projects[0] || "";
  const projectField = '<label class="field">' + t("项目文件夹", "Project folder") + '<select name="drive_project">' +
    config.projects.map(name => '<option value="' + escapeHtml(name) + '"' + (name === project ? ' selected' : '') + '>' +
      escapeHtml(name) + '</option>').join("") +
    '<option value="">' + t("新建项目…", "New project…") + '</option></select></label>' +
    '<label class="field" data-new-project>' + t("项目名称", "Project name") +
    '<input name="drive_new_project" maxlength="60" autocomplete="off" placeholder="' + t("例如，课程素材", "Example: Course assets") + '"></label>';
  const pickerLabel = config.configured ? t("更换同步目录", "Change sync folder") : t("选择同步目录", "Choose sync folder");
  const details = ready
    ? t("文件将复制到同步目录，由 Google Drive 桌面版继续同步。", "The file is copied to the sync folder. Google Drive for desktop handles the upload.")
    : config.configured
      ? t("同步目录暂不可用。打开 Google Drive 桌面版，或重新选择目录。", "The sync folder is unavailable. Open Google Drive for desktop or choose the folder again.")
      : t("在 Google Drive 桌面版登录上方账号，然后选择该账号的“我的云端硬盘”或同步文件夹。", "Sign in to the account above in Google Drive for desktop, then choose its My Drive or sync folder.");
  const tools = '<div class="drive-folder-tools"><button type="button" class="button compact" data-drive-pick>' +
    pickerLabel + '</button>' + (ready ? "" : '<a class="text-link" target="_blank" rel="noopener noreferrer" href="' +
      driveDesktopHelp + '">' + t("安装与使用 Google Drive 桌面版 ↗", "Set up Google Drive for desktop ↗") + '</a>') + '</div>';

  setDialog(t("转存云盘", "Save to Drive"),
    contextRows([[t("作品", "Output"), job.title]]) + accountField + (ready ? projectField : "") +
    '<p class="panel-note">' + details + '</p>' + tools,
    ready ? t("一键转存", "Save to Drive") : null, async fields => {
      const result = await api(studioUrl(job.id, "/archive"), {
        account_id: profile.id,
        project: fields.get("drive_project") || fields.get("drive_new_project"),
      });
      announce(t("已复制到云盘同步目录。", "Copied to the Drive sync folder.") + " " +
        profile.name + " · " + result.archive.project);
      await refreshStudio();
    });

  const form = dialogContent.querySelector("form");
  const accountSelect = form.elements.drive_account;
  accountSelect.addEventListener("change", () => {
    const next = data.accounts.find(row => row.id === accountSelect.value);
    if (next && next.id !== profile.id) loadDriveDialog(job, next);
  });
  if (ready) {
    const projectSelect = form.elements.drive_project;
    const newProject = form.querySelector("[data-new-project]");
    const input = form.elements.drive_new_project;
    function syncProject() {
      const creating = !projectSelect.value;
      newProject.hidden = !creating;
      input.disabled = !creating;
      input.required = creating;
    }
    projectSelect.addEventListener("change", syncProject);
    syncProject();
  }
  const pick = form.querySelector("[data-drive-pick]");
  pick.addEventListener("click", async () => {
    const submit = form.querySelector('[type="submit"]');
    const controls = [pick, accountSelect, submit].filter(Boolean);
    controls.forEach(control => { control.disabled = true; });
    form.querySelector(".dialog-problem").hidden = true;
    try {
      const folder = await desktop.pickDriveFolder.postMessage(null);
      if (!folder || !form.isConnected || !dialog.open) return;
      const result = await api(driveAccountPath(profile.id), { folder });
      if (form.isConnected && dialog.open) renderDriveDialog(job, profile, result.drive);
    } catch (error) {
      driveDialogError(form, error);
    } finally {
      controls.forEach(control => { control.disabled = false; });
    }
  });
}
