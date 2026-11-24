using System;
using System.IO;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace Doorstop
{
    public static class Entrypoint
    {
        private static string _agentDir;
        private static string _jobPath;
        private static bool _hooked = false;

        public static void Start()
        {
            try
            {
                _jobPath = Environment.GetEnvironmentVariable("ADOFAI_RENDER_JOB");
                if (string.IsNullOrEmpty(_jobPath) || !File.Exists(_jobPath))
                    return;

                _agentDir = Path.GetDirectoryName(typeof(Entrypoint).Assembly.Location);
                AppDomain.CurrentDomain.AssemblyResolve += OnAssemblyResolve;
                AppDomain.CurrentDomain.AssemblyLoad += OnAssemblyLoad;

                // In case Assembly-CSharp is already loaded
                Assembly[] loaded = AppDomain.CurrentDomain.GetAssemblies();
                for (int i = 0; i < loaded.Length; i++)
                {
                    if (loaded[i].GetName().Name == "Assembly-CSharp")
                    {
                        TryHookStartup();
                        break;
                    }
                }
            }
            catch (Exception ex)
            {
                WriteBootstrapError("Doorstop.Entrypoint.Start failed: " + ex);
            }
        }

        private static Assembly OnAssemblyResolve(object sender, ResolveEventArgs args)
        {
            try
            {
                string simpleName = new AssemblyName(args.Name).Name;
                if (!string.IsNullOrEmpty(_agentDir))
                {
                    string localPath = Path.Combine(_agentDir, simpleName + ".dll");
                    if (File.Exists(localPath))
                        return Assembly.LoadFrom(localPath);
                }

                // Fallback to Steam ADOFAI's UnityModManager folder for 0Harmony.dll
                string ummPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                    @"A Dance of Fire and Ice_Data\Managed\UnityModManager\" + simpleName + ".dll");
                if (File.Exists(ummPath))
                    return Assembly.LoadFrom(ummPath);
            }
            catch { }
            return null;
        }

        private static void OnAssemblyLoad(object sender, AssemblyLoadEventArgs args)
        {
            try
            {
                if (_hooked) return;
                if (args.LoadedAssembly.GetName().Name == "Assembly-CSharp")
                {
                    AppDomain.CurrentDomain.AssemblyLoad -= OnAssemblyLoad;
                    TryHookStartup();
                }
            }
            catch (Exception ex)
            {
                WriteBootstrapError("OnAssemblyLoad failed: " + ex);
            }
        }

        private static void TryHookStartup()
        {
            if (_hooked) return;
            _hooked = true;
            AdofaiRenderAgent.AgentBootstrap.InstallStartupHook(_jobPath);
        }

        private static void WriteBootstrapError(string msg)
        {
            try
            {
                string p = Path.Combine(_agentDir ?? Path.GetTempPath(), "doorstop_bootstrap_error.log");
                File.AppendAllText(p, DateTime.Now.ToString("O") + " " + msg + Environment.NewLine);
            }
            catch { }
        }
    }
}

namespace AdofaiRenderAgent
{
    public static class AgentBootstrap
    {
        private static string _jobPath;
        private static Harmony _harmony;
        private static bool _started = false;

        public static void InstallStartupHook(string jobPath)
        {
            _jobPath = jobPath;
            try
            {
                _harmony = new Harmony("com.adofai.offline.renderer");
                MethodInfo startup = typeof(ADOStartup).GetMethod("Startup", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                MethodInfo postfix = typeof(AgentBootstrap).GetMethod("ADOStartup_Postfix", BindingFlags.Static | BindingFlags.NonPublic);
                _harmony.Patch(startup, null, new HarmonyMethod(postfix), null, null);
            }
            catch (Exception ex)
            {
                try
                {
                    string dir = Path.GetDirectoryName(jobPath);
                    File.AppendAllText(Path.Combine(dir, "bootstrap_error.log"), ex.ToString() + Environment.NewLine);
                }
                catch { }
            }
        }

        private static void ADOStartup_Postfix()
        {
            if (_started) return;
            _started = true;
            try
            {
                // Immediately mute local speakers before any splash/menu audio can play
                AudioListener.volume = 0f;

                string json = File.ReadAllText(_jobPath);
                RenderJobConfig cfg = JsonUtility.FromJson<RenderJobConfig>(json);
                if (cfg == null)
                    throw new Exception("Failed to parse RenderJobConfig JSON from " + _jobPath);

                AgentLog.Init(cfg.logPath, cfg.progressPath);
                AgentLog.Info("=== AdofaiRenderAgent Starting ===");
                AgentLog.Info("Level: " + cfg.levelPath);
                AgentLog.Info(string.Format("Target: {0}x{1} @ {2}fps, trim=[{3:F2}s, {4:F2}s], endPad={5:F2}s",
                    cfg.width, cfg.height, cfg.fps, cfg.startSec, cfg.endSec, cfg.endPaddingSec));
                AgentLog.Info("Unity version: " + Application.unityVersion + ", GPU: " + SystemInfo.graphicsDeviceName + " (" + SystemInfo.graphicsDeviceType + ")");

                RenderAgentBehaviour.Config = cfg;
                AudioTimelineExporter.Init(cfg.sfxCacheDir, cfg.captureAudioClips);

                VirtualClock.ApplyPatches(_harmony);
                AudioTimelineExporter.ApplyPatches(_harmony);
                UiAndQualityPatches.ApplyPatches(_harmony, cfg);

                GameObject go = new GameObject("AdofaiRenderAgentHost");
                UnityEngine.Object.DontDestroyOnLoad(go);
                go.AddComponent<RenderAgentBehaviour>();
            }
            catch (Exception ex)
            {
                AgentLog.Error("ADOStartup_Postfix failed: " + ex);
            }
        }
    }
}
