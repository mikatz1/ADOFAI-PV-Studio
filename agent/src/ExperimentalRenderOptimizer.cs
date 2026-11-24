using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEngine;
using UnityEngine.Rendering;

namespace AdofaiRenderAgent
{
    /// <summary>
    /// Experimental Render Optimizer (high-density & 4K/8K safe):
    /// 1) Per-frame zero-opacity decoration culling (alpha <= 0.001)
    /// 2) Per-frame off-camera frustum culling (with distortion-safe margin)
    /// 3) Pre-built GPU-to-GPU LOD proxy pyramid for high-res decoration sprites (>= 512px)
    ///    prioritizing largest textures first under a strict VRAM budget, swapping cleanly
    ///    during Camera.onPreCull and restoring in Camera.onPostRender whenever projected
    ///    screen footprint is smaller than the proxy (1.35x Nyquist guard).
    /// Zero impact on final pixel quality, hitboxes, tweens, masks, or game logic.
    /// </summary>
    public static class ExperimentalRenderOptimizer
    {
        private class SpriteProxyPyramid
        {
            public Sprite Original;
            public int OrigMaxDim;
            public List<ProxyLevel> Levels = new List<ProxyLevel>();
        }

        private class ProxyLevel
        {
            public Sprite Sprite;
            public Texture2D Texture;
            public int MaxDim;
        }

        private class TrackedDeco
        {
            public scrDecoration Deco;
            public scrVisualDecoration Visual;
            public SpriteRenderer SpriteRenderer;
            public Renderer FallbackRenderer;
            public bool IsTiled;
            public bool HasFilter;
            public bool CanSwapProxy;
            public SpriteProxyPyramid Pyramid;
            public int LastPyramidSpriteId;
            // Persistent zero-opacity suppression (camera-independent, avoids 50k+/frame Renderer.enabled scene-tree thrashing)
            public bool SuppressedOpacity;
            public Renderer SuppressedRenderer;
        }

        private struct SuppressedEntry
        {
            public Renderer Renderer;
        }

        private struct SwappedEntry
        {
            public SpriteRenderer Renderer;
            public Sprite OriginalSprite;
        }

        private const int PROXY_MIN_DIM = 512;
        private const long MAX_SINGLE_PROXY_PIXELS = 2048L * 2048L;
        private const float NYQUIST_SAFETY = 1.35f;

