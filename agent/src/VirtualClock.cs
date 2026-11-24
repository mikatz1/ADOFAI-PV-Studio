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
        public static int LastConductorPostFrame = -1;
        public static int DeferredPlayerControlCount = 0;

        private static bool _runningDeferredPlayerControl = false;
        private static scrController _deferredController = null;
        private static int _deferredFrame = -1;

        private static readonly FieldInfo _fiLastReported =
            typeof(scrConductor).GetField("lastReportedPlayheadPosition", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiPrevFrameTime =
            typeof(scrConductor).GetField("previousFrameTime", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly MethodInfo _miPlayerControlUpdate =
            typeof(scrController).GetMethod("PlayerControl_Update", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly PropertyInfo _piPlayerChosenPlanet =
            typeof(scrPlayer).GetProperty("chosenPlanet", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

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
            LastConductorPostFrame = -1;
            DeferredPlayerControlCount = 0;
            _runningDeferredPlayerControl = false;
            _deferredController = null;
            _deferredFrame = -1;
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
                    new HarmonyMethod(typeof(VirtualClock).GetMethod("Conductor_Update_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                harmony.Patch(condStart,
                    null,
                    new HarmonyMethod(typeof(VirtualClock).GetMethod("Conductor_Reset_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                harmony.Patch(condRewind,
                    null,
                    new HarmonyMethod(typeof(VirtualClock).GetMethod("Conductor_Reset_Postfix", BindingFlags.Static | BindingFlags.NonPublic)),
                    null, null);
                if (_miPlayerControlUpdate != null)
                {
                    harmony.Patch(_miPlayerControlUpdate,
                        new HarmonyMethod(typeof(VirtualClock).GetMethod("PlayerControl_Update_Prefix", BindingFlags.Static | BindingFlags.NonPublic)),
                        null, null, null);
                }
                AgentLog.Info("Patched scrConductor Update/Start/Rewind + deterministic PlayerControl ordering.");
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
            UiAndQualityPatches.EnsureZeroCalibrationFast();
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

        private static void RefreshAllChosenPlanetAngles()
        {
            try
            {
                scrPlayerManager pm = ADOBase.playerManager;
                if (pm != null)
                {
                    foreach (scrPlayer pl in pm)
                    {
                        if (pl == null) continue;
                        scrPlanet chosen = null;
                        if (_piPlayerChosenPlanet != null)
                            chosen = _piPlayerChosenPlanet.GetValue(pl, null) as scrPlanet;
                        if (chosen != null)
                            chosen.Update_RefreshAngles();
                    }
                    return;
                }
            }
            catch { }

            try
            {
                scrController ctrl = scrController.instance;
                if (ctrl != null && ctrl.planetarySystem != null && ctrl.planetarySystem.chosenPlanet != null)
                    ctrl.planetarySystem.chosenPlanet.Update_RefreshAngles();
            }
            catch { }
        }

        private static bool PlayerControl_Update_Prefix(scrController __instance)
        {
            if (!Active || _runningDeferredPlayerControl) return true;
            int fc = Time.frameCount;
            scrConductor cond = scrConductor.instance;
            if (LastConductorPostFrame != fc && cond != null && cond.enabled && cond.gameObject.activeInHierarchy)
            {
                // scrController.Update ran before scrConductor.Update on this frame.
                // Defer until Conductor_Update_Postfix so Auto-play checks THIS frame's songposition_minusi
                // instead of lagging 1 frame behind (which causes visible planet overshoot past tiles at high BPM).
                _deferredController = __instance;
                _deferredFrame = fc;
                DeferredPlayerControlCount++;
                return false;
            }
            RefreshAllChosenPlanetAngles();
            return true;
        }

        private static void Conductor_Update_Postfix(scrConductor __instance)
        {
            if (!Active) return;
            int fc = Time.frameCount;
            LastConductorPostFrame = fc;
            if (_deferredController != null && _deferredFrame == fc && _miPlayerControlUpdate != null)
            {
                scrController ctrl = _deferredController;
                _deferredController = null;
                _deferredFrame = -1;
                if (ctrl != null && ctrl.gameObject != null && ctrl.gameObject.activeInHierarchy)
                {
                    RefreshAllChosenPlanetAngles();
                    _runningDeferredPlayerControl = true;
                    try
                    {
                        _miPlayerControlUpdate.Invoke(ctrl, null);
                    }
                    catch (Exception ex)
                    {
                        AgentLog.Warn("Deferred PlayerControl_Update error: " + ex.Message);
                    }
                    finally
                    {
                        _runningDeferredPlayerControl = false;
                    }
                }
            }
        }

        private static void Conductor_Reset_Postfix(scrConductor __instance)
        {
            if (!Active) return;
            SyncConductor(__instance);
        }
    }
}
