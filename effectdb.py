# -*- coding: utf-8 -*-
"""解析 Blitzkrieg 的特效数据（纯 XML）：组合特效 / 粒子 / 武器。

三层结构：
  Effects/Effects/<name>.xml     组合：<effect><effect><particles><item start duration scale>
  Effects/Particles/<name>.xml   粒子：<KeyData ...> + 18 条曲线 + TextureName/Wind/Direction
  Weapons/<name>.xml             武器：EffectGunFire / EffectHit* / FlashFire

约定：曲线点的 time 在 XML 里是 0..1000，这里统一归一化成 0..1。
"""
import xml.etree.ElementTree as ET

import numpy as np

CURVE_TAGS = (
    "lifeTime", "GenerateArea", "Density", "BeginSpeed", "BeginSpeedRandomizer",
    "GenerateAngel", "ParticleLifeTimeRandomizer", "GenerateSpin",
    "GenerateSpinRnd", "GenerateSpinRand", "GenerateOpacity", "Spin",
    "Wight", "Weight", "TextureFrame", "Size", "Opacity", "Speed", "SpeedRnd",
)


def _fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _vec3(el, default=(0.0, 0.0, 0.0)):
    if el is None:
        return np.array(default, np.float64)
    return np.array([_fnum(el.get("x")), _fnum(el.get("y")), _fnum(el.get("z"))], np.float64)


class Curve(object):
    """线性插值曲线，time 归一化到 0..1。"""

    __slots__ = ("times", "values", "_const")

    def __init__(self, times, values):
        order = np.argsort(times) if len(times) else np.array([], np.int64)
        self.times = np.asarray(times, np.float64)[order] if len(times) else np.zeros(0)
        self.values = np.asarray(values, np.float64)[order] if len(values) else np.zeros(0)
        self._const = float(self.values[0]) if len(self.values) else 0.0

    def __len__(self):
        return len(self.times)

    def sample(self, f):
        if not len(self.times):
            return 0.0
        if len(self.times) == 1:
            return self._const
        return float(np.interp(float(f), self.times, self.values))

    def eval(self, arr):
        if not len(self.times):
            return np.zeros(np.shape(arr), np.float64)
        if len(self.times) == 1:
            return np.full(np.shape(arr), self._const, np.float64)
        return np.interp(arr, self.times, self.values)

    @classmethod
    def parse(cls, el):
        if el is None:
            return cls([], [])
        times, vals = [], []
        for it in el.iter("item"):
            times.append(_fnum(it.get("time")) / 1000.0)
            vals.append(_fnum(it.get("value")))
        return cls(times, vals)

    @classmethod
    def constant(cls, v):
        return cls([0.0, 1.0], [v, v])


class ParticleDef(object):
    """一个粒子定义（Effects/Particles/*.xml）。"""

    def __init__(self):
        self.name = ""
        self.lifetime = 1000.0          # KeyData@LifeTime（ms）
        self.gravity = 0.0
        self.tex_dx = 1
        self.tex_dy = 1
        self.area_type = 0
        self.radial_wind = 0.0
        self.complex = 0
        self.texture = ""
        self.wind = np.zeros(3, np.float64)
        self.direction = np.array([0.0, 0.0, 1.0], np.float64)
        self.curves = {}
        self.sub_effect = ""

    def curve(self, name, default=0.0):
        c = self.curves.get(name)
        if c is None and name == "Wight":
            c = self.curves.get("Weight")
        if c is None:
            c = self.curves.get("Weight" if name == "Wight" else name)
        return c if c is not None else Curve.constant(default)

    @classmethod
    def parse(cls, data):
        root = ET.fromstring(data)
        kd = root.find(".//KeyData")
        if kd is None:
            kd = root
        d = cls()
        d.lifetime = _fnum(kd.get("LifeTime"), 1000.0)
        d.gravity = _fnum(kd.get("Gravity"), 0.0)
        d.tex_dx = max(1, int(_fnum(kd.get("TextureDX"), 1)))
        d.tex_dy = max(1, int(_fnum(kd.get("TextureDY"), 1)))
        d.area_type = int(_fnum(kd.get("AreaType"), 0))
        d.radial_wind = _fnum(kd.get("RadialWind"), 0.0)
        d.complex = int(_fnum(kd.get("ComplexParticleSource"), 0))
        tn = kd.find("TextureName")
        d.texture = (tn.text or "").strip() if tn is not None else ""
        d.wind = _vec3(kd.find("Wind"))
        dir_el = kd.find("Direction")
        if dir_el is not None:
            v = _vec3(dir_el, (0, 0, 1))
            n = float(np.linalg.norm(v))
            d.direction = v / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
        for tag in CURVE_TAGS:
            el = kd.find(tag)
            if el is None and tag == "Wight":
                el = kd.find("Weight")
            if el is not None:
                d.curves[tag] = Curve.parse(el)
        pe = kd.find("ParticleEffect")
        if pe is not None and (pe.text or "").strip():
            d.sub_effect = pe.text.strip()
        return d


