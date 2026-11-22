# -*- coding: utf-8 -*-
"""粒子运行时：按 Blitzkrieg 的 KeyBasedParticleSource 还原（16ms 固定步长）。

只做 CPU 侧：发射 / 积分 / 生成屏幕空间四边形批。OpenGL 调用都在 glviewport 里。

关键约定
  · 时间单位 ms；步长固定 16ms（与引擎一致），speed 通过「多跑子步」实现
  · 粒子尺寸按引擎语义 = 屏幕像素（再做 view_h/600 的分辨率换算）
  · 曲线 time 已在 effectdb 里归一化到 0..1
"""
import math

import numpy as np

STEP_MS = 16.0
SIZE_REF_HEIGHT = 600.0     # 引擎按 800x600 标定，实际屏幕按高度等比放大
# 引擎：area = trackGenerateArea(f) * fScale（fScale 就是 <item scale>）
AREA_SCALE = 0.1
# 引擎 <pos> 原样加到定位器上；但这些 XML 的数值明显偏大（z=8 / z=-5），
# 按分米理解更接近实际观感，集中在这里方便一处调整
POS_SCALE = 0.1


class _Emitter(object):
    __slots__ = ("eff", "origin", "basis", "loop", "t", "acc", "dead", "handle")

    def __init__(self, eff, origin, basis, loop, handle):
        self.eff = eff
        self.origin = np.asarray(origin, np.float64)
        self.basis = np.asarray(basis, np.float64) if basis is not None else np.eye(3)
        self.loop = loop
        self.t = 0.0
        self.acc = [0.0] * max(1, len(eff.items))
        self.dead = False
        self.handle = handle


