using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace GooglePerksManager;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] is "probe" or "mux")
            return Media.RunAsync(args).GetAwaiter().GetResult();

        var directory = Path.GetFullPath(Environment.GetEnvironmentVariable("GOOGLE_MANAGER_DATA_DIR")
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "HeiGeAi", "GooglePerksManager"));
        var hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(directory.ToUpperInvariant())));
        var name = @"Local\HeiGeAi.GooglePerksManager." + hash;
        using var activate = new EventWaitHandle(false, EventResetMode.AutoReset, name + ".activate");
        using var single = new Mutex(true, name, out var first);
        if (!first)
        {
            activate.Set();
            return 0;
        }

        ApplicationConfiguration.Initialize();
        using var window = new DesktopWindow(directory);
        var registration = ThreadPool.RegisterWaitForSingleObject(activate,
            (_, _) => window.BringForward(), null, Timeout.Infinite, false);
        try { Application.Run(window); }
        finally { registration.Unregister(null); }
        return 0;
    }
}

internal sealed class DesktopWindow : Form
{
    private static readonly string[] BridgeMethods =
    [
        "getAppInfo", "setLanguage", "getAccountSelection", "setAccountSelection", "chooseOAuthClient",
        "chooseCliToken", "openGoogleAuthorization", "pickMediaFile", "revealProductionFile",
        "copyCommunityRemark", "copyAgentPrompt", "checkForUpdate", "downloadUpdate", "installUpdate",
    ];

