import AVFoundation
import Foundation

enum MediaError: Error {
    case invalidInput
    case exportFailed
}

@main struct MediaTool {
    static func main() async {
        do {
            let args = CommandLine.arguments
            switch (args.count, args.dropFirst().first ?? "") {
            case (3, "probe"):
                try await probe(URL(fileURLWithPath: args[2]))
            case (5, "mux"):
                try await mux(
                    video: URL(fileURLWithPath: args[2]),
                    audio: URL(fileURLWithPath: args[3]),
                    output: URL(fileURLWithPath: args[4])
                )
            default:
                throw MediaError.invalidInput
            }
        } catch {
            FileHandle.standardError.write(Data("媒体格式无法读取或导出。\n".utf8))
            exit(1)
        }
    }

    static func probe(_ url: URL) async throws {
        let asset = AVURLAsset(url: url)
        guard let track = try await asset.loadTracks(withMediaType: .video).first,
              try await asset.load(.isPlayable) else { throw MediaError.invalidInput }
        let length = try await asset.load(.duration).seconds
        let size = try await track.load(.naturalSize)
        guard length.isFinite, length > 0, size.width >= 1, size.height >= 1 else { throw MediaError.invalidInput }
        let payload: [String: Any] = [
            "seconds": length, "width": Int(size.width), "height": Int(size.height)
        ]
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: payload))
    }

    static func mux(video: URL, audio: URL, output: URL) async throws {
        let movie = AVURLAsset(url: video)
        let sound = AVURLAsset(url: audio)
        guard let picture = try await movie.loadTracks(withMediaType: .video).first,
              let narration = try await sound.loadTracks(withMediaType: .audio).first else {
            throw MediaError.invalidInput
        }
        let pictureRange = try await picture.load(.timeRange)
        let narrationRange = try await narration.load(.timeRange)
        let target = narrationRange.duration
        guard pictureRange.duration.seconds.isFinite, pictureRange.duration.seconds >= 0.1,
              target.seconds.isFinite, target.seconds >= 0.1, target.seconds <= 600 else {
            throw MediaError.invalidInput
        }
        let mix = AVMutableComposition()
        guard let videoTrack = mix.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid),
              let audioTrack = mix.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw MediaError.invalidInput
        }
        videoTrack.preferredTransform = try await picture.load(.preferredTransform)
        try audioTrack.insertTimeRange(narrationRange, of: narration, at: .zero)
        var offset = CMTime.zero
        while CMTimeCompare(offset, target) < 0 {
            let length = CMTimeMinimum(pictureRange.duration, CMTimeSubtract(target, offset))
            try videoTrack.insertTimeRange(CMTimeRange(start: pictureRange.start, duration: length), of: picture, at: offset)
            offset = CMTimeAdd(offset, length)
        }
        guard let exporter = AVAssetExportSession(asset: mix, presetName: AVAssetExportPresetHighestQuality),
              exporter.supportedFileTypes.contains(.mp4) else { throw MediaError.exportFailed }
        exporter.shouldOptimizeForNetworkUse = true
        if #available(macOS 15.0, *) {
            try await exporter.export(to: output, as: .mp4)
        } else {
            try await exportLegacy(exporter, output: output)
        }
    }

    @available(macOS, introduced: 14.0, obsoleted: 15.0)
    static func exportLegacy(_ export: AVAssetExportSession, output: URL) async throws {
        export.outputURL = output
        export.outputFileType = .mp4
        await export.export()
        guard export.status == .completed else { throw MediaError.exportFailed }
    }
}
