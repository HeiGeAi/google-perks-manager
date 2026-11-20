"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.GM_PLAYWRIGHT);

async function main() {
  const [debug, repo] = process.argv.slice(2);
  const browser = await chromium.connectOverCDP(debug);
  try {
    const context = browser.contexts()[0];
    const page = context.pages()[0] || await context.waitForEvent("page");
    const errors = [];
    page.on("pageerror", () => errors.push("pageerror"));
    await page.locator(".local-indicator.is-ready").waitFor({ timeout: 20000, state: "attached" });
    const initial = await page.evaluate(() => window.googlePerksDesktop.getAppInfo.postMessage(null));
    assert.equal(initial.agentShell, "powershell");
    assert.match(initial.agentCommand, /GoogleManagerService\.exe$/);

    await page.locator('[data-page="settings"]').click();
    await page.locator("#settings-language").selectOption("zh-CN");
    await page.waitForFunction(() => document.documentElement.lang === "zh-CN");
    await page.locator("#settings-language").selectOption("en");
    await page.waitForFunction(() => document.documentElement.lang === "en");

    await page.locator('[data-page="accounts"]').click();
    await page.locator('.page-header [data-action="add-account"]').click();
    const dialog = page.locator("dialog");
    await dialog.locator('[name="name"]').fill("Windows test");
    await dialog.locator('[name="email"]').fill("windows-test@example.test");
    await dialog.locator('[type="submit"]').click();
    await page.locator(".account-row").waitFor();
    assert.equal(await page.locator(".account-identity strong").textContent(), "Windows test");
    assert.ok(!(await page.locator(".account-row").innerText()).includes("@"));

    await page.locator('.account-row [data-action="connect-account"]').click();
    const code = dialog.locator('[name="code"]');
    await code.waitFor({ timeout: 20000 });
    const google = new URL(await dialog.locator("a").getAttribute("href"));
    assert.equal(google.hostname, "accounts.google.com");
    assert.equal(google.searchParams.get("code_challenge_method"), "S256");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await dialog.waitFor({ state: "hidden" });

    await page.locator('[data-page="agent"]').click();
    const prompt = await page.locator("#agent-prompt").inputValue();
    assert.match(prompt, /& '.+GoogleManagerService\.exe' --agent guide --language en/);
    await page.locator('[data-page="benefits"]').click();
    await page.locator("#student-pro-note").waitFor();
    assert.match(await page.locator("#student-pro-note").textContent(), /Google AI Pro student offers/);
    for (const [plan, material] of [["pro", "gold"], ["ultra-5x", "platinum"], ["ultra-20x", "diamond"]]) {
      const filter = page.locator(`.benefit-filters [data-id="${plan}"].${material}`);
      await filter.click();
      assert.equal(await filter.getAttribute("aria-pressed"), "true");
    }
    await page.locator('.benefit-filters [data-id="all"]').click();
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(repo, "dist", "windows-smoke.png") });
    fs.writeFileSync(path.join(repo, "dist", "windows-smoke.json"), JSON.stringify({
      installed: true, nativeWebView2: true, version: initial.version,
      languages: ["zh-CN", "en"], accountAlias: true, oauthGooglePage: true, tiers: 3,
    }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
