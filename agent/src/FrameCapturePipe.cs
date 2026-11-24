using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

namespace AdofaiRenderAgent
{
    public class FrameCapturePipe
    {
        private readonly int _width;
        private readonly int _height;
        private readonly int _frameBytes;
        private readonly bool _flipVertical;
        private readonly bool _requestedSafeMode;
        private readonly int _maxRamUsagePercent;

        private bool _useSyncReadback = false;
        private int _stagingSlots = 3;
        private int _maxQueuedFrames = 4;

        private RenderTexture[] _staging;
        private Texture2D _syncTex;
        private int _slotIndex = 0;
        private int _inFlightReadbacks = 0;

        private Process _ffmpeg;
        private Stream _stdin;
        private Thread _writerThread;
        private readonly object _queueLock = new object();
        private readonly Queue<byte[]> _writeQueue = new Queue<byte[]>();
        private readonly Stack<byte[]> _bufferPool = new Stack<byte[]>();
        private bool _eof = false;
        private bool _aborted = false;
        private Exception _writerError = null;
        private int _framesSubmitted = 0;
        private int _framesWritten = 0;

        public int FramesSubmitted { get { return _framesSubmitted; } }
        public int FramesWritten { get { return _framesWritten; } }
        public bool HasError { get { return _writerError != null || _aborted; } }
        public string LastError { get { return _writerError != null ? _writerError.Message : ""; } }
        public bool UsingSyncFallback { get { return _useSyncReadback; } }

        public FrameCapturePipe(int width, int height, bool flipVertical, bool lowSpecSafeMode, int maxRamUsagePercent)
        {
            _width = width;
            _height = height;
            _frameBytes = width * height * 4;
            _flipVertical = flipVertical;
            _requestedSafeMode = lowSpecSafeMode;
            _maxRamUsagePercent = Math.Max(30, Math.Min(95, maxRamUsagePercent > 0 ? maxRamUsagePercent : 80));
        }

