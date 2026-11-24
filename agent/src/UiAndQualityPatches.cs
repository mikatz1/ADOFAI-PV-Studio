using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Reflection.Emit;
using HarmonyLib;
using UnityEngine;

namespace AdofaiRenderAgent
{
    public static class UiAndQualityPatches
    {
        public static bool ResolutionOverrideActive = false;
        public static bool FastSeekSkipRender = false;
        public static int TargetWidth = 1920;
        public static int TargetHeight = 1080;
        public static bool HideCountdown = true;
        public static bool HideHitText = true;
        public static bool ShouldTriggerStart = false;
        public static bool ScreenPatched = false;

        private static bool _savedCamStates = false;
        private static bool _origCamObjEnabled = true;
        private static bool _origBgCamEnabled = true;
        private static bool _origBgStaticEnabled = true;
        private static bool _origOverlayEnabled = true;

        // Planet color & opacity configuration
        public static bool CustomPlanetColorsEnabled = true;
        public static Color RedPlanetColor = new Color(1f, 0f, 0f, 1f);
        public static Color BluePlanetColor = new Color(0f, 0f, 1f, 1f);
        public static bool RedIsPureDefault = true;
        public static bool BlueIsPureDefault = true;

        private static readonly FieldInfo _fiCamRT =
            typeof(scrCamera).GetField("camRT", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiUseRTCam =
            typeof(scrCamera).GetField("useRTCam", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiCamQuadMesh =
            typeof(scrCamera).GetField("camQuadMesh", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiDisableCongrats =
            typeof(scrController).GetField("disableCongratsMessage", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

        private static readonly MethodInfo _miScreenWidth =
            typeof(Screen).GetProperty("width", BindingFlags.Static | BindingFlags.Public).GetGetMethod();
        private static readonly MethodInfo _miScreenHeight =
            typeof(Screen).GetProperty("height", BindingFlags.Static | BindingFlags.Public).GetGetMethod();
        private static readonly MethodInfo _miEffectiveWidth =
            typeof(UiAndQualityPatches).GetMethod("GetEffectiveWidth", BindingFlags.Static | BindingFlags.Public);
        private static readonly MethodInfo _miEffectiveHeight =
            typeof(UiAndQualityPatches).GetMethod("GetEffectiveHeight", BindingFlags.Static | BindingFlags.Public);

        public static int GetEffectiveWidth()
        {
            return ResolutionOverrideActive ? TargetWidth : Screen.width;
        }

        public static int GetEffectiveHeight()
        {
            return ResolutionOverrideActive ? TargetHeight : Screen.height;
        }

        private static Color ParseHexColor(string hex, float opacity, Color fallback)
        {
            float a = Mathf.Clamp01(opacity);
            if (string.IsNullOrEmpty(hex))
                return new Color(fallback.r, fallback.g, fallback.b, a);
            string s = hex.Trim();
            if (s.StartsWith("#", StringComparison.Ordinal))
                s = s.Substring(1);
            if (s.Length == 6)
            {
                int r, g, b;
                if (int.TryParse(s.Substring(0, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out r) &&
                    int.TryParse(s.Substring(2, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out g) &&
                    int.TryParse(s.Substring(4, 2), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out b))
                {
                    return new Color(r / 255f, g / 255f, b / 255f, a);
                }
            }
            return new Color(fallback.r, fallback.g, fallback.b, a);
        }

        private static bool IsHexMatch(string hex, string expected)
        {
            if (string.IsNullOrEmpty(hex)) return true;
            string s = hex.Trim().ToUpperInvariant();
            if (!s.StartsWith("#", StringComparison.Ordinal)) s = "#" + s;
            return s == expected;
        }

        public static void ApplyPatches(Harmony harmony, RenderJobConfig cfg)
        {
            TargetWidth = cfg.width;
            TargetHeight = cfg.height;
            HideCountdown = cfg.hideCountdown;
            HideHitText = cfg.hideHitText;

            CustomPlanetColorsEnabled = cfg.customPlanetColors;
            RedPlanetColor = ParseHexColor(cfg.redPlanetColor, cfg.redPlanetOpacity, Color.red);
            BluePlanetColor = ParseHexColor(cfg.bluePlanetColor, cfg.bluePlanetOpacity, Color.blue);
            RedIsPureDefault = IsHexMatch(cfg.redPlanetColor, "#FF0000") && RedPlanetColor.a >= 0.999f;
            BlueIsPureDefault = IsHexMatch(cfg.bluePlanetColor, "#0000FF") && BluePlanetColor.a >= 0.999f;

            AgentLog.Info(string.Format(CultureInfo.InvariantCulture,
                "Planet color config: custom={0}, red=({1:F3},{2:F3},{3:F3},a={4:F2},def={5}), blue=({6:F3},{7:F3},{8:F3},a={9:F2},def={10})",
                CustomPlanetColorsEnabled,
                RedPlanetColor.r, RedPlanetColor.g, RedPlanetColor.b, RedPlanetColor.a, RedIsPureDefault,
                BluePlanetColor.r, BluePlanetColor.g, BluePlanetColor.b, BluePlanetColor.a, BlueIsPureDefault));

            // 1) Manage scrCamera.camRT directly (since Screen.width/height are Unity 6 native icalls)
            TryPatchGetter(harmony, typeof(scrCamera), "camRTNeedsRecreation", "CamRTNeedsRecreation_Prefix", null);
            TryPatchMethods(harmony, typeof(scrCamera), "Update", null, "ScrCamera_Update_Postfix");

            // Transpile CameraFilterPack* OnRenderImage methods that call Screen.width / Screen.height
            TranspileCameraFilterPackScreenSize(harmony);

            // 2) Force maximum visual quality & full VFX without modifying saved prefs
            TryPatchGetter(harmony, typeof(Persistence), "visualQuality", "Persistence_VisualQuality_Prefix", null);
            TryPatchGetter(harmony, typeof(Persistence), "visualEffects", "Persistence_VisualEffects_Prefix", null);
            TryPatchGetter(harmony, typeof(Persistence), "skipIntroBehavior", "Persistence_SkipIntro_Prefix", null);
            TryPatchGetter(harmony, typeof(Persistence), "hitSoundVolume", "Persistence_HitSoundVolume_Prefix", null);

            // 2b) Planet color & opacity hooks (cleanly bypasses player saved cosmetics without mutating save file)
            if (CustomPlanetColorsEnabled)
            {
                TryPatchMethods(harmony, typeof(Persistence), "GetPlayerColor", "Persistence_GetPlayerColor_Prefix", null);
                TryPatchMethods(harmony, typeof(Persistence), "GetSamuraiMode", "ReturnFalseBool_Prefix", null);
                TryPatchMethods(harmony, typeof(Persistence), "GetEmojiMode", "ReturnFalseBool_Prefix", null);
                TryPatchMethods(harmony, typeof(PlanetRenderer), "LoadPlanetColor", null, "PlanetRenderer_LoadPlanetColor_Postfix");
                TryPatchMethods(harmony, typeof(PlanetarySystem), "ApplyMultiplanetColors", null, "PlanetarySystem_ApplyMultiplanetColors_Postfix");
            }

            // 3) Zero all device calibration offsets & disable async input so preview and final renders default strictly to 0ms offset
            TryPatchGetter(harmony, typeof(scrConductor), "calibration_i", "ReturnZeroFloat_Prefix", null);
            TryPatchGetter(harmony, typeof(scrConductor), "calibration_v", "ReturnZeroFloat_Prefix", null);
            TryPatchGetter(harmony, typeof(Persistence), "inputOffset", "ReturnZeroFloat_Prefix", null);
            TryPatchGetter(harmony, typeof(Persistence), "visualOffset", "ReturnZeroFloat_Prefix", null);
            TryPatchGetter(harmony, typeof(AsyncInputManager), "isActive", "ReturnFalseBool_Prefix", null);
            TryPatchMethods(harmony, typeof(Persistence), "GetChosenAsynchronousInput", "ReturnFalseBool_Prefix", null);
            TryPatchMethods(harmony, typeof(AsyncInputManager), "StartHook", "SkipVoid_Prefix", null);
            ForceZeroCalibrationOffsets();

            // 4) Force Auto-play (modern 0.5 deg precision), NoHUD, NoAutoHUD
            TryPatchGetter(harmony, typeof(RDC), "auto", "ReturnTrueBool_Prefix", null);
            TryPatchGetter(harmony, typeof(RDC), "useOldAuto", "ReturnFalseBool_Prefix", null);
            TryPatchGetter(harmony, typeof(RDC), "noHud", "ReturnTrueBool_Prefix", null);
            TryPatchGetter(harmony, typeof(RDC), "noAutoHud", "ReturnTrueBool_Prefix", null);

            // 5) Suppress judgment hit text, countdown, endscreen lanterns, wipes, focus-pause, error popups, and save mutations
            TryPatchMethods(harmony, typeof(scrHitTextManager), "ShowHitText", "ShowHitText_Prefix", null);
            TryPatchMethods(harmony, typeof(scrCountdown), "ShowGetReady", "Countdown_Suppress_Prefix", null);
            TryPatchMethods(harmony, typeof(scrCountdown), "Update", "Countdown_Suppress_Prefix", null);
            TryPatchMethods(harmony, typeof(scrUIController), "ShowEndscreenLanterns", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scrUIController), "PrepareWipeFromBlack", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scrUIController), "WipeFromBlack", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scrUIController), "FadeFromBlack", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scrController), "OnApplicationPauseCallback", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scnSplash), "GoToMenu", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(ErrorCanvas), "ShowError", "ErrorCanvas_ShowError_Prefix", null);

            // Prevent modifying user's save file / calibration presets / Steam stats during offline rendering
            TryPatchMethods(harmony, typeof(scrMistakesManager), "Save", "MistakesSave_Prefix", null);
            TryPatchMethods(harmony, typeof(Persistence), "Save", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(Persistence), "WriteSaveToDisk", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(Persistence), "SetLevelTutorialProgress", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scrConductor), "SaveCurrentPreset", "SkipVoid_Prefix", null);
            TryPatchMethods(harmony, typeof(scrConductor), "SaveVisualOffset", "SkipVoid_Prefix", null);

            // 6) Trigger auto-start cleanly when WaitForStartCo waits for player input
            TryPatchMethods(harmony, typeof(scrPlayerManager), "AnyValidInputWasTriggered", "AnyValidInput_Prefix", null);

            // 7) Guarantee absolute local speaker silence from startup to exit
            AudioListener.volume = 0f;
            TryPatchMethods(harmony, typeof(scrController), "UpdateListenerVolume", "MuteListener_Prefix", null);
            TryPatchMethods(harmony, typeof(scrController), "UpdateVolumeFromPersistence", "MuteListener_Prefix", null);
            try
            {
                MethodInfo setVol = typeof(AudioListener).GetProperty("volume", BindingFlags.Static | BindingFlags.Public).GetSetMethod();
                TryPatchMethodBase(harmony, setVol, "AudioListener_set_volume_Prefix", null);
            }
            catch { }

            AgentLog.Info("UiAndQualityPatches applied cleanly.");
        }

