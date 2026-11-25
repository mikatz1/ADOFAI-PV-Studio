using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using HarmonyLib;
using UnityEngine;

namespace AdofaiRenderAgent
{
    public class RecordedAudioEvent
    {
        public string clipName;
        public string wavPath;
        public double timeDsp;
        public double endTimeDsp;
        public float volume;
        public float pitch;
        public string category; // "hitsound", "holdsound", "countdown", "sfx"
    }

    public static class AudioTimelineExporter
    {
        private static readonly object _lock = new object();
        private static readonly List<RecordedAudioEvent> _events = new List<RecordedAudioEvent>();
        private static readonly HashSet<string> _dedupKeys = new HashSet<string>();
        private static readonly Dictionary<string, string> _exportedClipPaths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public static string SfxCacheDir = "";
        public static bool CaptureEnabled = true;

        public static double CaptureStartDsp = 0.0;
        public static double CaptureEndDsp = 0.0;
        public static double LevelStartDsp = 0.0;
        public static double SongStartDsp = 0.0;
        public static double DspTimeSong = 0.0;
        public static double AddOffset = 0.0;
        public static float SongPitch = 1.0f;
        public static float SongVolume = 1.0f;
        public static float Bpm = 100f;
        public static string SongFilePath = "";
        public static int TotalCapturedFrames = 0;
        public static int Fps = 60;
        public static bool HasSongSchedule = false;
        public static bool PlayHitTimesCaptured = false;

