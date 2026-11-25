using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Video;

namespace AdofaiRenderAgent
{
    public class RenderAgentBehaviour : MonoBehaviour
    {
        private enum Stage
        {
            Boot,
            WaitingForScene,
            WaitingForLevelLoad,
            StartingPlayback,
            WaitingForMusicScheduled,
            SimulatingToStart,
            Capturing,
            Finishing,
            Done,
            IdleWarm
        }

        public static RenderJobConfig Config;
        private Stage _stage = Stage.Boot;
        private int _stageFrames = 0;
        private int _settledFrames = 0;
        private bool _isWarmReuse = false;
        private string _warmSessionDir = "";
        private string _warmLoadedLevelPath = "";
        private int _warmParentPid = 0;
        private int _warmIdleTimeoutSec = 300;
        private Stopwatch _idleWatch = null;
        private double _lastParentCheckSec = 0.0;
        private FrameCapturePipe _pipe;
        private Stopwatch _renderWallClock;
        private readonly GpuPacer _gpuPacer = new GpuPacer();
        private bool _fastSeekPrerollRestored = false;

        private double _levelStartDsp = 0.0;
        private int _fastStartFloor = -1;
        private bool _checkpointRestartIssued = false;
        private double _captureStartDsp = 0.0;
        private double _wonElapsedSec = 0.0;
        private double _estimatedTotalSec = 0.0;
        private bool _hasCapturedFirstFrame = false;
        private bool _wonReached = false;
        private int _lastProgressFrame = -100;
        private double _lastTileEntryTime = 0.0;
        private int _totalFloors = 0;
        private int _captureSimTick = 0;