        private static bool Persistence_GetPlayerColor_Prefix(bool __0, ref PlanetColor __result)
        {
            if (!CustomPlanetColorsEnabled) return true;
            if (__0)
            {
                __result = RedIsPureDefault
                    ? new PlanetColor(PlanetColorPreset.DefaultRed)
                    : new PlanetColor(RedPlanetColor);
            }
            else
            {
                __result = BlueIsPureDefault
                    ? new PlanetColor(PlanetColorPreset.DefaultBlue)
                    : new PlanetColor(BluePlanetColor);
            }
            return false;
        }

        private static void PlanetRenderer_LoadPlanetColor_Postfix(PlanetRenderer __instance, bool __0)
        {
            if (!CustomPlanetColorsEnabled || __instance == null || __instance.objectDecoration != null) return;
            Color target = __0 ? RedPlanetColor : BluePlanetColor;
            ApplyOpacityToPlanetRenderer(__instance, target);
        }

        private static void PlanetarySystem_ApplyMultiplanetColors_Postfix(PlanetarySystem __instance)
        {
            if (!CustomPlanetColorsEnabled || __instance == null || __instance.allPlanets == null) return;
            float extraAlpha = Mathf.Clamp01(0.5f * (RedPlanetColor.a + BluePlanetColor.a));
            if (extraAlpha >= 0.999f) return;

            for (int i = 2; i < __instance.allPlanets.Count; i++)
            {
                scrPlanet p = __instance.allPlanets[i];
                if (p == null || p.planetRenderer == null) continue;
                Color cur = p.planetRenderer.GetPlanetColor();
                cur.a = extraAlpha;
                p.planetRenderer.SetPlanetColor(cur);
                p.planetRenderer.SetTailColor(cur);
                ApplyOpacityToPlanetRenderer(p.planetRenderer, cur);
            }
        }