        public bool Start(string ffmpegPath, string ffmpegArgs)
        {
            try
            {
                int vramMb = SystemInfo.graphicsMemorySize;
                bool supportsAsync = SystemInfo.supportsAsyncGPUReadback;
                bool highRes = ((long)_width * (long)_height) >= (2560L * 1440L);

                // Automatic low-spec & RAM budget sizing (zero quality compromise)
                int sysRamMb = SystemInfo.systemMemorySize;
                long frameMb = Math.Max(1L, _frameBytes / (1024L * 1024L));
                // Reserve up to 8% of allowed RAM budget for frame pipe queue (leaving plenty for Unity & FFmpeg)
                long pipeBudgetMb = (sysRamMb > 0) ? (long)(sysRamMb * (_maxRamUsagePercent / 100.0) * 0.08) : 256L;
                int maxByRam = (int)Math.Max(2L, Math.Min(8L, pipeBudgetMb / frameMb));

                bool lowVram = (vramMb > 0 && vramMb < 2048);
                bool highVram = (vramMb >= 4096);
                _useSyncReadback = _requestedSafeMode || !supportsAsync || (lowVram && highRes);
                _stagingSlots = _useSyncReadback ? 1 : (lowVram ? 2 : ((!highRes && highVram) ? 4 : 3));
                _maxQueuedFrames = (_requestedSafeMode || lowVram) ? Math.Min(2, maxByRam) : (highRes ? Math.Min(4, maxByRam) : Math.Min(8, maxByRam));

                AgentLog.Info(string.Format(
                    "FrameCapturePipe init: {0}x{1}, gpu='{2}', api={3}, vram={4}MB, sysRam={5}MB (cap {6}%), asyncReadback={7}, mode={8}, slots={9}, queue={10}",
                    _width, _height, SystemInfo.graphicsDeviceName, SystemInfo.graphicsDeviceType,
                    vramMb, sysRamMb, _maxRamUsagePercent, supportsAsync,
                    _useSyncReadback ? "SYNC_SAFE_READPIXELS" : "ASYNC_GPU_READBACK", _stagingSlots, _maxQueuedFrames));

                _staging = new RenderTexture[_stagingSlots];
                for (int i = 0; i < _stagingSlots; i++)
                {
                    RenderTexture rt = new RenderTexture(_width, _height, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
                    rt.antiAliasing = 1;
                    if (!rt.Create())
                    {
                        AgentLog.Warn("Staging RenderTexture.Create() failed at slot " + i + "; retrying single-slot sync mode.");
                        rt.Release();
                        UnityEngine.Object.Destroy(rt);
                        if (i == 0) return false;
                        // Shrink staging array to what succeeded
                        RenderTexture[] smaller = new RenderTexture[i];
                        Array.Copy(_staging, smaller, i);
                        _staging = smaller;
                        _stagingSlots = i;
                        _useSyncReadback = true;
                        break;
                    }
                    _staging[i] = rt;
                }

                if (_useSyncReadback)
                {
                    EnsureSyncTexture();
                }

                // Pre-allocate buffer pool to eliminate GC pauses at 4K while keeping low-RAM footprint safe
                int poolCount = _maxQueuedFrames + _stagingSlots;
                for (int i = 0; i < poolCount; i++)
                {
                    _bufferPool.Push(new byte[_frameBytes]);
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = ffmpegPath;
                psi.Arguments = ffmpegArgs;
                psi.UseShellExecute = false;
                psi.RedirectStandardInput = true;
                psi.RedirectStandardError = true;
                psi.RedirectStandardOutput = false;
                psi.CreateNoWindow = true;

                AgentLog.Info("Spawning FFmpeg: \"" + ffmpegPath + "\" " + ffmpegArgs);
                _ffmpeg = Process.Start(psi);
                if (_ffmpeg == null)
                {
                    AgentLog.Error("Failed to start FFmpeg process.");
                    return false;
                }

                _stdin = _ffmpeg.StandardInput.BaseStream;
                _ffmpeg.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        AgentLog.Info("[ffmpeg] " + e.Data);
                };
                _ffmpeg.BeginErrorReadLine();

                _writerThread = new Thread(WriterLoop);
                _writerThread.IsBackground = true;
                _writerThread.Name = "FFmpegFrameWriter";
                _writerThread.Start();

                return true;
            }
            catch (Exception ex)
            {
                AgentLog.Error("FrameCapturePipe.Start failed: " + ex);
                return false;
            }
        }

        private void EnsureSyncTexture()
        {
            if (_syncTex == null)
            {
                _syncTex = new Texture2D(_width, _height, TextureFormat.RGBA32, false, false);
            }
        }

        public void WaitForGpuIdle()
        {
            if (_useSyncReadback) return;
            try
            {
                if (_inFlightReadbacks > 0)
                    AsyncGPUReadback.WaitAllRequests();
            }
            catch { }
        }

        public void CaptureFrame(RenderTexture source)
        {
            if (_aborted || source == null || _staging == null) return;

            if (_useSyncReadback)
            {
                CaptureFrameSync(source);
                return;
            }

            // Backpressure so we never overwrite an in-flight staging texture
            if (_inFlightReadbacks >= _stagingSlots)
            {
                AsyncGPUReadback.WaitAllRequests();
            }

            RenderTexture dst = _staging[_slotIndex];
            _slotIndex = (_slotIndex + 1) % _stagingSlots;

            BlitSourceToStaging(source, dst);

            _inFlightReadbacks++;
            _framesSubmitted++;
            try
            {
                AsyncGPUReadback.Request(dst, 0, TextureFormat.RGBA32, OnReadbackComplete);
            }
            catch (Exception ex)
            {
                _inFlightReadbacks--;
                _framesSubmitted--;
                AgentLog.Warn("AsyncGPUReadback.Request threw (" + ex.Message + "); switching permanently to synchronous ReadPixels fallback.");
                _useSyncReadback = true;
                CaptureFrameSync(source);
            }
        }

        private void BlitSourceToStaging(RenderTexture source, RenderTexture dst)
        {
            if (_flipVertical)
            {
                Graphics.Blit(source, dst, new Vector2(1f, -1f), new Vector2(0f, 1f));
            }
            else
            {
                Graphics.Blit(source, dst);
            }
        }

        private void CaptureFrameSync(RenderTexture source)
        {
            RenderTexture dst = _staging[0];
            // Texture2D.ReadPixels reads bottom-to-top in Unity; flip vertically so row 0 is top of image for FFmpeg
            if (_flipVertical)
            {
                Graphics.Blit(source, dst, new Vector2(1f, -1f), new Vector2(0f, 1f));
            }
            else
            {
                Graphics.Blit(source, dst);
            }

            EnsureSyncTexture();
            RenderTexture prevActive = RenderTexture.active;
            try
            {
                RenderTexture.active = dst;
                _syncTex.ReadPixels(new Rect(0, 0, _width, _height), 0, 0, false);
            }
            finally
            {
                RenderTexture.active = prevActive;
            }

            NativeArray<byte> raw = _syncTex.GetRawTextureData<byte>();
            if (raw.Length != _frameBytes)
            {
                AgentLog.Error(string.Format("Sync readback unexpected byte size: got {0}, expected {1}", raw.Length, _frameBytes));
                return;
            }

            byte[] buf = AcquireBuffer();
            raw.CopyTo(buf);
            _framesSubmitted++;
            EnqueueBuffer(buf);
        }

        private void OnReadbackComplete(AsyncGPUReadbackRequest req)
        {
            _inFlightReadbacks--;
            if (_aborted) return;

            if (req.hasError)
            {
                AgentLog.Warn("AsyncGPUReadback reported GPU error; engaging synchronous ReadPixels fallback for subsequent frames.");
                _useSyncReadback = true;
                return;
            }

            NativeArray<byte> data = req.GetData<byte>();
            if (data.Length != _frameBytes)
            {
                AgentLog.Error(string.Format("Unexpected frame byte size: got {0}, expected {1}", data.Length, _frameBytes));
                return;
            }

            byte[] buf = AcquireBuffer();
            data.CopyTo(buf);
            EnqueueBuffer(buf);
        }

        private void EnqueueBuffer(byte[] buf)
        {
            lock (_queueLock)
            {
                while (_writeQueue.Count >= _maxQueuedFrames && !_aborted)
                {
                    Monitor.Wait(_queueLock, 50);
                }
                if (!_aborted)
                {
                    _writeQueue.Enqueue(buf);
                    Monitor.PulseAll(_queueLock);
                }
                else
                {
                    _bufferPool.Push(buf);
                }
            }
        }

        private byte[] AcquireBuffer()
        {
            lock (_queueLock)
            {
                if (_bufferPool.Count > 0)
                    return _bufferPool.Pop();
            }
            return new byte[_frameBytes];
        }

        private void WriterLoop()
        {
            try
            {
                while (true)
                {
                    byte[] buf = null;
                    lock (_queueLock)
                    {
                        while (_writeQueue.Count == 0 && !_eof && !_aborted)
                        {
                            Monitor.Wait(_queueLock, 100);
                        }
                        if (_aborted) break;
                        if (_writeQueue.Count > 0)
                        {
                            buf = _writeQueue.Dequeue();
                            Monitor.PulseAll(_queueLock);
                        }
                        else if (_eof)
                        {
                            break;
                        }
                    }

                    if (buf != null)
                    {
                        _stdin.Write(buf, 0, buf.Length);
                        _framesWritten++;
                        lock (_queueLock)
                        {
                            _bufferPool.Push(buf);
                        }
                    }
                }
                if (_stdin != null)
                {
                    _stdin.Flush();
                    _stdin.Close();
                }
            }
            catch (Exception ex)
            {
                _writerError = ex;
                _aborted = true;
                AgentLog.Error("FFmpeg writer thread failed: " + ex);
            }
        }

        public int Finish(bool cancel)
        {
            if (!_useSyncReadback)
            {
                try
                {
                    AsyncGPUReadback.WaitAllRequests();
                }
                catch { }
            }

            lock (_queueLock)
            {
                if (cancel) _aborted = true;
                _eof = true;
                Monitor.PulseAll(_queueLock);
            }

            if (_writerThread != null && _writerThread.IsAlive)
            {
                _writerThread.Join(60000);
            }

            int exitCode = -1;
            if (_ffmpeg != null)
            {
                try
                {
                    if (cancel && !_ffmpeg.HasExited)
                    {
                        _ffmpeg.Kill();
                    }
                    else
                    {
                        _ffmpeg.WaitForExit(300000);
                    }
                    exitCode = _ffmpeg.HasExited ? _ffmpeg.ExitCode : -2;
                }
                catch (Exception ex)
                {
                    AgentLog.Warn("FFmpeg exit wait error: " + ex.Message);
                }
                try { _ffmpeg.Dispose(); } catch { }
                _ffmpeg = null;
            }

            if (_syncTex != null)
            {
                UnityEngine.Object.Destroy(_syncTex);
                _syncTex = null;
            }

            if (_staging != null)
            {
                for (int i = 0; i < _staging.Length; i++)
                {
                    if (_staging[i] != null)
                    {
                        _staging[i].Release();
                        UnityEngine.Object.Destroy(_staging[i]);
                    }
                }
                _staging = null;
            }

            AgentLog.Info(string.Format("FrameCapturePipe finished: submitted={0}, written={1}, ffmpegExit={2}, syncFallback={3}",
                _framesSubmitted, _framesWritten, exitCode, _useSyncReadback));
            return exitCode;
        }
    }
}
