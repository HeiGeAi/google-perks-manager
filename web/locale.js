"use strict";

const desktop = window.webkit?.messageHandlers;
const languageChoices = ["system", "zh-CN", "en"];
const languagePreferenceKey = "google-perks-language";
const projectRepository = "HeiGeAi/google-perks-manager";

function systemLanguage() {
  const preferred = navigator.languages?.[0] || navigator.language || "en";
  return /^zh(?:[-_]|$)/i.test(preferred) ? "zh-CN" : "en";
}

function savedLanguage() {
  try {
    const value = localStorage.getItem(languagePreferenceKey);
    if (languageChoices.includes(value)) return value;
  } catch {
    // Private browsing and some browser policies disable storage.
  }
  return "system";
}

function languageFor(choice) {
  return choice === "system" ? systemLanguage() : choice;
}

let appInfo = {
  language: savedLanguage(),
  effectiveLanguage: languageFor(savedLanguage()),
  version: "",
  agentCommand: "",
  repository: projectRepository,
  hasContactQr: false,
};

function t(zh, en) {
  return appInfo.effectiveLanguage === "en" ? en : zh;
}

function appName() {
  return t("谷歌羊毛管理器", "Google Perks Manager");
}

const pageLabels = {
  colab: ["Colab", "Colab"],
  studio: ["创作", "Studio"],
  benefits: ["权益", "Perks"],
  accounts: ["账号管理", "Accounts"],
  agent: ["一键接入 Agent", "Connect an Agent"],
  settings: ["设置", "Settings"],
};

function pageLabel(key) {
  return t(...pageLabels[key]);
}

function localizeFrame() {
  document.documentElement.lang = appInfo.effectiveLanguage;
  document.title = appName();
  document.querySelector("#brand-name").textContent = appName();
  document.querySelector(".brand").setAttribute("aria-label", t("谷歌羊毛管理器首页", "Google Perks Manager home"));
  document.querySelector(".sidebar").setAttribute("aria-label", t("应用导航", "App navigation"));
  document.querySelector(".nav").setAttribute("aria-label", t("工作区", "Workspace"));
  document.querySelector(".skip-link").textContent = t("跳到主内容", "Skip to main content");
  for (const link of document.querySelectorAll("[data-page]")) {
    link.querySelector("span").textContent = pageLabel(link.dataset.page);
  }
}

function useAppInfo(info) {
  if (!info || !languageChoices.includes(info.language) || !["zh-CN", "en"].includes(info.effectiveLanguage)) {
    throw new Error(t("无法读取应用语言设置。", "Could not read the app language settings."));
  }
  appInfo = info;
  localizeFrame();
}

async function initializeAppInfo() {
  if (desktop?.getAppInfo) {
    try {
      useAppInfo(await desktop.getAppInfo.postMessage(null));
      return;
    } catch {
      // Use the browser language and the loopback service's public app metadata.
    }
  }
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (response.ok) {
      const health = await response.json();
      appInfo.version = typeof health.version === "string" ? health.version : "";
      appInfo.hasContactQr = health.has_contact_qr === true;
    }
  } catch {
    // The main workspace reports connection errors.
  }
  localizeFrame();
}

async function chooseLanguage(value) {
  if (!languageChoices.includes(value)) return;
  if (desktop?.setLanguage) {
    useAppInfo(await desktop.setLanguage.postMessage(value));
  } else {
    try {
      localStorage.setItem(languagePreferenceKey, value);
    } catch {
      // The language still changes for this browser session.
    }
    useAppInfo({ ...appInfo, language: value, effectiveLanguage: languageFor(value) });
  }
}

window.addEventListener("languagechange", () => {
  if (appInfo.language === "system" && !desktop?.getAppInfo) {
    useAppInfo({ ...appInfo, effectiveLanguage: systemLanguage() });
    announce("");
    render();
  }
});

localizeFrame();
