"use strict";

const benefitPlans = { pro: "Pro", "ultra-5x": "Ultra 5x", "ultra-20x": "Ultra 20x" };
const planMaterials = {
  pro: "plan-material gold",
  "ultra-5x": "plan-material platinum",
  "ultra-20x": "plan-material diamond",
};
const benefitFilters = { all: null, ...benefitPlans };
const benefitViews = new Map();
const officialPlanComparison = "https://one.google.com/intl/en/about/google-ai-plans/?gl=US&hl=en";

let benefitEnglish = {};

async function loadBenefitTranslations() {
  try {
    const response = await fetch("/benefits.en.json", { cache: "no-store" });
    if (response.ok) benefitEnglish = await response.json();
  } catch {
    // Keep the official catalog accessible while the translation is unavailable.
  }
}

function localizedBenefit(benefit) {
  const translation = appInfo.effectiveLanguage === "en" && benefitEnglish[benefit.id];
  if (!translation) return benefit;
  const { plan_values: values, ...fields } = translation;
  const localized = { ...benefit, ...fields };
  if (values) localized.plan_values = { ...benefit.plan_values, ...values };
  return localized;
}

function planLabel(plan, label = benefitPlans[plan]) {
  const material = planMaterials[plan];
  const text = escapeHtml(label);
  return material ? `<span class="plan-badge ${material}">${text}</span>` : text;
}

function planBadge(benefit) {
  if (benefit.independent) return `<span class="plan-badge">${t("独立资格", "Separate eligibility")}</span>`;
  const plans = Object.keys(benefitPlans).filter(key => benefit.plan_values?.[key]);
  if (!plans.length) return `<span class="plan-badge">${t("资格待核对", "Check eligibility")}</span>`;
  if (plans.length === 3) return `<span class="plan-badge">${t("Pro 与 Ultra", "Pro and Ultra")}</span>`;
  if (plans.length === 1) {
    const plan = plans[0];
    const label = benefitPlans[plan];
    return planLabel(plan, plan === "pro" ? label : t(`${label} 专享`, `${label} only`));
  }
  if (!plans.includes("pro")) {
    return `<span class="plan-badge exclusive">${t("Ultra 专享", "Ultra only")}</span>`;
  }
  return '<span class="plan-badge">' + plans.map(key => benefitPlans[key]).join(" · ") + '</span>';
}

function planValue(value, unit) {
  if (!value) return `<span class="quota-unavailable">${t("本档无此项", "Not included")}</span>`;
  return '<strong>' + escapeHtml(value) + '</strong>' + (unit ? '<wbr><small>' + escapeHtml(unit) + '</small>' : '');
}

