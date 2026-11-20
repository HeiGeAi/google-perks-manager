import AppKit
import Foundation
import UniformTypeIdentifiers
import WebKit

private let dataDirectoryName = "谷歌管理器"

private enum InterfaceLanguage: String {
    case system
    case chinese = "zh-CN"
    case english = "en"

    var effective: InterfaceLanguage {
        guard self == .system else { return self }
        for preferred in Locale.preferredLanguages {
            switch preferred.lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).first {
            case "zh": return .chinese
            case "en": return .english
            default: continue
            }
        }
        return .english
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandlerWithReply {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var statusView: NSView!
    private var statusLabel: NSTextField!
    private var spinner: NSProgressIndicator!
    private var retryButton: NSButton!
    private var service: Process?
    private var portOutput: Pipe?
    private var portBuffer = Data()
    private var port: Int?
    private var launchTimeout: DispatchWorkItem?
    private var filePanel: NSOpenPanel?
    private var installingUpdate = false
    private let updateToken = UUID().uuidString + UUID().uuidString
    private let updater = ReleaseUpdater()

    private var dataDirectory: URL {
        let override = ProcessInfo.processInfo.environment["GOOGLE_MANAGER_DATA_DIR"]
        let defaultPath = NSHomeDirectory() + "/Library/Application Support/" + dataDirectoryName
        let path = ((override?.isEmpty == false ? override : nil) ?? defaultPath) as NSString
        return URL(fileURLWithPath: path.expandingTildeInPath).standardizedFileURL
    }

    private var selectionKey: String { "selectedAccount:" + dataDirectory.path }
    private var languageKey: String { "interfaceLanguage:" + dataDirectory.path }
    private var language: InterfaceLanguage {
        let choice = UserDefaults.standard.string(forKey: languageKey) ?? ""
        return InterfaceLanguage(rawValue: choice) ?? .system
    }
    private var productName: String { localized("谷歌羊毛管理器", "Google Perks Manager") }
    private var version: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "" }
    private var serviceExecutable: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("backend/GoogleManagerService")
    }

    private func localized(_ chinese: String, _ english: String) -> String {
        language.effective == .chinese ? chinese : english
    }

    private func appInfo() -> [String: Any] {
        let contactQr = dataDirectory.appendingPathComponent("branding/wechat-contact.png")
        return [
            "language": language.rawValue,
            "effectiveLanguage": language.effective.rawValue,
            "version": version,
            "agentCommand": serviceExecutable?.path ?? "",
            "repository": ReleaseUpdater.repository,
            "hasContactQr": FileManager.default.isReadableFile(atPath: contactQr.path)
        ]
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        configureMenu()
        configureWindow()
        showWindow()
        startService()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        stopService()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        showWindow()
        return true
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard urls.contains(where: { $0.absoluteString == "googlemanager://return" }) else { return }
        showWindow()
        if let webView, isLocal(webView.url) { webView.reload() }
    }

    private func showWindow() {
        guard let window else { return }
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func showAbout(_ sender: Any?) {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: productName, .applicationVersion: version])
    }

    private func configureMenu() {
        let main = NSMenu()
        let appMenu = addMenu(productName, to: main)
        appMenu.addItem(withTitle: localized("关于", "About ") + productName, action: #selector(showAbout(_:)), keyEquivalent: "").target = self
        appMenu.addItem(.separator())
        let settings = appMenu.addItem(withTitle: localized("设置…", "Settings…"), action: #selector(navigate(_:)), keyEquivalent: ",")
        settings.target = self
        settings.representedObject = "settings"
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: localized("隐藏", "Hide ") + productName, action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: localized("退出", "Quit ") + productName, action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let edit = addMenu(localized("编辑", "Edit"), to: main)
        for (title, action, key) in [(localized("撤销", "Undo"), "undo:", "z"),
                                     (localized("重做", "Redo"), "redo:", "Z"),
                                     (localized("剪切", "Cut"), "cut:", "x"),
                                     (localized("复制", "Copy"), "copy:", "c"),
                                     (localized("粘贴", "Paste"), "paste:", "v"),
                                     (localized("全选", "Select All"), "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: NSSelectorFromString(action), keyEquivalent: key)
        }

        let view = addMenu(localized("显示", "View"), to: main)
        for (title, route, key) in [("Colab", "colab", "1"),
                                    (localized("权益", "Benefits"), "benefits", "2"),
                                    (localized("账号管理", "Accounts"), "accounts", "3"),
                                    (localized("创作", "Studio"), "studio", "4"),
                                    (localized("一键接入 Agent", "Connect an Agent"), "agent", "5")] {
            let item = view.addItem(withTitle: title, action: #selector(navigate(_:)), keyEquivalent: key)
            item.representedObject = route
            item.target = self
        }
        view.addItem(.separator())
        view.addItem(withTitle: localized("刷新页面", "Reload Page"), action: #selector(refresh(_:)), keyEquivalent: "r").target = self

        let windows = addMenu(localized("窗口", "Window"), to: main)
        windows.addItem(withTitle: localized("关闭窗口", "Close Window"), action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windows.addItem(withTitle: localized("最小化", "Minimize"), action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windows.addItem(withTitle: localized("缩放窗口", "Zoom"), action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        NSApp.windowsMenu = windows
        NSApp.mainMenu = main
    }

    private func addMenu(_ title: String, to main: NSMenu) -> NSMenu {
        let item = NSMenuItem()
        let menu = NSMenu(title: title)
        item.submenu = menu
        main.addItem(item)
        return menu
    }

    @objc private func navigate(_ sender: NSMenuItem) {
        guard let port, let route = sender.representedObject as? String,
              let url = URL(string: "http://127.0.0.1:\(port)/#\(route)") else { return }
        showWindow()
        webView.load(URLRequest(url: url))
    }

    @objc private func refresh(_ sender: Any?) {
        if isLocal(webView.url) { webView.reload() }
    }

    private func configureWindow() {
        let content = NSView()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1160, height: 760),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = productName
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(srgbRed: 0.969, green: 0.953, blue: 0.925, alpha: 1)
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 550, height: 440)
        window.contentView = content
        window.center()
        window.setFrameAutosaveName("GoogleManagerMainWindow")

        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        for name in ["openGoogleAuthorization", "chooseOAuthClient", "chooseCliToken", "getAccountSelection", "setAccountSelection",
                     "pickMediaFile", "revealProductionFile", "copyCommunityRemark", "getAppInfo",
                     "setLanguage", "copyAgentPrompt", "checkForUpdate", "downloadUpdate", "installUpdate"] {
            configuration.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: name)
        }
        webView = WKWebView(frame: content.bounds, configuration: configuration)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        content.addSubview(webView)

        statusView = NSView(frame: content.bounds)
        statusView.autoresizingMask = [.width, .height]
        content.addSubview(statusView)
        statusLabel = NSTextField(wrappingLabelWithString: "")
        statusLabel.alignment = .center
        spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .small
        retryButton = NSButton(title: localized("重试", "Retry"), target: self, action: #selector(retry(_:)))
        let stack = NSStackView(views: [spinner, statusLabel, retryButton])
        stack.orientation = .vertical
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        statusView.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: statusView.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: statusView.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: statusView.widthAnchor, multiplier: 0.85)
        ])
    }

    @objc private func retry(_ sender: Any?) {
        if let port, service?.isRunning == true {
            displayStatus(localized("正在打开工作区…", "Opening your workspace…"), loading: true)
            loadWorkspace(port: port)
        } else {
            startService()
        }
    }

    private func displayStatus(_ message: String, loading: Bool) {
        statusLabel.stringValue = message
        statusView.isHidden = false
        webView.isHidden = true
        retryButton.isHidden = loading
        spinner.isHidden = !loading
        if loading { spinner.startAnimation(nil) } else { spinner.stopAnimation(nil) }
    }

    private func startService() {
        stopService()
        displayStatus(localized("正在打开工作区…", "Opening your workspace…"), loading: true)
        guard let executable = serviceExecutable,
              FileManager.default.isExecutableFile(atPath: executable.path) else {
            displayStatus(localized("应用缺少本机服务，请重新安装谷歌羊毛管理器。",
                                    "The local service is missing. Reinstall Google Perks Manager."), loading: false)
            return
        }

        let child = Process()
        let output = Pipe()
        child.executableURL = executable
        child.currentDirectoryURL = executable.deletingLastPathComponent()
        child.arguments = ["--port", "0", "--announce-port"]
        var environment = ProcessInfo.processInfo.environment
        environment["GOOGLE_MANAGER_DATA_DIR"] = dataDirectory.path
        environment["GOOGLE_MANAGER_LANGUAGE"] = language.effective.rawValue
        environment["GOOGLE_MANAGER_UPDATE_TOKEN"] = updateToken
        child.environment = environment
        child.standardOutput = output
        child.standardInput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        service = child
        portOutput = output
        portBuffer.removeAll()

        output.fileHandleForReading.readabilityHandler = { [weak self, weak child] handle in
            let bytes = handle.availableData
            if bytes.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async {
                guard let self, let child, self.service === child, self.port == nil else { return }
                self.receivePort(bytes)
            }
        }
        child.terminationHandler = { [weak self] child in
            DispatchQueue.main.async {
                guard let self, self.service === child else { return }
                let wasReady = self.port != nil
                self.stopService()
                self.displayStatus(wasReady
                    ? self.localized("本机服务已退出，请重试。", "The local service stopped. Try again.")
                    : self.localized("本机服务启动失败。请确认数据目录可用，并关闭使用同一目录的应用副本。",
                                     "The local service could not start. Check the data folder and close other copies using it."),
                                   loading: false)
            }
        }
        do {
            try child.run()
        } catch {
            stopService()
            displayStatus(localized("无法启动应用内的本机服务，请检查安装包是否完整。",
                                    "The local service could not start. Check that the app is installed correctly."), loading: false)
            return
        }
        let timeout = DispatchWorkItem { [weak self, weak child] in
            guard let self, let child, self.service === child, self.port == nil else { return }
            self.stopService()
            self.displayStatus(self.localized("本机服务启动超时，请重试。", "The local service timed out. Try again."), loading: false)
        }
        launchTimeout = timeout
        DispatchQueue.main.asyncAfter(deadline: .now() + 30, execute: timeout)
    }

    private func receivePort(_ bytes: Data) {
        portBuffer.append(bytes)
        if let end = portBuffer.firstIndex(of: 10), end < 32,
           let line = String(data: portBuffer.prefix(upTo: end), encoding: .utf8),
           line.hasPrefix("GM_PORT="), let value = Int(line.dropFirst(8)),
           (1...65535).contains(value), line == "GM_PORT=\(value)" {
            launchTimeout?.cancel()
            portBuffer.removeAll()
            port = value
            loadWorkspace(port: value)
        } else if portBuffer.contains(10) || portBuffer.count >= 32 {
            stopService()
            displayStatus(localized("本机服务返回了无效的启动信息，请重新打开应用。",
                                    "The local service returned invalid startup information. Reopen the app."), loading: false)
        }
    }

    private func loadWorkspace(port: Int) {
        if let url = URL(string: "http://127.0.0.1:\(port)/") {
            webView.load(URLRequest(url: url))
        }
    }

    private func stopService() {
        launchTimeout?.cancel()
        launchTimeout = nil
        let child = service
        service = nil
        port = nil
        portOutput?.fileHandleForReading.readabilityHandler = nil
        portOutput = nil
        if child?.isRunning == true { child?.terminate() }
    }

    private func isLocal(_ url: URL?) -> Bool {
        guard let url, let port else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == port
            && url.user == nil && url.password == nil
    }

    private func isTrusted(_ frame: WKFrameInfo) -> Bool {
        guard let port else { return false }
        return frame.isMainFrame && frame.securityOrigin.protocol == "http"
            && frame.securityOrigin.host == "127.0.0.1" && frame.securityOrigin.port == port
    }

    private func isExternalLink(_ action: WKNavigationAction) -> Bool {
        guard let url = action.request.url else { return false }
        return action.navigationType == .linkActivated && isTrusted(action.sourceFrame)
            && url.scheme == "https" && url.host != nil && url.user == nil && url.password == nil
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if isLocal(action.request.url) {
            decisionHandler(.allow)
        } else if isExternalLink(action) && action.targetFrame == nil {
            // WebKit will pass the new-window request to createWebViewWith below.
            decisionHandler(.allow)
        } else {
            if isExternalLink(action), let url = action.request.url { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if isExternalLink(action), let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard isLocal(webView.url) else { return }
        spinner.stopAnimation(nil)
        statusView.isHidden = true
        webView.isHidden = false
        window.makeFirstResponder(webView)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        handleNavigationError(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        handleNavigationError(error)
    }

    private func handleNavigationError(_ error: Error) {
        let problem = error as NSError
        if problem.domain == NSURLErrorDomain && problem.code == NSURLErrorCancelled { return }
        if port != nil { displayStatus(localized("工作区暂时无法打开，请重试。", "The workspace could not open. Try again."), loading: false) }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if port != nil { displayStatus(localized("窗口内容已关闭，请重试。", "The workspace closed unexpectedly. Try again."), loading: false) }
    }

    private func selectFile(title: String, contentType: UTType, reply: @escaping (Any?, String?) -> Void) {
        guard filePanel == nil else {
            reply(nil, localized("文件选择窗口已打开。", "A file picker is already open."))
            return
        }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [contentType]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.title = title
        panel.prompt = localized("选择", "Choose")
        filePanel = panel
        panel.beginSheetModal(for: window) { [weak self] result in
            self?.filePanel = nil
            let path = result == .OK ? panel.url?.path : nil
            reply(path as Any? ?? NSNull(), nil)
        }
    }

    private func updateWorkspace(_ action: String, port: Int) async throws {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/update/\(action)") else {
            throw UpdateFailure.workspaceUnavailable
        }
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("http://127.0.0.1:\(port)", forHTTPHeaderField: "Origin")
        request.setValue(updateToken, forHTTPHeaderField: "X-Google-Manager-Update")
        request.httpBody = Data("{}".utf8)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.connectionProxyDictionary = [:]
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        let session = URLSession(configuration: configuration, delegate: UpdateServiceRedirects(), delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw UpdateFailure.workspaceUnavailable }
            if http.statusCode == 409 { throw UpdateFailure.workspaceBusy }
            guard http.statusCode == 200 else { throw UpdateFailure.workspaceUnavailable }
            if action == "prepare" {
                let body = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                guard body?["ready"] as? Bool == true else { throw UpdateFailure.workspaceUnavailable }
            }
        } catch let failure as UpdateFailure {
            throw failure
        } catch {
            throw UpdateFailure.workspaceUnavailable
        }
    }

    private func installDownloadedUpdate() async throws -> [String: String] {
        guard let child = service, child.isRunning, let existingPort = port else {
            throw UpdateFailure.workspaceUnavailable
        }
        let update = try await updater.prepareInstall(at: Bundle.main.bundleURL, currentVersion: version)
        var exchanged = false
        var serviceStopped = false
        do {
            try await updateWorkspace("prepare", port: existingPort)
            guard service === child, child.isRunning else { throw UpdateFailure.workspaceUnavailable }
            stopService()
            serviceStopped = true
            for _ in 0..<80 where child.isRunning {
                try await Task.sleep(for: .milliseconds(100))
            }
            guard !child.isRunning else { throw UpdateFailure.installFailed }
            try update.exchange()
            exchanged = true

            let launch = NSWorkspace.OpenConfiguration()
            launch.createsNewApplicationInstance = true
            launch.activates = true
            launch.promptsUserIfNeeded = false
            var environment = ["GOOGLE_MANAGER_DATA_DIR": dataDirectory.path]
            if let client = ProcessInfo.processInfo.environment["GOOGLE_MANAGER_OAUTH_CLIENT"] {
                environment["GOOGLE_MANAGER_OAUTH_CLIENT"] = client
            }
            launch.environment = environment
            let application = try await NSWorkspace.shared.openApplication(at: update.installed, configuration: launch)
            guard application.processIdentifier != ProcessInfo.processInfo.processIdentifier,
                  application.bundleURL?.resolvingSymlinksInPath().path == update.installed.path,
                  !application.isTerminated else { throw UpdateFailure.installFailed }
            update.discard()
            return ["status": "installing", "version": update.version]
        } catch {
            if exchanged {
                do {
                    try update.exchange()
                } catch {
                    // The staged original remains available when an exchange cannot be reversed.
                    throw UpdateFailure.rollbackFailed
                }
            }
            update.discard()
            if serviceStopped {
                if child.isRunning {
                    service = child
                    port = existingPort
                    try? await updateWorkspace("cancel", port: existingPort)
                } else {
                    startService()
                }
            } else {
                // The prepare response can be lost after the backend has accepted the request.
                try? await updateWorkspace("cancel", port: existingPort)
            }
            throw error
        }
    }

    private func handleUpdate(_ action: String, reply: @escaping (Any?, String?) -> Void) {
        guard !installingUpdate, webView.url?.fragment == "settings" else {
            let message = UpdateFailure.busy.message
            reply(nil, localized(message.chinese, message.english))
            return
        }
        if action == "installUpdate" { installingUpdate = true }
        Task { @MainActor in
            do {
                switch action {
                case "checkForUpdate":
                    reply(try await updater.check(currentVersion: version), nil)
                case "downloadUpdate":
                    reply(try await updater.downloadVerified(), nil)
                default:
                    reply(try await installDownloadedUpdate(), nil)
                    DispatchQueue.main.async { NSApp.terminate(nil) }
                }
            } catch {
                let fallback: UpdateFailure = action == "installUpdate" ? .installFailed : .unavailable
                let message = (error as? UpdateFailure ?? fallback).message
                reply(nil, localized(message.chinese, message.english))
                if action == "installUpdate" { installingUpdate = false }
            }
        }
    }

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.webView === webView, isTrusted(message.frameInfo) else {
            replyHandler(nil, localized("请求来源无效。", "Invalid request origin."))
            return
        }
        switch message.name {
        case "getAccountSelection":
            replyHandler(UserDefaults.standard.string(forKey: selectionKey) as Any? ?? NSNull(), nil)
        case "getAppInfo":
            replyHandler(appInfo(), nil)
        case "setLanguage":
            guard let value = message.body as? String, let choice = InterfaceLanguage(rawValue: value) else {
                replyHandler(nil, localized("不支持此界面语言。", "This interface language is unsupported."))
                return
            }
            UserDefaults.standard.set(choice.rawValue, forKey: languageKey)
            configureMenu()
            window.title = productName
            retryButton.title = localized("重试", "Retry")
            replyHandler(appInfo(), nil)
        case "copyAgentPrompt":
            guard webView.url?.fragment == "agent", let prompt = message.body as? String,
                  !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, prompt.utf8.count <= 65_536 else {
                replyHandler(nil, localized("复制参数无效。", "Invalid text to copy."))
                return
            }
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            replyHandler(pasteboard.setString(prompt, forType: .string), nil)
        case "checkForUpdate", "downloadUpdate", "installUpdate":
            guard message.body is NSNull else {
                replyHandler(nil, localized("更新请求无效。", "Invalid update request."))
                return
            }
            handleUpdate(message.name, reply: replyHandler)
        case "copyCommunityRemark":
            guard message.body is NSNull else {
                replyHandler(nil, localized("复制参数无效。", "Invalid text to copy."))
                return
            }
            let pasteboard = NSPasteboard.general
            pasteboard.clearContents()
            replyHandler(pasteboard.setString("谷歌交流群", forType: .string), nil)
        case "setAccountSelection":
            if let account = message.body as? String,
               account.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil {
                UserDefaults.standard.set(account, forKey: selectionKey)
            } else if message.body is NSNull {
                UserDefaults.standard.removeObject(forKey: selectionKey)
            } else {
                replyHandler(nil, localized("账号标识无效。", "Invalid account ID."))
                return
            }
            replyHandler(NSNull(), nil)
        case "openGoogleAuthorization":
            guard let value = message.body as? String, value.utf8.count <= 16_384,
                  let url = URL(string: value), url.scheme == "https", url.host == "accounts.google.com",
                  url.user == nil, url.password == nil, url.port == nil, url.fragment == nil,
                  ["/o/oauth2/auth", "/o/oauth2/v2/auth"].contains(url.path) else {
                replyHandler(nil, localized("授权网址校验失败。请重新连接 Google。", "The authorization URL could not be verified. Connect to Google again."))
                return
            }
            replyHandler(NSWorkspace.shared.open(url), nil)
        case "chooseOAuthClient":
            guard message.body is NSNull else {
                replyHandler(nil, localized("文件选择参数无效。", "Invalid file picker request."))
                return
            }
            selectFile(title: localized("选择 Google 桌面 OAuth 配置文件", "Choose a Google Desktop OAuth configuration"),
                       contentType: .json, reply: replyHandler)
        case "chooseCliToken":
            guard message.body is NSNull else {
                replyHandler(nil, localized("文件选择参数无效。", "Invalid file picker request."))
                return
            }
            selectFile(title: localized("选择官方 Colab CLI 授权文件", "Choose the official Colab CLI credential file"),
                       contentType: .json, reply: replyHandler)
        case "pickMediaFile":
            switch message.body as? String {
            case "video": selectFile(title: localized("选择视频画面", "Choose a video"), contentType: .movie, reply: replyHandler)
            case "audio": selectFile(title: localized("选择配音音轨", "Choose an audio track"), contentType: .audio, reply: replyHandler)
            default: replyHandler(nil, localized("请选择支持的媒体格式。", "Choose a supported media format."))
            }
        case "revealProductionFile":
            guard let values = message.body as? [String: String], let id = values["id"], let name = values["name"],
                  id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
                  ["output.wav", "output.mp4"].contains(name) else {
                replyHandler(nil, localized("找不到作品。", "The artifact could not be found."))
                return
            }
            let folder = dataDirectory.resolvingSymlinksInPath().appendingPathComponent("production", isDirectory: true)
            let path = folder.appendingPathComponent(id, isDirectory: true).appendingPathComponent(name)
            guard path.resolvingSymlinksInPath().path.hasPrefix(folder.path + "/"),
                  FileManager.default.isReadableFile(atPath: path.path) else {
                replyHandler(nil, localized("作品文件已移动或无法读取。", "The artifact has moved or cannot be read."))
                return
            }
            NSWorkspace.shared.activateFileViewerSelecting([path])
            replyHandler(NSNull(), nil)
        default:
            replyHandler(nil, localized("未知操作。", "Unknown action."))
        }
    }
}

@main
enum ApplicationMain {
    @MainActor
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.setActivationPolicy(.regular)
        application.appearance = NSAppearance(named: .aqua)
        application.delegate = delegate
        withExtendedLifetime(delegate) { application.run() }
    }
}