        private static void ApplyOpacityToPlanetRenderer(PlanetRenderer pr, Color color)
        {
            if (pr == null) return;
            float a = Mathf.Clamp01(color.a);
            try
            {
                if (a <= 0.001f)
                {
                    // Full 0% opacity: cleanly hide sprite, ring, glow, and particles
                    if (pr.sprite != null)
                    {
                        pr.sprite.color = new Color(color.r, color.g, color.b, 0f);
                        pr.sprite.visible = false;
                    }
                    if (pr.glow != null) pr.glow.enabled = false;
                    if (pr.ring != null) pr.ring.enabled = false;
                    if (pr.ringComp != null) pr.ringComp.color = new Color(color.r, color.g, color.b, 0f);
                    if (pr.coreParticles != null) { pr.coreParticles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear); }
                    if (pr.tailParticles != null) { pr.tailParticles.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear); }
                    if (pr.sparks != null) { pr.sparks.Stop(true, ParticleSystemStopBehavior.StopEmittingAndClear); }
                }
                else if (a < 0.999f)
                {
                    if (pr.sprite != null)
                    {
                        pr.sprite.visible = true;
                        Color sc = pr.sprite.color;
                        pr.sprite.color = new Color(sc.r, sc.g, sc.b, a);
                    }
                    if (pr.glow != null)
                    {
                        pr.glow.enabled = true;
                        // Avoid compounding alpha; normalize from base glow opacity (~0.5)
                        Color gc = pr.glow.color;
                        pr.glow.color = new Color(color.r, color.g, color.b, Mathf.Clamp01(0.5f * a));
                    }
                    if (pr.ringComp != null)
                    {
                        pr.ringComp.color = new Color(color.r, color.g, color.b, 0.4f * a);
                    }
                    ScaleParticleAlpha(pr.coreParticles, a);
                    ScaleParticleAlpha(pr.tailParticles, a);
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("ApplyOpacityToPlanetRenderer warning: " + ex.Message);
            }
        }