class FxSystem(object):
    def __init__(self, max_particles=4096):
        self.cap = int(max_particles)
        self._n = 0
        self._defs = []          # list[ParticleDef]
        self._def_id = {}
        # 粒子数组（SoA）
        self.pos = np.zeros((self.cap, 3), np.float64)
        self.vel = np.zeros((self.cap, 3), np.float64)
        self.birth = np.zeros(self.cap, np.float64)
        self.death = np.zeros(self.cap, np.float64)
        self.ang = np.zeros(self.cap, np.float64)
        self.angv = np.zeros(self.cap, np.float64)
        self.scale = np.ones(self.cap, np.float64)
        self.pdef = np.full(self.cap, -1, np.int32)
        self.alive = np.zeros(self.cap, bool)
        self.now = 0.0
        self._emitters = []
        self._next_handle = 1
        self.size_scale = 1.0    # 全局倍率（面板可调）
        self.zoom = 1.0          # 镜头缩放因子（由视口每帧写入）

    # ---------------------------------------------------------------- 控制
    def play(self, eff, origin, basis=None, loop=False, speed=1.0):
        if eff is None or not eff.items:
            return None
        h = self._next_handle
        self._next_handle += 1
        em = _Emitter(eff, origin, basis, loop, h)
        self._emitters.append(em)
        # 立刻推进一小步，让第一帧就有粒子
        self._advance_emitter(em, STEP_MS, first=True)
        return h

    def stop(self, handle=None):
        if handle is None:
            self._emitters = []
            self.alive[:] = False
        else:
            self._emitters = [e for e in self._emitters if e.handle != handle]

    def active(self):
        return bool(self._emitters) or bool(self.alive.any())

    def count(self):
        return int(self.alive.sum())

    def set_anchor(self, handle, origin, basis=None):
        """更新发射点；已经飞出去的粒子也跟着平移，预览里才不会和部件脱节。"""
        for em in self._emitters:
            if handle is None or em.handle == handle:
                new_o = np.asarray(origin, np.float64)
                delta = new_o - em.origin
                if np.any(np.abs(delta) > 1e-9) and self.alive.any():
                    self.pos[self.alive] += delta
                em.origin = new_o
                if basis is not None:
                    em.basis = np.asarray(basis, np.float64)

    # ---------------------------------------------------------------- 更新
    def tick(self, dt_ms, sub_steps=None):
        """推进 dt_ms（按 16ms 子步）。返回是否还有活粒子。"""
        if dt_ms <= 0:
            return self.active()
        if sub_steps is None:
            sub_steps = max(1, int(round(dt_ms / STEP_MS)))
        for _ in range(sub_steps):
            self._step(STEP_MS)
        return self.active()

    def _step(self, dt):
        self.now += dt
        # 发射
        for em in list(self._emitters):
            self._advance_emitter(em, dt)
            if em.dead:
                self._emitters.remove(em)
        # 积分
        if not self.alive.any():
            return
        idx = np.nonzero(self.alive)[0]
        for did in np.unique(self.pdef[idx]):
            if did < 0:
                continue
            d = self._defs[int(did)]
            m = idx[self.pdef[idx] == did]
            self._integrate(m, d, dt)

    def _advance_emitter(self, em, dt, first=False):
        eff = em.eff
        total = max(1.0, eff.duration)
        local_t = em.t
        for i, item in enumerate(eff.items):
            t_rel = local_t - item.start
            if t_rel < 0 or t_rel > item.duration or item.particle is None:
                continue
            d = item.particle
            f = min(1.0, t_rel / max(1.0, item.duration))
            dens = d.curve("Density").sample(f)
            if dens <= 0:
                continue
            em.acc[i] += dens * dt
            n = int(em.acc[i])
            if n <= 0:
                continue
            em.acc[i] -= n
            n = min(n, 256)                      # 单步上限，防爆量
            self._spawn(n, d, em, item, f)
        em.t += dt
        if em.loop:
            if em.t > total:
                em.t = 0.0
                em.acc = [0.0] * len(em.acc)
        elif em.t > total + 2000.0:
            em.dead = True

    def _spawn(self, n, d, em, item, f=0.0):
        free = np.nonzero(~self.alive)[0]
        if len(free) == 0:
            return
        free = free[:n]
        m = len(free)
        did = self._def_id.get(id(d))
        if did is None:
            did = len(self._defs)
            self._defs.append(d)
            self._def_id[id(d)] = did
        rnd = np.random.random((m, 3))

        # 引擎的 scale 只作用于尺寸；早期版本乘到这里会让粒子飞出定位器很远
        area = d.curve("GenerateArea").sample(f) * float(item.scale) * AREA_SCALE
        # <pos> 的数值按分米级理解：直接当米用会让粒子飞出定位器好几米
        off = np.asarray(item.offset, np.float64) * POS_SCALE
        local = (rnd - 0.5) * 2.0 * area
        world = em.origin + em.basis.dot(off) + local.dot(np.asarray(em.basis, float).T)

        # 方向：以 direction 为轴、半角 GenerateAngel 的圆锥
        ang_half = d.curve("GenerateAngel").sample(f)
        dirv = self._cone_dirs(d.direction, ang_half, m)
        speed0 = d.curve("BeginSpeed").sample(f)
        rnd_speed = d.curve("BeginSpeedRandomizer").sample(f)
        spd = speed0 * (1.0 + (rnd[:, 0] - 0.5) * 2.0 * rnd_speed)
        vel = dirv * spd[:, None]

        life0 = d.curve("lifeTime").sample(f)
        life_rnd = d.curve("ParticleLifeTimeRandomizer").sample(f)
        life = np.maximum(16.0, life0 * (1.0 + (rnd[:, 1] - 0.5) * 2.0 * life_rnd))

        spin0 = d.curve("GenerateSpin").sample(f)
        spin_rnd = d.curve("GenerateSpinRnd").sample(f)

        self.pos[free] = world
        self.vel[free] = vel
        self.birth[free] = self.now
        self.death[free] = self.now + life
        self.ang[free] = rnd[:, 2] * math.pi * 2.0
        self.angv[free] = spin0 * (1.0 + (rnd[:, 0] - 0.5) * 2.0 * spin_rnd)
        self.scale[free] = item.scale
        self.pdef[free] = did
        self.alive[free] = True

    @staticmethod
    def _cone_dirs(axis, half_angle, n):
        axis = np.asarray(axis, np.float64)
        norm = float(np.linalg.norm(axis))
        axis = axis / norm if norm > 1e-9 else np.array([0.0, 0.0, 1.0])
        if half_angle <= 1e-6:
            return np.repeat(axis[None, :], n, axis=0)
        # 构造与 axis 垂直的两个基
        tmp = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(axis, tmp)
        u /= max(1e-9, np.linalg.norm(u))
        v = np.cross(axis, u)
        cos_max = math.cos(half_angle)
        r = np.random.random(n)
        cos_t = 1.0 - r * (1.0 - cos_max)
        sin_t = np.sqrt(np.maximum(0.0, 1.0 - cos_t * cos_t))
        phi = np.random.random(n) * math.pi * 2.0
        return (np.outer(cos_t, axis)
                + np.outer(sin_t * np.cos(phi), u)
                + np.outer(sin_t * np.sin(phi), v))

    def _integrate(self, m, d, dt):
        now = self.now
        f = np.clip((now - self.birth[m]) / np.maximum(1.0, self.death[m] - self.birth[m]), 0.0, 1.0)
        # 速度/风力（引擎式：vel 为初速，Speed(f) 是倍率）
        spd = d.curve("Speed").sample(0.0)
        spd_arr = d.curve("Speed").eval(f) if len(d.curve("Speed")) else np.full(len(m), spd)
        dt_s = dt / 1000.0
        wind = d.wind
        self.pos[m] += (self.vel[m] * spd_arr[:, None] + wind[None, :]) * dt_s * self.scale[m][:, None]
        if d.gravity:
            self.pos[m, 2] -= d.gravity * dt_s * dt_s * self.scale[m]
        # 自转
        spin_arr = d.curve("Spin").eval(f) if len(d.curve("Spin")) else np.zeros(len(m))
        self.ang[m] += self.angv[m] * spin_arr * dt_s * 2.0 * math.pi
        # 死亡回收
        dead = now >= self.death[m]
        if dead.any():
            self.alive[m[dead]] = False

    # ---------------------------------------------------------------- 渲染数据
    def build_quads(self, project, view_h, tex_size=None):
        """project: f(np(N,3)) -> (sx, sy, depth, ok)；返回 [(tex_key, verts(N,2), uvs(N,2), colors(N,4))]"""
        out = {}
        if not self.alive.any():
            return []
        idx = np.nonzero(self.alive)[0]
        px_scale = (float(view_h) / SIZE_REF_HEIGHT) * float(self.size_scale) * float(self.zoom)
        for did in np.unique(self.pdef[idx]):
            if did < 0:
                continue
            d = self._defs[int(did)]
            m = idx[self.pdef[idx] == did]
            if not len(m):
                continue
            now = self.now
            f = np.clip((now - self.birth[m]) / np.maximum(1.0, self.death[m] - self.birth[m]), 0.0, 1.0)
            size = d.curve("Size").eval(f) * self.scale[m] * px_scale
            if not np.any(size > 0.02):
                continue
            alpha = (d.curve("GenerateOpacity").eval(f) / 255.0) * d.curve("Opacity").eval(f)
            frame = d.curve("TextureFrame").eval(f)
            sx, sy, depth, ok = project(self.pos[m])
            keep = ok & (alpha > 0.004) & (size > 0.02)
            if not keep.any():
                continue
            m2 = m[keep]
            sx, sy, depth = sx[keep], sy[keep], depth[keep]
            size, alpha, frame = size[keep], alpha[keep], frame[keep]
            ang = self.ang[m2]

            half = size * 0.5
            c, s = np.cos(ang), np.sin(ang)
            corners = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], np.float64)
            ox = (corners[None, :, 0] * c[:, None] - corners[None, :, 1] * s[:, None]) * half[:, None]
            oy = (corners[None, :, 0] * s[:, None] + corners[None, :, 1] * c[:, None]) * half[:, None]
            verts = np.stack([sx[:, None] + ox, sy[:, None] + oy], axis=-1)   # (N,4,2)

            # 图集
            dx = max(1, d.tex_dx)
            dy = max(1, d.tex_dy)
            cells = dx * dy
            fi = np.mod((frame * cells).astype(np.int64), cells)
            col = (fi % dx).astype(np.float64)
            row = (fi // dx).astype(np.float64)
            u0 = col / dx
            u1 = (col + 1.0) / dx
            v1 = 1.0 - row / dy
            v0 = 1.0 - (row + 1.0) / dy
            # 内缩半个像素，避免双线性采样吃到相邻格/格间缝隙
            tw = th = 0
            if tex_size is not None:
                try:
                    key = "%s|%d|%d" % ((d.texture or "").replace(chr(92), "/").lower(), dx, dy)
                    got = tex_size(key)
                    if got:
                        tw, th = int(got[0]), int(got[1])
                except Exception:  # noqa: BLE001
                    tw = th = 0
            if tw > 0 and th > 0:
                iu = 0.5 / float(tw)
                iv = 0.5 / float(th)
                u0 = u0 + iu
                u1 = u1 - iu
                v0 = v0 + iv
                v1 = v1 - iv
            uvs = np.stack([np.stack([u0, v0], -1), np.stack([u1, v0], -1),
                            np.stack([u1, v1], -1), np.stack([u0, v1], -1)], axis=1)

            tri = np.array([0, 1, 2, 0, 2, 3])
            v6 = verts[:, tri, :].reshape(-1, 2)
            uv6 = uvs[:, tri, :].reshape(-1, 2)
            a6 = np.repeat(alpha, 6)
            colors = np.stack([np.ones_like(a6), np.ones_like(a6), np.ones_like(a6), a6], axis=1)

            key = (d.texture or "").replace("\\", "/").lower()
            slot = out.setdefault(key, [])
            slot.append((v6, uv6, colors, depth.repeat(6)))

        batches = []
        for key, parts in out.items():
            v = np.concatenate([p[0] for p in parts], axis=0)
            uv = np.concatenate([p[1] for p in parts], axis=0)
            col = np.concatenate([p[2] for p in parts], axis=0)
            dep = np.concatenate([p[3] for p in parts], axis=0)
            order = np.argsort(-dep)          # 远的先画
            batches.append((key, v[order], uv[order], col[order]))
        return batches