    private readonly string dataPath;
    private readonly string preferencePath;
    private readonly string backend = Path.Combine(AppContext.BaseDirectory, "backend", "GoogleManagerService.exe");
    private readonly string updateToken = Convert.ToHexString(RandomNumberGenerator.GetBytes(32));
    private readonly HttpClient local = new(new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false })
        { Timeout = TimeSpan.FromSeconds(8) };
    private readonly WebView2 view = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Label status = new() { AutoSize = true, Padding = new Padding(10), Dock = DockStyle.Top };
    private readonly Button retry = new() { AutoSize = true, Dock = DockStyle.Top, Visible = false };
    private Process? service;
    private Uri? origin;
    private string language = "system";
    private string? selected;
    private string? downloadedVersion;
    private string? availableVersion;
    private bool installing;
    private bool initialized;
    private string Version => typeof(DesktopWindow).Assembly.GetName().Version!.ToString(3);
    private string EffectiveLanguage => language == "system"
        ? (CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "zh" ? "zh-CN" : "en")
        : language;

    internal DesktopWindow(string directory)
    {
        dataPath = directory;
        preferencePath = Path.Combine(directory, "desktop-preferences.json");
        ReadPreferences();
        Text = WindowTitle();
        Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Width = 1160;
        Height = 795;
        MinimumSize = new Size(550, 460);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(250, 248, 243);
        Controls.Add(view);
        Controls.Add(retry);
        Controls.Add(status);
        Shown += async (_, _) => await StartWorkspaceAsync();
        retry.Click += async (_, _) => await StartWorkspaceAsync();
    }

    internal void BringForward()
    {
        if (!IsHandleCreated || IsDisposed) return;
        try
        {
            BeginInvoke(() =>
            {
                if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
                Activate();
            });
        }
        catch (InvalidOperationException) { }
    }

    private string WindowTitle() => T("谷歌羊毛管理器", "Google Perks Manager");
    private string T(string zh, string en) => EffectiveLanguage == "zh-CN" ? zh : en;

    private void ReadPreferences()
    {
        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(preferencePath));
            var saved = doc.RootElement;
            if (saved.TryGetProperty("language", out var lang) && lang.GetString() is "system" or "zh-CN" or "en")
                language = lang.GetString()!;
            if (saved.TryGetProperty("selected", out var account) && IsId(account.GetString()))
                selected = account.GetString();
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException) { }
    }

    private void SavePreferences()
    {
        var pending = preferencePath + ".tmp";
        try
        {
            File.WriteAllText(pending, JsonSerializer.Serialize(new { language, selected }));
            File.Move(pending, preferencePath, overwrite: true);
        }
        finally { File.Delete(pending); }
    }

    private ProcessStartInfo ServiceCommand(params string[] arguments)
    {
        var start = new ProcessStartInfo(backend)
        {
            WorkingDirectory = Path.GetDirectoryName(backend),
            UseShellExecute = false, CreateNoWindow = true, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8,
            StandardInputEncoding = new UTF8Encoding(false),
        };
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        start.Environment["GOOGLE_MANAGER_DATA_DIR"] = dataPath;
        start.Environment["GOOGLE_MANAGER_LANGUAGE"] = EffectiveLanguage;
        return start;
    }

    private async Task StartWorkspaceAsync()
    {
        retry.Visible = false;
        status.Visible = true;
        status.Text = T("正在启动本机工作区…", "Starting the local workspace…");
        try
        {
            var start = ServiceCommand("--port", "0", "--announce-port");
            start.Environment["GOOGLE_MANAGER_UPDATE_TOKEN"] = updateToken;
            service = Process.Start(start) ?? throw new InvalidOperationException();
            service.ErrorDataReceived += (_, _) => { };
            service.BeginErrorReadLine();
            var output = await service.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(20));
            if (output is null || !output.StartsWith("GM_PORT=") || !int.TryParse(output[8..], out var port)
                || port is < 1 or > 65535) throw new InvalidOperationException();
            origin = new Uri($"http://127.0.0.1:{port}/");
            if (!initialized)
            {
                var environment = await CoreWebView2Environment.CreateAsync(
                    userDataFolder: Path.Combine(dataPath, "WebView"));
                await view.EnsureCoreWebView2Async(environment);
                ConfigureView();
                initialized = true;
            }
            var scopedOrigin = JsonSerializer.Serialize(origin.GetLeftPart(UriPartial.Authority));
            await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(BridgeScript(scopedOrigin));
            view.CoreWebView2.Navigate(origin.AbsoluteUri);
            view.Visible = true;
            status.Visible = false;
        }
        catch (WebView2RuntimeNotFoundException)
        {
            StopService();
            status.Text = T("请安装 Microsoft Edge WebView2 Runtime 后重试。", "Install Microsoft Edge WebView2 Runtime and retry.");
            retry.Text = T("重试", "Retry");
            retry.Visible = true;
        }
        catch (Exception)
        {
            StopService();
            status.Text = T("无法启动本机工作区。请检查应用安装和当前用户的数据目录。", "Could not start the local workspace. Check the installation and your app data folder.");
            retry.Text = T("重试", "Retry");
            retry.Visible = true;
        }
    }

    private void ConfigureView()
    {
        var core = view.CoreWebView2;
        core.Settings.AreDevToolsEnabled = false;
        core.Settings.AreHostObjectsAllowed = false;
        core.Settings.AreDefaultContextMenusEnabled = false;
        core.Settings.IsStatusBarEnabled = false;
        core.PermissionRequested += (_, e) => e.State = CoreWebView2PermissionState.Deny;
        core.FrameNavigationStarting += (_, e) => e.Cancel = !IsLocal(e.Uri);
        core.NavigationStarting += (_, e) =>
        {
            if (IsLocal(e.Uri)) return;
            e.Cancel = true;
            if (e.IsUserInitiated && HttpsUrl(e.Uri) is { } url) OpenExternal(url);
        };
        core.NewWindowRequested += (_, e) =>
        {
            e.Handled = true;
            if (e.IsUserInitiated && HttpsUrl(e.Uri) is { } url) OpenExternal(url);
        };
        core.WebMessageReceived += OnMessage;
    }

    private static string BridgeScript(string scopedOrigin) => $$$"""
        (() => {
          if (window.top !== window || location.origin !== {{{scopedOrigin}}}) return;
          let nextId = 0;
          const waiting = new Map();
          const nativeBridge = window.chrome.webview;
          nativeBridge.addEventListener("message", ({data}) => {
            const pending = waiting.get(data.id);
            if (!pending) return;
            waiting.delete(data.id);
            if (data.error) pending.reject(new Error(data.error));
            else pending.resolve(data.value);
          });
          const methods = {{{JsonSerializer.Serialize(BridgeMethods)}}};
          Object.defineProperty(window, "googlePerksDesktop", {value: Object.fromEntries(methods.map(method => [
            method, {postMessage: body => new Promise((resolve, reject) => {
              const id = ++nextId;
              waiting.set(id, {resolve, reject});
              nativeBridge.postMessage({id, method, body});
            })}
          ]))});
        })();
        """;

    private bool IsLocal(string? value) => origin is not null && Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "http" && uri.IdnHost == "127.0.0.1" && uri.Port == origin.Port && uri.UserInfo.Length == 0;

    private static Uri? HttpsUrl(string? value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.UserInfo.Length == 0 ? uri : null;

    private static bool IsId(string? value) => value is not null && Regex.IsMatch(value, "^[a-f0-9]{32}$");

    private static bool OpenExternal(Uri url)
    {
        try
        {
            using var process = Process.Start(new ProcessStartInfo(url.AbsoluteUri) { UseShellExecute = true });
            return true;
        }
        catch (System.ComponentModel.Win32Exception) { return false; }
    }

    private async void OnMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        if (!IsLocal(e.Source) || e.WebMessageAsJson.Length > 131072) return;
        var id = 0;
        object? value = null;
        string? error = null;
        try
        {
            using var message = JsonDocument.Parse(e.WebMessageAsJson);
            var root = message.RootElement;
            id = root.GetProperty("id").GetInt32();
            if (id <= 0) return;
            value = await DispatchAsync(root.GetProperty("method").GetString(), root.GetProperty("body"));
        }
        catch (InvalidOperationException ex) { error = ex.Message; }
        catch (Exception) { error = T("无法完成此操作，请重试。", "This operation could not be completed. Try again."); }
        if (id > 0 && IsLocal(view.Source?.AbsoluteUri) && !IsDisposed)
            view.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(new { id, value, error }));
        if (installing) BeginInvoke(() => Close());
    }

    private object AppInfo() => new
    {
        language, effectiveLanguage = EffectiveLanguage, version = Version, agentCommand = backend,
        agentShell = "powershell", repository = "HeiGeAi/google-perks-manager",
        hasContactQr = File.Exists(Path.Combine(dataPath, "branding", "wechat-contact.png")),
    };

    private async Task<object?> DispatchAsync(string? method, JsonElement body)
    {
        switch (method)
        {
            case "getAppInfo": return AppInfo();
            case "getAccountSelection": return selected;
            case "setAccountSelection":
                var selectedId = body.GetString();
                if (selectedId is not null && !IsId(selectedId)) break;
                selected = selectedId;
                SavePreferences();
                return null;
            case "setLanguage":
                if (body.GetString() is not ("system" or "zh-CN" or "en")) break;
                language = body.GetString()!;
                SavePreferences();
                Text = WindowTitle();
                return AppInfo();
            case "openGoogleAuthorization":
                var google = HttpsUrl(body.GetString());
                if (google?.Host != "accounts.google.com" || !google.IsDefaultPort || google.Fragment.Length != 0
                    || google.AbsolutePath is not ("/o/oauth2/auth" or "/o/oauth2/v2/auth")) break;
                return OpenExternal(google);
            case "chooseOAuthClient":
            case "chooseCliToken":
                if (body.ValueKind != JsonValueKind.Null) break;
                return SelectFile(T("选择本机授权文件", "Choose a local authorization file"), "JSON (*.json)|*.json");
            case "pickMediaFile":
                return body.GetString() switch
                {
                    "video" => SelectFile(T("选择视频", "Choose a video"), "Video (*.mp4;*.mov;*.m4v)|*.mp4;*.mov;*.m4v"),
                    "audio" => SelectFile(T("选择配音", "Choose audio"), "Audio (*.wav;*.m4a;*.mp3)|*.wav;*.m4a;*.mp3"),
                    _ => null,
                };
            case "revealProductionFile":
                Reveal(body);
                return true;
            case "copyCommunityRemark":
                if (body.ValueKind != JsonValueKind.Null) break;
                Clipboard.SetText("谷歌交流群");
                return true;
            case "copyAgentPrompt":
                var prompt = body.GetString();
                if (view.Source?.Fragment != "#agent" || string.IsNullOrWhiteSpace(prompt) || prompt.Length > 65536) break;
                Clipboard.SetText(prompt);
                return true;
            case "checkForUpdate":
            case "downloadUpdate":
            case "installUpdate":
                if (body.ValueKind != JsonValueKind.Null) break;
                return await UpdateAsync(method);
        }
        throw new InvalidOperationException(T("请求参数无效。", "Invalid request."));
    }

    private string? SelectFile(string title, string filter)
    {
        using var dialog = new OpenFileDialog { Title = title, Filter = filter, CheckFileExists = true };
        return dialog.ShowDialog(this) == DialogResult.OK ? dialog.FileName : null;
    }

    private void Reveal(JsonElement body)
    {
        var id = body.GetProperty("id").GetString();
        var name = body.GetProperty("name").GetString();
        if (!IsId(id) || name is not ("output.wav" or "output.mp4"))
            throw new InvalidOperationException(T("找不到作品。", "The output could not be found."));
        var folder = Path.Combine(dataPath, "production", id!);
        var file = Path.Combine(folder, name);
        if (!File.Exists(file) || (File.GetAttributes(file) & FileAttributes.ReparsePoint) != 0
            || (File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException(T("找不到作品。", "The output could not be found."));
        using var process = Process.Start(new ProcessStartInfo("explorer.exe", "/select,\"" + file + "\"")
            { UseShellExecute = true });
    }

    private async Task<JsonElement> UpdateCommandAsync(params string[] arguments)
    {
        using var process = Process.Start(ServiceCommand(["--windows-update", .. arguments]))
            ?? throw new InvalidOperationException();
        process.StandardInput.Close();
        var read = process.StandardOutput.ReadToEndAsync();
        var errors = process.StandardError.ReadToEndAsync();
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(10));
        try { await process.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException)
        {
            process.Kill(entireProcessTree: true);
            throw new InvalidOperationException(T("更新请求超时。", "The update request timed out."));
        }
        await errors;
        using var response = JsonDocument.Parse(await read);
        if (process.ExitCode != 0)
            throw new InvalidOperationException(T("更新校验失败或网络不可用。", "The update could not be verified or downloaded."));
        return response.RootElement.Clone();
    }

    private async Task<object> UpdateAsync(string method)
    {
        if (method == "checkForUpdate")
        {
            var result = await UpdateCommandAsync("check");
            availableVersion = result.GetProperty("status").GetString() == "available"
                ? result.GetProperty("version").GetString() : null;
            downloadedVersion = null;
            return result;
        }
        if (method == "downloadUpdate" && availableVersion is { } available)
        {
            var result = await UpdateCommandAsync("download", available);
            downloadedVersion = result.GetProperty("version").GetString();
            return result;
        }
        if (method != "installUpdate" || downloadedVersion is null)
            throw new InvalidOperationException(T("请先检查并下载更新。", "Check for updates and download the installer first."));
        var resultFile = await UpdateCommandAsync("verify", downloadedVersion);
        var setup = resultFile.GetProperty("path").GetString()!;
        if (MessageBox.Show(this, T($"安装 v{downloadedVersion}？应用将退出并打开安装程序。",
                $"Install v{downloadedVersion}? The app will close and open the installer."),
            WindowTitle(), MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK)
            return new { status = "cancelled" };
        await UpdateGateAsync("prepare");
        try
        {
            using var process = Process.Start(new ProcessStartInfo(setup, "/CLOSEAPPLICATIONS /NORESTART")
                { UseShellExecute = true });
        }
        catch
        {
            await UpdateGateAsync("cancel");
            throw;
        }
        installing = true;
        return new { status = "installing", version = downloadedVersion };
    }

    private async Task UpdateGateAsync(string action)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(origin!, "api/update/" + action));
        request.Headers.Add("Origin", origin!.GetLeftPart(UriPartial.Authority));
        request.Headers.Add("X-Google-Manager-Update", updateToken);
        request.Content = new StringContent("{}", Encoding.UTF8, "application/json");
        using var response = await local.SendAsync(request);
        if (response.StatusCode == HttpStatusCode.Conflict)
            throw new InvalidOperationException(T("请先处理进行中的授权或创作任务。", "Finish pending authorization or production jobs first."));
        response.EnsureSuccessStatusCode();
    }

    private void StopService()
    {
        var child = service;
        service = null;
        origin = null;
        if (child is null) return;
        using (child)
        {
            if (child.HasExited) return;
            child.StandardInput.Close();
            if (!child.WaitForExit(1500)) child.Kill(entireProcessTree: true);
        }
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        StopService();
        local.Dispose();
        base.OnFormClosed(e);
    }
}