        private static void ScaleParticleAlpha(ParticleSystem ps, float alpha)
        {
            if (ps == null) return;
            try
            {
                ParticleSystem.MainModule main = ps.main;
                ParticleSystem.MinMaxGradient sc = main.startColor;
                if (sc.mode == ParticleSystemGradientMode.Color)
                {
                    Color c = sc.color;
                    c.a = alpha;
                    main.startColor = new ParticleSystem.MinMaxGradient(c);
                }
            }
            catch { }
        }

        private static void TranspileCameraFilterPackScreenSize(Harmony harmony)
        {
            int patchedCount = 0;
            try
            {
                HarmonyMethod transpiler = new HarmonyMethod(
                    typeof(UiAndQualityPatches).GetMethod("ScreenSize_Transpiler", BindingFlags.Static | BindingFlags.NonPublic));
                Assembly[] assemblies = AppDomain.CurrentDomain.GetAssemblies();
                for (int a = 0; a < assemblies.Length; a++)
                {
                    string name = assemblies[a].GetName().Name;
                    if (name != "Assembly-CSharp-firstpass" && name != "Assembly-CSharp") continue;
                    Type[] types;
                    try { types = assemblies[a].GetTypes(); }
                    catch { continue; }
                    for (int i = 0; i < types.Length; i++)
                    {
                        Type t = types[i];
                        if (!t.Name.StartsWith("CameraFilterPack", StringComparison.Ordinal)) continue;
                        MethodInfo onRender = t.GetMethod("OnRenderImage", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                        if (onRender == null || onRender.DeclaringType != t) continue;
                        try
                        {
                            List<CodeInstruction> ins = PatchProcessor.GetOriginalInstructions(onRender);
                            bool usesScreen = false;
                            for (int k = 0; k < ins.Count; k++)
                            {
                                if ((ins[k].opcode == OpCodes.Call || ins[k].opcode == OpCodes.Callvirt) &&
                                    (object.Equals(ins[k].operand, _miScreenWidth) || object.Equals(ins[k].operand, _miScreenHeight)))
                                {
                                    usesScreen = true;
                                    break;
                                }
                            }
                            if (usesScreen)
                            {
                                harmony.Patch(onRender, null, null, transpiler, null);
                                patchedCount++;
                            }
                        }
                        catch { }
                    }
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("TranspileCameraFilterPackScreenSize warning: " + ex.Message);
            }
            AgentLog.Info("Transpiled Screen.width/height in " + patchedCount + " CameraFilterPack OnRenderImage methods.");
        }

        private static IEnumerable<CodeInstruction> ScreenSize_Transpiler(IEnumerable<CodeInstruction> instructions)
        {
            foreach (CodeInstruction ci in instructions)
            {
                if ((ci.opcode == OpCodes.Call || ci.opcode == OpCodes.Callvirt) && object.Equals(ci.operand, _miScreenWidth))
                {
                    yield return new CodeInstruction(OpCodes.Call, _miEffectiveWidth);
                }
                else if ((ci.opcode == OpCodes.Call || ci.opcode == OpCodes.Callvirt) && object.Equals(ci.operand, _miScreenHeight))
                {
                    yield return new CodeInstruction(OpCodes.Call, _miEffectiveHeight);
                }
                else
                {
                    yield return ci;
                }
            }
        }

        private static void TryPatchGetter(Harmony harmony, Type type, string propName, string prefixName, string postfixName)
        {
            try
            {
                PropertyInfo pi = type.GetProperty(propName, BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (pi == null)
                {
                    AgentLog.Warn("Property not found: " + type.Name + "." + propName);
                    return;
                }
                MethodInfo getter = pi.GetGetMethod(true);
                TryPatchMethodBase(harmony, getter, prefixName, postfixName);
            }
            catch (Exception ex)
            {
                AgentLog.Warn("TryPatchGetter failed on " + type.Name + "." + propName + ": " + ex.Message);
            }
        }

        private static void TryPatchMethods(Harmony harmony, Type type, string methodName, string prefixName, string postfixName)
        {
            try
            {
                MethodInfo[] all = type.GetMethods(BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
                int count = 0;
                for (int i = 0; i < all.Length; i++)
                {
                    if (all[i].Name == methodName && !all[i].IsGenericMethod)
                    {
                        TryPatchMethodBase(harmony, all[i], prefixName, postfixName);
                        count++;
                    }
                }
                if (count == 0)
                    AgentLog.Warn("Method not found: " + type.Name + "." + methodName);
            }
            catch (Exception ex)
            {
                AgentLog.Warn("TryPatchMethods failed on " + type.Name + "." + methodName + ": " + ex.Message);
            }
        }

        private static void TryPatchMethodBase(Harmony harmony, MethodBase target, string prefixName, string postfixName)
        {
            if (target == null) return;
            try
            {
                HarmonyMethod pre = string.IsNullOrEmpty(prefixName) ? null :
                    new HarmonyMethod(typeof(UiAndQualityPatches).GetMethod(prefixName, BindingFlags.Static | BindingFlags.NonPublic));
                HarmonyMethod post = string.IsNullOrEmpty(postfixName) ? null :
                    new HarmonyMethod(typeof(UiAndQualityPatches).GetMethod(postfixName, BindingFlags.Static | BindingFlags.NonPublic));
                harmony.Patch(target, pre, post, null, null);
            }
            catch (Exception ex)
            {
                AgentLog.Warn("TryPatch failed on " + target.DeclaringType.Name + "." + target.Name + ": " + ex.Message);
            }
        }

        public static void EnforceCamRT(scrCamera cam)
        {
            if (cam == null || !ResolutionOverrideActive) return;
            try
            {
                RenderTexture rt = _fiCamRT != null ? (_fiCamRT.GetValue(cam) as RenderTexture) : null;
                if (rt == null || rt.width != TargetWidth || rt.height != TargetHeight)
                {
                    if (rt != null) rt.Release();

                    RenderTexture newRt = new RenderTexture(TargetWidth, TargetHeight, 24, RenderTextureFormat.ARGB32);
                    newRt.antiAliasing = 1;
                    if (!newRt.Create())
                    {
                        // Low-spec fallback: retry with 16-bit depth buffer
                        AgentLog.Warn("24-bit depth RenderTexture.Create() failed; falling back to 16-bit depth.");
                        newRt.Release();
                        UnityEngine.Object.Destroy(newRt);
                        newRt = new RenderTexture(TargetWidth, TargetHeight, 16, RenderTextureFormat.ARGB32);
                        newRt.antiAliasing = 1;
                        newRt.Create();
                    }

                    if (_fiCamRT != null) _fiCamRT.SetValue(cam, newRt);
                    cam.SetupRTCam(true);

                    if (cam.Overlaycam != null && cam.quad != null)
                    {
                        float h = cam.Overlaycam.orthographicSize * 2f;
                        float w = h * ((float)TargetWidth / (float)TargetHeight);
                        cam.quad.transform.localScale = new Vector3(w, h, 1f);
                    }

                    MeshRenderer quadMesh = _fiCamQuadMesh != null ? (_fiCamQuadMesh.GetValue(cam) as MeshRenderer) : null;
                    if (quadMesh != null && !cam.enableCustomFPS && quadMesh.material != null)
                        quadMesh.material.mainTexture = newRt;

                    AgentLog.Info(string.Format("Created offscreen camRT: {0}x{1} (depth={2})", TargetWidth, TargetHeight, newRt.depth));
                }
                else
                {
                    bool useRT = _fiUseRTCam != null && (bool)_fiUseRTCam.GetValue(cam);
                    if (!useRT)
                    {
                        cam.SetupRTCam(true);
                    }
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("EnforceCamRT error: " + ex.Message);
            }
        }

        public static RenderTexture GetFinalCameraTexture(scrCamera cam)
        {
            if (cam == null) return null;
            if (cam.enableCustomFPS && _fiCamQuadMesh != null)
            {
                MeshRenderer mr = _fiCamQuadMesh.GetValue(cam) as MeshRenderer;
                if (mr != null && mr.material != null)
                {
                    RenderTexture qrt = mr.material.mainTexture as RenderTexture;
                    if (qrt != null) return qrt;
                }
            }
            return _fiCamRT != null ? (_fiCamRT.GetValue(cam) as RenderTexture) : null;
        }

        private static readonly FieldInfo _fiCurrentPreset =
            typeof(scrConductor).GetField("currentPreset", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
        private static readonly FieldInfo _fiUserPresets =
            typeof(scrConductor).GetField("userPresets", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
        private static readonly FieldInfo _fiDefaultPresets =
            typeof(scrConductor).GetField("defaultPresets", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
        private static readonly FieldInfo _fiPresetInputOffset =
            typeof(CalibrationPreset).GetField("inputOffset", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);

        /// <summary>
        /// Zeroes ADOFAI's in-memory device calibration fields directly.
        /// Mono JIT inlines the 18-byte static getter scrConductor.get_calibration_i() into scrConductor.Update(),
        /// so patching the getter alone is insufficient; zeroing currentPreset.inputOffset and visualOffset
        /// guarantees exact 0ms device offset in both rough previews and final renders (without saving to disk).
        /// </summary>
        public static void ForceZeroCalibrationOffsets()
        {
            try
            {
                scrConductor.visualOffset = 0;

                if (_fiCurrentPreset != null && _fiPresetInputOffset != null)
                {
                    object boxed = _fiCurrentPreset.GetValue(null);
                    if (boxed != null)
                    {
                        int cur = (int)_fiPresetInputOffset.GetValue(boxed);
                        if (cur != 0)
                        {
                            _fiPresetInputOffset.SetValue(boxed, 0);
                            _fiCurrentPreset.SetValue(null, boxed);
                        }
                    }
                }

                ZeroPresetList(_fiUserPresets);
                ZeroPresetList(_fiDefaultPresets);
            }
            catch (Exception ex)
            {
                AgentLog.Warn("ForceZeroCalibrationOffsets warning: " + ex.Message);
            }
        }

        private static void ZeroPresetList(FieldInfo fiList)
        {
            if (fiList == null || _fiPresetInputOffset == null) return;
            System.Collections.IList list = fiList.GetValue(null) as System.Collections.IList;
            if (list == null) return;
            for (int i = 0; i < list.Count; i++)
            {
                object item = list[i];
                if (item == null) continue;
                int val = (int)_fiPresetInputOffset.GetValue(item);
                if (val != 0)
                {
                    _fiPresetInputOffset.SetValue(item, 0);
                    list[i] = item; // value-type struct: write modified copy back into list
                }
            }
        }

        public static void EnsureZeroCalibrationFast()
        {
            try
            {
                if (scrConductor.visualOffset != 0)
                    scrConductor.visualOffset = 0;
                if (_fiCurrentPreset != null && _fiPresetInputOffset != null)
                {
                    object boxed = _fiCurrentPreset.GetValue(null);
                    if (boxed != null && (int)_fiPresetInputOffset.GetValue(boxed) != 0)
                    {
                        _fiPresetInputOffset.SetValue(boxed, 0);
                        _fiCurrentPreset.SetValue(null, boxed);
                    }
                }
            }
            catch { }
        }

        public static void SuppressAllOutOfLevelUI()
        {
            try
            {
                scrUIController ui = scrUIController.instance;
                if (ui != null)
                {
                    if (ui.canvas != null && ui.canvas.enabled)
                        ui.canvas.enabled = false;
                }

                scrController ctrl = scrController.instance;
                if (ctrl != null)
                {
                    if (_fiDisableCongrats != null)
                        _fiDisableCongrats.SetValue(ctrl, true);
                    if (ctrl.errorMeter != null && ctrl.errorMeter.gameObject.activeSelf)
                        ctrl.errorMeter.gameObject.SetActive(false);
                    if (ctrl.pauseMenu != null && ctrl.pauseMenu.gameObject.activeSelf)
                        ctrl.pauseMenu.gameObject.SetActive(false);
                }
            }
            catch { }
        }

        private static bool CamRTNeedsRecreation_Prefix(ref bool __result)
        {
            if (!ResolutionOverrideActive) return true;
            __result = false;
            return false;
        }

        public static void SetFastSeekCameraSkip(scrCamera cam, bool skip)
        {
            FastSeekSkipRender = skip;
            if (cam == null) return;
            try
            {
                if (skip)
                {
                    if (!_savedCamStates)
                    {
                        _origCamObjEnabled = (cam.camobj != null) && cam.camobj.enabled;
                        _origBgCamEnabled = (cam.BGcam != null) && cam.BGcam.enabled;
                        _origBgStaticEnabled = (cam.Bgcamstatic != null) && cam.Bgcamstatic.enabled;
                        _origOverlayEnabled = (cam.Overlaycam != null) && cam.Overlaycam.enabled;
                        _savedCamStates = true;
                    }
                    if (cam.camobj != null && cam.camobj.enabled) cam.camobj.enabled = false;
                    if (cam.BGcam != null && cam.BGcam.enabled) cam.BGcam.enabled = false;
                    if (cam.Bgcamstatic != null && cam.Bgcamstatic.enabled) cam.Bgcamstatic.enabled = false;
                    if (cam.Overlaycam != null && cam.Overlaycam.enabled) cam.Overlaycam.enabled = false;
                }
                else if (_savedCamStates)
                {
                    if (cam.camobj != null) cam.camobj.enabled = _origCamObjEnabled;
                    if (cam.BGcam != null) cam.BGcam.enabled = _origBgCamEnabled;
                    if (cam.Bgcamstatic != null) cam.Bgcamstatic.enabled = _origBgStaticEnabled;
                    if (cam.Overlaycam != null) cam.Overlaycam.enabled = _origOverlayEnabled;
                    _savedCamStates = false;
                    EnforceCamRT(cam);
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("SetFastSeekCameraSkip warning: " + ex.Message);
            }
        }

        private static void ScrCamera_Update_Postfix(scrCamera __instance)
        {
            if (FastSeekSkipRender)
            {
                if (__instance != null)
                {
                    if (__instance.camobj != null && __instance.camobj.enabled) __instance.camobj.enabled = false;
                    if (__instance.BGcam != null && __instance.BGcam.enabled) __instance.BGcam.enabled = false;
                    if (__instance.Bgcamstatic != null && __instance.Bgcamstatic.enabled) __instance.Bgcamstatic.enabled = false;
                    if (__instance.Overlaycam != null && __instance.Overlaycam.enabled) __instance.Overlaycam.enabled = false;
                }
                return;
            }
            if (ResolutionOverrideActive)
                EnforceCamRT(__instance);
        }

        private static bool Persistence_VisualQuality_Prefix(ref VisualQuality __result)
        {
            __result = VisualQuality.High;
            return false;
        }

        private static bool Persistence_VisualEffects_Prefix(ref VisualEffects __result)
        {
            __result = VisualEffects.Full;
            return false;
        }

        private static bool Persistence_SkipIntro_Prefix(ref SkipIntroBehavior __result)
        {
            __result = (SkipIntroBehavior)0;
            return false;
        }

        private static bool Persistence_HitSoundVolume_Prefix(ref int __result)
        {
            __result = 10; // ADOFAI Persistence.hitSoundVolume is 0..10 (10 = 100%)
            return false;
        }

        private static bool ReturnZeroFloat_Prefix(ref float __result)
        {
            __result = 0f;
            return false;
        }

        private static bool ReturnFalseBool_Prefix(ref bool __result)
        {
            __result = false;
            return false;
        }

        private static bool ReturnTrueBool_Prefix(ref bool __result)
        {
            __result = true;
            return false;
        }

        private static bool ShowHitText_Prefix()
        {
            return !HideHitText;
        }

        private static bool Countdown_Suppress_Prefix()
        {
            return !HideCountdown;
        }

        private static bool SkipVoid_Prefix()
        {
            return false;
        }

        private static bool ErrorCanvas_ShowError_Prefix(ErrorCanvas __instance, string __0)
        {
            AgentLog.Warn("Suppressed ADOFAI ErrorCanvas popup: " + (__0 ?? "(empty)"));
            try
            {
                if (__instance != null && __instance.mainPanel != null && __instance.mainPanel.activeSelf)
                    __instance.mainPanel.SetActive(false);
            }
            catch { }
            return false;
        }

        private static bool MistakesSave_Prefix(ref scrMistakesManager.EndLevelInfo __result)
        {
            __result = default(scrMistakesManager.EndLevelInfo);
            return false;
        }

        private static bool AnyValidInput_Prefix(ref bool __result)
        {
            if (ShouldTriggerStart)
            {
                ShouldTriggerStart = false;
                __result = true;
                return false;
            }
            __result = false;
            return false;
        }

        private static bool MuteListener_Prefix()
        {
            AudioListener.volume = 0f;
            return false;
        }

        private static void AudioListener_set_volume_Prefix(ref float value)
        {
            value = 0f;
        }
    }
}