        private static readonly FieldInfo _fiHitSoundsData =
            typeof(scrConductor).GetField("hitSoundsData", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiHoldSoundsData =
            typeof(scrConductor).GetField("holdSoundsData", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiExtraTicks =
            typeof(scrConductor).GetField("extraTicksCountdown", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

        public static void Init(string sfxCacheDir, bool captureEnabled)
        {
            SfxCacheDir = sfxCacheDir;
            CaptureEnabled = captureEnabled;
            CaptureStartDsp = 0.0;
            CaptureEndDsp = 0.0;
            LevelStartDsp = 0.0;
            SongStartDsp = 0.0;
            DspTimeSong = 0.0;
            AddOffset = 0.0;
            SongPitch = 1.0f;
            SongVolume = 1.0f;
            Bpm = 100f;
            SongFilePath = "";
            TotalCapturedFrames = 0;
            HasSongSchedule = false;
            PlayHitTimesCaptured = false;
            lock (_lock)
            {
                _events.Clear();
                _dedupKeys.Clear();
                _exportedClipPaths.Clear();
            }
            if (!string.IsNullOrEmpty(SfxCacheDir) && !Directory.Exists(SfxCacheDir))
            {
                try { Directory.CreateDirectory(SfxCacheDir); } catch { }
            }
        }

        public static void ApplyPatches(Harmony harmony)
        {
            try
            {
                MethodInfo playHitTimes = typeof(scrConductor).GetMethod("PlayHitTimes", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                harmony.Patch(playHitTimes, null,
                    new HarmonyMethod(typeof(AudioTimelineExporter).GetMethod("PlayHitTimes_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                AgentLog.Info("Patched scrConductor.PlayHitTimes.");
            }
            catch (Exception ex)
            {
                AgentLog.Error("Failed to patch scrConductor.PlayHitTimes: " + ex);
            }

            try
            {
                MethodInfo amPlay = typeof(AudioManager).GetMethod("_Play", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                harmony.Patch(amPlay, null,
                    new HarmonyMethod(typeof(AudioTimelineExporter).GetMethod("AudioManager_Play_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                AgentLog.Info("Patched AudioManager._Play.");
            }
            catch (Exception ex)
            {
                AgentLog.Warn("Failed to patch AudioManager._Play: " + ex.Message);
            }

            try
            {
                MethodInfo playEnd = typeof(scrConductor).GetMethod("PlayWithEndTime", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                harmony.Patch(playEnd, null,
                    new HarmonyMethod(typeof(AudioTimelineExporter).GetMethod("Conductor_PlayWithEndTime_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                AgentLog.Info("Patched scrConductor.PlayWithEndTime.");
            }
            catch (Exception ex)
            {
                AgentLog.Warn("Failed to patch scrConductor.PlayWithEndTime: " + ex.Message);
            }
        }

        private static void PlayHitTimes_Postfix(scrConductor __instance)
        {
            try
            {
                CaptureConductorTiming(__instance);
                PlayHitTimesCaptured = true;

                if (!CaptureEnabled) return;

                int hitCount = 0;
                int holdCount = 0;
                int tickCount = 0;

                if (_fiHitSoundsData != null)
                {
                    object rawList = _fiHitSoundsData.GetValue(__instance);
                    System.Collections.IList list = rawList as System.Collections.IList;
                    if (list != null)
                    {
                        for (int i = 0; i < list.Count; i++)
                        {
                            object item = list[i];
                            Type t = item.GetType();
                            object hsVal = t.GetField("hitSound").GetValue(item);
                            double time = (double)t.GetField("time").GetValue(item);
                            float vol = (float)t.GetField("volume").GetValue(item);
                            string hsName = hsVal.ToString();
                            if (hsName == "None") continue;
                            string clipName = "snd" + hsName;
                            RecordEvent(clipName, null, time, -1.0, vol, 1.0f, "hitsound");
                            hitCount++;
                        }
                    }
                }

                if (_fiHoldSoundsData != null)
                {
                    object rawList = _fiHoldSoundsData.GetValue(__instance);
                    System.Collections.IList list = rawList as System.Collections.IList;
                    if (list != null)
                    {
                        for (int i = 0; i < list.Count; i++)
                        {
                            object item = list[i];
                            Type t = item.GetType();
                            string name = (string)t.GetField("name").GetValue(item);
                            double time = (double)t.GetField("time").GetValue(item);
                            double endTime = (double)t.GetField("endTime").GetValue(item);
                            float vol = (float)t.GetField("volume").GetValue(item);
                            if (string.IsNullOrEmpty(name)) continue;
                            RecordEvent(name, null, time, endTime, vol, 1.0f, "holdsound");
                            holdCount++;
                        }
                    }
                }

                if (_fiExtraTicks != null)
                {
                    object rawList = _fiExtraTicks.GetValue(__instance);
                    System.Collections.IList list = rawList as System.Collections.IList;
                    if (list != null)
                    {
                        for (int i = 0; i < list.Count; i++)
                        {
                            object item = list[i];
                            Type t = item.GetType();
                            double time = (double)t.GetField("time").GetValue(item);
                            RecordEvent("sndHat", null, time, -1.0, __instance.hitSoundVolume, 1.0f, "countdown");
                            tickCount++;
                        }
                    }
                }

                AgentLog.Info(string.Format("PlayHitTimes captured: hits={0}, holds={1}, extraTicks={2}, songStartDsp={3:F6}, dspTimeSong={4:F6}, pitch={5:F4}, addoffset={6:F6}",
                    hitCount, holdCount, tickCount, SongStartDsp, DspTimeSong, SongPitch, AddOffset));
            }
            catch (Exception ex)
            {
                AgentLog.Error("PlayHitTimes_Postfix error: " + ex);
            }
        }

        public static void CaptureConductorTiming(scrConductor cond)
        {
            if (cond == null) return;
            DspTimeSong = cond.dspTimeSong;
            AddOffset = cond.addoffset;
            Bpm = cond.bpm;
            float pitch = (cond.song != null && cond.song.pitch > 0.001f) ? cond.song.pitch : 1.0f;
            SongPitch = pitch;

            double extraCountdown = cond.separateCountdownTime
                ? (cond.crotchetAtStart / (double)pitch * (double)cond.adjustedCountdownTicks)
                : 0.0;
            SongStartDsp = cond.dspTimeSong + extraCountdown;
            HasSongSchedule = true;

            if (scnGame.instance != null && scnGame.instance.levelData != null)
            {
                try
                {
                    ADOFAI.LevelData ld = scnGame.instance.levelData;
                    string songName = ld.songFilename;
                    if (!string.IsNullOrEmpty(songName) && !string.IsNullOrEmpty(scnGame.instance.levelPath))
                    {
                        string dir = Path.GetDirectoryName(scnGame.instance.levelPath);
                        SongFilePath = Path.Combine(dir, songName);
                    }
                    SongVolume = ld.volume / 100.0f;
                }
                catch { }
            }
        }

        private static void AudioManager_Play_Postfix(string snd, double time, float volume, AudioSource __result)
        {
            if (__result != null)
            {
                try { __result.mute = true; __result.volume = 0f; } catch { }
            }
            if (!CaptureEnabled || !VirtualClock.Active) return;
            if (string.IsNullOrEmpty(snd)) return;
            try
            {
                AudioClip clip = (__result != null) ? __result.clip : null;
                float pitch = (__result != null) ? __result.pitch : 1.0f;
                string cat = snd.StartsWith("snd", StringComparison.OrdinalIgnoreCase) ? "hitsound" : "sfx";
                if (snd == "sndCymbalCrash") cat = "cymbal";
                RecordEvent(snd, clip, time, -1.0, volume, pitch, cat);
            }
            catch { }
        }

        private static void Conductor_PlayWithEndTime_Postfix(string snd, double time, double endTime, float volume)
        {
            if (!CaptureEnabled || !VirtualClock.Active) return;
            if (string.IsNullOrEmpty(snd)) return;
            try
            {
                RecordEvent(snd, null, time, endTime, volume, 1.0f, "holdsound");
            }
            catch { }
        }

        private static void RecordEvent(string clipName, AudioClip clip, double timeDsp, double endTimeDsp, float volume, float pitch, string category)
        {
            if (string.IsNullOrEmpty(clipName) || volume <= 0.0001f) return;
            string dedup = string.Format(CultureInfo.InvariantCulture, "{0}|{1:F4}|{2:F4}", clipName, timeDsp, endTimeDsp);
            lock (_lock)
            {
                if (_dedupKeys.Contains(dedup)) return;
                _dedupKeys.Add(dedup);
            }

            string wavPath = EnsureClipExported(clipName, clip);
            RecordedAudioEvent ev = new RecordedAudioEvent();
            ev.clipName = clipName;
            ev.wavPath = wavPath ?? "";
            ev.timeDsp = timeDsp;
            ev.endTimeDsp = endTimeDsp;
            ev.volume = volume;
            ev.pitch = pitch;
            ev.category = category;

            lock (_lock)
            {
                _events.Add(ev);
            }
        }

        private static string EnsureClipExported(string clipName, AudioClip clip)
        {
            if (string.IsNullOrEmpty(SfxCacheDir)) return null;
            lock (_lock)
            {
                string existing;
                if (_exportedClipPaths.TryGetValue(clipName, out existing))
                    return existing;
            }

            string safeName = SanitizeFileName(clipName);
            string wavPath = Path.Combine(SfxCacheDir, safeName + ".wav");
            if (File.Exists(wavPath) && new FileInfo(wavPath).Length > 44)
            {
                lock (_lock) { _exportedClipPaths[clipName] = wavPath; }
                return wavPath;
            }

            try
            {
                if (clip == null && AudioManager.Instance != null)
                {
                    if (AudioManager.Instance.audioLib != null && AudioManager.Instance.audioLib.ContainsKey(clipName))
                        clip = AudioManager.Instance.audioLib[clipName];
                    if (clip == null)
                        clip = AudioManager.Instance.FindOrLoadAudioClip(clipName, null, false);
                }

                if (clip == null)
                {
                    AgentLog.Warn("AudioClip not found for export: " + clipName);
                    return null;
                }

                if (clip.loadState == AudioDataLoadState.Unloaded)
                    clip.LoadAudioData();

                int sampleCount = clip.samples;
                int channels = clip.channels;
                int freq = clip.frequency;
                if (sampleCount <= 0 || channels <= 0 || freq <= 0)
                    return null;

                float[] data = new float[sampleCount * channels];
                if (!clip.GetData(data, 0))
                {
                    AgentLog.Warn("AudioClip.GetData failed for: " + clipName);
                    return null;
                }

                WriteWav16(wavPath, data, channels, freq);
                lock (_lock) { _exportedClipPaths[clipName] = wavPath; }
                AgentLog.Info(string.Format("Exported real AudioClip '{0}' -> {1} ({2}Hz, {3}ch, {4} samples)",
                    clipName, wavPath, freq, channels, sampleCount));
                return wavPath;
            }
            catch (Exception ex)
            {
                AgentLog.Warn("Failed to export AudioClip '" + clipName + "': " + ex.Message);
                return null;
            }
        }

        private static string SanitizeFileName(string name)
        {
            char[] invalid = Path.GetInvalidFileNameChars();
            StringBuilder sb = new StringBuilder(name.Length);
            for (int i = 0; i < name.Length; i++)
            {
                char c = name[i];
                bool bad = false;
                for (int j = 0; j < invalid.Length; j++)
                {
                    if (c == invalid[j]) { bad = true; break; }
                }
                sb.Append(bad ? '_' : c);
            }
            return sb.ToString();
        }

        private static void WriteWav16(string path, float[] samples, int channels, int sampleRate)
        {
            int totalSamples = samples.Length;
            int dataBytes = totalSamples * 2;
            using (FileStream fs = new FileStream(path, FileMode.Create, FileAccess.Write))
            using (BinaryWriter bw = new BinaryWriter(fs))
            {
                bw.Write(Encoding.ASCII.GetBytes("RIFF"));
                bw.Write(36 + dataBytes);
                bw.Write(Encoding.ASCII.GetBytes("WAVE"));
                bw.Write(Encoding.ASCII.GetBytes("fmt "));
                bw.Write(16);
                bw.Write((short)1); // PCM
                bw.Write((short)channels);
                bw.Write(sampleRate);
                bw.Write(sampleRate * channels * 2);
                bw.Write((short)(channels * 2));
                bw.Write((short)16);
                bw.Write(Encoding.ASCII.GetBytes("data"));
                bw.Write(dataBytes);

                byte[] pcm = new byte[dataBytes];
                for (int i = 0; i < totalSamples; i++)
                {
                    float v = samples[i];
                    if (v > 1.0f) v = 1.0f;
                    else if (v < -1.0f) v = -1.0f;
                    short s = (short)Math.Round(v * 32767.0f);
                    pcm[i * 2] = (byte)(s & 0xFF);
                    pcm[i * 2 + 1] = (byte)((s >> 8) & 0xFF);
                }
                bw.Write(pcm);
            }
        }

        public static void WriteTimelineJson(string path)
        {
            if (string.IsNullOrEmpty(path)) return;
            try
            {
                // Make one final pass to ensure every recorded event has its wavPath exported
                List<RecordedAudioEvent> snapshot;
                lock (_lock)
                {
                    snapshot = new List<RecordedAudioEvent>(_events);
                }
                for (int i = 0; i < snapshot.Count; i++)
                {
                    if (string.IsNullOrEmpty(snapshot[i].wavPath))
                        snapshot[i].wavPath = EnsureClipExported(snapshot[i].clipName, null) ?? "";
                }

                StringBuilder sb = new StringBuilder(65536);
                CultureInfo inv = CultureInfo.InvariantCulture;
                sb.AppendLine("{");
                sb.AppendFormat(inv, "  \"fps\": {0},\n", Fps);
                sb.AppendFormat(inv, "  \"totalCapturedFrames\": {0},\n", TotalCapturedFrames);
                sb.AppendFormat(inv, "  \"videoDurationSec\": {0:F6},\n", (double)TotalCapturedFrames / (double)Math.Max(1, Fps));
                sb.AppendFormat(inv, "  \"levelStartDsp\": {0:F8},\n", LevelStartDsp);
                sb.AppendFormat(inv, "  \"captureStartDsp\": {0:F8},\n", CaptureStartDsp);
                sb.AppendFormat(inv, "  \"captureEndDsp\": {0:F8},\n", CaptureEndDsp);
                sb.AppendFormat(inv, "  \"dspTimeSong\": {0:F8},\n", DspTimeSong);
                sb.AppendFormat(inv, "  \"songStartDsp\": {0:F8},\n", SongStartDsp);
                sb.AppendFormat(inv, "  \"songStartInVideoSec\": {0:F8},\n", SongStartDsp - CaptureStartDsp);
                sb.AppendFormat(inv, "  \"addOffset\": {0:F8},\n", AddOffset);
                sb.AppendFormat(inv, "  \"bpm\": {0:F4},\n", Bpm);
                sb.AppendFormat(inv, "  \"songPitch\": {0:F6},\n", SongPitch);
                sb.AppendFormat(inv, "  \"songVolume\": {0:F6},\n", SongVolume);
                sb.AppendFormat("  \"songFilePath\": \"{0}\",\n", AgentLog.EscapeJson(SongFilePath));
                sb.AppendLine("  \"events\": [");
                for (int i = 0; i < snapshot.Count; i++)
                {
                    RecordedAudioEvent ev = snapshot[i];
                    double relTime = ev.timeDsp - CaptureStartDsp;
                    double relEnd = ev.endTimeDsp > 0.0 ? (ev.endTimeDsp - CaptureStartDsp) : -1.0;
                    sb.AppendFormat(inv,
                        "    {{\"clip\":\"{0}\",\"wav\":\"{1}\",\"timeSec\":{2:F6},\"endTimeSec\":{3:F6},\"volume\":{4:F4},\"pitch\":{5:F4},\"category\":\"{6}\"}}{7}\n",
                        AgentLog.EscapeJson(ev.clipName),
                        AgentLog.EscapeJson(ev.wavPath),
                        relTime,
                        relEnd,
                        ev.volume,
                        ev.pitch,
                        AgentLog.EscapeJson(ev.category),
                        (i + 1 < snapshot.Count) ? "," : "");
                }
                sb.AppendLine("  ]");
                sb.AppendLine("}");

                string dir = Path.GetDirectoryName(path);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
                    Directory.CreateDirectory(dir);
                File.WriteAllText(path, sb.ToString(), Encoding.UTF8);
                AgentLog.Info(string.Format("Wrote audio timeline JSON ({0} events) -> {1}", snapshot.Count, path));
            }
            catch (Exception ex)
            {
                AgentLog.Error("WriteTimelineJson failed: " + ex);
            }
        }
    }
}