        private static readonly FieldInfo _fiWaitForStartCallCount =
            typeof(scrController).GetField("waitForStartCoCallCount", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiTransitioningLevel =
            typeof(scrController).GetField("transitioningLevel", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiPlayedHitSounds =
            typeof(scrConductor).GetField("playedHitSounds", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiConductorBuffer =
            typeof(scrConductor).GetField("buffer", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

        private bool _windowHiddenOffscreen = false;

        private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll", EntryPoint = "GetWindowLongPtr")]
        private static extern IntPtr GetWindowLongPtr64(IntPtr hWnd, int nIndex);

        [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr")]
        private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

        [DllImport("user32.dll")]
        private static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        private void ParkUnityWindowOffscreen()
        {
            try
            {
                const int GWL_EXSTYLE = -20;
                const long WS_EX_TOOLWINDOW = 0x00000080L;
                const long WS_EX_APPWINDOW = 0x00040000L;
                const long WS_EX_NOACTIVATE = 0x08000000L;
                const uint SWP_NOSIZE = 0x0001;
                const uint SWP_NOZORDER = 0x0004;
                const uint SWP_NOACTIVATE = 0x0010;
                const uint SWP_FRAMECHANGED = 0x0020;
                const int SW_HIDE = 0;
                const int SW_SHOWNOACTIVATE = 4;

                uint myPid = (uint)Process.GetCurrentProcess().Id;
                int movedCount = 0;
                EnumWindows((hWnd, lParam) =>
                {
                    uint pid;
                    GetWindowThreadProcessId(hWnd, out pid);
                    if (pid == myPid && IsWindowVisible(hWnd))
                    {
                        long ex = GetWindowLongPtr64(hWnd, GWL_EXSTYLE).ToInt64();
                        long newEx = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW;
                        if (newEx != ex)
                        {
                            // Toggle visibility briefly so Windows Explorer drops the taskbar button cleanly
                            ShowWindow(hWnd, SW_HIDE);
                            SetWindowLongPtr64(hWnd, GWL_EXSTYLE, new IntPtr(newEx));
                            ShowWindow(hWnd, SW_SHOWNOACTIVATE);
                        }
                        SetWindowPos(hWnd, IntPtr.Zero, -32000, -32000, 0, 0,
                            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED);
                        movedCount++;
                    }
                    return true;
                }, IntPtr.Zero);

                if (movedCount > 0 && !_windowHiddenOffscreen)
                {
                    _windowHiddenOffscreen = true;
                    AgentLog.Info("Parked Unity host window off-screen (toolwindow, no taskbar/focus steal).");
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("ParkUnityWindowOffscreen warning: " + ex.Message);
            }
        }

        private void Awake()
        {
            DontDestroyOnLoad(this.gameObject);
            Application.runInBackground = true;
            AudioListener.volume = 0f;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = -1;
            ParkUnityWindowOffscreen();
            AgentLog.Info(string.Format(
                "RenderAgentBehaviour Awake. GPU='{0}', API={1}, VRAM={2}MB, maxTex={3}, asyncReadback={4}",
                SystemInfo.graphicsDeviceName, SystemInfo.graphicsDeviceType,
                SystemInfo.graphicsMemorySize, SystemInfo.maxTextureSize, SystemInfo.supportsAsyncGPUReadback));
            _gpuPacer.Initialize();
        }

        private void Start()
        {
            StartCoroutine(EndOfFrameCaptureLoop());
        }

        private void Update()
        {
            if (_stage == Stage.Done || _stage == Stage.Finishing) return;
            if (_stage == Stage.IdleWarm)
            {
                UpdateIdleWarm();
                return;
            }

            // Keep live speakers 100% silent and keep running in background
            MuteAllLocalAudio();
            Application.runInBackground = true;

            if (CheckCancelRequested())
            {
                AgentLog.Warn("Cancel sentinel detected! Aborting render cleanly.");
                FinishRender(true, "cancelled");
                return;
            }

            _stageFrames++;

            switch (_stage)
            {
                case Stage.Boot:
                    UpdateBoot();
                    break;
                case Stage.WaitingForScene:
                    UpdateWaitingForScene();
                    break;
                case Stage.WaitingForLevelLoad:
                    UpdateWaitingForLevelLoad();
                    break;
                case Stage.StartingPlayback:
                    UpdateStartingPlayback();
                    break;
                case Stage.WaitingForMusicScheduled:
                    UpdateWaitingForMusicScheduled();
                    break;
                case Stage.SimulatingToStart:
                    UpdateSimulatingToStart();
                    break;
                case Stage.Capturing:
                    UpdateCapturing();
                    break;
            }
        }

        private void LateUpdate()
        {
            if (_stage >= Stage.WaitingForLevelLoad && _stage <= Stage.Capturing)
            {
                bool fastSkip = UiAndQualityPatches.FastSeekSkipRender && _stage == Stage.SimulatingToStart;
                if (!fastSkip || (_stageFrames % 60 == 0))
                    UiAndQualityPatches.SuppressAllOutOfLevelUI();
                if (!fastSkip)
                    SyncVideoBackgroundIfPresent();
                if (UiAndQualityPatches.FastSeekSkipRender && scrCamera.instance != null)
                {
                    UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, true);
                }
                if (Config != null && Config.previewMode)
                    DisableHeavyFiltersForPreview();
                if (_stage == Stage.Capturing && PlanetTailArc.Enabled)
                {
                    try { PlanetTailArc.Tick(VirtualClock.Dt); }
                    catch (Exception ex) { AgentLog.Warn("PlanetTailArc: " + ex.Message); PlanetTailArc.Enabled = false; PlanetTailArc.Restore(); }
                }
            }
        }

        private readonly List<MonoBehaviour> _previewCamComps = new List<MonoBehaviour>(64);
        private readonly Dictionary<Type, bool> _heavyFilterTypeCache = new Dictionary<Type, bool>(32);
        private readonly HashSet<string> _previewSuppressedFilters = new HashSet<string>(StringComparer.Ordinal);
        private int _previewSuppressedHits = 0;

        private static bool IsHeavyProxyFilterName(string typeName)
        {
            if (string.IsNullOrEmpty(typeName)) return false;
            if (typeName == "CameraMotionBlur" || typeName == "MotionBlur" ||
                typeName == "Blur" || typeName == "BlurOptimized" ||
                typeName == "DepthOfFieldDeprecated" || typeName == "ScreenSpaceAmbientOcclusion")
                return true;
            if (typeName == "VideoBloom" || typeName == "Bloom" || typeName == "BloomOptimized" ||
                typeName == "BloomAndFlares" || typeName == "MarvelousBloom")
                return true;
            if (typeName.IndexOf("OilPaint", StringComparison.Ordinal) >= 0) return true;

            // Match both CameraFilterPack_* and CameraFilterPackLegacy_* families
            string sub = null;
            if (typeName.StartsWith("CameraFilterPackLegacy_", StringComparison.Ordinal))
                sub = typeName.Substring("CameraFilterPackLegacy_".Length);
            else if (typeName.StartsWith("CameraFilterPack_", StringComparison.Ordinal))
                sub = typeName.Substring("CameraFilterPack_".Length);

            if (sub != null)
            {
                if (sub.StartsWith("Blur_", StringComparison.Ordinal)) return true;
                if (sub.StartsWith("Glow_", StringComparison.Ordinal)) return true;
                if (sub.StartsWith("Atmosphere_Rain", StringComparison.Ordinal) ||
                    sub.StartsWith("Atmosphere_Snow", StringComparison.Ordinal) ||
                    sub.StartsWith("Rain_", StringComparison.Ordinal) ||
                    sub.StartsWith("3D_Snow", StringComparison.Ordinal) ||
                    sub.StartsWith("AAA_WaterDrop", StringComparison.Ordinal) ||
                    sub == "Blizzard")
                    return true;
            }
            return false;
        }

        private bool IsHeavyProxyFilter(Type t)
        {
            bool res;
            if (_heavyFilterTypeCache.TryGetValue(t, out res)) return res;
            res = IsHeavyProxyFilterName(t.Name);
            _heavyFilterTypeCache[t] = res;
            return res;
        }

        // Proxy mode only: suppress the heaviest multi-pass / high-tap post-FX filters
        // (Bloom, Gaussian/Radial/Focus Blur, Glow, OilPaint, MotionBlur, Rain/Snow/Blizzard/WaterDrop)
        // across both CameraFilterPack_* and CameraFilterPackLegacy_* families so proxy generation
        // stays fast without ever affecting final renders.
        private void DisableHeavyFiltersForPreview()
        {
            try
            {
                Camera[] cams = Camera.allCameras;
                if (cams == null) return;
                for (int c = 0; c < cams.Length; c++)
                {
                    Camera cam = cams[c];
                    if (cam == null) continue;
                    _previewCamComps.Clear();
                    cam.GetComponents<MonoBehaviour>(_previewCamComps);
                    for (int i = 0; i < _previewCamComps.Count; i++)
                    {
                        MonoBehaviour mb = _previewCamComps[i];
                        if (mb == null || !mb.enabled) continue;
                        Type t = mb.GetType();
                        if (IsHeavyProxyFilter(t))
                        {
                            mb.enabled = false;
                            _previewSuppressedHits++;
                            _previewSuppressedFilters.Add(t.Name);
                        }
                    }
                }
            }
            catch { }
        }

        private void UpdateBoot()
        {
            if (!_windowHiddenOffscreen)
                ParkUnityWindowOffscreen();
            if (_stageFrames < 3) return;
            ParkUnityWindowOffscreen();

            ReportProgress("loading_scene", 0, 0, 0.0, 0.0);
            AgentLog.Info("Configuring GCS for custom level and loading scnGame: " + Config.levelPath);

            GCS.customLevelPaths = new string[] { Config.levelPath };
            GCS.customLevelIndex = 0;
            GCS.internalLevelName = null;
            GCS.loadCustomFromBundle = false;
            GCS.checkpointNum = 0;
            GCS.practiceMode = false;
            GCS.speedTrialMode = false;
            GCS.useNoFail = false;

            try { RDC.auto = true; } catch { }
            try { RDC.noHud = true; } catch { }
            try { RDC.noAutoHud = true; } catch { }

            TransitionTo(Stage.WaitingForScene);
            SceneManager.LoadScene("scnGame");
        }

        private void UpdateWaitingForScene()
        {
            if (scnGame.instance != null && scrController.instance != null &&
                scrConductor.instance != null && scrCamera.instance != null)
            {
                ParkUnityWindowOffscreen();
                UiAndQualityPatches.ForceZeroCalibrationOffsets();
                AgentLog.Info("scnGame loaded. Activating resolution override: " + Config.width + "x" + Config.height);
                UiAndQualityPatches.ResolutionOverrideActive = true;
                scrCamera.instance.forceRTCam = true;
                UiAndQualityPatches.EnforceCamRT(scrCamera.instance);
                UiAndQualityPatches.SuppressAllOutOfLevelUI();
                ReportProgress("loading_level", 0, 0, 0.0, 0.0);
                TransitionTo(Stage.WaitingForLevelLoad);
            }
            else if (_stageFrames > (Config.lowSpecSafeMode ? 3600 : 1200))
            {
                AgentLog.Error("Timed out waiting for scnGame scene objects.");
                FinishRender(true, "timeout_scene");
            }
        }

        private void UpdateWaitingForLevelLoad()
        {
            UiAndQualityPatches.SuppressAllOutOfLevelUI();
            if (scrCamera.instance != null)
                UiAndQualityPatches.EnforceCamRT(scrCamera.instance);

            if (scnGame.instance == null || scnGame.instance.isLoading)
            {
                _settledFrames = 0;
                return;
            }

            scrLevelMaker lm = scrLevelMaker.instance;
            if (lm == null || lm.listFloors == null || lm.listFloors.Count <= 1)
            {
                _settledFrames = 0;
                return;
            }

            if (AudioManager.Instance != null && AudioManager.Instance.loadingExternalSong)
            {
                _settledFrames = 0;
                return;
            }

            int waitCount = 0;
            if (_fiWaitForStartCallCount != null && scrController.instance != null)
                waitCount = (int)_fiWaitForStartCallCount.GetValue(scrController.instance);
            if (waitCount <= 0)
            {
                _settledFrames = 0;
                return;
            }

            // Wait for background video preparation if level uses a video background (with 10s safety cap)
            VideoPlayer vp = scnGame.instance.videoBG;
            if (vp != null && !string.IsNullOrEmpty(vp.url) && !vp.isPrepared && _stageFrames < 600)
            {
                _settledFrames = 0;
                return;
            }

            _settledFrames++;
            // Allow WaitForStartCo's 2-frame shader warmup & asset settling
            int neededSettle = (Config.previewMode || _isWarmReuse || _checkpointRestartIssued) ? 5 : 12;
            if (_settledFrames < neededSettle) return;

            // Cold launch fastStart checkpoint jump: once the level & floor entry times are loaded,
            // restart cleanly into ADOFAI's official checkpoint path (GCS.checkpointNum = ck,
            // ResetScene(true), Play(ck, true)) so WaitForStartCo runs PrepVfx, ScrubToFloorNumber(ck),
            // and scrVfxPlus.ScrubToTime() ONCE with exact seqID=ck and remakeFloors=true.
            if (!_checkpointRestartIssued)
            {
                int ck = ComputeFastStartFloor();
                if (ck > 0 && GCS.checkpointNum != ck)
                {
                    _checkpointRestartIssued = true;
                    _fastStartFloor = ck;
                    _settledFrames = 0;
                    GCS.checkpointNum = ck;
                    AgentLog.Info(string.Format(CultureInfo.InvariantCulture,
                        "Fast start official checkpoint scrub: GCS.checkpointNum={0} (entryTime={1:F3}s, target startSec={2:F2}s)",
                        ck, lm.listFloors[ck].entryTime, Config.startSec));

                    try { if (scrController.instance != null) scrController.instance.StopCoroutine("WaitForStartCo"); } catch { }
                    if (_fiWaitForStartCallCount != null && scrController.instance != null)
                        _fiWaitForStartCallCount.SetValue(scrController.instance, 0);
                    if (_fiPlayedHitSounds != null && scrConductor.instance != null)
                        _fiPlayedHitSounds.SetValue(scrConductor.instance, false);
                    if (scrUIController.instance != null)
                    {
                        try { scrUIController.instance.HideEndscreenLanterns(); } catch { }
                    }
                    ResetCameraFilterTimeAccumulators();
                    scnGame.instance.ResetScene(true);
                    scnGame.instance.Play(ck, true);
                    try { if (_fiTransitioningLevel != null && scrController.instance != null) _fiTransitioningLevel.SetValue(scrController.instance, false); } catch { }
                    try { if (ADOBase.playerManager != null) ADOBase.playerManager.SetAllPlayerResponsive(true); } catch { }
                    if (scrCamera.instance != null)
                    {
                        scrCamera.instance.forceRTCam = true;
                        UiAndQualityPatches.EnforceCamRT(scrCamera.instance);
                    }
                    UiAndQualityPatches.ReapplyPlanetColors();
                    UiAndQualityPatches.SuppressAllOutOfLevelUI();
                    return;
                }
                _checkpointRestartIssued = true;
            }

            if (_isWarmReuse || _fastStartFloor > 0)
            {
                UiAndQualityPatches.ReapplyPlanetColors();
            }

            _totalFloors = lm.listFloors.Count;
            try
            {
                scrFloor lastFloor = lm.listFloors[_totalFloors - 1];
                _lastTileEntryTime = lastFloor.entryTimePitchAdj;
                if (_lastTileEntryTime <= 0.0) _lastTileEntryTime = lastFloor.entryTime;
            }
            catch { }

            _estimatedTotalSec = _lastTileEntryTime + (double)Config.endPaddingSec + 1.0;
            if (Config.endSec > 0f && (double)Config.endSec < _estimatedTotalSec)
                _estimatedTotalSec = (double)Config.endSec;

            AgentLog.Info(string.Format("Level loaded and settled! floors={0}, lastTileEntryTime={1:F3}s, estTotal={2:F3}s",
                _totalFloors, _lastTileEntryTime, _estimatedTotalSec));

            if (scrUIController.instance != null)
            {
                try { scrUIController.instance.SetToTransparent(); } catch { }
            }
            if (scrDecorationManager.instance != null)
            {
                try { scrDecorationManager.instance.ShowEmptyDecorations(false); } catch { }
            }
            UiAndQualityPatches.SuppressAllOutOfLevelUI();

            // Initialize Experimental Render Optimizer (zero-opacity cull, frustum cull, LOD proxy pyramids)
            ExperimentalRenderOptimizer.Enabled = Config.experimentalOptimizeRender;
            if (Config.experimentalOptimizeRender)
            {
                ExperimentalRenderOptimizer.InitializeAfterLevelLoad(Config.width, Config.height);
            }

            TransitionTo(Stage.StartingPlayback);
        }

        private int ComputeFastStartFloor()
        {
            try
            {
                if (Config == null || scrLevelMaker.instance == null || scrLevelMaker.instance.listFloors == null)
                    return -1;
                var lf = scrLevelMaker.instance.listFloors;
                if (lf.Count <= 2) return -1;

                int ck = Config.startFloor;
                if (ck < 0 && Config.fastStart && Config.startSec > 12.0f)
                {
                    scrConductor cd = scrConductor.instance;
                    double addoff = (cd != null) ? Math.Max(0.0, cd.addoffset) : 0.0;
                    double target = (double)Config.startSec - addoff - 5.0;
                    if (target >= 4.0)
                    {
                        for (int i = 1; i < lf.Count; i++)
                        {
                            scrFloor fl = lf[i];
                            if (fl == null) continue;
                            double et = fl.entryTimeAfterExtraBeats;
                            if (et <= 0.0) et = fl.entryTime;
                            if (et <= target) ck = i;
                            else break;
                        }
                        // Avoid landing checkpoint on midspin, freeroam, or hold tiles (mirrors scrController.Scrub safety)
                        while (ck > 1 && lf[ck] != null && (lf[ck].midSpin || lf[ck].freeroam || lf[ck].holdLength > 0))
                        {
                            ck--;
                        }
                    }
                    if (ck < 1) ck = -1;
                }
                if (ck > 0 && ck < lf.Count) return ck;
            }
            catch (Exception ex)
            {
                AgentLog.Warn("ComputeFastStartFloor warning: " + ex.Message);
            }
            return -1;
        }

        private void UpdateStartingPlayback()
        {
            // Engage deterministic virtual clock BEFORE triggering start!
            UiAndQualityPatches.ForceZeroCalibrationOffsets();
            double baseDsp = AudioSettings.dspTime;
            if (baseDsp < 10.0) baseDsp = 100.0;
            VirtualClock.Engage(Config.fps, baseDsp);
            VirtualClock.SyncConductor(scrConductor.instance);

            AudioTimelineExporter.Fps = Config.fps;
            if (_fastStartFloor < 0 && GCS.checkpointNum > 0)
                _fastStartFloor = GCS.checkpointNum;
            UiAndQualityPatches.ShouldTriggerStart = true;

            AgentLog.Info("Triggered WaitForStartCo start input under VirtualClock.");
            ReportProgress("starting_playback", 0, _totalFloors, 0.0, _estimatedTotalSec);
            TransitionTo(Stage.WaitingForMusicScheduled);
        }

        private void UpdateWaitingForMusicScheduled()
        {
            scrConductor cond = scrConductor.instance;
            if (cond == null) return;

            bool played = AudioTimelineExporter.PlayHitTimesCaptured;
            if (!played && _fiPlayedHitSounds != null)
            {
                try { played = (bool)_fiPlayedHitSounds.GetValue(cond); } catch { }
            }

            if (!played)
            {
                if (_stageFrames > Config.fps * 10)
                {
                    AgentLog.Warn("Timed out waiting for PlayHitTimes; capturing conductor timing directly.");
                    AudioTimelineExporter.CaptureConductorTiming(cond);
                }
                else
                {
                    return;
                }
            }

            _levelStartDsp = VirtualClock.CurrentDsp;
            AudioTimelineExporter.LevelStartDsp = _levelStartDsp;
            if (_fastStartFloor > 0)
            {
                // Exact analytical mirror of scrConductor.StartMusicCo when checkpointNum == 0:
                // On floor-0 start (RDC.auto=true -> fastTakeoff=true), StartMusicCo runs on frame N:
                //   dspTimeSong_0 = dspTime_N + buffer - adjustedCountdownTicks * crotchetAtStart / pitch - skip0
                // and calls PlayHitTimes() on frame N. UpdateWaitingForMusicScheduled observes it on frame N+1
                // (CurrentDsp = dspTime_N + Dt), so:
                //   levelStartDsp_0 = dspTimeSong_0 + (Dt - buffer + countdown0 + skip0)
                // Setting _levelStartDsp = cdc.dspTimeSong + delta0 makes songposition_minusi(elapsed)
                // identical to a full-from-0 render to machine precision at any FPS, BPM, or pitch.
                scrConductor cdc = scrConductor.instance;
                double pitch = (cdc.song != null && cdc.song.pitch > 0.001f) ? (double)cdc.song.pitch : 1.0;
                double buf = 0.5;
                if (_fiConductorBuffer != null)
                {
                    try { buf = (double)(float)_fiConductorBuffer.GetValue(cdc); } catch { }
                }
                double countdown0 = (double)cdc.adjustedCountdownTicks * cdc.crotchetAtStart / pitch;
                double skip0 = 0.0;
                try
                {
                    int skipMode = (int)Persistence.skipIntroBehavior;
                    if (cdc.addoffset > 0.0 &&
                        ((skipMode == 1 && scrController.deaths > 0) ||
                         (skipMode == 2 && cdc.addoffset > (double)GCS.longIntroThresholdSec)))
                    {
                        skip0 = cdc.addoffset / pitch;
                    }
                }
                catch { }
                double delta0 = VirtualClock.Dt - buf + countdown0 + skip0;
                _levelStartDsp = cdc.dspTimeSong + delta0;
                // Snap VirtualClock's sub-frame phase onto the exact _levelStartDsp + k * Dt grid
                // so every captured frame samples the exact same songposition_minusi as a full-from-0 render.
                double rawElapsed = VirtualClock.CurrentDsp - _levelStartDsp;
                double snappedElapsed = Math.Round(rawElapsed * (double)Config.fps) / (double)Config.fps;
                double phaseShift = snappedElapsed - rawElapsed;
                if (Math.Abs(phaseShift) > 1e-7)
                {
                    VirtualClock.ShiftPhase(phaseShift);
                    VirtualClock.SyncConductor(cdc);
                }
                AgentLog.Info(string.Format(CultureInfo.InvariantCulture,
                    "Fast start exact re-base: dspTimeSong={0:F6} buf={1:F4} dt={2:F4} countdown0={3:F4} skip0={4:F4} delta0={5:F4} phaseShift={6:+0.0000;-0.0000}s -> elapsed={7:F4}s (songpos={8:F4}s)",
                    cdc.dspTimeSong, buf, VirtualClock.Dt, countdown0, skip0, delta0, phaseShift,
                    VirtualClock.CurrentDsp - _levelStartDsp, cdc.songposition_minusi));
            }
            AgentLog.Info(string.Format("Music scheduled! levelStartDsp={0:F6}, dspTimeSong={1:F6}, songStartDsp={2:F6}, cal_i={3:F3}s, cal_v={4:F3}s",
                _levelStartDsp, AudioTimelineExporter.DspTimeSong, AudioTimelineExporter.SongStartDsp,
                scrConductor.calibration_i, scrConductor.calibration_v));

            if (Config.startSec > 0.001f)
            {
                _fastSeekPrerollRestored = false;
                if (Config.startSec >= 5.0f && scrCamera.instance != null)
                {
                    UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, true);
                    int seekFps = Math.Min(Config.fps, 60);
                    if (seekFps < Config.fps)
                    {
                        VirtualClock.SetRate(seekFps);
                        AgentLog.Info(string.Format("Fast-seek GPU render skip enabled until {0:F2}s (seek sim rate={1}fps -> restoring {2}fps before preroll).",
                            Config.startSec - 3.0f, seekFps, Config.fps));
                    }
                    else
                    {
                        AgentLog.Info(string.Format("Fast-seek GPU render skip enabled until {0:F2}s (3.0s full-render preroll before startSec={1:F3}s).",
                            Config.startSec - 3.0f, Config.startSec));
                    }
                }
                else
                {
                    AgentLog.Info(string.Format("Fast-simulating deterministically to startSec={0:F3}s...", Config.startSec));
                }
                ReportProgress("seeking", 0, _totalFloors, 0.0, _estimatedTotalSec);
                TransitionTo(Stage.SimulatingToStart);
            }
            else
            {
                BeginCapturePipe();
            }
        }

        private void UpdateSimulatingToStart()
        {
            double elapsed = VirtualClock.CurrentDsp - _levelStartDsp;
            double remaining = (double)Config.startSec - elapsed;

            // Restore exact target simulation rate 3.5s before capture (0.5s before cameras wake up)
            if (VirtualClock.TargetFps != Config.fps && remaining <= 3.5)
            {
                VirtualClock.SetRate(Config.fps);
                AgentLog.Info(string.Format("Fast-seek: restored full sim rate {0}fps at elapsed={1:F3}s.", Config.fps, elapsed));
            }

            // Restore full camera rendering 3.0s (180 frames at 60fps) prior to capture start
            // so all shaders, filters, trails, particles, and videoBG warm up cleanly!
            if (UiAndQualityPatches.FastSeekSkipRender && remaining <= 3.0)
            {
                UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, false);
                _fastSeekPrerollRestored = true;
                AgentLog.Info(string.Format("Fast-seek preroll: restored full camera rendering at elapsed={0:F3}s ({1:F2}s before capture).",
                    elapsed, remaining));
            }

            if (elapsed + (0.5 * VirtualClock.Dt) >= (double)Config.startSec)
            {
                if (VirtualClock.TargetFps != Config.fps)
                    VirtualClock.SetRate(Config.fps);
                if (UiAndQualityPatches.FastSeekSkipRender || !_fastSeekPrerollRestored)
                {
                    UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, false);
                    _fastSeekPrerollRestored = true;
                }
                BeginCapturePipe();
            }
            else if (_stageFrames % 60 == 0)
            {
                int curFloor = GetCurrentFloorIndex();
                ReportProgress("seeking", curFloor, _totalFloors, elapsed, _estimatedTotalSec);
            }
        }

        private void BeginCapturePipe()
        {
            _pipe = new FrameCapturePipe(Config.width, Config.height, Config.flipVertical, Config.lowSpecSafeMode, Config.maxRamUsagePercent);
            if (!_pipe.Start(Config.ffmpegPath, Config.ffmpegArgs))
            {
                FinishRender(true, "ffmpeg_start_failed");
                return;
            }
            _renderWallClock = Stopwatch.StartNew();
            _hasCapturedFirstFrame = false;
            _wonReached = false;
            _wonElapsedSec = 0.0;
            _captureSimTick = 0;
            PlanetTailArc.Reset();
            PlanetTailArc.Enabled = Config.arcTails;
            AgentLog.Info("Entering Capturing stage.");
            ReportProgress("rendering", GetCurrentFloorIndex(), _totalFloors, 0.0, _estimatedTotalSec);
            TransitionTo(Stage.Capturing);
        }

        private void UpdateCapturing()
        {
            if (_pipe != null && _pipe.HasError)
            {
                AgentLog.Error("Aborting due to FFmpeg pipe error: " + _pipe.LastError);
                FinishRender(true, "ffmpeg_pipe_error");
                return;
            }

            scrController ctrl = scrController.instance;
            double levelElapsed = VirtualClock.CurrentDsp - _levelStartDsp;

            // Check trim endSec
            if (Config.endSec > 0f && levelElapsed >= (double)Config.endSec)
            {
                AgentLog.Info(string.Format("Reached endSec={0:F3}s (levelElapsed={1:F3}s). Finishing.", Config.endSec, levelElapsed));
                FinishRender(false, "done");
                return;
            }

            // Check natural level completion (Won state)
            if (ctrl != null)
            {
                if (!_wonReached && ctrl.currentState == States.Won)
                {
                    _wonReached = true;
                    _wonElapsedSec = 0.0;
                    AgentLog.Info(string.Format("Level reached States.Won at levelElapsed={0:F3}s! Rendering {1:F2}s end padding.",
                        levelElapsed, Config.endPaddingSec));
                }
                else if (_wonReached)
                {
                    _wonElapsedSec += VirtualClock.Dt;
                    if (_wonElapsedSec >= (double)Config.endPaddingSec)
                    {
                        AgentLog.Info("End padding complete. Finishing render.");
                        FinishRender(false, "done");
                        return;
                    }
                }
                else if (_lastTileEntryTime > 0.0 && levelElapsed > _lastTileEntryTime + (double)Config.endPaddingSec + 20.0)
                {
                    AgentLog.Warn("Exceeded lastTileEntryTime + 20s safety margin without States.Won; finishing cleanly.");
                    FinishRender(false, "done");
                    return;
                }
            }
        }

        private IEnumerator EndOfFrameCaptureLoop()
        {
            WaitForEndOfFrame waitEof = new WaitForEndOfFrame();
            while (true)
            {
                yield return waitEof;

                if (_stage == Stage.Capturing && _pipe != null)
                {
                    scrCamera cam = scrCamera.instance;
                    if (cam != null)
                    {
                        UiAndQualityPatches.EnforceCamRT(cam);
                        RenderTexture rt = UiAndQualityPatches.GetFinalCameraTexture(cam);
                        if (rt != null)
                        {
                            int stride = Math.Max(1, Config.captureStride);
                            bool shouldEmit = (_captureSimTick % stride) == 0;
                            _captureSimTick++;

                            if (shouldEmit)
                            {
                                if (!_hasCapturedFirstFrame)
                                {
                                    _hasCapturedFirstFrame = true;
                                    _captureStartDsp = VirtualClock.CurrentDsp;
                                    AudioTimelineExporter.CaptureStartDsp = _captureStartDsp;
                                    AgentLog.Info(string.Format("Captured frame 0: captureStartDsp={0:F6}, rt={1}x{2}, stride={3}",
                                        _captureStartDsp, rt.width, rt.height, stride));
                                }

                                _pipe.CaptureFrame(rt);

                                int frames = _pipe.FramesSubmitted;
                                int progInterval = Config.previewMode ? 4 : 15;
                                if (frames - _lastProgressFrame >= progInterval || frames == 1)
                                {
                                    _lastProgressFrame = frames;
                                    double levelElapsed = VirtualClock.CurrentDsp - _levelStartDsp;
                                    ReportProgress("rendering", GetCurrentFloorIndex(), _totalFloors, levelElapsed, _estimatedTotalSec);
                                }
                            }
                        }
                    }
                }

                // Duty-cycle GPU throttling to respect maxGpuUsagePercent (e.g. <= 30%)
                ThrottleGpuDutyCycle();
            }
        }

        private void MuteAllLocalAudio()
        {
            AudioListener.volume = 0f;
            try
            {
                scrConductor cond = scrConductor.instance;
                if (cond != null)
                {
                    if (cond.song != null && !cond.song.mute) cond.song.mute = true;
                    if (cond.song2 != null && !cond.song2.mute) cond.song2.mute = true;
                    if (cond.song3 != null && !cond.song3.mute) cond.song3.mute = true;
                }
                if (scnGame.instance != null && scnGame.instance.videoBG != null)
                {
                    if (scnGame.instance.videoBG.audioOutputMode != VideoAudioOutputMode.None)
                        scnGame.instance.videoBG.audioOutputMode = VideoAudioOutputMode.None;
                }
            }
            catch { }
        }

        private void ThrottleGpuDutyCycle()
        {
            if (Config == null) return;
            if (_stage == Stage.Done || _stage == Stage.Finishing || _stage == Stage.IdleWarm) return;
            _gpuPacer.PaceFrame(Config.maxGpuUsagePercent, _pipe, UiAndQualityPatches.FastSeekSkipRender);
        }

        private void SyncVideoBackgroundIfPresent()
        {
            try
            {
                if (scnGame.instance == null || scrConductor.instance == null) return;
                VideoPlayer vp = scnGame.instance.videoBG;
                if (vp == null || !vp.isPrepared || !VirtualClock.Active) return;
                scrVfxPlus vfx = scrVfxPlus.instance;
                double vidOff = (vfx != null) ? (double)vfx.vidOffset : 0.0;
                double targetTime = scrConductor.instance.songposition_minusv + vidOff;
                if (targetTime >= 0.0 && vp.length > 0.0)
                {
                    if (vp.isLooping) targetTime = targetTime % vp.length;
                    if (targetTime <= vp.length && Math.Abs(vp.time - targetTime) > (1.5 * VirtualClock.Dt))
                    {
                        vp.time = targetTime;
                    }
                }
            }
            catch { }
        }

        private int GetCurrentFloorIndex()
        {
            try
            {
                if (scrController.instance != null && scrController.instance.currFloor != null)
                    return scrController.instance.currFloor.seqID;
            }
            catch { }
            return 0;
        }

        private bool CheckCancelRequested()
        {
            if (Config == null || string.IsNullOrEmpty(Config.cancelSentinelPath)) return false;
            try { return File.Exists(Config.cancelSentinelPath); }
            catch { return false; }
        }

        private void TransitionTo(Stage next)
        {
            _stage = next;
            _stageFrames = 0;
        }

        private int _finalFrames = 0;

        private void ReportProgress(string stageName, int curFloor, int totalFloors, double songSec, double estTotalSec)
        {
            int frames = (_pipe != null) ? _pipe.FramesSubmitted : _finalFrames;
            double wallSec = (_renderWallClock != null) ? _renderWallClock.Elapsed.TotalSeconds : 0.0;
            double renderFps = (wallSec > 0.05) ? ((double)frames / wallSec) : 0.0;
            int stride = Math.Max(1, Config.captureStride);
            double outFps = (double)Math.Max(1, Config.fps) / (double)stride;
            double videoSec = (double)frames / Math.Max(0.1, outFps);

            CultureInfo inv = CultureInfo.InvariantCulture;
            string json = "{" + string.Format(inv,
                "\"stage\":\"{0}\",\"frames\":{1},\"videoSec\":{2:F3},\"songSec\":{3:F3},\"estTotalSec\":{4:F3},\"floor\":{5},\"totalFloors\":{6},\"renderFps\":{7:F2},\"wallSec\":{8:F2}",
                AgentLog.EscapeJson(stageName), frames, videoSec, songSec, estTotalSec, curFloor, totalFloors, renderFps, wallSec) + "}";
            AgentLog.WriteProgress(json);
        }

        private void FinishRender(bool cancelled, string finalStage)
        {
            if (_stage == Stage.Finishing || _stage == Stage.Done) return;
            _stage = Stage.Finishing;
            AgentLog.Info(string.Format("PlanetTailArc stats: enabled={0}, particles={1}, arcTicks={2}, linearTicks={3}", PlanetTailArc.Enabled, PlanetTailArc.ParticlesEmitted, PlanetTailArc.ArcTicks, PlanetTailArc.LinearTicks));
            PlanetTailArc.Enabled = false;
            PlanetTailArc.Restore();

            _finalFrames = (_pipe != null) ? _pipe.FramesSubmitted : _finalFrames;
            AudioTimelineExporter.CaptureEndDsp = VirtualClock.CurrentDsp;
            AudioTimelineExporter.TotalCapturedFrames = _finalFrames;

            int ffmpegExit = 0;
            if (_pipe != null)
            {
                ffmpegExit = _pipe.Finish(cancelled);
                _pipe = null;
            }

            if (!cancelled)
            {
                AudioTimelineExporter.WriteTimelineJson(Config.audioTimelinePath);
            }

            VirtualClock.Disengage();
            UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, false);
            UiAndQualityPatches.ResolutionOverrideActive = false;
            ExperimentalRenderOptimizer.ShutdownAndLogSummary();
            if (Config != null && Config.previewMode)
            {
                string[] names = new string[_previewSuppressedFilters.Count];
                _previewSuppressedFilters.CopyTo(names);
                Array.Sort(names, StringComparer.Ordinal);
                AgentLog.Info(string.Format("Proxy heavy-filter bypass: suppressed {0} distinct filters ({1} disables): [{2}]",
                    names.Length, _previewSuppressedHits, string.Join(", ", names)));
            }
            AgentLog.Info(string.Format("GpuPacer summary: nvml={0}, avg3d={1:F1}%, peak3d={2}%, peakEnc={3}% (cap={4}%)",
                _gpuPacer.NvmlActive, _gpuPacer.Avg3dUtilObserved, _gpuPacer.Peak3dUtilObserved, _gpuPacer.PeakEncUtilObserved,
                Config != null ? Config.maxGpuUsagePercent : 0));

            if (!cancelled && ffmpegExit != 0)
            {
                finalStage = "ffmpeg_failed_" + ffmpegExit;
            }

            double levelElapsed = VirtualClock.CurrentDsp - _levelStartDsp;
            ReportProgress(finalStage, GetCurrentFloorIndex(), _totalFloors, levelElapsed, _estimatedTotalSec);

            bool canStayWarm = (!cancelled && ffmpegExit == 0 && finalStage == "done" &&
                Config != null && Config.keepAliveAfterFinish && !string.IsNullOrEmpty(Config.sessionDir) &&
                scnGame.instance != null && scrController.instance != null);

            if (canStayWarm)
            {
                EnterIdleWarm();
                return;
            }

            AgentLog.Info("RenderAgent exiting with stage=" + finalStage + ", ffmpegExit=" + ffmpegExit);
            ShutdownProcess(cancelled || ffmpegExit != 0 ? 1 : 0);
        }

        private void EnterIdleWarm()
        {
            try
            {
                _warmSessionDir = Config.sessionDir;
                _warmLoadedLevelPath = Config.levelPath;
                _warmParentPid = Config.parentPid;
                _warmIdleTimeoutSec = (Config.idleTimeoutSec > 0) ? Config.idleTimeoutSec : 300;
                _idleWatch = Stopwatch.StartNew();
                _lastParentCheckSec = 0.0;

                // Stop any active audio/video playback & put cameras to sleep (0% GPU idle)
                MuteAllLocalAudio();
                try
                {
                    if (scrConductor.instance != null)
                    {
                        if (scrConductor.instance.song != null) scrConductor.instance.song.Stop();
                        if (scrConductor.instance.song2 != null) scrConductor.instance.song2.Stop();
                        if (scrConductor.instance.song3 != null) scrConductor.instance.song3.Stop();
                    }
                    if (scnGame.instance != null && scnGame.instance.videoBG != null && scnGame.instance.videoBG.isPlaying)
                        scnGame.instance.videoBG.Stop();
                }
                catch { }

                UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, true);
                Time.timeScale = 0f;
                Application.targetFrameRate = 10;

                if (!Directory.Exists(_warmSessionDir))
                    Directory.CreateDirectory(_warmSessionDir);

                string readyPath = Path.Combine(_warmSessionDir, "ready.json");
                string tmpPath = readyPath + ".tmp";
                string json = "{" + string.Format(CultureInfo.InvariantCulture,
                    "\"pid\":{0},\"levelPath\":\"{1}\",\"state\":\"idle\"",
                    Process.GetCurrentProcess().Id,
                    AgentLog.EscapeJson(_warmLoadedLevelPath ?? "")) + "}";
                File.WriteAllText(tmpPath, json, Encoding.UTF8);
                if (File.Exists(readyPath)) File.Delete(readyPath);
                File.Move(tmpPath, readyPath);

                AgentLog.Info("Entering IdleWarm resident session (level & decorations kept loaded in RAM/VRAM).");
                TransitionTo(Stage.IdleWarm);
            }
            catch (Exception ex)
            {
                AgentLog.Warn("EnterIdleWarm failed, exiting normally: " + ex.Message);
                ShutdownProcess(0);
            }
        }

