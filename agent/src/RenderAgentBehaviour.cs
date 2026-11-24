using System;
using System.Collections;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
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
            Done
        }

        public static RenderJobConfig Config;
        private Stage _stage = Stage.Boot;
        private int _stageFrames = 0;
        private int _settledFrames = 0;
        private FrameCapturePipe _pipe;
        private Stopwatch _renderWallClock;
        private readonly GpuPacer _gpuPacer = new GpuPacer();
        private bool _fastSeekPrerollRestored = false;

        private double _levelStartDsp = 0.0;
        private int _fastStartFloor = -1;
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
        private static readonly FieldInfo _fiPlayedHitSounds =
            typeof(scrConductor).GetField("playedHitSounds", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

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
                UiAndQualityPatches.SuppressAllOutOfLevelUI();
                SyncVideoBackgroundIfPresent();
                if (UiAndQualityPatches.FastSeekSkipRender && scrCamera.instance != null)
                {
                    UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, true);
                }
                if (Config != null && Config.previewMode)
                    DisableMotionBlurForPreview();
                if (_stage == Stage.Capturing && PlanetTailArc.Enabled)
                {
                    try { PlanetTailArc.Tick(VirtualClock.Dt); }
                    catch (Exception ex) { AgentLog.Warn("PlanetTailArc: " + ex.Message); PlanetTailArc.Enabled = false; PlanetTailArc.Restore(); }
                }
            }
        }

        private UnityStandardAssets.ImageEffects.CameraMotionBlur[] _motionBlurs = new UnityStandardAssets.ImageEffects.CameraMotionBlur[0];
        private int _motionBlurScan = 0;

        // Rough preview only: the level's MotionBlur filter smears the thumbnail frames, so keep it switched off.
        private void DisableMotionBlurForPreview()
        {
            try
            {
                if ((_motionBlurScan++ % 30) == 0)
                    _motionBlurs = UnityEngine.Object.FindObjectsOfType<UnityStandardAssets.ImageEffects.CameraMotionBlur>();
                for (int i = 0; i < _motionBlurs.Length; i++)
                {
                    UnityStandardAssets.ImageEffects.CameraMotionBlur mb = _motionBlurs[i];
                    if (mb != null && mb.enabled) mb.enabled = false;
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
            int neededSettle = Config.previewMode ? 5 : 12;
            if (_settledFrames < neededSettle) return;

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

        private void UpdateStartingPlayback()
        {
            // Engage deterministic virtual clock BEFORE triggering start!
            double baseDsp = AudioSettings.dspTime;
            if (baseDsp < 10.0) baseDsp = 100.0;
            VirtualClock.Engage(Config.fps, baseDsp);
            VirtualClock.SyncConductor(scrConductor.instance);

            AudioTimelineExporter.Fps = Config.fps;
            _fastStartFloor = -1;
            try
            {
                int ck = Config.startFloor;
                if (ck < 0 && Config.fastStart && Config.startSec > 12.0f)
                {
                    // Elapsed (trim) time = songposition + C with C ~= addoffset - ~0.6s. Pick a floor that is at least
                    // 5s of song time before startSec even for C = addoffset, then simulate/pre-roll the rest normally.
                    scrConductor cd = scrConductor.instance;
                    double addoff = (cd != null) ? Math.Max(0.0, cd.addoffset) : 0.0;
                    double target = (double)Config.startSec - addoff - 5.0;
                    var lf = scrLevelMaker.instance.listFloors;
                    for (int i = 1; i < lf.Count; i++)
                    {
                        if (lf[i] != null && lf[i].entryTime <= target) ck = i; else if (lf[i] != null && lf[i].entryTime > target) break;
                    }
                    if (ck < 1) ck = -1;
                }
                if (ck > 0)
                {
                    GCS.checkpointNum = ck;
                    _fastStartFloor = ck;
                    AgentLog.Info("Fast start: GCS.checkpointNum=" + GCS.checkpointNum + " (skipping simulation of the level before this floor)");
                }
            }
            catch (Exception ex) { AgentLog.Warn("Fast start unavailable, simulating from the beginning: " + ex.Message); _fastStartFloor = -1; }
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
                // songposition = curDsp - dspTimeSong - addoffset - k. Natural start has songposition(at schedule) = -C
                // with C = addoffset + k - delta (delta = lead between level start and song schedule, constant 0.896s).
                // Re-base _levelStartDsp so that "elapsed" keeps the exact same meaning as a from-zero start.
                scrConductor cdc = scrConductor.instance;
                double songpos = cdc.songposition_minusi;
                double k = VirtualClock.CurrentDsp - cdc.dspTimeSong - cdc.addoffset - songpos;
                double C = cdc.addoffset + k - 0.896;
                _levelStartDsp = VirtualClock.CurrentDsp - (songpos + C);
                AgentLog.Info(string.Format("Fast start re-base: songpos={0:F4} k={1:F4} C={2:F4} -> elapsed at schedule={3:F4}", songpos, k, C, songpos + C));
            }
            try
            {
                scrConductor cd0 = scrConductor.instance;
                AgentLog.Info(string.Format("PROBE music-scheduled: curDsp={0:F4} songpos_minusi={1:F4} dspTimeSong={2:F4} addoffset={3:F4} countdownTicks={4} crotchetAtStart={5:F5} cdSpeedMul={6} floor={7} ckpt={8}",
                    VirtualClock.CurrentDsp, cd0.songposition_minusi, cd0.dspTimeSong, cd0.addoffset, cd0.countdownTicks, cd0.crotchetAtStart, cd0.countdownSpeedMultiplier, GetCurrentFloorIndex(), GCS.checkpointNum));
            }
            catch (Exception ex) { AgentLog.Warn("probe2: " + ex.Message); }
            AgentLog.Info(string.Format("Music scheduled! levelStartDsp={0:F6}, dspTimeSong={1:F6}, songStartDsp={2:F6}",
                _levelStartDsp, AudioTimelineExporter.DspTimeSong, AudioTimelineExporter.SongStartDsp));

            if (Config.startSec > 0.001f)
            {
                _fastSeekPrerollRestored = false;
                if (Config.startSec >= 5.0f && scrCamera.instance != null)
                {
                    UiAndQualityPatches.SetFastSeekCameraSkip(scrCamera.instance, true);
                    AgentLog.Info(string.Format("Fast-seek GPU render skip enabled until {0:F2}s (3.0s full-render preroll before startSec={1:F3}s).",
                        Config.startSec - 3.0f, Config.startSec));
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
            try
            {
                scrConductor cd = scrConductor.instance;
                AgentLog.Info(string.Format("PROBE capture-begin: elapsed={0:F4} levelStartDsp={1:F4} curDsp={2:F4} songpos_minusi={3:F4} songpos_minusv={4:F4} floor={5}",
                    VirtualClock.CurrentDsp - _levelStartDsp, _levelStartDsp, VirtualClock.CurrentDsp, cd.songposition_minusi, cd.songposition_minusv, GetCurrentFloorIndex()));
                var lf = scrLevelMaker.instance.listFloors;
                for (int i = 0; i < lf.Count; i += Math.Max(1, lf.Count / 12))
                    AgentLog.Info(string.Format("PROBE floor{0}: entryTime={1:F4} entryTimePitchAdj={2:F4} afterExtra={3:F4}", i, lf[i].entryTime, lf[i].entryTimePitchAdj, lf[i].entryTimeAfterExtraBeats));
            }
            catch (Exception ex) { AgentLog.Warn("probe: " + ex.Message); }
            PlanetTailArc.Reset();
            PlanetTailArc.Enabled = Config.arcTails;
            AgentLog.Info("Entering Capturing stage.");
            ReportProgress("rendering", GetCurrentFloorIndex(), _totalFloors, 0.0, _estimatedTotalSec);
            TransitionTo(Stage.Capturing);
        }

        private void ProbePlanetTails()
        {
            try
            {
                scrController c = scrController.instance;
                if (c == null || c.planetarySystem == null || c.planetarySystem.allPlanets == null) return;
                foreach (scrPlanet p in c.planetarySystem.allPlanets)
                {
                    if (p == null || p.planetRenderer == null) continue;
                    ParticleSystem ps = p.planetRenderer.tailParticles;
                    if (ps == null) { AgentLog.Info("PROBE planet" + p.planetIndex + ": no tailParticles"); continue; }
                    var m = ps.main; var e = ps.emission; var sh = ps.shape;
                    AgentLog.Info(string.Format("PROBE planet{0}: space={1} life={2}/{3}/{4} speed={5} rateT={6} rateD={7} shape={8} maxP={9} simSpeed={10} unscaled={11} playing={12} startSpd={13} loop={14} scaleMode={15} customSim={16} tr={17}",
                        p.planetIndex, m.simulationSpace, m.startLifetime.mode, m.startLifetime.constantMin, m.startLifetime.constantMax,
                        m.startSpeed.constant, e.rateOverTime.constant, e.rateOverDistance.constant, sh.enabled, m.maxParticles,
                        m.simulationSpeed, m.useUnscaledTime, ps.isPlaying, m.startSpeed.mode, m.loop, m.scalingMode, m.customSimulationSpace != null, ps.trails.enabled));
                }
            }
            catch (Exception ex) { AgentLog.Warn("ProbePlanetTails: " + ex.Message); }
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
            if (_stage == Stage.Done || _stage == Stage.Finishing) return;
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
            AgentLog.Info(string.Format("GpuPacer summary: nvml={0}, avg3d={1:F1}%, peak3d={2}%, peakEnc={3}% (cap={4}%)",
                _gpuPacer.NvmlActive, _gpuPacer.Avg3dUtilObserved, _gpuPacer.Peak3dUtilObserved, _gpuPacer.PeakEncUtilObserved,
                Config != null ? Config.maxGpuUsagePercent : 0));
            _gpuPacer.Shutdown();

            if (!cancelled && ffmpegExit != 0)
            {
                finalStage = "ffmpeg_failed_" + ffmpegExit;
            }

            double levelElapsed = VirtualClock.CurrentDsp - _levelStartDsp;
            ReportProgress(finalStage, GetCurrentFloorIndex(), _totalFloors, levelElapsed, _estimatedTotalSec);
            AgentLog.Info("RenderAgent exiting with stage=" + finalStage + ", ffmpegExit=" + ffmpegExit);

            _stage = Stage.Done;
            // All files & FFmpeg have been cleanly flushed and closed; terminate immediately
            // so Unity/Steamworks shutdown hooks cannot hang in background.
            try { Process.GetCurrentProcess().Kill(); } catch { }
            try { Environment.Exit(cancelled || ffmpegExit != 0 ? 1 : 0); } catch { }
        }
    }
}
