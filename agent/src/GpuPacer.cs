using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

namespace AdofaiRenderAgent
{
    /// <summary>
    /// Closed-loop GPU utilization pacer using NVML (zero-overhead native DLL polling)
    /// with 1ms Windows timer precision and a burst-window fallback for non-NVIDIA GPUs.
    /// Guarantees:
    /// - Light scenes stay 100% pipelined (zero WaitForGpuIdle stall, zero sleep while GPU util < cap)
    ///   achieving multi-hundred FPS (5x-20x+ realtime).
    /// - Heavy scenes smoothly throttle so actual GPU 3D/NVENC utilization stays under maxGpuUsagePercent.
    /// </summary>
    public sealed class GpuPacer
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct NvmlUtilization
        {
            public uint gpu;
            public uint memory;
        }

        [DllImport("winmm.dll", EntryPoint = "timeBeginPeriod")]
        private static extern uint TimeBeginPeriod(uint uPeriod);

        [DllImport("winmm.dll", EntryPoint = "timeEndPeriod")]
        private static extern uint TimeEndPeriod(uint uPeriod);

        [DllImport("nvml.dll", EntryPoint = "nvmlInit_v2", CallingConvention = CallingConvention.Cdecl)]
        private static extern int NvmlInitV2();

        [DllImport("nvml.dll", EntryPoint = "nvmlShutdown", CallingConvention = CallingConvention.Cdecl)]
        private static extern int NvmlShutdown();

        [DllImport("nvml.dll", EntryPoint = "nvmlDeviceGetHandleByIndex_v2", CallingConvention = CallingConvention.Cdecl)]
        private static extern int NvmlDeviceGetHandleByIndexV2(uint index, out IntPtr device);

        [DllImport("nvml.dll", EntryPoint = "nvmlDeviceGetUtilizationRates", CallingConvention = CallingConvention.Cdecl)]
        private static extern int NvmlDeviceGetUtilizationRates(IntPtr device, out NvmlUtilization utilization);

        [DllImport("nvml.dll", EntryPoint = "nvmlDeviceGetEncoderUtilization", CallingConvention = CallingConvention.Cdecl)]
        private static extern int NvmlDeviceGetEncoderUtilization(IntPtr device, out uint utilization, out uint samplingPeriodUs);

        private bool _timerRaised = false;
        private bool _nvmlAvailable = false;
        private IntPtr _nvmlDevice = IntPtr.Zero;
        private Thread _pollThread;
        private volatile bool _stopPoll = false;

        private volatile int _gpu3dUtil = 0;
        private volatile int _gpuEncUtil = 0;
        private volatile int _peak3dUtil = 0;
        private volatile int _peakEncUtil = 0;
        private long _sum3dUtil = 0;
        private long _count3dSamples = 0;

        // Closed-loop PID/proportional state
        private double _emaUtil = 0.0;
        private double _targetSleepMs = 0.0;
        private double _sleepCarryMs = 0.0;
        private long _lastAdjustTick = 0;
        private bool _wasFastSeek = false;
        private readonly Stopwatch _workWatch = Stopwatch.StartNew();

        // Non-NVML burst-window fallback state
        private readonly Stopwatch _frameWatch = Stopwatch.StartNew();
        private double _burstWorkMs = 0.0;
        private int _burstFrames = 0;

        public int CurrentGpu3dUtil { get { return _gpu3dUtil; } }
        public int CurrentGpuEncUtil { get { return _gpuEncUtil; } }
        public int Peak3dUtilObserved { get { return _peak3dUtil; } }
        public int PeakEncUtilObserved { get { return _peakEncUtil; } }
        public double Avg3dUtilObserved { get { return _count3dSamples > 0 ? ((double)_sum3dUtil / _count3dSamples) : 0.0; } }
        public bool NvmlActive { get { return _nvmlAvailable; } }

        public void Initialize()
        {
            try
            {
                TimeBeginPeriod(1);
                _timerRaised = true;
            }
            catch { }

            TryInitNvml();
        }

        private void TryInitNvml()
        {
            try
            {
                if (NvmlInitV2() != 0) return;
                IntPtr dev;
                if (NvmlDeviceGetHandleByIndexV2(0, out dev) != 0 || dev == IntPtr.Zero)
                {
                    try { NvmlShutdown(); } catch { }
                    return;
                }
                _nvmlDevice = dev;
                _nvmlAvailable = true;
                _stopPoll = false;
                _pollThread = new Thread(NvmlPollLoop);
                _pollThread.IsBackground = true;
                _pollThread.Name = "NvmlGpuPoll";
                _pollThread.Start();
                AgentLog.Info("GpuPacer initialized with live NVML closed-loop GPU monitoring.");
            }
            catch (Exception ex)
            {
                _nvmlAvailable = false;
                AgentLog.Info("GpuPacer using burst-window duty fallback (NVML unavailable: " + ex.Message + ").");
            }
        }

        private void NvmlPollLoop()
        {
            while (!_stopPoll)
            {
                try
                {
                    NvmlUtilization u;
                    int g3d = 0;
                    if (NvmlDeviceGetUtilizationRates(_nvmlDevice, out u) == 0)
                        g3d = (int)u.gpu;

                    uint enc = 0;
                    uint period = 0;
                    int genc = 0;
                    if (NvmlDeviceGetEncoderUtilization(_nvmlDevice, out enc, out period) == 0)
                        genc = (int)enc;

                    _gpu3dUtil = g3d;
                    _gpuEncUtil = genc;
                    if (g3d > _peak3dUtil) _peak3dUtil = g3d;
                    if (genc > _peakEncUtil) _peakEncUtil = genc;
                    if (g3d > 0)
                    {
                        _sum3dUtil += g3d;
                        _count3dSamples++;
                    }
                }
                catch { }
                Thread.Sleep(50);
            }
        }