        private void UpdateIdleWarm()
        {
            MuteAllLocalAudio();
            Application.runInBackground = true;

            try
            {
                if (!string.IsNullOrEmpty(_warmSessionDir))
                {
                    if (File.Exists(Path.Combine(_warmSessionDir, "shutdown.flag")))
                    {
                        AgentLog.Info("IdleWarm received shutdown.flag; exiting cleanly.");
                        ShutdownProcess(0);
                        return;
                    }

                    string nextJobPath = Path.Combine(_warmSessionDir, "next_job.json");
                    if (File.Exists(nextJobPath))
                    {
                        string json = null;
                        try
                        {
                            json = File.ReadAllText(nextJobPath, Encoding.UTF8);
                            File.Delete(nextJobPath);
                        }
                        catch (IOException)
                        {
                            // File still being replaced; retry next tick
                            Thread.Sleep(25);
                            return;
                        }

                        try
                        {
                            string readyPath = Path.Combine(_warmSessionDir, "ready.json");
                            if (File.Exists(readyPath)) File.Delete(readyPath);
                        }
                        catch { }

                        RenderJobConfig nextCfg = JsonUtility.FromJson<RenderJobConfig>(json);
                        if (nextCfg == null || string.IsNullOrEmpty(nextCfg.levelPath))
                        {
                            AgentLog.Error("IdleWarm received invalid next_job.json; exiting.");
                            ShutdownProcess(1);
                            return;
                        }
                        BeginWarmJob(nextCfg);
                        return;
                    }
                }

                double idleSec = (_idleWatch != null) ? _idleWatch.Elapsed.TotalSeconds : 0.0;
                if (idleSec > (double)_warmIdleTimeoutSec)
                {
                    AgentLog.Info(string.Format("IdleWarm timeout ({0}s) reached; releasing memory and exiting.", _warmIdleTimeoutSec));
                    ShutdownProcess(0);
                    return;
                }

                if (_warmParentPid > 0 && (idleSec - _lastParentCheckSec) >= 2.0)
                {
                    _lastParentCheckSec = idleSec;
                    bool parentAlive = false;
                    try
                    {
                        using (Process pp = Process.GetProcessById(_warmParentPid))
                        {
                            parentAlive = !pp.HasExited;
                        }
                    }
                    catch { parentAlive = false; }

                    if (!parentAlive)
                    {
                        AgentLog.Info("Parent GUI process exited; IdleWarm self-terminating.");
                        ShutdownProcess(0);
                        return;
                    }
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("UpdateIdleWarm exception: " + ex.Message);
            }

            Thread.Sleep(60);
        }

        private void BeginWarmJob(RenderJobConfig newCfg)
        {
            try
            {
                Config = newCfg;
                AgentLog.Init(newCfg.logPath, newCfg.progressPath);
                AgentLog.Info("=== AdofaiRenderAgent Warm Session Reuse ===");
                AgentLog.Info("Level: " + newCfg.levelPath);
                AgentLog.Info(string.Format(CultureInfo.InvariantCulture,
                    "Warm target: {0}x{1} @ {2}fps, previewMode={3}, trim=[{4:F2}s, {5:F2}s], endPad={6:F2}s",
                    newCfg.width, newCfg.height, newCfg.fps, newCfg.previewMode,
                    newCfg.startSec, newCfg.endSec, newCfg.endPaddingSec));

                if (!string.Equals(Path.GetFullPath(newCfg.levelPath), Path.GetFullPath(_warmLoadedLevelPath), StringComparison.OrdinalIgnoreCase))
                {
                    AgentLog.Warn("Warm session level mismatch; aborting warm pass for cold reload.");
                    ReportProgress("warm_failed", 0, _totalFloors, 0.0, 0.0);
                    ShutdownProcess(1);
                    return;
                }

                // Reset all per-job runtime state cleanly
                _isWarmReuse = true;
                _stageFrames = 0;
                _settledFrames = 0;
                _pipe = null;
                _renderWallClock = null;
                _fastSeekPrerollRestored = false;
                _levelStartDsp = 0.0;
                _fastStartFloor = -1;
                _checkpointRestartIssued = true;
                _captureStartDsp = 0.0;
                _wonElapsedSec = 0.0;
                _estimatedTotalSec = 0.0;
                _hasCapturedFirstFrame = false;
                _wonReached = false;
                _lastProgressFrame = -100;
                _captureSimTick = 0;
                _finalFrames = 0;
                _previewSuppressedFilters.Clear();
                _previewSuppressedHits = 0;

                _gpuPacer.ResetStats();
                AudioTimelineExporter.Init(newCfg.sfxCacheDir, newCfg.captureAudioClips);
                PlanetTailArc.Enabled = false;
                PlanetTailArc.Reset();
                PlanetTailArc.Restore();
                VirtualClock.Disengage();

                Time.timeScale = 1f;
                QualitySettings.vSyncCount = 0;
                Application.targetFrameRate = -1;
                Application.runInBackground = true;
                MuteAllLocalAudio();
                ParkUnityWindowOffscreen();

                UiAndQualityPatches.UpdateConfig(newCfg);
                UiAndQualityPatches.ForceZeroCalibrationOffsets();
                UiAndQualityPatches.ShouldTriggerStart = false;
                UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, false);
                UiAndQualityPatches.ResolutionOverrideActive = true;

                int warmCk = ComputeFastStartFloor();
                if (warmCk > 0)
                {
                    GCS.checkpointNum = warmCk;
                    _fastStartFloor = warmCk;
                    AgentLog.Info(string.Format(CultureInfo.InvariantCulture,
                        "Warm fast start official checkpoint scrub: GCS.checkpointNum={0} (target startSec={1:F2}s)",
                        warmCk, newCfg.startSec));
                }
                else
                {
                    GCS.checkpointNum = 0;
                    _fastStartFloor = -1;
                }
                try { RDC.auto = true; RDC.noHud = true; RDC.noAutoHud = true; } catch { }

                try { if (scrController.instance != null) scrController.instance.StopCoroutine("WaitForStartCo"); } catch { }
                if (_fiWaitForStartCallCount != null && scrController.instance != null)
                    _fiWaitForStartCallCount.SetValue(scrController.instance, 0);
                if (_fiPlayedHitSounds != null && scrConductor.instance != null)
                    _fiPlayedHitSounds.SetValue(scrConductor.instance, false);

                if (scrUIController.instance != null)
                {
                    try { scrUIController.instance.HideEndscreenLanterns(); } catch { }
                }

                // Ensure all floors are active before resetting scene (mirrors scrController.ResetCustomLevel)
                scrLevelMaker lm = scrLevelMaker.instance;
                if (lm != null && lm.listFloors != null)
                {
                    for (int i = 0; i < lm.listFloors.Count; i++)
                    {
                        scrFloor fl = lm.listFloors[i];
                        if (fl != null && fl.gameObject != null && !fl.gameObject.activeSelf)
                            fl.gameObject.SetActive(true);
                    }
                }

                // Zero-reload in-memory restart: keeps decoded decorations/textures/audio/shaders in RAM/VRAM
                // while completely resetting filters, VFX, tweens, floors, decorations, video, and conductor.
                // Must pass remakeFloors=true to BOTH ResetScene and Play (matching PortalTravelAction / SetPracticeMode)
                // so FinishCustomLevelLoading re-applies all floor events (trackDisappearAnimation, MoveTrack, etc.).
                ResetCameraFilterTimeAccumulators();
                scnGame.instance.ResetScene(true);
                scnGame.instance.Play(warmCk > 0 ? warmCk : 0, true);

                // Mirror scrController.ResetCustomLevel completion: clear transitioningLevel & re-enable player
                // responsiveness (States.Won sets scrPlayer.responsive = false at the end of a completed pass).
                try
                {
                    if (_fiTransitioningLevel != null && scrController.instance != null)
                        _fiTransitioningLevel.SetValue(scrController.instance, false);
                }
                catch { }
                try
                {
                    if (ADOBase.playerManager != null)
                        ADOBase.playerManager.SetAllPlayerResponsive(true);
                }
                catch { }

                if (scrCamera.instance != null)
                {
                    scrCamera.instance.forceRTCam = true;
                    UiAndQualityPatches.EnforceCamRT(scrCamera.instance);
                }
                UiAndQualityPatches.ReapplyPlanetColors();
                UiAndQualityPatches.SuppressAllOutOfLevelUI();

                ReportProgress("loading_level", 0, _totalFloors, 0.0, 0.0);
                TransitionTo(Stage.WaitingForLevelLoad);
            }
            catch (Exception ex)
            {
                AgentLog.Error("BeginWarmJob failed (falling back to cold start): " + ex);
                ReportProgress("warm_failed", 0, _totalFloors, 0.0, 0.0);
                ShutdownProcess(1);
            }
        }

