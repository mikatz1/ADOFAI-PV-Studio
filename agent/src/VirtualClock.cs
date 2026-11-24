using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace AdofaiRenderAgent
{
    public static class VirtualClock
    {
        public static bool Active = false;
        public static double StartDsp = 0.0;
        public static double CurrentDsp = 0.0;
        public static long SimFrameIndex = 0;
        public static int TargetFps = 60;
        private static int _lastSteppedUnityFrame = -1;
        public static bool DspGetterPatched = false;

        private static readonly FieldInfo _fiLastReported =
            typeof(scrConductor).GetField("lastReportedPlayheadPosition", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiPrevFrameTime =
            typeof(scrConductor).GetField("previousFrameTime", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

        public static double Dt
        {
            get { return 1.0 / (double)TargetFps; }
        }

        public static void Engage(int fps, double initialDsp)
        {
            TargetFps = fps < 1 ? 60 : fps;
            StartDsp = initialDsp;
            CurrentDsp = initialDsp;
            SimFrameIndex = 0;
            _lastSteppedUnityFrame = Time.frameCount;
            Active = true;

            Time.captureDeltaTime = 1.0f / (float)TargetFps;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = -1;
            Application.runInBackground = true;

            AgentLog.Info(string.Format("VirtualClock engaged: fps={0}, initialDsp={1:F6}, captureDeltaTime={2:F6}, dspGetterPatched={3}",
                TargetFps, initialDsp, Time.captureDeltaTime, DspGetterPatched));
        }

        public static void Disengage()
        {
            Active = false;
            Time.captureDeltaTime = 0f;
        }

        public static void StepFrameOnce()
        {
            if (!Active) return;
            int fc = Time.frameCount;
            if (fc == _lastSteppedUnityFrame) return;
            _lastSteppedUnityFrame = fc;
            SimFrameIndex++;
            CurrentDsp = StartDsp + ((double)SimFrameIndex / (double)TargetFps);
        }

        public static void SyncConductor(scrConductor cond)
        {
            if (!Active || cond == null) return;
            cond.dspTime = CurrentDsp;
            if (_fiLastReported != null)
                _fiLastReported.SetValue(cond, AudioSettings.dspTime);
            if (_fiPrevFrameTime != null)
                _fiPrevFrameTime.SetValue(cond, Time.unscaledTimeAsDouble);
        }

        public static void ApplyPatches(Harmony harmony)
        {
            // 1) Try patching AudioSettings.get_dspTime directly
            try
            {
                MethodInfo dspGetter = typeof(AudioSettings).GetProperty("dspTime", BindingFlags.Static | BindingFlags.Public).GetGetMethod();
                MethodInfo prefix = typeof(VirtualClock).GetMethod("AudioSettings_get_dspTime_Prefix", BindingFlags.Static | BindingFlags.NonPublic);
                harmony.Patch(dspGetter, new HarmonyMethod(prefix), null, null, null);
                DspGetterPatched = true;
                AgentLog.Info("Patched UnityEngine.AudioSettings.get_dspTime successfully.");
            }
            catch (Exception ex)
            {
                DspGetterPatched = false;
                AgentLog.Warn("Could not patch AudioSettings.get_dspTime directly (using scrConductor direct clock injection): " + ex.Message);
            }

            // 2) Always patch scrConductor.Update / Start / Rewind for rock-solid deterministic stepping
            try
            {
                MethodInfo condUpdate = typeof(scrConductor).GetMethod("Update", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                MethodInfo condStart = typeof(scrConductor).GetMethod("Start", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                MethodInfo condRewind = typeof(scrConductor).GetMethod("Rewind", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

                harmony.Patch(condUpdate,
                    new HarmonyMethod(typeof(VirtualClock).GetMethod("Conductor_Update_Prefix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null, null);
                harmony.Patch(condStart,
                    null,
                    new HarmonyMethod(typeof(VirtualClock).GetMethod("Conductor_Reset_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                harmony.Patch(condRewind,
                    null,
                    new HarmonyMethod(typeof(VirtualClock).GetMethod("Conductor_Reset_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                AgentLog.Info("Patched scrConductor Update/Start/Rewind.");
            }
            catch (Exception ex)
            {
                AgentLog.Error("Failed to patch scrConductor: " + ex);
            }
        }

        private static bool AudioSettings_get_dspTime_Prefix(ref double __result)
        {
            if (Active)
            {
                StepFrameOnce();
                __result = CurrentDsp;
                return false;
            }
            return true;
        }

        private static void Conductor_Update_Prefix(scrConductor __instance)
        {
            if (!Active) return;
            StepFrameOnce();
            // Guarantee scrConductor uses exact VirtualClock.CurrentDsp even if AudioSettings.dspTime icall isn't patched
            // or if window loses focus.
            __instance.dspTime = CurrentDsp;
            if (!DspGetterPatched)
            {
                if (_fiLastReported != null)
                    _fiLastReported.SetValue(__instance, AudioSettings.dspTime);
                if (_fiPrevFrameTime != null)
                    _fiPrevFrameTime.SetValue(__instance, Time.unscaledTimeAsDouble);
            }
        }

        private static void Conductor_Reset_Postfix(scrConductor __instance)
        {
            if (!Active) return;
            SyncConductor(__instance);
        }
    }
}