class EffectItem(object):
    """组合特效里的一条粒子发射项。"""

    __slots__ = ("start", "duration", "scale", "path", "offset", "particle")

    def __init__(self):
        self.start = 0.0
        self.duration = 1000.0
        self.scale = 1.0
        self.path = ""
        self.offset = np.zeros(3, np.float64)
        self.particle = None        # ParticleDef，解析后填


class EffectDef(object):
    def __init__(self):
        self.name = ""
        self.items = []
        self.sound = ""
        self.duration = 0.0

    @classmethod
    def parse(cls, data, name=""):
        root = ET.fromstring(data)
        inner = root.find("effect/effect")
        if inner is None:
            inner = root.find("effect")
        if inner is None:
            inner = root
        d = cls()
        d.name = name
        snd = inner.find("sound")
        d.sound = (snd.text or "").strip() if snd is not None else ""
        for it in inner.findall("./particles/item"):
            item = EffectItem()
            item.start = _fnum(it.get("start"), 0.0)
            item.duration = _fnum(it.get("duration"), 0.0)
            item.scale = _fnum(it.get("scale"), 1.0) or 1.0
            p = it.find("path")
            item.path = (p.text or "").strip() if p is not None else ""
            item.offset = _vec3(it.find("pos"))
            d.items.append(item)
        d.duration = max([i.start + i.duration for i in d.items] or [0.0])
        return d


class WeaponDef(object):
    TAGS = ("EffectGunFire", "EffectTrajectory", "EffectHitDirect", "EffectHitMiss",
            "EffectHitReflect", "EffectHitGround", "EffectHitWater", "EffectHitAir")

    def __init__(self):
        self.name = ""
        self.effects = {}
        self.flash_fire = (0, 0)
        self.flash_explosion = (0, 0)

    @classmethod
    def parse(cls, data, name=""):
        root = ET.fromstring(data)
        d = cls()
        d.name = name
        for tag in cls.TAGS:
            el = root.find(".//" + tag)
            d.effects[tag] = (el.text or "").strip() if el is not None else ""
        for tag, attr in (("FlashFire", "flash_fire"), ("FlashExplosion", "flash_explosion")):
            el = root.find(".//" + tag)
            if el is not None:
                setattr(d, attr, (int(_fnum(el.get("Power"))), int(_fnum(el.get("Duration")))))
        return d


class EffectDB(object):
    """基于 PakFS 的解析缓存。"""

    def __init__(self, fs):
        self.fs = fs
        self._effects = {}
        self._particles = {}
        self._weapons = {}
        self._bad = set()

    # ---------------- 特效
    def effect(self, name):
        key = (name or "").strip().lower()
        if not key:
            return None
        if key in self._effects:
            return self._effects[key]
        if key in self._bad:
            return None
        path = self.fs.effect_path(key)
        data = self.fs.read(path) if path else None
        if not data:
            self._bad.add(key)
            return None
        try:
            eff = EffectDef.parse(data, key)
        except Exception:
            self._bad.add(key)
            return None
        for item in eff.items:
            item.particle = self.particle(item.path)
        self._effects[key] = eff
        return eff

    def particle(self, path):
        key = (path or "").replace("\\", "/").strip().lower()
        if not key:
            return None
        if key in self._particles:
            return self._particles[key]
        if key in self._bad:
            return None
        real = self.fs.particle_path(key)
        data = self.fs.read(real) if real else None
        if not data:
            self._bad.add(key)
            return None
        try:
            p = ParticleDef.parse(data)
        except Exception:
            self._bad.add(key)
            return None
        p.name = key
        self._particles[key] = p
        return p

    def weapon(self, name):
        key = (name or "").strip().lower()
        if not key:
            return None
        if key in self._weapons:
            return self._weapons[key]
        if key in self._bad:
            return None
        path = self.fs.weapon_path(key)
        data = self.fs.read(path) if path else None
        if not data:
            self._bad.add(key)
            return None
        try:
            w = WeaponDef.parse(data, key)
        except Exception:
            self._bad.add(key)
            return None
        self._weapons[key] = w
        return w

    def effect_names(self):
        return self.fs.effects()

    def clear(self):
        self._effects.clear()
        self._particles.clear()
        self._weapons.clear()
        self._bad.clear()
