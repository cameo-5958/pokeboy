// Offline Windows Media fallback for make_boot_rom.py. Emits 30 full-LCD Gray8 frames.
using System;
using System.IO;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Threading.Tasks;
using Windows.Graphics.Imaging;
using Windows.Media.Editing;
using Windows.Media.MediaProperties;
using Windows.Storage;

internal static class ExtractBootFramesWindows
{
    private const int Width = 160;
    private const int Height = 144;
    private const int FrameCount = 30;

    private static void Main(string[] args)
    {
        if (args.Length != 2)
            throw new ArgumentException("usage: extract_boot_frames_windows <video> <gray-output>");
        Run(args[0], args[1]).GetAwaiter().GetResult();
    }

    private static async Task Run(string input, string output)
    {
        StorageFile file = await StorageFile.GetFileFromPathAsync(Path.GetFullPath(input));
        MediaClip clip = await MediaClip.CreateFromFileAsync(file);
        MediaComposition composition = new MediaComposition();
        composition.Clips.Add(clip);
        VideoEncodingProperties properties = clip.GetVideoEncodingProperties();
        double sourceAspect = (double)properties.Width / properties.Height;
        int requestWidth = sourceAspect > (double)Width / Height
            ? (int)Math.Ceiling(Height * sourceAspect)
            : Width;
        int requestHeight = sourceAspect > (double)Width / Height
            ? Height
            : (int)Math.Ceiling(Width / sourceAspect);
        using (FileStream destination = File.Create(output))
        {
            for (int frame = 0; frame < FrameCount; frame++)
            {
                TimeSpan timestamp = TimeSpan.FromSeconds(frame / 6.0);
                using (var thumbnail = await composition.GetThumbnailAsync(
                    timestamp, requestWidth, requestHeight, VideoFramePrecision.NearestFrame))
                {
                    BitmapDecoder decoder = await BitmapDecoder.CreateAsync(thumbnail);
                    PixelDataProvider provider = await decoder.GetPixelDataAsync(
                        BitmapPixelFormat.Bgra8,
                        BitmapAlphaMode.Straight,
                        new BitmapTransform(),
                        ExifOrientationMode.IgnoreExifOrientation,
                        ColorManagementMode.DoNotColorManage);
                    byte[] source = provider.DetachPixelData();
                    int width = (int)decoder.PixelWidth;
                    int height = (int)decoder.PixelHeight;
                    byte[] padded = new byte[Width * Height];
                    int left = Math.Max(0, (Width - width) / 2);
                    int top = Math.Max(0, (Height - height) / 2);
                    int copyWidth = Math.Min(width, Width);
                    int copyHeight = Math.Min(height, Height);
                    int sourceLeft = Math.Max(0, (width - copyWidth) / 2);
                    int sourceTop = Math.Max(0, (height - copyHeight) / 2);
                    for (int y = 0; y < copyHeight; y++)
                    {
                        for (int x = 0; x < copyWidth; x++)
                        {
                            int sourceOffset = ((sourceTop + y) * width + sourceLeft + x) * 4;
                            int blue = source[sourceOffset];
                            int green = source[sourceOffset + 1];
                            int red = source[sourceOffset + 2];
                            padded[(top + y) * Width + left + x] =
                                (byte)((red * 77 + green * 150 + blue * 29) >> 8);
                        }
                    }
                    await destination.WriteAsync(padded, 0, padded.Length);
                }
            }
        }
    }
}
