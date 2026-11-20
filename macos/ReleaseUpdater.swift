import AppKit
import CryptoKit
import Darwin
import Foundation

enum UpdateFailure: Error {
    case unconfigured
    case unavailable
    case invalidRelease
    case invalidSignature
    case invalidArchive
    case incompatible
    case checkFirst
    case downloadFirst
    case busy
    case workspaceBusy
    case workspaceUnavailable
    case installLocation
    case installFailed
    case rollbackFailed

    var message: (chinese: String, english: String) {
        switch self {
        case .unconfigured: ("此安装包未配置更新验证公钥。", "This build has no update verification key.")
        case .unavailable: ("暂时无法读取 GitHub 发布，请稍后重试。", "Could not read the GitHub release. Try again shortly.")
        case .invalidRelease: ("GitHub 发布缺少有效的更新文件。", "The GitHub release is missing valid update files.")
        case .invalidSignature: ("更新签名验证失败，请在 GitHub 核对发布。", "The update signature could not be verified. Check the GitHub release.")
        case .invalidArchive: ("安装包校验失败，请重新检查更新。", "The update package failed verification. Check for updates again.")
        case .incompatible: ("此更新与当前 Mac 不兼容。", "This update is incompatible with this Mac.")
        case .checkFirst: ("请先检查更新。", "Check for updates first.")
        case .downloadFirst: ("请先下载并验证更新。", "Download and verify the update first.")
        case .busy: ("更新操作正在进行，请稍候。", "An update operation is already in progress.")
        case .workspaceBusy: ("请先完成正在进行的授权、Colab 操作或创作任务。", "Finish active sign-in, Colab, or Studio operations first.")
        case .workspaceUnavailable: ("目前无法核对工作区，请先恢复本机服务。", "The workspace could not be checked. Restore the local service first.")
        case .installLocation: ("当前安装位置不可写。请将 App 移到可写的应用程序目录后重试。", "Move the app to a writable Applications folder and try again.")
        case .installFailed: ("本次升级未能完成，请重试。", "The update could not be installed. Try again.")
        case .rollbackFailed: ("无法恢复旧版。可从 GitHub 下载并重新安装。", "The previous version could not be restored. Reinstall from GitHub.")
        }
    }
}

private struct ReleaseVersion: Comparable {
    private let components: [Int]

    init?(_ value: String) {
        let fields = value.split(separator: ".", omittingEmptySubsequences: false)
        guard fields.count == 3,
              fields.allSatisfy({ field in
                  !field.isEmpty && field.count <= 8 &&
                      (field.count == 1 || field.first != "0") &&
                      field.utf8.allSatisfy({ 48...57 ~= $0 })
              }) else { return nil }
        components = fields.compactMap { Int($0) }
    }

    static func < (lhs: Self, rhs: Self) -> Bool {
        lhs.components.lexicographicallyPrecedes(rhs.components)
    }
}

private struct UpdateManifest: Decodable {
    let version: String
    let asset: String
    let sha256: String
    let size: Int?
    let bundle_id: String
}

// GitHub redirects release assets to its object storage. Only those HTTPS hosts are accepted.
private final class ReleaseRedirects: NSObject, URLSessionTaskDelegate {
    static func allows(_ url: URL?) -> Bool {
        guard let url else { return false }
        return url.scheme == "https" && (url.port == nil || url.port == 443) &&
            url.user == nil && url.password == nil && [
                "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
            ].contains(url.host)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(Self.allows(request.url) ? request : nil)
    }
}

private struct VerifiedRelease: Sendable {
    let version: String
    let sha256: String
    let archiveURL: URL
    let archiveSize: Int
}

struct PreparedUpdate: Sendable {
    let version: String
    let directory: URL
    let candidate: URL
    let installed: URL