function renderPlanComparison(catalog, filter) {
  const compared = catalog.filter(item => item.comparison && item.plan_values);
  if (!compared.length) return "";
  const lastChecked = compared.map(item => item.checked_on).filter(Boolean).sort()[0];
  const helpLanguage = t("zh-Hans", "en");
  const headers = Object.keys(benefitPlans).map(key =>
    '<th scope="col"' + (key === filter ? ' class="current-plan"' : '') + '>' + planLabel(key) + '</th>'
  ).join("");
  const rows = compared.map(item => '<tr><th scope="row">' + escapeHtml(item.comparison) + '</th>' +
    Object.keys(benefitPlans).map(key => '<td' + (key === filter ? ' class="current-plan"' : '') + '>' +
      planValue(item.plan_values[key], item.plan_unit) + '</td>').join("") + '</tr>').join("");
  return `<section class="panel plan-reference" aria-labelledby="plan-reference-title">
    <header class="panel-header">
      <h2 id="plan-reference-title">${t("会员权益与额度", "Plan benefits and allowances")}</h2>
      ${externalLink(t("Google 官方套餐对照", "Compare official Google plans"), officialPlanComparison, "text compact")}
    </header>
    <div class="plan-student-note">
      <p id="student-pro-note"><strong>${t("学生优惠 Pro", "Google AI Pro student offers")}</strong>
        ${t(
          "表中 Pro 按标准付费套餐展示。免费试用不含 YouTube Premium Lite，Colab 算力也有单独限制。部分地区另有 Pro 与 YouTube Premium 学生组合。10 TB 存储加购仅适用于符合条件的常规 Pro 方案。",
          "The Pro column shows the standard paid plan. Free trials exclude YouTube Premium Lite and have separate Colab limits. Some regions offer a separate Pro and YouTube Premium student bundle. The 10 TB storage add-on is available to eligible standard Pro plans."
        )}
      </p>
      <div class="actions">
        ${externalLink(t("学生优惠说明", "Student offer details"), `https://support.google.com/googleone/answer/17422238?hl=${helpLanguage}`)}
        ${externalLink(t("试用权益限制", "Trial benefit restrictions"), `https://support.google.com/googleone/answer/16105039?hl=${helpLanguage}`)}
      </div>
    </div>
    <table class="plan-comparison" aria-labelledby="plan-reference-title" aria-describedby="student-pro-note">
      <thead><tr><th scope="col">${t("权益项目", "Perk")}</th>${headers}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="plan-reference-note">${t("地区、订阅状态和实际额度以 Google 账号显示为准。", "Check your Google account for regional availability, subscription eligibility and actual allowances.")}${lastChecked ? ` ${t("核对日期", "Checked")} <time datetime="${escapeHtml(lastChecked)}">${escapeHtml(lastChecked)}</time>` : ""}</p>
  </section>`;
}
function renderBenefitValues(benefit, filter) {
  const values = benefit.plan_values;
  if (!values) return "";
  const groups = new Map();
  for (const key of Object.keys(benefitPlans)) {
    const amount = values[key];
    if (!amount || (filter !== "all" && key !== filter)) continue;
    const plans = groups.get(amount) || [];
    groups.set(amount, [...plans, key]);
  }
  if (!groups.size) return "";
  const rows = [...groups].map(([amount, plans]) => {
    let label = plans.map(key => benefitPlans[key]).join(" · ");
    if (plans.length === 1) label = planLabel(plans[0]);
    else if (plans.length === 3) label = t("Pro 与 Ultra", "Pro and Ultra");
    else if (!plans.includes("pro")) label = "Ultra";
    return '<div><dt>' + label + '</dt><dd>' + planValue(amount, benefit.plan_unit) + '</dd></div>';
  });
  return `<dl class="benefit-values" aria-label="${t("按档位查看权益", "Perk allowance by plan")}">${rows.join("")}</dl>`;
}

function renderBenefitCard(benefit, profile, filter) {
  const record = profile?.benefit_status?.[benefit.id];
  const checked = formatTime(record?.checked_at);
  const claim = safeUrl(benefit.claim_url);
  const verify = safeUrl(benefit.verification_url);
  const source = safeUrl(benefit.source_url);
  const link = externalLink(benefit.action_label || t("去领取", "Redeem"), claim, "compact");
  const verification = verify !== claim ? externalLink(t("查看领取结果", "Check status"), verify, "text compact") : "";
  const sourceLink = source !== claim && source !== verify ? externalLink(t("官方说明", "Official help"), source, "text compact") : "";
  const recordAction = profile ? button(t("记录核验结果", "Record verified status"), "record-benefit", "text compact", benefit.id, busy) : "";
  const steps = Array.isArray(benefit.steps) && benefit.steps.length
    ? `<details class="steps"><summary>${t("领取与核验步骤", "How to redeem and verify")}</summary><ol>` +
      benefit.steps.map(step => '<li>' + escapeHtml(step) + '</li>').join("") + '</ol></details>'
    : "";
  return `<article class="panel benefit" aria-labelledby="benefit-${escapeHtml(benefit.id)}">
    <div class="benefit-top">
      <h3 id="benefit-${escapeHtml(benefit.id)}">${escapeHtml(benefit.title)}</h3>
      ${planBadge(benefit)}
    </div>
    ${benefit.summary ? `<p>${escapeHtml(benefit.summary)}</p>` : ""}
    ${renderBenefitValues(benefit, filter)}
    ${benefit.eligibility ? `<p class="panel-note">${escapeHtml(benefit.eligibility)}</p>` : ""}${steps}
    <div class="actions">${link}${verification}${sourceLink}${recordAction}</div>
    ${profile ? `<div class="benefit-record">
      ${status(t(...(benefitLabels[record?.status] || benefitLabels.unverified)), record?.status === "claimed" ? "good" : "")}
      ${checked ? `<span class="secondary">${t("本人记录", "Local record")} · ${escapeHtml(checked)}</span>` : ""}
      ${record?.note ? `<p>${escapeHtml(record.note)}</p>` : ""}
    </div>` : ""}
  </article>`;
}
function renderBenefits(profile) {
  pageTitle(profile ? "" : button(t("添加账号", "Add account"), "add-account"));
  const filter = benefitViews.get(profile?.id) || "all";
  const catalog = data.benefits.map(localizedBenefit);
  const visible = catalog.filter(item => filter === "all" || item.independent || !item.plan_values || item.plan_values[filter]);
  const filters = Object.entries(benefitFilters).map(([key, label]) => {
    const material = planMaterials[key] ? ` ${planMaterials[key]}` : "";
    return `<button type="button" class="button compact${material}" data-action="filter-benefits"
      data-id="${key}" aria-pressed="${key === filter}">${label || t("全部", "All")}</button>`;
  }).join("");
  const context = profile ? `<div class="panel summary-top">${identity(profile)}<p class="panel-note">${t("前往 Google 后，请核对页面使用的邮箱。", "Verify the email address when you open a Google page.")}</p></div>` : "";
  return `<div class="layout-stack">
    ${context}
    ${renderPlanComparison(catalog, filter)}
    <section aria-labelledby="benefits-list-title" class="benefits-catalog">
      <header class="benefit-toolbar">
        <h2 id="benefits-list-title">${t("权益目录", "Perk catalog")} <span>${t(`${visible.length} 项`, `${visible.length} ${visible.length === 1 ? "perk" : "perks"}`)}</span></h2>
        <div class="benefit-filters" role="group" aria-label="${t("按会员档位筛选", "Filter by membership tier")}">${filters}</div>
      </header>
      ${visible.length
        ? `<div class="benefit-grid">${visible.map(item => renderBenefitCard(item, profile, filter)).join("")}</div>`
        : `<p class="panel state-panel">${t("当前档位暂无权益条目。", "No perks listed for this tier.")}</p>`}
    </section>
  </div>`;
}
function filterBenefits(plan) {
  if (!Object.hasOwn(benefitFilters, plan)) return;
  const profile = account();
  benefitViews.set(profile?.id, plan);
  pageContent.innerHTML = renderBenefits(profile);
  pageContent.querySelector('[data-action="filter-benefits"][aria-pressed="true"]')?.focus({ preventScroll: true });
}