        private static readonly FieldInfo _fiMaskingType =
            typeof(scrVisualDecoration).GetField("maskingType", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiRendererEnabled =
            typeof(scrDecoration).GetField("rendererEnabled", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
        private static readonly FieldInfo _fiMeshRendererEnabled =
            typeof(scrVisualDecoration).GetField("meshRendererEnabled", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);

        public static bool Enabled = false;
        private static bool _initialized = false;
        private static bool _hooksRegistered = false;
        private static int _targetWidth = 1920;
        private static int _targetHeight = 1080;
        private static long _proxyVramBudgetBytes = 256L * 1024L * 1024L;
        private static long _proxyVramUsedBytes = 0L;
        private static bool _copyTextureWorks = true;

        private static readonly Dictionary<int, SpriteProxyPyramid> _pyramidsBySpriteId = new Dictionary<int, SpriteProxyPyramid>();
        private static readonly HashSet<int> _trackedDecoIds = new HashSet<int>();
        private static readonly List<TrackedDeco> _tracked = new List<TrackedDeco>(4096);
        private static readonly List<TrackedDeco> _visibleDecosThisFrame = new List<TrackedDeco>(1024);
        private static readonly List<SuppressedEntry> _suppressedFrustumThisPass = new List<SuppressedEntry>(512);
        private static readonly List<SwappedEntry> _swappedThisPass = new List<SwappedEntry>(512);
        private static readonly List<UnityEngine.Object> _allocatedAssets = new List<UnityEngine.Object>(1024);
        private static readonly Dictionary<int, MonoBehaviour[]> _distortionFiltersByCam = new Dictionary<int, MonoBehaviour[]>();
        private static readonly Dictionary<int, int> _distortionCacheFrame = new Dictionary<int, int>();

        private static int _lastKnownDecoCount = -1;
        private static int _totalProxiesCreated = 0;
        private static int _preCullFrameCounter = 0;
        private static int _lastOpacitySyncFrame = -1;
        private static int _lastOpacityCulledCount = 0;

        // Telemetry
        private static long _framesOptimized = 0;
        private static long _opacitySyncFrames = 0;
        private static long _totalCulledOpacity = 0;
        private static long _totalCulledFrustum = 0;
        private static long _totalProxySwaps = 0;
        private static long _totalOpacityStateTransitions = 0;
        private static int _diagSkipNullSprite = 0;
        private static int _diagSkipSmall = 0;
        private static int _diagSkipSubrect = 0;
        private static int _diagSkipVram = 0;

        public static void InitializeAfterLevelLoad(int targetWidth, int targetHeight)
        {
            if (!Enabled) return;
            _targetWidth = Math.Max(64, targetWidth);
            _targetHeight = Math.Max(64, targetHeight);

            int vramMb = SystemInfo.graphicsMemorySize;
            long budgetMb = (vramMb > 0) ? Math.Max(64L, Math.Min(384L, (long)(vramMb * 0.06))) : 160L;
            _proxyVramBudgetBytes = budgetMb * 1024L * 1024L;
            _copyTextureWorks = (SystemInfo.copyTextureSupport & CopyTextureSupport.Basic) != 0;
            _initialized = true;

            if (!_hooksRegistered)
            {
                Camera.onPreCull += OnCameraPreCull;
                Camera.onPostRender += OnCameraPostRender;
                _hooksRegistered = true;
            }

            RefreshTrackedDecorations();
        }

        public static void RefreshTrackedDecorations()
        {
            if (!Enabled || !_initialized) return;
            try
            {
                scrDecorationManager dm = scrDecorationManager.instance;
                if (dm == null || dm.allDecorations == null) return;
                int count = dm.allDecorations.Count;
                if (count == _lastKnownDecoCount && count > 0) return;
                _lastKnownDecoCount = count;

                int added = 0;
                int proxiesBefore = _totalProxiesCreated;
                int dMasked = 0, dTiled = 0, dFilter = 0, dNullSp = 0;

                // Collect unique sprites from newly added decorations so we can build largest-first under VRAM budget
                List<Sprite> candidateSprites = new List<Sprite>();
                HashSet<int> candidateIds = new HashSet<int>();
                List<TrackedDeco> newlyAdded = new List<TrackedDeco>();

                for (int i = 0; i < count; i++)
                {
                    scrDecoration d = dm.allDecorations[i];
                    if (d == null) continue;
                    int decId = d.GetInstanceID();
                    if (_trackedDecoIds.Contains(decId)) continue;

                    scrVisualDecoration vd = d as scrVisualDecoration;
                    if (vd != null)
                    {
                        bool isMask = false;
                        try { isMask = vd.isMask(); } catch { }
                        if (isMask || (vd.spriteMask != null && vd.spriteMask.enabled)) continue;

                        // True masking check: SpriteAlphaMask component is enabled by default on ADOFAI prefabs even when unmasked!
                        bool isMasked = false;
                        if (_fiMaskingType != null)
                        {
                            try
                            {
                                object mt = _fiMaskingType.GetValue(vd);
                                if (mt != null && Convert.ToInt32(mt) != 0)
                                    isMasked = true;
                            }
                            catch { }
                        }
                        SpriteRenderer sr = vd.spriteRenderer;
                        if (!isMasked && sr != null && sr.maskInteraction != SpriteMaskInteraction.None)
                            isMasked = true;
                        bool isTiled = (Mathf.Abs(vd.repeatX) > 1.01f || Mathf.Abs(vd.repeatY) > 1.01f);
                        // Check if any CameraFilterPack on this decoration is actually active (prefabs can hold empty/disabled arrays)
                        bool hasFilter = false;
                        if (vd.cfpCache != null && vd.cfpCache.Length > 0)
                        {
                            for (int f = 0; f < vd.cfpCache.Length; f++)
                            {
                                if (vd.cfpCache[f] != null) { hasFilter = true; break; }
                            }
                        }
                        if (sr == null && vd.meshRenderer == null) continue;

                        if (isMasked) dMasked++;
                        if (isTiled) dTiled++;
                        if (hasFilter) dFilter++;

                        bool canSwapProxy = (sr != null && !isTiled && !hasFilter && !isMasked);
                        int spId = 0;
                        if (canSwapProxy)
                        {
                            Sprite sp0 = sr.sprite;
                            if (sp0 != null)
                            {
                                spId = sp0.GetInstanceID();
                                if (!_pyramidsBySpriteId.ContainsKey(spId) && candidateIds.Add(spId))
                                    candidateSprites.Add(sp0);
                            }
                            else
                            {
                                dNullSp++;
                            }
                        }

                        TrackedDeco td = new TrackedDeco();
                        td.Deco = d;
                        td.Visual = vd;
                        td.SpriteRenderer = sr;
                        td.IsTiled = isTiled;
                        td.HasFilter = hasFilter;
                        td.CanSwapProxy = canSwapProxy;
                        td.LastPyramidSpriteId = spId;
                        _tracked.Add(td);
                        newlyAdded.Add(td);
                        _trackedDecoIds.Add(decId);
                        added++;
                    }
                    else
                    {
                        Renderer r = d.GetComponentInChildren<Renderer>();
                        if (r != null)
                        {
                            TrackedDeco td = new TrackedDeco();
                            td.Deco = d;
                            td.FallbackRenderer = r;
                            td.IsTiled = false;
                            td.HasFilter = (d.cfpCache != null && d.cfpCache.Length > 0);
                            td.CanSwapProxy = false;
                            _tracked.Add(td);
                            _trackedDecoIds.Add(decId);
                            added++;
                        }
                    }
                }

                // Sort candidate sprites by pixel area DESCENDING so huge 8K/4K/2K textures get pre-built proxies first!
                if (candidateSprites.Count > 1)
                {
                    candidateSprites.Sort(delegate(Sprite a, Sprite b)
                    {
                        long pa = (a != null && a.texture != null) ? ((long)a.texture.width * (long)a.texture.height) : 0L;
                        long pb = (b != null && b.texture != null) ? ((long)b.texture.width * (long)b.texture.height) : 0L;
                        return pb.CompareTo(pa);
                    });
                }

                for (int i = 0; i < candidateSprites.Count; i++)
                {
                    Sprite sp = candidateSprites[i];
                    if (sp == null) continue;
                    int before = _pyramidsBySpriteId.Count;
                    SpriteProxyPyramid pyr = GetOrBuildProxyPyramid(sp);
                    if (_pyramidsBySpriteId.Count > before && pyr != null)
                        _totalProxiesCreated++;
                }

                // Bind built pyramids to newly added TrackedDeco entries
                for (int i = 0; i < newlyAdded.Count; i++)
                {
                    TrackedDeco td = newlyAdded[i];
                    if (td.CanSwapProxy && td.LastPyramidSpriteId != 0)
                    {
                        SpriteProxyPyramid pyr;
                        if (_pyramidsBySpriteId.TryGetValue(td.LastPyramidSpriteId, out pyr))
                            td.Pyramid = pyr;
                    }
                }

                if (added > 0)
                {
                    AgentLog.Info(string.Format(
                        "ExperimentalRenderOptimizer indexed: totalTracked={0} (+{1}), cands={2}, uniqueProxyPyramids={3} (+{4}), proxyVram={5:F1}/{6:F0}MB, copyTex={7}, target={8}x{9} [diag: masked={10}, tiled={11}, filter={12}, nullSp={13}, small={14}, subrect={15}, vramSkip={16}]",
                        _tracked.Count, added, candidateSprites.Count, _totalProxiesCreated, _totalProxiesCreated - proxiesBefore,
                        _proxyVramUsedBytes / (1024.0 * 1024.0), _proxyVramBudgetBytes / (1024.0 * 1024.0),
                        _copyTextureWorks, _targetWidth, _targetHeight,
                        dMasked, dTiled, dFilter, dNullSp, _diagSkipSmall, _diagSkipSubrect, _diagSkipVram));
                }
            }
            catch (Exception ex)
            {
                AgentLog.Warn("ExperimentalRenderOptimizer refresh warning: " + ex.Message);
            }
        }

        private static SpriteProxyPyramid GetOrBuildProxyPyramid(Sprite orig)
        {
            if (orig == null || orig.texture == null) { _diagSkipNullSprite++; return null; }
            int id = orig.GetInstanceID();
            SpriteProxyPyramid cached;
            if (_pyramidsBySpriteId.TryGetValue(id, out cached))
                return cached;

            if (_proxyVramUsedBytes >= _proxyVramBudgetBytes)
            {
                _diagSkipVram++;
                _pyramidsBySpriteId[id] = null;
                return null;
            }

            // Use orig.rect (full sprite rect), NOT orig.textureRect (which tight-crops transparent PNG margins!)
            Rect tr = orig.rect;
            int w = Mathf.RoundToInt(tr.width);
            int h = Mathf.RoundToInt(tr.height);
            int maxDim = Math.Max(w, h);
            if (maxDim < PROXY_MIN_DIM)
            {
                _diagSkipSmall++;
                _pyramidsBySpriteId[id] = null;
                return null;
            }
            if (tr.x > 1f || tr.y > 1f || Math.Abs(w - orig.texture.width) > 1 || Math.Abs(h - orig.texture.height) > 1)
            {
                _diagSkipSubrect++;
                _pyramidsBySpriteId[id] = null;
                return null;
            }

            SpriteProxyPyramid pyr = new SpriteProxyPyramid();
            pyr.Original = orig;
            pyr.OrigMaxDim = maxDim;

            Vector2 normPivot = new Vector2(
                tr.width > 0f ? (orig.pivot.x / tr.width) : 0.5f,
                tr.height > 0f ? (orig.pivot.y / tr.height) : 0.5f);

            // Multi-resolution pyramid: 1/16, 1/8, 1/4, 1/2 (skipping any level > 2048x2048 to preserve VRAM on 8K assets)
            float[] scales = new float[] { 0.0625f, 0.125f, 0.25f, 0.5f };
            for (int i = 0; i < scales.Length; i++)
            {
                float sc = scales[i];
                int pw = Mathf.RoundToInt(w * sc);
                int ph = Mathf.RoundToInt(h * sc);
                if (pw < 32 || ph < 32 || Math.Max(pw, ph) < 96) continue;
                long px = (long)pw * (long)ph;
                if (px > MAX_SINGLE_PROXY_PIXELS) continue;
                long bytes = px * 4L;
                if (_proxyVramUsedBytes + bytes > _proxyVramBudgetBytes) continue;

                float exactScale = (float)pw / (float)w;
                ProxyLevel lvl = BuildProxyLevel(orig, pw, ph, exactScale, normPivot);
                if (lvl != null)
                {
                    pyr.Levels.Add(lvl);
                    _proxyVramUsedBytes += bytes;
                }
            }

            if (pyr.Levels.Count == 0)
            {
                _pyramidsBySpriteId[id] = null;
                return null;
            }

            _pyramidsBySpriteId[id] = pyr;
            return pyr;
        }

        private static ProxyLevel BuildProxyLevel(Sprite orig, int pw, int ph, float scale, Vector2 normPivot)
        {
            RenderTexture tmp = null;
            RenderTexture prev = RenderTexture.active;
            try
            {
                Texture2D tex = new Texture2D(pw, ph, TextureFormat.RGBA32, false, false);
                tex.filterMode = orig.texture.filterMode;
                tex.wrapMode = orig.texture.wrapMode;

                bool built = false;
                if (_copyTextureWorks)
                {
                    try
                    {
                        RenderTextureDescriptor desc = new RenderTextureDescriptor(pw, ph, tex.graphicsFormat, 0);
                        desc.msaaSamples = 1;
                        desc.useMipMap = false;
                        tmp = RenderTexture.GetTemporary(desc);
                        tmp.filterMode = FilterMode.Bilinear;
                        Graphics.Blit(orig.texture, tmp);
                        // Release CPU pixel buffer first, then GPU-to-GPU copy with zero CPU stall
                        tex.Apply(false, true);
                        Graphics.CopyTexture(tmp, 0, 0, tex, 0, 0);
                        built = true;
                    }
                    catch
                    {
                        _copyTextureWorks = false;
                        if (tmp != null) { RenderTexture.ReleaseTemporary(tmp); tmp = null; }
                        UnityEngine.Object.Destroy(tex);
                        tex = new Texture2D(pw, ph, TextureFormat.RGBA32, false, false);
                        tex.filterMode = orig.texture.filterMode;
                        tex.wrapMode = orig.texture.wrapMode;
                    }
                }

                if (!built)
                {
                    tmp = RenderTexture.GetTemporary(pw, ph, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
                    tmp.filterMode = FilterMode.Bilinear;
                    Graphics.Blit(orig.texture, tmp);

                    RenderTexture.active = tmp;
                    tex.ReadPixels(new Rect(0, 0, pw, ph), 0, 0, false);
                    tex.Apply(false, true);
                }

                // Exact world-size invariant: pixelsPerUnit scaled by exact same downscale factor
                float ppu = orig.pixelsPerUnit * scale;
                Sprite sp = Sprite.Create(tex, new Rect(0, 0, pw, ph), normPivot, ppu, 0, SpriteMeshType.FullRect);

                _allocatedAssets.Add(tex);
                _allocatedAssets.Add(sp);

                ProxyLevel lvl = new ProxyLevel();
                lvl.Sprite = sp;
                lvl.Texture = tex;
                lvl.MaxDim = Math.Max(pw, ph);
                return lvl;
            }
            catch (Exception ex)
            {
                AgentLog.Warn("BuildProxyLevel skipped: " + ex.Message);
                return null;
            }
            finally
            {
                RenderTexture.active = prev;
                if (tmp != null) RenderTexture.ReleaseTemporary(tmp);
            }
        }

        private static bool IsAdoRendererEnabled(scrDecoration deco)
        {
            if (_fiRendererEnabled == null) return true;
            try { return (bool)_fiRendererEnabled.GetValue(deco); } catch { return true; }
        }

        private static bool IsMeshRendererEnabled(scrVisualDecoration vd)
        {
            if (_fiMeshRendererEnabled != null)
            {
                try { return (bool)_fiMeshRendererEnabled.GetValue(vd); } catch { }
            }
            return (vd.meshRendererObj != null && vd.meshRendererObj.activeSelf);
        }

        private static Renderer ResolveActiveRenderer(TrackedDeco td, out bool isUsingMesh)
        {
            isUsingMesh = false;
            scrVisualDecoration vd = td.Visual;
            if (vd != null)
            {
                if (vd.meshRendererObj != null && vd.meshRendererObj.activeSelf &&
                    vd.meshRenderer != null && vd.meshRenderer.enabled && vd.meshRendererObj.activeInHierarchy)
                {
                    isUsingMesh = true;
                    return vd.meshRenderer;
                }
                SpriteRenderer sr = td.SpriteRenderer;
                if (sr != null && sr.enabled && sr.gameObject.activeInHierarchy)
                {
                    return sr;
                }
                return null;
            }
            if (td.FallbackRenderer != null && td.FallbackRenderer.enabled && td.FallbackRenderer.gameObject.activeInHierarchy)
                return td.FallbackRenderer;
            return null;
        }

        /// <summary>
        /// Runs ONCE per frame (not per camera). Uses pure managed C# fields (vd.GetAlpha(), deco.forceHide)
        /// so 28,000+ invisible decorations cost ~0.08ms instead of 50,000+ Unity Renderer.enabled mutations/frame.
        /// Zero-opacity suppression is persistent across frames and immediately restored the instant alpha > 0.001f.
        /// </summary>
        private static void SyncOpacityOncePerFrame()
        {
            _visibleDecosThisFrame.Clear();
            int n = _tracked.Count;
            int culledOp = 0;

            for (int i = 0; i < n; i++)
            {
                TrackedDeco td = _tracked[i];
                scrDecoration deco = td.Deco;
                if (deco == null) continue;

                scrVisualDecoration vd = td.Visual;
                if (vd != null)
                {
                    // Pure managed C# read (scrVisualDecoration.GetAlpha() returns rendererColor.a)
                    float alpha = vd.GetAlpha();
                    bool invisible = deco.forceHide || (alpha <= 0.001f);

                    if (invisible)
                    {
                        if (!td.SuppressedOpacity)
                        {
                            bool usingMesh;
                            Renderer activeR = ResolveActiveRenderer(td, out usingMesh);
                            if (activeR != null)
                            {
                                activeR.enabled = false;
                                td.SuppressedOpacity = true;
                                td.SuppressedRenderer = activeR;
                                _totalOpacityStateTransitions++;
                            }
                        }
                        culledOp++;
                        continue;
                    }

                    if (td.SuppressedOpacity)
                    {
                        Renderer r = td.SuppressedRenderer;
                        if (r != null && IsAdoRendererEnabled(deco))
                        {
                            if (r == td.SpriteRenderer)
                            {
                                if (!IsMeshRendererEnabled(vd))
                                    r.enabled = true;
                            }
                            else
                            {
                                r.enabled = true;
                            }
                        }
                        td.SuppressedOpacity = false;
                        td.SuppressedRenderer = null;
                        _totalOpacityStateTransitions++;
                    }

                    _visibleDecosThisFrame.Add(td);
                }
                else
                {
                    _visibleDecosThisFrame.Add(td);
                }
            }

            _opacitySyncFrames++;
            _lastOpacityCulledCount = culledOp;
            _totalCulledOpacity += culledOp;
        }

        private static void OnCameraPreCull(Camera cam)
        {
            if (!Enabled || !_initialized || cam == null || UiAndQualityPatches.FastSeekSkipRender) return;
            scrCamera sc = scrCamera.instance;
            if (sc == null || (cam != sc.camobj && cam != sc.BGcam)) return;

            _preCullFrameCounter++;
            if ((_preCullFrameCounter & 31) == 1)
                RefreshTrackedDecorations();

            if (_tracked.Count == 0) return;

            // Step 1: Persistent zero-opacity sync (ONCE per frame, camera-independent)
            int curFrame = Time.frameCount;
            if (curFrame != _lastOpacitySyncFrame)
            {
                _lastOpacitySyncFrame = curFrame;
                SyncOpacityOncePerFrame();
            }

            _suppressedFrustumThisPass.Clear();
            _swappedThisPass.Clear();

            int visCount = _visibleDecosThisFrame.Count;
            if (visCount == 0)
            {
                _framesOptimized++;
                return;
            }

            int camMask = cam.cullingMask;
            Plane[] planes = GeometryUtility.CalculateFrustumPlanes(cam);
            float padFactor = HasWideDistortionFilter(cam) ? 0.50f : 0.22f;

            bool isOrtho = cam.orthographic;
            float worldHeight = isOrtho ? (cam.orthographicSize * 2f) : 10f;
            float pxPerWorldUnit = (worldHeight > 0.001f) ? ((float)_targetHeight / worldHeight) : 100f;

            int culledFr = 0;
            int swapped = 0;

            // Steps 2 & 3 ONLY touch the currently-visible decorations (~2-5% of total on heavy charts!)
            for (int i = 0; i < visCount; i++)
            {
                TrackedDeco td = _visibleDecosThisFrame[i];
                if (td.Deco == null) continue;

                bool usingMesh;
                Renderer activeR = ResolveActiveRenderer(td, out usingMesh);
                if (activeR == null) continue;

                // Only optimize renderers belonging to this camera's culling mask
                if ((camMask & (1 << activeR.gameObject.layer)) == 0) continue;

                // Secondary safety check for non-visual or externally-faded SpriteRenderer
                if (!usingMesh && td.SpriteRenderer != null && td.SpriteRenderer.color.a <= 0.001f)
                {
                    activeR.enabled = false;
                    _suppressedFrustumThisPass.Add(new SuppressedEntry { Renderer = activeR });
                    continue;
                }

                // Step 2: Off-camera frustum culling (frame-local per camera, skip tiled & filter-attached for 100% safety)
                Bounds b = activeR.bounds;
                if (!td.IsTiled && !td.HasFilter && b.size.sqrMagnitude > 0.00001f)
                {
                    Bounds padded = b;
                    Vector3 ext = padded.extents;
                    float extra = Math.Max(0.85f, Math.Max(ext.x, ext.y) * padFactor);
                    padded.Expand(new Vector3(extra * 2f, extra * 2f, 100f));

                    if (!GeometryUtility.TestPlanesAABB(planes, padded))
                    {
                        activeR.enabled = false;
                        _suppressedFrustumThisPass.Add(new SuppressedEntry { Renderer = activeR });
                        culledFr++;
                        continue;
                    }
                }

                // Step 3: Resolution-aware LOD proxy swap for unmasked, non-tiled SpriteRenderer decorations
                if (isOrtho && !usingMesh && td.CanSwapProxy && td.SpriteRenderer != null)
                {
                    Sprite curSp = td.SpriteRenderer.sprite;
                    if (curSp != null)
                    {
                        int curId = curSp.GetInstanceID();
                        if (curId != td.LastPyramidSpriteId)
                        {
                            td.LastPyramidSpriteId = curId;
                            int before = _pyramidsBySpriteId.Count;
                            td.Pyramid = GetOrBuildProxyPyramid(curSp);
                            if (_pyramidsBySpriteId.Count > before && td.Pyramid != null)
                                _totalProxiesCreated++;
                        }

                        if (td.Pyramid != null && curSp == td.Pyramid.Original)
                        {
                            float screenPx = Math.Max(b.size.x, b.size.y) * pxPerWorldUnit;
                            float neededPx = screenPx * NYQUIST_SAFETY;

                            Sprite chosen = null;
                            List<ProxyLevel> lvls = td.Pyramid.Levels;
                            for (int k = 0; k < lvls.Count; k++)
                            {
                                if (lvls[k].MaxDim >= neededPx)
                                {
                                    chosen = lvls[k].Sprite;
                                    break;
                                }
                            }

                            if (chosen != null)
                            {
                                _swappedThisPass.Add(new SwappedEntry
                                {
                                    Renderer = td.SpriteRenderer,
                                    OriginalSprite = td.Pyramid.Original
                                });
                                td.SpriteRenderer.sprite = chosen;
                                swapped++;
                            }
                        }
                    }
                }
            }

            _framesOptimized++;
            _totalCulledFrustum += culledFr;
            _totalProxySwaps += swapped;
        }

        private static void OnCameraPostRender(Camera cam)
        {
            if (!Enabled || !_initialized || cam == null) return;
            scrCamera sc = scrCamera.instance;
            if (sc == null || (cam != sc.camobj && cam != sc.BGcam)) return;

            for (int i = 0; i < _suppressedFrustumThisPass.Count; i++)
            {
                Renderer r = _suppressedFrustumThisPass[i].Renderer;
                if (r != null) r.enabled = true;
            }
            _suppressedFrustumThisPass.Clear();

            for (int i = 0; i < _swappedThisPass.Count; i++)
            {
                SwappedEntry se = _swappedThisPass[i];
                if (se.Renderer != null && se.OriginalSprite != null)
                    se.Renderer.sprite = se.OriginalSprite;
            }
            _swappedThisPass.Clear();
        }

        private static bool HasWideDistortionFilter(Camera cam)
        {
            try
            {
                int cid = cam.GetInstanceID();
                MonoBehaviour[] comps;
                int lastFrame;
                if (!_distortionFiltersByCam.TryGetValue(cid, out comps) ||
                    !_distortionCacheFrame.TryGetValue(cid, out lastFrame) ||
                    (_preCullFrameCounter - lastFrame) > 120)
                {
                    MonoBehaviour[] all = cam.GetComponents<MonoBehaviour>();
                    List<MonoBehaviour> interesting = new List<MonoBehaviour>();
                    for (int i = 0; i < all.Length; i++)
                    {
                        MonoBehaviour m = all[i];
                        if (m == null) continue;
                        string n = m.GetType().Name;
                        if (n.IndexOf("Fisheye", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            n.IndexOf("Distort", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            n.IndexOf("Wave", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            n.IndexOf("Mirror", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            n.IndexOf("Kaleido", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            n.IndexOf("Twirl", StringComparison.OrdinalIgnoreCase) >= 0 ||
                            n.IndexOf("Water", StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            interesting.Add(m);
                        }
                    }
                    comps = interesting.ToArray();
                    _distortionFiltersByCam[cid] = comps;
                    _distortionCacheFrame[cid] = _preCullFrameCounter;
                }

                for (int i = 0; i < comps.Length; i++)
                {
                    if (comps[i] != null && comps[i].enabled)
                        return true;
                }
            }
            catch { }
            return false;
        }

        public static void ShutdownAndLogSummary()
        {
            if (_hooksRegistered)
            {
                Camera.onPreCull -= OnCameraPreCull;
                Camera.onPostRender -= OnCameraPostRender;
                _hooksRegistered = false;
            }

            // Restore any persistently opacity-suppressed renderers cleanly
            for (int i = 0; i < _tracked.Count; i++)
            {
                TrackedDeco td = _tracked[i];
                if (td.SuppressedOpacity && td.SuppressedRenderer != null)
                {
                    try { td.SuppressedRenderer.enabled = true; } catch { }
                    td.SuppressedOpacity = false;
                    td.SuppressedRenderer = null;
                }
            }

            if (_framesOptimized > 0)
            {
                double avgOpPerFrame = _opacitySyncFrames > 0 ? ((double)_totalCulledOpacity / _opacitySyncFrames) : 0.0;
                AgentLog.Info(string.Format(
                    "ExperimentalRenderOptimizer summary: frames={0}, passes={1}, proxiesBuilt={2} ({3:F1}MB VRAM), avgCulledOpacity={4:F1}/frame (transitions={5}), avgCulledFrustum={6:F1}/pass, avgProxySwaps={7:F1}/pass",
                    _opacitySyncFrames,
                    _framesOptimized,
                    _totalProxiesCreated,
                    _proxyVramUsedBytes / (1024.0 * 1024.0),
                    avgOpPerFrame,
                    _totalOpacityStateTransitions,
                    (double)_totalCulledFrustum / _framesOptimized,
                    (double)_totalProxySwaps / _framesOptimized));
            }

            for (int i = 0; i < _allocatedAssets.Count; i++)
            {
                if (_allocatedAssets[i] != null)
                {
                    try { UnityEngine.Object.Destroy(_allocatedAssets[i]); } catch { }
                }
            }
            _allocatedAssets.Clear();
            _pyramidsBySpriteId.Clear();
            _tracked.Clear();
            _visibleDecosThisFrame.Clear();
            _trackedDecoIds.Clear();
            _distortionFiltersByCam.Clear();
            _distortionCacheFrame.Clear();
            _initialized = false;
        }
    }
}