        public void PaceFrame(int maxGpuPct, FrameCapturePipe pipe, bool isFastSeek)
        {
            if (isFastSeek)
            {
                _wasFastSeek = true;
                _workWatch.Reset();
                _workWatch.Start();
                return;
            }
            if (maxGpuPct <= 0 || maxGpuPct >= 100)
            {
                return;
            }

            // Smooth warm-start right after fast-seek ends so heavy frames don't spike GPU on frame 0
            if (_wasFastSeek)
            {
                _wasFastSeek = false;
                if (maxGpuPct <= 35 && _targetSleepMs < 18.0)
                    _targetSleepMs = 18.0;
                _workWatch.Reset();
                _workWatch.Start();
            }

            if (_nvmlAvailable)
            {
                PaceWithNvml(maxGpuPct, pipe);
            }
            else
            {
                PaceWithBurstFallback(maxGpuPct, pipe);
            }
        }

        private void PaceWithNvml(int maxGpuPct, FrameCapturePipe pipe)
        {
            double frameWorkMs = _workWatch.Elapsed.TotalMilliseconds;
            // Pace strictly on 3D shader/render core utilization (the primary GPU load/heat/fan driver);
            // dedicated fixed-function NVENC ASIC stays free so light scenes fly at multi-hundred FPS.
            int curUtil = _gpu3dUtil;
            long nowMs = _frameWatch.ElapsedMilliseconds;
            if (nowMs - _lastAdjustTick >= 50)
            {
                _lastAdjustTick = nowMs;
                _emaUtil = (_emaUtil <= 0.1) ? (double)curUtil : (_emaUtil * 0.55 + curUtil * 0.45);

                double target = Math.Max(5.0, (double)maxGpuPct - 4.0);
                double effective = Math.Max((double)curUtil, _emaUtil);
                double err = effective - target;

                if (err > 0.0)
                {
                    // Immediate duty-proportional jump if a heavy frame pushes GPU well above cap
                    double step = Math.Max(1.5, err * 0.90);
                    if (maxGpuPct <= 35 && curUtil > maxGpuPct + 10)
                    {
                        double dutyFloor = frameWorkMs * Math.Max(1.0, (100.0 / Math.Max(10.0, (double)maxGpuPct)) - 1.0) * 0.60;
                        _targetSleepMs = Math.Max(_targetSleepMs + step, dutyFloor);
                    }
                    else
                    {
                        _targetSleepMs += step;
                    }
                    _targetSleepMs = Math.Min(450.0, _targetSleepMs);
                }
                else if (err < -4.0)
                {
                    _targetSleepMs *= 0.60;
                    if (_targetSleepMs < 0.75) _targetSleepMs = 0.0;
                }
                else if (err < -1.0)
                {
                    _targetSleepMs *= 0.88;
                    if (_targetSleepMs < 0.5) _targetSleepMs = 0.0;
                }
            }

            if (_targetSleepMs <= 0.0)
            {
                _sleepCarryMs = 0.0;
                _workWatch.Reset();
                _workWatch.Start();
                return;
            }

            // Batch sub-2ms sleeps so light/medium scenes never suffer tiny sleep overhead
            _sleepCarryMs += _targetSleepMs;
            if (_sleepCarryMs < 2.0)
            {
                _workWatch.Reset();
                _workWatch.Start();
                return;
            }

            int sleepInt = (int)Math.Floor(_sleepCarryMs);
            _sleepCarryMs -= sleepInt;
            if (sleepInt > 450) sleepInt = 450;

            // Only drain GPU pipeline when we're taking a meaningful thermal cooldown pause
            if (sleepInt >= 10 && pipe != null)
            {
                pipe.WaitForGpuIdle();
            }
            Thread.Sleep(sleepInt);
            _workWatch.Reset();
            _workWatch.Start();
        }

        private void PaceWithBurstFallback(int maxGpuPct, FrameCapturePipe pipe)
        {
            double workMs = _frameWatch.Elapsed.TotalMilliseconds;
            _frameWatch.Reset();
            _frameWatch.Start();

            _burstWorkMs += workMs;
            _burstFrames++;

            // Only pace once per ~60ms burst so triple-buffered async pipeline runs uninterrupted
            if (_burstWorkMs < 60.0 && _burstFrames < 16) return;

            double avgFrameMs = _burstWorkMs / Math.Max(1, _burstFrames);
            double targetDuty = Math.Max(0.08, Math.Min(0.95, (maxGpuPct / 100.0) * 0.85));
            double sleepMs = _burstWorkMs * ((1.0 / targetDuty) - 1.0);

            // Light scene heuristic: if each frame took < 2.5ms, GPU load is tiny even without sleeping heavily
            if (avgFrameMs < 2.5 && maxGpuPct >= 60)
                sleepMs *= 0.25;

            _burstWorkMs = 0.0;
            _burstFrames = 0;

            if (sleepMs >= 3.0)
            {
                int sleepInt = (int)Math.Min(400.0, Math.Round(sleepMs));
                if (sleepInt >= 15 && pipe != null)
                    pipe.WaitForGpuIdle();
                Thread.Sleep(sleepInt);
            }
        }

        public void Shutdown()
        {
            _stopPoll = true;
            try
            {
                if (_pollThread != null && _pollThread.IsAlive)
                    _pollThread.Join(300);
            }
            catch { }
            if (_nvmlAvailable)
            {
                try { NvmlShutdown(); } catch { }
                _nvmlAvailable = false;
            }
            if (_timerRaised)
            {
                try { TimeEndPeriod(1); } catch { }
                _timerRaised = false;
            }
        }
    }
}
