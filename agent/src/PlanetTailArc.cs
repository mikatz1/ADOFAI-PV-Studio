using System;
using System.Collections.Generic;
using UnityEngine;

namespace AdofaiRenderAgent
{
    /// <summary>
    /// Re-emits each planet's tail particles along the true circular orbit arc between two simulated ticks.
    /// Unity places particles emitted during one frame on the straight chord between the emitter's previous and current
    /// position, so at coarse time steps / very high BPM the tail becomes a polygon instead of a circle. Here the
    /// ParticleSystem's own emission is switched off and the same number of particles (rateOverTime * dt) is emitted
    /// manually on the arc around the pivot planet, each pre-aged by its position inside the tick.
    /// </summary>
    public static class PlanetTailArc
    {
        private class State
        {
            public bool Has;
            public Vector3 PrevPos;
            public Vector3 PrevPivot;
            public bool PrevOrbiting;
            public float Acc;
        }

        private static readonly Dictionary<int, State> _states = new Dictionary<int, State>();
        public static bool Enabled = false;
        public static int ParticlesEmitted = 0;
        public static int ArcTicks = 0;
        public static int LinearTicks = 0;

        public static void Reset()
        {
            _states.Clear();
            ParticlesEmitted = ArcTicks = LinearTicks = 0;
        }

        public static void Restore()
        {
            try
            {
                scrController c = scrController.instance;
                if (c == null || c.planetarySystem == null || c.planetarySystem.allPlanets == null) return;
                foreach (scrPlanet p in c.planetarySystem.allPlanets)
                {
                    if (p == null || p.planetRenderer == null || p.planetRenderer.tailParticles == null) continue;
                    var em = p.planetRenderer.tailParticles.emission;
                    em.enabled = true;
                }
            }
            catch { }
        }

        public static void Tick(double dt)
        {
            if (!Enabled || dt <= 0.0) return;
            scrController c = scrController.instance;
            if (c == null || c.planetarySystem == null || c.planetarySystem.allPlanets == null) return;

            foreach (scrPlanet p in c.planetarySystem.allPlanets)
            {
                if (p == null || p.planetRenderer == null) continue;
                ParticleSystem ps = p.planetRenderer.tailParticles;
                if (ps == null) continue;

                int id = p.GetInstanceID();
                State st;
                if (!_states.TryGetValue(id, out st)) { st = new State(); _states[id] = st; }

                Vector3 pos = p.transform.position;
                bool orbiting = false;
                Vector3 pivot = pos;
                try
                {
                    if (!p.isChosen && p.other != null)
                    {
                        pivot = p.other.transform.position;
                        orbiting = true;
                    }
                }
                catch { }

                bool active = ps.gameObject.activeInHierarchy && ps.isPlaying;
                var em = ps.emission;
                if (!active)
                {
                    st.Has = false;
                    continue;
                }

                float rate = em.rateOverTimeMultiplier;
                em.enabled = false; // we emit manually from now on (world-space system)

                st.Acc += rate * (float)dt;
                int n = (int)st.Acc;
                st.Acc -= n;

                Vector3 p0 = st.Has ? st.PrevPos : pos;
                Vector3 pv0 = st.Has ? st.PrevPivot : pivot;

                bool arc = false;
                float a0 = 0f, delta = 0f, r0 = 0f, r1 = 0f;
                if (st.Has && orbiting && st.PrevOrbiting)
                {
                    float r = Mathf.Max(0.0001f, Vector2.Distance(pivot, pos));
                    float pivMove = Vector2.Distance(pv0, pivot);
                    r0 = Vector2.Distance(pv0, p0);
                    r1 = r;
                    if (pivMove < 0.002f * Mathf.Max(1f, r) && Mathf.Abs(r1 - r0) < 0.25f * r)
                    {
                        a0 = Mathf.Atan2(p0.y - pv0.y, p0.x - pv0.x);
                        float a1 = Mathf.Atan2(pos.y - pivot.y, pos.x - pivot.x);
                        delta = Mathf.DeltaAngle(a0 * Mathf.Rad2Deg, a1 * Mathf.Rad2Deg) * Mathf.Deg2Rad;
                        arc = Mathf.Abs(delta) <= 170f * Mathf.Deg2Rad;
                    }
                }

                if (n > 0)
                {
                    float life = ps.main.startLifetime.constantMax;
                    ParticleSystem.EmitParams ep = new ParticleSystem.EmitParams();
                    ep.applyShapeToPosition = true;
                    for (int i = 0; i < n; i++)
                    {
                        float u = (float)(i + 1) / (float)n;
                        Vector3 e;
                        if (arc)
                        {
                            float a = a0 + delta * u;
                            float rr = Mathf.Lerp(r0, r1, u);
                            e = new Vector3(pivot.x + Mathf.Cos(a) * rr, pivot.y + Mathf.Sin(a) * rr, pos.z);
                        }
                        else
                        {
                            e = Vector3.Lerp(p0, pos, u);
                        }
                        ep.position = e;
                        ep.startLifetime = Mathf.Max(0.02f, life - (1f - u) * (float)dt);
                        ps.Emit(ep, 1);
                    }
                    ParticlesEmitted += n;
                    if (arc) ArcTicks++; else LinearTicks++;
                }

                st.Has = true;
                st.PrevPos = pos;
                st.PrevPivot = pivot;
                st.PrevOrbiting = orbiting;
            }
        }
    }
}
