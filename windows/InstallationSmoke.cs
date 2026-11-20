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

            stage = "Colab runtime and deployment menu";
            await Execute(view, "location.hash = 'apps'");
            await Expect(view, """
                document.querySelectorAll('.deployment-card').length === 5 &&
                document.querySelector('[data-action=deploy-project][data-id=h3]') &&
                window.googlePerksDesktop.openPrivateEditor
                """);
            await Execute(view, "startInstance({...account(), auth_status: 'connected'})");
            await Expect(view, """
                document.querySelector('dialog[open] [name=gpu]')?.value === 'A100' &&
                document.querySelector('dialog[open] [name=memory]')?.value === 'high' &&
                document.querySelector('dialog[open] [type=submit]')?.textContent === 'Start A100 & deploy' &&
                document.querySelector('#launch-h3-benchmark:not([hidden])')?.textContent.includes('1.5097 CU') &&
                document.querySelector('dialog[open] [name=legacy_nodes]')
                """);
            foreach (var gpu in new[] { "G4", "H100" })
            {
                await Execute(view, $$"""
                    { const select = document.querySelector('dialog [name=gpu]');
                      select.value = '{{gpu}}'; select.dispatchEvent(new Event('change', {bubbles:true})); }
                    """);
                await Expect(view, $$"""
                    document.querySelector('dialog[open] [name=gpu]')?.value === '{{gpu}}' &&
                    document.querySelector('dialog[open] [name=memory]')?.value === 'standard' &&
                    document.querySelector('dialog[open] [type=submit]')?.textContent === 'Start {{gpu}} & deploy'
                    """);
            }
            await Execute(view, "document.querySelector('dialog [name=purpose][value=custom]').click()");
            await Expect(view, "document.querySelectorAll('dialog [name=gpu] option').length === 9");
            await Execute(view, "document.querySelector('dialog [data-action=close-dialog]').click()");
            await Expect(view, "!document.querySelector('dialog[open]')");

            stage = "perks catalog and filters";
            await Execute(view, "location.hash = 'benefits'");
            await Expect(view, "document.querySelector('#student-pro-note')?.textContent.includes('Google AI Pro student offers')");
            foreach (var (plan, material) in new[] { ("pro", "gold"), ("ultra-5x", "platinum"), ("ultra-20x", "diamond") })
            {
                await Execute(view, $"document.querySelector('.benefit-filters [data-id={plan}].{material}').click()");
                await Expect(view, $"document.querySelector('.benefit-filters [data-id={plan}]').getAttribute('aria-pressed') === 'true'");
            }
            await Execute(view, "document.querySelector('.benefit-filters [data-id=all]').click()");

            stage = "studio and Drive project";
            await Execute(view, "location.hash = 'studio'");
            await Expect(view, """
                document.querySelector('.job-row [data-action=studio-reveal]') &&
                document.querySelector('.job-row [data-action=studio-archive]') &&
                window.googlePerksDesktop.pickDriveFolder
                """);
            await Execute(view, "document.querySelector('.job-row [data-action=studio-archive]').click()");
            await Expect(view, "document.querySelector('dialog[open] [data-drive-pick]') && !document.querySelector('dialog [type=submit]')");
            await Execute(view, "document.querySelector('dialog [data-action=close-dialog]').click()");
            var sync = Directory.CreateDirectory(Path.Combine(Path.GetDirectoryName(report)!, "Drive fixture"));
            await Execute(view, $$"""
                void api(driveAccountPath(account().id), {folder: {{JsonSerializer.Serialize(sync.FullName)}}})
                  .then(() => document.querySelector('.job-row [data-action=studio-archive]').click());
                """);
            await Expect(view, "document.querySelector('dialog[open] [name=drive_new_project]')");
            await Execute(view, """
                { const form = document.querySelector('dialog form');
                  form.elements.drive_new_project.value = 'Windows project';
                  form.requestSubmit(); }
                """);
            await Expect(view, """
                !document.querySelector('dialog[open]') &&
                document.querySelector('.job-archive')?.textContent.includes('Windows project')
                """);
            await Execute(view, "document.querySelector('.job-archive').scrollIntoView({block:'nearest'})");
            await using var screenshot = File.Create(Path.ChangeExtension(report, ".png"));
            await view.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, screenshot);
            Write(report, new
            {
                installed = true, nativeWebView2 = true, version,
                languages = new[] { "zh-CN", "en" }, accountAlias = true, oauthGooglePage = true, tiers = 3,
                studioFolderAction = true, driveProjectDialog = true, h3LaunchMenu = true, h3DefaultA100HighRam = true,
                deployableProjects = 5,
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