        private void ResetCameraFilterTimeAccumulators()
        {
            try
            {
                Camera[] cams = Camera.allCameras;
                if (cams == null) return;
                for (int c = 0; c < cams.Length; c++)
                {
                    Camera cam = cams[c];
                    if (cam == null) continue;
                    _previewCamComps.Clear();
                    cam.GetComponents<MonoBehaviour>(_previewCamComps);
                    for (int i = 0; i < _previewCamComps.Count; i++)
                    {
                        MonoBehaviour mb = _previewCamComps[i];
                        if (mb == null) continue;
                        Type t = mb.GetType();
                        if (!t.Name.StartsWith("CameraFilterPack", StringComparison.Ordinal)) continue;
                        FieldInfo fi = t.GetField("TimeX", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                        if (fi != null && fi.FieldType == typeof(float))
                            fi.SetValue(mb, 1.0f);
                    }
                }
            }
            catch { }
        }

        private void ShutdownProcess(int exitCode)
        {
            _stage = Stage.Done;
            try
            {
                if (!string.IsNullOrEmpty(_warmSessionDir))
                {
                    string readyPath = Path.Combine(_warmSessionDir, "ready.json");
                    if (File.Exists(readyPath)) File.Delete(readyPath);
                }
            }
            catch { }
            try { _gpuPacer.Shutdown(); } catch { }
            try { Process.GetCurrentProcess().Kill(); } catch { }
            try { Environment.Exit(exitCode); } catch { }
        }
    }
}
