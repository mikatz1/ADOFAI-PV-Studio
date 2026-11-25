using System;
using System.IO;
using System.Text;
using UnityEngine;

namespace AdofaiRenderAgent
{
    [Serializable]
    public class RenderJobConfig
    {
        public string levelPath = "";
        public string videoOutputPath = "";
        public string audioTimelinePath = "";
        public string sfxCacheDir = "";
        public string progressPath = "";
        public string logPath = "";
        public string cancelSentinelPath = "";
        public string ffmpegPath = "ffmpeg";
        public string ffmpegArgs = "";

        public int width = 1920;
        public int height = 1080;
        public int fps = 60;

        public float startSec = 0f;
        public float endSec = 0f;
        public float endPaddingSec = 2.0f;

        public bool hideCountdown = true;
        public bool hideHitText = true;
        public bool flipVertical = true;
        public bool captureAudioClips = true;
        public int maxGpuUsagePercent = 90;
        public int maxRamUsagePercent = 80;

        // Planet color & opacity customization (default: pure #FF0000 & #0000FF at 100% opacity)
        public bool customPlanetColors = true;
        public string redPlanetColor = "#FF0000";
        public float redPlanetOpacity = 1.0f;
        public string bluePlanetColor = "#0000FF";
        public float bluePlanetOpacity = 1.0f;

        // Low-spec safety, experimental render optimizer & rough preview modes
        public bool lowSpecSafeMode = false;
        public bool experimentalOptimizeRender = false;
        public bool previewMode = false;
        public int startFloor = -1; // >0: start the level from this floor via the game's own checkpoint (play-from-here) path instead of simulating from 0
        public bool fastStart = true; // jump to a checkpoint floor shortly before startSec via official ResetScene+Play(ck, true) scrub
        public bool arcTails = true; // re-emit planet tail particles along the true orbit arc between ticks
        public int captureStride = 1; // 1 = capture every simulated frame (final render); >1 = rough preview decimation

        // Resident warm-session keep-alive (reuses loaded level/decorations/textures across proxy -> final render)
        public bool keepAliveAfterFinish = false;
        public string sessionDir = "";
        public int parentPid = 0;
        public int idleTimeoutSec = 300;
    }

    public static class AgentLog
    {
        private static readonly object _lock = new object();
        private static string _logPath = null;
        private static string _progressPath = null;

        public static void Init(string logPath, string progressPath)
        {
            _logPath = logPath;
            _progressPath = progressPath;
            try
            {
                if (!string.IsNullOrEmpty(_logPath))
                {
                    string dir = Path.GetDirectoryName(_logPath);
                    if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                        Directory.CreateDirectory(dir);
                    File.WriteAllText(_logPath, "", Encoding.UTF8);
                }
                if (!string.IsNullOrEmpty(_progressPath))
                {
                    string dir = Path.GetDirectoryName(_progressPath);
                    if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                        Directory.CreateDirectory(dir);
                    File.WriteAllText(_progressPath, "", Encoding.UTF8);
                }
            }
            catch { }
        }

        public static void Info(string msg)
        {
            Write("INFO", msg);
        }

        public static void Warn(string msg)
        {
            Write("WARN", msg);
        }

        public static void Error(string msg)
        {
            Write("ERROR", msg);
        }

        private static void Write(string level, string msg)
        {
            string line = string.Format("[{0:HH:mm:ss.fff}] [{1}] {2}", DateTime.Now, level, msg);
            lock (_lock)
            {
                if (!string.IsNullOrEmpty(_logPath))
                {
                    try
                    {
                        File.AppendAllText(_logPath, line + Environment.NewLine, Encoding.UTF8);
                    }
                    catch { }
                }
            }
        }

        public static void WriteProgress(string jsonLine)
        {
            lock (_lock)
            {
                if (!string.IsNullOrEmpty(_progressPath))
                {
                    try
                    {
                        File.AppendAllText(_progressPath, jsonLine + "\n", Encoding.UTF8);
                    }
                    catch { }
                }
            }
        }

        public static string EscapeJson(string s)
        {
            if (s == null) return "";
            StringBuilder sb = new StringBuilder(s.Length + 8);
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                switch (c)
                {
                    case '\\': sb.Append("\\\\"); break;
                    case '"': sb.Append("\\\""); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 32)
                            sb.AppendFormat("\\u{0:x4}", (int)c);
                        else
                            sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }
    }
}
