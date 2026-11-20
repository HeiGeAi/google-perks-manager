using System.Text.Json;
using Microsoft.Web.WebView2.Core;

namespace GooglePerksManager;

// Explicitly invoked with an isolated data directory by windows/smoke.ps1.
internal static class InstallationSmoke
{
    internal static async Task RunAsync(CoreWebView2 view, string report, string version)
    {
        string stage = "native workspace";
        try
        {
            await Expect(view, "document.querySelector('.local-indicator.is-ready') && window.googlePerksDesktop");
            stage = "language preferences";
            await Execute(view, "location.hash = 'settings'");
            await Expect(view, "document.querySelector('#settings-language')");
            foreach (var language in new[] { "zh-CN", "en" })
            {
                await Execute(view, $$"""
                    { const select = document.querySelector('#settings-language');
                      select.value = '{{language}}'; select.dispatchEvent(new Event('change', {bubbles:true})); }
                    """);
                await Expect(view, $"document.documentElement.lang === '{language}' && !document.querySelector('#settings-language')?.disabled");
            }

            stage = "account alias";
            await Execute(view, "location.hash = 'accounts'");
            await Expect(view, "document.querySelector('.page-header [data-action=add-account]')");
            await Expect(view, "document.querySelectorAll('.account-row').length === 0");
            await Execute(view, "document.querySelector('.page-header [data-action=add-account]').click()");
            await Expect(view, "document.querySelector('dialog[open] [name=email]')");
            await Execute(view, """
                { const form = document.querySelector('dialog form');
                  form.elements.namedItem('name').value = 'Windows test';
                  form.elements.namedItem('email').value = 'windows-test@example.test';
                  form.requestSubmit(); }
                """);
            await Expect(view, "document.querySelector('.account-identity strong')?.textContent === 'Windows test' && !document.querySelector('dialog[open]')");
            await Expect(view, "!document.querySelector('.account-row').innerText.includes('@')");

            stage = "official OAuth";
            await Execute(view, "document.querySelector('.account-row [data-action=connect-account]').click()");
            await Expect(view, """
                document.querySelector('dialog[open] [name="code"]') &&
                new URL(document.querySelector('dialog a').href).hostname === 'accounts.google.com' &&
                new URL(document.querySelector('dialog a').href).searchParams.get('code_challenge_method') === 'S256'
                """);
            await Execute(view, "document.querySelector('dialog [data-action=close-dialog]').click()");
            await Expect(view, "!document.querySelector('dialog[open]')");

            stage = "PowerShell Agent prompt";
            await Execute(view, "location.hash = 'agent'");
            await Expect(view, """document.querySelector('#agent-prompt')?.value.includes("GoogleManagerService.exe' --agent guide --language en")""");

            stage = "perks catalog and filters";
            await Execute(view, "location.hash = 'benefits'");
            await Expect(view, "document.querySelector('#student-pro-note')?.textContent.includes('Google AI Pro student offers')");
            foreach (var (plan, material) in new[] { ("pro", "gold"), ("ultra-5x", "platinum"), ("ultra-20x", "diamond") })
            {
                await Execute(view, $"document.querySelector('.benefit-filters [data-id={plan}].{material}').click()");
                await Expect(view, $"document.querySelector('.benefit-filters [data-id={plan}]').getAttribute('aria-pressed') === 'true'");
            }
            await Execute(view, "document.querySelector('.benefit-filters [data-id=all]').click()");
            await using var screenshot = File.Create(Path.ChangeExtension(report, ".png"));
            await view.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, screenshot);
            Write(report, new
            {
                installed = true, nativeWebView2 = true, version,
                languages = new[] { "zh-CN", "en" }, accountAlias = true, oauthGooglePage = true, tiers = 3,
            });
        }
        catch (Exception ex)
        {
            Write(report, new { error = stage, reason = ex.GetType().Name });
        }
    }

    private static Task<string> Execute(CoreWebView2 view, string script) => view.ExecuteScriptAsync(script);

    private static async Task Expect(CoreWebView2 view, string condition)
    {
        for (int attempt = 0; attempt < 80; attempt++)
        {
            if (await Execute(view, $"Boolean({condition})") == "true") return;
            await Task.Delay(250);
        }
        throw new TimeoutException();
    }

    private static void Write(string path, object data) => File.WriteAllText(path, JsonSerializer.Serialize(data));
}
