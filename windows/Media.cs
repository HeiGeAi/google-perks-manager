using System.Text.Json;
using Windows.Media.Editing;
using Windows.Media.MediaProperties;
using Windows.Media.Transcoding;
using Windows.Storage;

namespace GooglePerksManager;

internal static class Media
{
    internal static async Task<int> RunAsync(string[] args)
    {
        try
        {
            if (args is ["probe", var source])
            {
                var file = await StorageFile.GetFileFromPathAsync(source);
                var video = await file.Properties.GetVideoPropertiesAsync();
                if (video.Width == 0 || video.Height == 0 || video.Duration <= TimeSpan.Zero)
                    throw new InvalidDataException();
                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    seconds = video.Duration.TotalSeconds, width = video.Width, height = video.Height,
                }));
            }
            else if (args is ["mux", var picture, var sound, var destination])
                await MuxAsync(picture, sound, destination);
            else
                return 1;
            return 0;
        }
        catch (Exception) { return 1; }
    }

    private static async Task MuxAsync(string picture, string sound, string destination)
    {
        var source = await StorageFile.GetFileFromPathAsync(picture);
        var audio = await StorageFile.GetFileFromPathAsync(sound);
        var clip = await MediaClip.CreateFromFileAsync(source);
        var narration = await BackgroundAudioTrack.CreateFromFileAsync(audio);
        if (clip.OriginalDuration.TotalSeconds < .1 || narration.OriginalDuration.TotalSeconds is < .1 or > 600)
            throw new InvalidDataException();

        var composition = new MediaComposition();
        var remaining = narration.OriginalDuration;
        while (remaining > TimeSpan.Zero)
        {
            var part = clip.Clone();
            part.Volume = 0;
            if (remaining < clip.OriginalDuration)
                part.TrimTimeFromEnd = clip.OriginalDuration - remaining;
            remaining -= remaining < clip.OriginalDuration ? remaining : clip.OriginalDuration;
            composition.Clips.Add(part);
        }
        composition.BackgroundAudioTracks.Add(narration);

        var folder = await StorageFolder.GetFolderFromPathAsync(Path.GetDirectoryName(destination)!);
        var output = await folder.CreateFileAsync(Path.GetFileName(destination), CreationCollisionOption.ReplaceExisting);
        var size = await source.Properties.GetVideoPropertiesAsync();
        if (size.Width == 0 || size.Height == 0) throw new InvalidDataException();
        var encoding = MediaEncodingProfile.CreateMp4(VideoEncodingQuality.HD720p);
        encoding.Video.Width = size.Width;
        encoding.Video.Height = size.Height;
        if (await composition.RenderToFileAsync(output, MediaTrimmingPreference.Precise, encoding)
            != TranscodeFailureReason.None) throw new IOException();
    }
}