    // Both bundles are under the same parent volume. A failed exchange leaves both paths intact.
    func exchange() throws {
        let result = candidate.withUnsafeFileSystemRepresentation { source in
            installed.withUnsafeFileSystemRepresentation { target in
                guard let source, let target else { return -1 as Int32 }
                return renamex_np(source, target, UInt32(RENAME_SWAP))
            }
        }
        guard result == 0 else { throw UpdateFailure.installLocation }
    }

    func discard() {
        try? FileManager.default.removeItem(at: directory)
    }
}

actor ReleaseUpdater {
    static let repository = "HeiGeAi/google-perks-manager"
    static let bundleID = "com.heige.google-manager"
    private static let archiveName = "GooglePerksManager-macOS-arm64.zip"
    private static let manifestName = "GooglePerksManager-update.json"
    private static let signatureName = "GooglePerksManager-update.sig"
    private static let bundleName = "谷歌羊毛管理器.app"
    private static let maxArchiveSize = 1_500_000_000
    private static let releases = URL(string: "https://github.com/\(repository)/releases")!

    private let session: URLSession
    private let key: Curve25519.Signing.PublicKey?
    private var verified: VerifiedRelease?
    private var download: (directory: URL, bundle: URL)?
    private var busy = false

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 900
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.httpAdditionalHeaders = ["User-Agent": "GooglePerksManager"]
        session = URLSession(configuration: configuration, delegate: ReleaseRedirects(), delegateQueue: nil)
        if let encoded = Bundle.main.object(forInfoDictionaryKey: "GooglePerksUpdatePublicKey") as? String,
           let decoded = Data(base64Encoded: encoded) {
            key = try? Curve25519.Signing.PublicKey(rawRepresentation: decoded)
        } else {
            key = nil
        }
    }

    deinit {
        if let download { try? FileManager.default.removeItem(at: download.directory) }
        session.invalidateAndCancel()
    }

    private func read(_ request: URLRequest, limit: Int) async throws -> Data {
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              ReleaseRedirects.allows(http.url),
              response.expectedContentLength <= Int64(limit) else {
            throw UpdateFailure.unavailable
        }
        var data = Data()
        for try await byte in bytes {
            guard data.count < limit else { throw UpdateFailure.invalidRelease }
            data.append(byte)
        }
        return data
    }

    func check(currentVersion: String) async throws -> [String: String] {
        guard !busy else { throw UpdateFailure.busy }
        guard let key else { throw UpdateFailure.unconfigured }
        guard let current = ReleaseVersion(currentVersion) else { throw UpdateFailure.invalidRelease }
        busy = true
        defer { busy = false }

        // Public release assets work without GitHub credentials or the REST API's shared IP quota.
        let latestManifest = Self.releases.appendingPathComponent("latest").appendingPathComponent("download")
            .appendingPathComponent(Self.manifestName)
        let data = try await read(URLRequest(url: latestManifest), limit: 16 * 1024)
        guard let manifest = try? JSONDecoder().decode(UpdateManifest.self, from: data),
              let latest = ReleaseVersion(manifest.version), manifest.asset == Self.archiveName,
              manifest.bundle_id == Self.bundleID, manifest.sha256.count == 64,
              manifest.sha256.utf8.allSatisfy({ 48...57 ~= $0 || 97...102 ~= $0 }) else {
            throw UpdateFailure.invalidRelease
        }
        let version = manifest.version
        let tag = "v" + version
        let base = Self.releases.appendingPathComponent("download").appendingPathComponent(tag)
        let signatureURL = base.appendingPathComponent(Self.signatureName)
        let signature = try await read(URLRequest(url: signatureURL), limit: 64)
        guard signature.count == 64, key.isValidSignature(signature, for: data) else {
            throw UpdateFailure.invalidSignature
        }

        let releaseURL = Self.releases.appendingPathComponent("tag").appendingPathComponent(tag).absoluteString
        if latest <= current {
            clearDownload()
            verified = nil
            return ["status": "current", "currentVersion": currentVersion, "version": version, "releaseURL": releaseURL]
        }

        guard let archiveSize = manifest.size, (1...Self.maxArchiveSize).contains(archiveSize) else {
            throw UpdateFailure.invalidRelease
        }
        if verified?.sha256 != manifest.sha256 || verified?.version != version { clearDownload() }
        verified = VerifiedRelease(version: version, sha256: manifest.sha256,
                                   archiveURL: base.appendingPathComponent(Self.archiveName), archiveSize: archiveSize)
        return ["status": "available", "currentVersion": currentVersion, "version": version, "releaseURL": releaseURL]
    }

    private func clearDownload() {
        if let download { try? FileManager.default.removeItem(at: download.directory) }
        download = nil
    }

    func downloadVerified() async throws -> [String: String] {
        guard !busy else { throw UpdateFailure.busy }
        guard let verified else { throw UpdateFailure.checkFirst }
        if download != nil { return ["status": "ready", "version": verified.version] }
        busy = true
        defer { busy = false }

        let fm = FileManager.default
        let directory = try Self.makeStagingDirectory(in: fm.temporaryDirectory)
        var succeeded = false
        defer { if !succeeded { try? fm.removeItem(at: directory) } }
        let (remoteFile, response) = try await session.download(from: verified.archiveURL)
        let archive = directory.appendingPathComponent("release.zip")
        defer { try? fm.removeItem(at: remoteFile) }
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              ReleaseRedirects.allows(http.url) else { throw UpdateFailure.unavailable }
        try fm.moveItem(at: remoteFile, to: archive)
        guard try Self.archiveHash(archive, expectedSize: verified.archiveSize) == verified.sha256 else {
            throw UpdateFailure.invalidArchive
        }
        let bundle = try Self.extract(archive, in: directory, version: verified.version)
        try fm.removeItem(at: archive)
        download = (directory, bundle)
        succeeded = true
        return ["status": "ready", "version": verified.version]
    }

    func prepareInstall(at bundle: URL, currentVersion: String) throws -> PreparedUpdate {
        guard !busy else { throw UpdateFailure.busy }
        guard let verified, let download else { throw UpdateFailure.downloadFirst }
        guard let current = ReleaseVersion(currentVersion), let latest = ReleaseVersion(verified.version),
              latest > current else { throw UpdateFailure.invalidRelease }

        let installed = bundle.resolvingSymlinksInPath()
        do {
            let original = try bundle.resourceValues(forKeys: [.isSymbolicLinkKey])
            let metadata = try installed.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey, .isWritableKey])
            guard installed.pathExtension == "app", original.isSymbolicLink != true,
                  metadata.isDirectory == true, metadata.isSymbolicLink != true, metadata.isWritable == true,
                  Bundle(url: installed)?.bundleIdentifier == Self.bundleID else { throw UpdateFailure.installLocation }
        } catch {
            throw UpdateFailure.installLocation
        }
        let directory: URL
        do {
            directory = try Self.makeStagingDirectory(in: installed.deletingLastPathComponent())
        } catch {
            throw UpdateFailure.installLocation
        }
        let candidate = directory.appendingPathComponent("replacement.app", isDirectory: true)
        do {
            // Recheck the original staging bundle as well as the local copy before installation.
            try Self.verifyBundle(download.bundle, version: verified.version)
            try FileManager.default.copyItem(at: download.bundle, to: candidate)
            try Self.verifyBundle(candidate, version: verified.version)
            return PreparedUpdate(version: verified.version, directory: directory, candidate: candidate, installed: installed)
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }

    private static func makeStagingDirectory(in parent: URL) throws -> URL {
        let directory = parent.resolvingSymlinksInPath()
            .appendingPathComponent(".GooglePerksUpdate-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        return directory
    }

    private static func archiveHash(_ archive: URL, expectedSize: Int) throws -> String {
        guard try archive.resourceValues(forKeys: [.fileSizeKey]).fileSize == expectedSize else {
            throw UpdateFailure.invalidArchive
        }
        let file = try FileHandle(forReadingFrom: archive)
        defer { try? file.close() }
        var hasher = SHA256()
        while let chunk = try file.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func extract(_ archive: URL, in privateDirectory: URL, version: String) throws -> URL {
        let listing = try run("/usr/bin/tar", ["-tf", archive.path])
        guard let names = String(data: listing, encoding: .utf8) else { throw UpdateFailure.invalidArchive }
        var paths = Set<String>()
        for name in names.split(separator: "\n") {
            let path = name.hasSuffix("/") ? name.dropLast() : name
            let fields = path.split(separator: "/", omittingEmptySubsequences: false)
            guard let first = fields.first,
                  fields.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." && !$0.contains("\\") }),
                  paths.insert(String(path)).inserted else { throw UpdateFailure.invalidArchive }
            if first == "__MACOSX" {
                guard fields.count == 1 || fields[1] == Self.bundleName || fields[1] == "._" + Self.bundleName else {
                    throw UpdateFailure.invalidArchive
                }
            } else {
                guard first == Self.bundleName else { throw UpdateFailure.invalidArchive }
            }
        }
        guard paths.contains("\(Self.bundleName)/Contents/Info.plist") else { throw UpdateFailure.invalidArchive }
        let extracted = privateDirectory.appendingPathComponent("extracted", isDirectory: true)
        try FileManager.default.createDirectory(at: extracted, withIntermediateDirectories: false)
        // libarchive's default extraction rejects traversal through symlinks and dot-dot components.
        _ = try run("/usr/bin/tar", ["-xf", archive.path, "-C", extracted.path])
        let bundle = extracted.appendingPathComponent(Self.bundleName, isDirectory: true)
        try verifyBundle(bundle, version: version)
        return bundle
    }

    private static func verifyBundle(_ app: URL, version: String) throws {
        let root = app.resolvingSymlinksInPath()
        guard root.path == app.path, let bundle = Bundle(url: app), bundle.bundleIdentifier == bundleID,
              bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String == version,
              bundle.object(forInfoDictionaryKey: "CFBundleExecutable") as? String == "GoogleManager",
              let binary = bundle.executableURL, FileManager.default.isExecutableFile(atPath: binary.path) else {
            throw UpdateFailure.invalidArchive
        }

        if let min = bundle.object(forInfoDictionaryKey: "LSMinimumSystemVersion") as? String {
            var fields = min.split(separator: ".")
            if fields.count == 2 { fields.append("0") }
            let minimum = fields.compactMap { Int($0) }
            guard fields.count == 3, minimum.count == 3,
                  ProcessInfo.processInfo.isOperatingSystemAtLeast(.init(
                    majorVersion: minimum[0], minorVersion: minimum[1], patchVersion: minimum[2])) else {
                throw UpdateFailure.incompatible
            }
        }

        let handle = try FileHandle(forReadingFrom: binary)
        defer { try? handle.close() }
        // Thin little-endian arm64 Mach-O header.
        guard try handle.read(upToCount: 8) == Data([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0, 0, 0x01]) else {
            throw UpdateFailure.incompatible
        }

        guard let entries = FileManager.default.enumerator(at: app,
            includingPropertiesForKeys: [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey]) else {
            throw UpdateFailure.invalidArchive
        }
        for case let entry as URL in entries {
            let metadata = try entry.resourceValues(forKeys: [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey])
            if metadata.isSymbolicLink == true {
                guard entry.resolvingSymlinksInPath().path.hasPrefix(root.path + "/") else {
                    throw UpdateFailure.invalidArchive
                }
            } else if metadata.isDirectory != true && metadata.isRegularFile != true {
                throw UpdateFailure.invalidArchive
            }
        }
        _ = try run("/usr/bin/codesign", ["--verify", "--strict", "--deep", app.path])
    }

    @discardableResult
    private static func run(_ executable: String, _ arguments: [String]) throws -> Data {
        let process = Process()
        let output = Pipe()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardOutput = output
        process.standardInput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw UpdateFailure.invalidArchive }
        return data
    }
}

// An updater request carries a per-process secret. Never forward it on a redirect.
final class UpdateServiceRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
