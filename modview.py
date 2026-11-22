# -*- coding: utf-8 -*-
"""闪电战 .mod 查看器 —— GUI 版
左侧：部件列表（id / 名字 / 类型 / 限制）
右侧：3D 视口（鼠标拖动旋转，滚轮缩放，右键平移）
底部：同目录贴图（.dds 自动解码预览）

依赖：仅 tkinter + numpy（都由 exe 内置）
"""

import os
import re
import sys
import math
import json
import zlib
import struct
import ctypes
from ctypes import wintypes
import xml.etree.ElementTree as ET
from collections import Counter

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ---------------------------------------------------------------- DPI
_dpi_done = False


def enable_dpi_awareness():
    """告诉 Windows 不要对我们的窗口做位图拉伸 —— 4K 上字发糊就是这个原因。"""
    global _dpi_done
    if _dpi_done:
        return
    _dpi_done = True
    u = ctypes.windll.user32
    try:                       # Win10 1703+：按显示器感知（最清晰）
        u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:          # noqa: BLE001
        pass
    try:                       # Win8.1+
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:          # noqa: BLE001
        pass
    try:                       # 老系统兜底
        u.SetProcessDPIAware()
    except Exception:          # noqa: BLE001
        pass

# ---------------------------------------------------------------- 拖放
WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32

_HDROP = ctypes.c_void_p
_user32.GetParent.restype = ctypes.c_void_p
_user32.GetParent.argtypes = [ctypes.c_void_p]
_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
_user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
_user32.CallWindowProcW.restype = ctypes.c_longlong
_user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_ulonglong, ctypes.c_longlong]
_shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, wintypes.BOOL]
_shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                    ctypes.c_wchar_p, ctypes.c_uint]
_shell32.DragQueryFileW.restype = ctypes.c_uint
_shell32.DragFinish.argtypes = [ctypes.c_void_p]

if hasattr(ctypes, "WINFUNCTYPE"):
    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                                  ctypes.c_ulonglong, ctypes.c_longlong)
else:  # pragma: no cover
    _WNDPROC = ctypes.CFUNCTYPE(ctypes.c_long, wintypes.HWND, ctypes.c_uint,
                                ctypes.c_ulong, ctypes.c_long)


class FileDrop:
    """把顶层窗口注册成文件投放目标；拖进来时回调 on_drop([路径, ...])。

    Tk 自身不支持文件拖放，这里走 Win32 的 DragAcceptFiles + 替换窗口过程。
    """

    def __init__(self, root, on_drop):
        self.root = root
        self.on_drop = on_drop
        self.hwnd = self._toplevel_hwnd()
        self._old_proc = None
        self._proc = None
        if not self.hwnd:
            return
        try:
            _shell32.DragAcceptFiles(ctypes.c_void_p(self.hwnd), True)
            self._proc = _WNDPROC(self._wndproc)       # 必须持有引用，否则会被回收
            self._old_proc = _user32.SetWindowLongPtrW(
                ctypes.c_void_p(self.hwnd), GWLP_WNDPROC,
                ctypes.cast(self._proc, ctypes.c_void_p))
        except Exception:  # noqa: BLE001
            self._proc = None

    def _toplevel_hwnd(self):
        try:
            h = int(self.root.wm_frame(), 16)
            if h:
                return h
        except Exception:  # noqa: BLE001
            pass
        try:
            return _user32.GetParent(ctypes.c_void_p(self.root.winfo_id()))
        except Exception:  # noqa: BLE001
            return None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            try:
                self._handle(wparam)
            except Exception:  # noqa: BLE001
                pass
            return 0
        return _user32.CallWindowProcW(ctypes.c_void_p(self._old_proc),
                                       ctypes.c_void_p(hwnd), msg, wparam, lparam)

    def _handle(self, hdrop):
        n = _shell32.DragQueryFileW(ctypes.c_void_p(hdrop), 0xFFFFFFFF, None, 0)
        paths = []
        for i in range(int(n)):
            ln = _shell32.DragQueryFileW(ctypes.c_void_p(hdrop), i, None, 0)
            buf = ctypes.create_unicode_buffer(int(ln) + 1)
            _shell32.DragQueryFileW(ctypes.c_void_p(hdrop), i, buf, int(ln) + 1)
            paths.append(buf.value)
        _shell32.DragFinish(ctypes.c_void_p(hdrop))
        if paths:
            self.on_drop(paths)


# ---------------------------------------------------------------- mod_scene
_here = os.path.dirname(os.path.abspath(__file__))
_cands = []
if hasattr(sys, "_MEIPASS"):
    _cands.append(sys._MEIPASS)
_cands.append(_here)
_cands.append(os.path.join(_here, "mod"))
for _v in ("5.2", "5.1", "5.0", "4.5", "4.2", "4.1", "4.0"):
    _cands.append(os.path.expandvars(
        r"%APPDATA%\Blender Foundation\Blender\{}\scripts\addons\mod".format(_v)))

for _p in _cands:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from glviewport import GLViewport
from i18n import tr, set_lang, LANG_NAMES

try:
    import numpy as np
    from mod_scene import ModScene, ModSceneObject, ModSceneMeshData
except Exception as exc:  # noqa: BLE001
    tk.messagebox.showerror("缺少依赖", "无法加载 mod_scene / numpy：\n{}".format(exc))
    sys.exit(1)


# ================================================================ 图片解码（GDI+）
class _GdiPlus:
    """用 Windows 自带的 GDI+ 读 PNG / JPG / BMP / GIF / TIFF，不依赖 Pillow。"""

    PixelFormat32bppARGB = 0x0026200A

    class StartupInput(ctypes.Structure):
        _fields_ = [("GdiplusVersion", ctypes.c_uint32),
                    ("DebugEventCallback", ctypes.c_void_p),
                    ("SuppressBackgroundThread", ctypes.c_int),
                    ("SuppressExternalCodecs", ctypes.c_int)]

    class Rect(ctypes.Structure):
        _fields_ = [("X", ctypes.c_int), ("Y", ctypes.c_int),
                    ("Width", ctypes.c_int), ("Height", ctypes.c_int)]

    class BitmapData(ctypes.Structure):
        _fields_ = [("Width", ctypes.c_uint32), ("Height", ctypes.c_uint32),
                    ("Stride", ctypes.c_int), ("PixelFormat", ctypes.c_int),
                    ("Scan0", ctypes.c_void_p), ("Reserved", ctypes.c_size_t)]

    def __init__(self):
        self.ok = False
        self.token = ctypes.c_size_t(0)
        try:
            self.gp = ctypes.windll.gdiplus
            self.gp.GdiplusStartup.argtypes = [ctypes.POINTER(ctypes.c_size_t),
                                               ctypes.POINTER(self.StartupInput),
                                               ctypes.c_void_p]
            self.gp.GdipCreateBitmapFromFile.argtypes = [ctypes.c_wchar_p,
                                                         ctypes.POINTER(ctypes.c_void_p)]
            self.gp.GdipDisposeImage.argtypes = [ctypes.c_void_p]
            self.gp.GdipGetImageWidth.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
            self.gp.GdipGetImageHeight.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
            self.gp.GdipBitmapLockBits.argtypes = [ctypes.c_void_p, ctypes.POINTER(self.Rect),
                                                   ctypes.c_uint, ctypes.c_int,
                                                   ctypes.POINTER(self.BitmapData)]
            self.gp.GdipBitmapUnlockBits.argtypes = [ctypes.c_void_p,
                                                     ctypes.POINTER(self.BitmapData)]
            si = self.StartupInput(1, None, 0, 0)
            st = self.gp.GdiplusStartup(ctypes.byref(self.token), ctypes.byref(si), None)
            self.ok = (st == 0)
        except Exception:  # noqa: BLE001
            self.ok = False

    def load(self, path):
        """返回 (w, h, HxWx3 uint8) 或 None。"""
        if not self.ok:
            return None
        bmp = ctypes.c_void_p()
        if self.gp.GdipCreateBitmapFromFile(ctypes.c_wchar_p(path), ctypes.byref(bmp)) != 0:
            return None
        try:
            w, h = ctypes.c_uint(), ctypes.c_uint()
            self.gp.GdipGetImageWidth(bmp, ctypes.byref(w))
            self.gp.GdipGetImageHeight(bmp, ctypes.byref(h))
            if w.value <= 0 or h.value <= 0:
                return None
            rect = self.Rect(0, 0, w.value, h.value)
            data = self.BitmapData()
            if self.gp.GdipBitmapLockBits(bmp, ctypes.byref(rect), 1,     # ImageLockModeRead
                                          self.PixelFormat32bppARGB,
                                          ctypes.byref(data)) != 0:
                return None
            try:
                stride = data.Stride
                buf = ctypes.string_at(data.Scan0, abs(stride) * h.value)
                arr = np.frombuffer(buf, np.uint8).reshape(h.value, abs(stride))
                arr = arr[:, :w.value * 4].reshape(h.value, w.value, 4)
                if stride < 0:              # 自下而上的位图，翻回来
                    arr = arr[::-1]
                return w.value, h.value, np.ascontiguousarray(arr[:, :, [2, 1, 0]])
            finally:
                self.gp.GdipBitmapUnlockBits(bmp, ctypes.byref(data))
        finally:
            self.gp.GdipDisposeImage(bmp)


_gdi = None


def load_image(path):
    """统一入口：.dds 走自带解码，其余交给 GDI+。返回 (w, h, rgb_bytes) 或 None。"""
    global _gdi
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dds":
        return load_dds(path)
    if _gdi is None:
        _gdi = _GdiPlus()
    r = _gdi.load(path)
    if not r:
        return None
    w, h, arr = r
    return w, h, arr.tobytes()


IMAGE_EXTS = (".dds", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff")


# ---------------- XML 字段中文说明（来自《1.xml 字段说明.md》） ----------------
XML_TAG_ZH = {
    "base": "单位定义",
    "History": "历史字段",
    "RPG": "基础属性",
    "KeyName": "单位内部名",
    "StatsType": "统计分类",
    "DamagedHPs": "受损血量分段",
    "Type": "装甲等级/类型",
    "AIClass": "AI 移动类型",
    "Commands": "可用指令（位数组）",
    "BitArray": "位数组",
    "Exposures": "暴露部位",
    "AnimDescs": "动画描述",
    "item": "条目",
    "data": "数据",
    "AABB_As": "攻击判定盒",
    "AABB_Ds": "防御盒（装甲分块）",
    "Center": "盒中心（2D）",
    "HalfSize": "半尺寸",
    "AcksRefs": "应答语音",
    "Platforms": "旋转平台（车体/炮塔）",
    "Constraint": "水平旋转限制",
    "ConstraintVertical": "垂直旋转限制",
    "Guns": "武器列表",
    "Weapon": "武器定义引用",
    "AABBCenter": "整车2D包围盒中心",
    "AABBHalfSize": "整车2D包围盒半尺寸",
    "ExhaustPoints": "排气管特效点",
    "DamagePoints": "受损冒烟点",
    "PeoplePoints": "乘员暴露点",
    "TowPoint2D": "牵引点（2D）",
    "HookPoint": "挂钩点",
    "FrontWheel": "前转向轮（2D）",
    "BackWheel": "后转向轮（2D）",
    "EntrancePoint2D": "上下车点（2D）",
    "AmmoPoint2D": "弹药补给点",
    "PeoplePoints2D": "乘员点（2D）",
    "Gunners": "乘员位",
    "EffectDiesel": "柴油机烟特效",
    "EffectSmoke": "中弹起火烟特效",
    "EffectWheelDust": "行驶扬尘特效",
    "EffectShootDust": "开火扬尘特效",
    "EffectFatality": "被击毁爆炸特效",
    "EffectEntrenching": "挖壕特效",
    "EffectDisappear": "残骸消失特效",
    "JoggingX": "X 向行驶抖动",
    "JoggingY": "Y 向行驶抖动",
    "JoggingZ": "Z 向行驶抖动",
    "SoundMoveStart": "起步音效",
    "SoundMove": "行驶循环音效",
    "SoundMoveStop": "停车音效",
    "DeathCraters": "死亡坑（随机选一个）",
}

XML_TAG_PREFIX_ZH = (
    ("Defence", "方向装甲带"),
    ("Armor", "六向装甲值"),
    ("Jogging", "行驶抖动"),
    ("Effect", "特效"),
)

XML_ATTR_ZH = {
    "MaxHP": "最大生命值",
    "RepairCost": "修理花费系数",
    "Sight": "基础视野",
    "SightPower": "光学倍率",
    "Speed": "最大行驶速度",
    "Passability": "通行性",
    "Priority": "优先级",
    "Camouflage": "隐蔽值",
    "Weight": "重量 kg",
    "Price": "造价系数",
    "UninstallRotate": "卸载时允许旋转",
    "UninstallTransport": "可被运输",
    "RotateSpeed": "原地转向速度",
    "TurnRadius": "转向半径",
    "TowingForce": "牵引力",
    "Crew": "乘员数",
    "Passangers": "可搭载步兵数",
    "BoundTileRadius": "占位格半径",
    "SmallAABBCoeff": "小碰撞盒系数",
    "MaxHeight": "可越过最大高度",
    "DivingAngle": "涉水角",
    "ClimbAngle": "爬坡角",
    "TiltAngle": "倾斜行驶角",
    "TiltRatio": "倾斜衰减比",
    "LeavesTracks": "是否留履带印",
    "TrackOffset": "压痕偏移",
    "TrackWidth": "压痕宽度",
    "TrackStart": "压痕起始",
    "TrackEnd": "压痕结束",
    "TrackIntensity": "压痕不透明度",
    "TrackLifetime": "压痕时长（ms）",
    "TowPoint": "牵引点 id",
    "EntrancePoint": "上下车点 id",
    "FatalitySmokePoint": "击毁冒烟点 id",
    "ShootDustPoint": "开火扬尘点 id",
    "MinArmor": "装甲下限",
    "MaxArmor": "装甲上限",
    "Silhouette": "轮廓系数",
    "Size": "位数/数量",
    "Length": "动画时长",
    "Action": "动作类型",
    "Index": "部件 id",
    "AABB_A": "攻击盒索引",
    "AABB_D": "防御盒索引",
    "x": "X",
    "y": "Y",
    "VerticalRotateSpeed": "垂直转速",
    "HorizontalRotationSpeed": "水平转速",
    "ModelPart": "部件 id",
    "NumGuns": "火炮数量",
    "GunCarriageParts": "炮架部件 id",
    "FirstGun": "首炮序号",
    "Min": "最小值",
    "Max": "最大值",
    "Ammo": "备弹量",
    "Direction": "射界",
    "ReloadCost": "装填耗时",
    "ShootPoint": "炮口点 id",
    "Recoil": "后座动画",
    "RecoilLength": "后座行程",
    "RecoilTime": "后座周期",
    "RecoilShakeTime": "车体抖动时长",
    "RecoilShakeAngle": "车体抖动幅度",
    "Period1": "周期1",
    "Period2": "周期2",
    "Amp1": "振幅1",
    "Amp2": "振幅2",
    "Phase1": "相位1",
    "Phase2": "相位2",
}


def _xml_zh_tag(tag):
    if tag in XML_TAG_ZH:
        return XML_TAG_ZH[tag]
    for pre, zh in XML_TAG_PREFIX_ZH:
        if tag.startswith(pre):
            return zh
    return None


def _xml_zh_attr(tag, key):
    v = XML_ATTR_ZH.get(tag + "." + key)
    if v:
        return v
    return XML_ATTR_ZH.get(key)



def _xml_friendly_id(elem):
    """给匿名的 <item> 找一个能认出来的名字（武器名/单位名/类型）。"""
    for t in ("KeyName", "Weapon", "Type", "StatsType"):
        try:
            ch = elem.find(t)
        except Exception:  # noqa: BLE001
            ch = None
        if ch is not None and (ch.text or "").strip():
            return ch.text.strip()
    return None


def _xml_child_items(elem):
    """子节点列表：重复的同名兄弟加 [n]，并附上武器名/单位名等可读标识。"""
    c = Counter(ch.tag for ch in elem)
    seen = {}
    out = []
    for ch in elem:
        seen[ch.tag] = seen.get(ch.tag, 0) + 1
        lbl = ch.tag
        if c[ch.tag] > 1:
            lbl += " [{}]".format(seen[ch.tag])
        fid = _xml_friendly_id(ch)
        if fid:
            lbl += "  " + fid
        out.append((ch, lbl))
    return out


def _indent_xml(elem, level=0):
    """给 ElementTree 套上制表符缩进（.mod 的 xml 原本就是 tab 缩进）。"""
    pad = "\n" + "\t" * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "\t"
        for ch in elem:
            _indent_xml(ch, level + 1)
            if not (ch.tail or "").strip():
                ch.tail = pad + "\t"
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = pad
    elif level and not (elem.tail or "").strip():
        elem.tail = pad


# ================================================================ DDS 解码
def _rgb565(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def _decode_dxt1(data, w, h):
    out = bytearray(w * h * 3)
    bw, bh = (w + 3) // 4, (h + 3) // 4
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            c0, c1, bits = (int.from_bytes(data[pos:pos + 2], "little"),
                            int.from_bytes(data[pos + 2:pos + 4], "little"),
                            int.from_bytes(data[pos + 4:pos + 8], "little"))
            pos += 8
            p0, p1 = _rgb565(c0), _rgb565(c1)
            if c0 > c1:
                pal = [p0, p1,
                       tuple((2 * a + b) // 3 for a, b in zip(p0, p1)),
                       tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]
                alpha3 = 255
            else:
                pal = [p0, p1,
                       tuple((a + b) // 2 for a, b in zip(p0, p1)),
                       (0, 0, 0)]
                alpha3 = 0
            for py in range(4):
                for px in range(4):
                    idx = (bits >> (2 * (py * 4 + px))) & 3
                    x, y = bx * 4 + px, by * 4 + py
                    if x >= w or y >= h:
                        continue
                    o = (y * w + x) * 3
                    out[o:o + 3] = bytes(pal[idx])
    return bytes(out)


def _decode_dxt5(data, w, h):
    """DXT5：每块 16 字节 = 8 字节 alpha + 8 字节颜色。"""
    out = np.zeros((h, w, 3), np.uint8)
    bw, bh = (w + 3) // 4, (h + 3) // 4
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            a0, a1 = data[pos], data[pos + 1]
            abits = int.from_bytes(data[pos + 2:pos + 8], "little")
            pos += 8
            c0 = int.from_bytes(data[pos:pos + 2], "little")
            c1 = int.from_bytes(data[pos + 2:pos + 4], "little")
            cbits = int.from_bytes(data[pos + 4:pos + 8], "little")
            pos += 8

            p0, p1 = _rgb565(c0), _rgb565(c1)
            if c0 > c1:
                pal = [p0, p1,
                       tuple((2 * x + y) // 3 for x, y in zip(p0, p1)),
                       tuple((x + 2 * y) // 3 for x, y in zip(p0, p1))]
            else:
                pal = [p0, p1,
                       tuple((x + y) // 2 for x, y in zip(p0, p1)),
                       (0, 0, 0)]

            if a0 > a1:
                apal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                apal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]

            for py in range(4):
                for px in range(4):
                    x, y = bx * 4 + px, by * 4 + py
                    if x >= w or y >= h:
                        continue
                    r, g, b = pal[(cbits >> (2 * (py * 4 + px))) & 3]
                    # 不乘 alpha：_h 颜色贴图是不透明的，alpha 字段多为无效数据
                    out[y, x] = (r, g, b)
    return out


def load_dds(path):
    """解析 DDS，返回 (w, h, rgb_bytes) 或 None。仅支持 DXT1 / DXT5 / 未压缩 RGB。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"DDS ":
            return None
        h = int.from_bytes(data[12:16], "little")
        w = int.from_bytes(data[16:20], "little")
        fourcc = data[84:88]
        hdr = 128
        if w <= 0 or h <= 0 or w > 4096 or h > 4096:
            return None

        dflags = int.from_bytes(data[80:84], "little")
        rgbbits = int.from_bytes(data[88:92], "little")

        # 未压缩 32 位（A8R8G8B8 / X8R8G8B8），BGRA 顺序
        if fourcc == bytes(4) and rgbbits == 32 and (dflags & 0x40):
            px = data[hdr:hdr + w * h * 4]
            arr = np.frombuffer(px, dtype=np.uint8).reshape(-1, 4)
            rgb = arr[:, [2, 1, 0]]          # BGR -> RGB
            return w, h, rgb.tobytes()

        if fourcc == b"DXT1":
            return w, h, _decode_dxt1(data[hdr:], w, h)
        if fourcc == b"DXT5":
            arr = _decode_dxt5(data[hdr:], w, h)
            return w, h, arr.tobytes()
        return None
    except Exception:  # noqa: BLE001
        return None


def parse_uv_from_obj(obj_path):
    """从 .obj 读 UV：{部件名: face_uv (M,3,2)}，面顺序与 Blender 导出一致。"""
    vts = []
    parts = {}
    cur = None
    try:
        fh = open(obj_path, encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return {}
    with fh:
        for line in fh:
            if line.startswith("o "):
                cur = line[2:].strip()
                parts[cur] = []
            elif line.startswith("vt "):
                p = line.split()
                vts.append((float(p[1]), float(p[2])))
            elif line.startswith("f ") and cur:
                tidx = []
                for seg in line[2:].split():
                    pr = seg.split("/")
                    tidx.append(int(pr[1]) - 1 if len(pr) > 1 and pr[1] else 0)
                for k in range(1, len(tidx) - 1):
                    parts[cur].append([tidx[0], tidx[k], tidx[k + 1]])
    if not vts:
        return {}
    arr_vts = np.array(vts, dtype=np.float64)
    out = {}
    for name, faces in parts.items():
        if not faces:
            continue
        a = np.array(faces, dtype=np.int64)
        out[name] = arr_vts[a]
    return out


def _install_mod_scene_fastpath():
    """给 mod_scene 的解析器打两处无损加速（不改插件本体）。

    1) decode_data 的数组分支原来是逐元素 struct.unpack，几百万次调用；
       改成一次 struct.unpack 批量解包。
    2) Node.__getitem__ 按名字查子节点原来是线性扫描 + 列表推导，
       解析后节点数上百万；改成首次查询时建一次 name->child 映射。
    """
    try:
        from mod_scene import node as _node
    except Exception:  # noqa: BLE001
        return
    if getattr(_node, "_fast_patched", False):
        return
    import struct as _struct

    _fmt = {"int": "i", "float": "f", "short": "H"}
    _size = {"int": 4, "float": 4, "short": 2}

    def decode_data(data, typ):
        if typ == "int":
            return _struct.unpack("<i", data)[0]
        if typ == "short":
            return _struct.unpack("<H", data)[0]
        if typ == "float":
            return _struct.unpack("<f", data)[0]
        if typ == "str":
            return data.decode(encoding="cp1251")
        if isinstance(typ, str) and typ.endswith("_"):
            base = typ[:-1]
            if base in _fmt:
                step = _size[base]
                n = len(data) // step
                if n <= 0:
                    return []
                return list(_struct.unpack("<{}{}".format(n, _fmt[base]),
                                           data[:n * step]))
        return data

    def __getitem__(self, item):
        if isinstance(item, int):
            return self.children[item]
        m = self.__dict__.get("_name_map")
        if m is None:
            m = {}
            for c in self.children:
                if c.name not in m:
                    m[c.name] = c
            self.__dict__["_name_map"] = m
        got = m.get(item)
        if got is None:
            raise ValueError("No child with name '{}'".format(item))
        return got

    # 3) 递归解析改迭代（149 万次递归调用 → 显式栈），结构完全等价
    _orig_from_bytes = _node.Node._from_bytes.__func__

    def _from_bytes(cls, data, descriptor, tag=None):
        try:
            root = cls(None, tag, descriptor.get("name", tag), descriptor.get("type", ""))
            stack = [(root, data, descriptor)]
            while stack:
                node, d, desc = stack.pop()
                if node.typ or len(d) < 5 or d[0] != 1:
                    node.data = decode_data(d, node.typ)
                    continue
                ptr, n, kids = 0, len(d), []
                child_map = desc.get("children", {})
                while ptr + 1 < n:
                    child_tag = d[ptr:ptr + 1].hex()
                    dbl = d[ptr + 1]
                    if dbl % 2:
                        child_start = ptr + 5
                        dbl = int.from_bytes(d[ptr + 1:child_start], "little")
                    else:
                        child_start = ptr + 2
                    ptr = child_end = child_start + dbl // 2
                    cdata = d[child_start:child_end]
                    cdesc = child_map.get(child_tag, {})
                    child = cls(None, child_tag, cdesc.get("name", child_tag),
                                cdesc.get("type", ""))
                    kids.append(child)
                    stack.append((child, cdata, cdesc))
                node.children = kids
            return root
        except Exception:  # noqa: BLE001
            return _orig_from_bytes(cls, data, descriptor, tag)

    _node.decode_data = decode_data
    _node.Node.__getitem__ = __getitem__
    _node.Node._from_bytes = classmethod(_from_bytes)
    _node._fast_patched = True


_install_mod_scene_fastpath()


def parse_uv(mod_path, scene=None):
    """.mod 内的 UV 优先，缺失的部件再用同目录 .obj 补。

    曾经的做法是 .obj 优先，但同目录的 .obj 可能是旧版本导出的：
    面数一样而面序不同时，UV 会被贴到错误的三角形上，表现为贴图撕裂/扭曲。
    .mod 里的 loops->uvs 索引才是模型自身的数据，实测与 Blender 导出的 .obj 完全一致。
    """
    out = _parse_uv_mod(mod_path, scene)
    folder = os.path.dirname(mod_path)
    try:
        objs = [f for f in os.listdir(folder) if f.lower().endswith(".obj")]
    except Exception:  # noqa: BLE001
        objs = []
    if not objs:
        return out
    # .mod 里已经全有 UV 就不用再读 .obj（省掉一遍大文件扫描）
    try:
        if scene is not None:
            need = [o.name for o in scene.objects
                    if o.data is not None and len(o.data.faces)
                    and o.name not in out]
            if not need:
                return out
    except Exception:  # noqa: BLE001
        pass
    for f in objs:
        r = parse_uv_from_obj(os.path.join(folder, f))
        if r:
            for k, v in r.items():
                if k not in out:
                    out[k] = (None, v)     # 格式：(faces=None, face_uv)，沿用 ModScene 的 faces
    return out


def _parse_uv_mod(mod_path, scene=None):
    """用 ModSceneMeshData.loops 直接取 UV。

    注意：mod_scene 插件在读取时已经把 V 翻转过了（uvs[:,1] = 1 - uvs[:,1]），
    所以这里拿到的 UV 和 .obj 导出的完全一致，渲染时不需要再翻转。
    """
    if scene is None:
        try:
            scene = ModScene()
            scene.from_file(mod_path)
        except Exception:  # noqa: BLE001
            return {}

    out = {}
    for o in scene.objects:
        d = o.data
        if d is None or len(d.uvs) == 0 or len(d.faces) == 0:
            continue
        if len(d.loops) != len(d.faces) * 3:
            continue
        try:
            uv_idx = np.asarray(d.loops[:, 2], dtype=np.int64)
        except Exception:  # noqa: BLE001
            continue
        if uv_idx.size == 0 or int(uv_idx.max()) >= len(d.uvs):
            continue
        out[o.name] = (np.asarray(d.faces, dtype=np.int64),
                       d.uvs[uv_idx].reshape(-1, 3, 2))
    return out


TEX_MAX = 2048     # 贴图最大边长，超过才缩（现在是 GPU 渲染，512 太糊了）


def _shrink_tex(arr, max_side=TEX_MAX):
    """把贴图等比缩小到 max_side 以内，最近邻采样。"""
    h, w = arr.shape[:2]
    m = max(w, h)
    if m <= max_side:
        return w, h, arr
    k = max_side / float(m)
    nw, nh = max(1, int(w * k)), max(1, int(h * k))
    yi = (np.arange(nh) * h // nh).clip(0, h - 1)
    xi = (np.arange(nw) * w // nw).clip(0, w - 1)
    return nw, nh, np.ascontiguousarray(arr[yi][:, xi])


def find_texture(mod_path):
    """按命名约定找主贴图（1_h.dds 优先），返回 (w, h, ndarray) 或 None（已预缩放）。"""
    folder = os.path.dirname(mod_path)
    cands = ["1_h.dds", "1a_h.dds", "1+++_h.dds", "1++_h.dds", "1+_h.dds", "1.dds", "2_h.dds",
             "1_h.png", "1a_h.png", "1.png", "1_h.jpg", "1.jpg", "1_h.jpeg"]
    try:
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(IMAGE_EXTS) and not f.lower().startswith("icon"):
                if f not in cands:
                    cands.append(f)
    except Exception:  # noqa: BLE001
        pass

    for cand in cands:
        p = os.path.join(folder, cand)
        if not os.path.exists(p):
            continue
        r = load_image(p)
        if not r:
            continue
        w, h, rgb = r
        arr = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
        return _shrink_tex(arr)
    return None


def parse_obj(path):
    """极简 OBJ 解析（预览用）：v/vt/f/o/g/usemtl/mtllib + MTL 的 Kd/map_Kd。
    多边形按扇形三角化；usemtl 变化时拆分组（组内材质色一致）。
    返回 (parts, tex_path, up_axis)：
      parts = [(name, v(n,3), f(m,3), color01, uv(m,3,2) 或 None), ...]
    up_axis：modview 自己导出的文件带 "# modview-export: z-up/y-up" 标记，
    按标记处理；其它文件按 OBJ 惯例当 Y-up。"""
    folder = os.path.dirname(path)
    up_axis = "y"
    mtls = []

    # 预扫描：导出标记 + mtllib（材质色在主循环前就要用到）
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            low = ln.lower()
            if "modview-export: y-up" in low:
                up_axis = "y"
            elif "modview-export: z-up" in low:
                up_axis = "z"
            elif ln.startswith("mtllib"):
                t = ln.split()
                if len(t) > 1:
                    mtls.append(" ".join(t[1:]))

    # MTL：材质色 + 贴图路径
    mat_color = {}
    tex_name = None
    for mname in mtls:
        mp = mname if os.path.isabs(mname) else os.path.join(folder, mname)
        if not os.path.exists(mp):
            continue
        try:
            with open(mp, "r", encoding="utf-8", errors="replace") as fh:
                cur = None
                for ln in fh:
                    t = ln.split()
                    if not t:
                        continue
                    if t[0] == "newmtl" and len(t) > 1:
                        cur = " ".join(t[1:])
                    elif t[0] == "Kd" and len(t) >= 4 and cur is not None:
                        try:
                            mat_color[cur] = tuple(
                                max(0.0, min(1.0, float(x))) for x in t[1:4])
                        except ValueError:
                            pass
                    elif t[0] == "map_Kd" and len(t) >= 2 and tex_name is None:
                        tex_name = t[-1]        # 末尾 token（跳过 -选项）
        except OSError:
            pass

    verts = []
    vts = []
    tris = []            # 当前组的 (vi, ti, color01) x3
    cur_color = (0.75, 0.75, 0.75)
    cur_name = None
    groups = []          # (name, [(vi,ti,col) x3, ...])

    def flush():
        if tris:
            groups.append((cur_name, list(tris)))
            del tris[:]

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            if ln.startswith("#"):
                continue
            t = ln.split()
            tag = t[0]
            if tag == "v" and len(t) >= 4:
                verts.append((float(t[1]), float(t[2]), float(t[3])))
            elif tag == "vt" and len(t) >= 3:
                vts.append((float(t[1]), float(t[2])))
            elif tag == "f" and len(t) >= 4:
                idx = []
                for tok in t[1:]:
                    seg = tok.split("/")
                    vi = int(seg[0])
                    vi = vi - 1 if vi > 0 else len(verts) + vi
                    ti = None
                    if len(seg) >= 2 and seg[1]:
                        ti = int(seg[1])
                        ti = ti - 1 if ti > 0 else len(vts) + ti
                    idx.append((vi, ti))
                for k in range(1, len(idx) - 1):        # 扇形三角化
                    tris.append((idx[0][0], idx[k][0], idx[k + 1][0],
                                 idx[0][1], idx[k][1], idx[k + 1][1],
                                 cur_color))
            elif tag in ("o", "g"):
                flush()
                cur_name = " ".join(t[1:]) or None
            elif tag == "usemtl":
                flush()
                cur_color = mat_color.get(" ".join(t[1:]), cur_color)
    flush()

    parts = []
    used_colors = set()
    for gname, gtris in groups:
        vis = [c for tri in gtris for c in tri[:3]]
        lo, hi = min(vis), max(vis)
        v = np.asarray(verts[lo:hi + 1], dtype=float)
        f = np.empty((len(gtris), 3), dtype=np.int64)
        uvs = None
        has_t = vts and all(c[3] is not None and c[4] is not None and c[5] is not None
                            for c in gtris)
        if has_t:
            uvs = np.empty((len(gtris), 3, 2), dtype=float)
        for r, (a, b, cc, ta, tb, tc, tcol) in enumerate(gtris):
            f[r] = (a - lo, b - lo, cc - lo)
            if has_t:
                uvs[r] = vts[ta], vts[tb], vts[tc]
        col = None
        for c in gtris:
            col = c[6]
            if col not in used_colors:
                break
        if col is None:
            col = cur_color
        used_colors.add(col)
        parts.append((gname or "part", v, f, col, uvs if has_t else None))

    tex_path = None
    if tex_name:
        cand = tex_name if os.path.isabs(tex_name) else os.path.join(folder, tex_name)
        if os.path.exists(cand):
            tex_path = cand
    return parts, tex_path, up_axis


def dds_to_ppm(path, max_side=256):
    """贴图 -> PPM 字节串（tkinter PhotoImage 能直接读）"""
    r = load_image(path)
    if not r:
        return None
    w, h, rgb = r
    step = max(1, max(w, h) // max_side)
    nw, nh = max(1, w // step), max(1, h // step)
    buf = bytearray(nw * nh * 3)
    for y in range(nh):
        for x in range(nw):
            sx, sy = x * step, y * step
            so = (sy * w + sx) * 3
            do = (y * nw + x) * 3
            buf[do:do + 3] = rgb[so:so + 3]
    return ("P6\n{} {}\n255\n".format(nw, nh)).encode("ascii") + bytes(buf)


# ================================================================ 3D 视口
class Viewport3D(tk.Canvas):
    def __init__(self, master, **kw):
        super().__init__(master, bg="#000000", highlightthickness=0, **kw)
        self.meshes = []          # [(name, verts, faces, rgb, normals, uv)]
        self.texture = None       # (w, h, ndarray HxWx3)
        self.center = np.zeros(3)
        self.radius = 1.0
        self.yaw = 35.0
        self.pitch = -22.0
        self.dist = 3.0
        self.pan = [0.0, 0.0]
        self.mode = "solid"       # solid / wire
        self.show_all = True
        self.sel_index = -1
        self.dragging = False
        # 渲染质量：(拖动时降分辨率倍数, 拖动时保留的三角形比例, 静止时降分辨率倍数)
        self.quality = (2, 0.08, 1)
        self._drag = None
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-2>", self._on_pan_press)
        self.bind("<B2-Motion>", self._on_pan)
        self.bind("<Button-3>", self._on_pan_press)
        self.bind("<B3-Motion>", self._on_pan)
        self.bind("<MouseWheel>", self._on_wheel)
        self._cfg_redraw_job = None

        def _do_cfg_redraw():
            self._cfg_redraw_job = None
            try:
                if not self.winfo_exists():
                    return
            except Exception:  # noqa: BLE001
                return
            self.redraw()

        def _queue_cfg_redraw(e):
            # 缩放窗口时 Configure 事件很密，合并成每拍一次全场景重绘
            if self._cfg_redraw_job is None:
                self._cfg_redraw_job = self.after_idle(_do_cfg_redraw)

        self.bind("<Configure>", _queue_cfg_redraw)

    # ---------------- 数据
    def set_meshes(self, meshes):
        """预处理：过滤非法索引、预计算面法线。"""
        self.meshes = []
        minv = np.array([1e18] * 3)
        maxv = np.array([-1e18] * 3)
        for item in meshes:
            name, verts, faces, color = item[:4]
            uv = item[4] if len(item) > 4 else None
            if len(verts) == 0 or len(faces) == 0:
                continue
            v = np.asarray(verts, dtype=np.float64)
            f = np.asarray(faces, dtype=np.int64)
            # 过滤越界索引时必须同步过滤 uv，否则 UV 和三角形错位、贴图全乱
            keep = (f[:, 0] < len(v)) & (f[:, 1] < len(v)) & (f[:, 2] < len(v))
            if not keep.all():
                f = f[keep]
                if uv is not None:
                    uv = uv[keep]
            if len(f) == 0:
                continue
            if uv is not None and len(uv) != len(f):
                uv = None
            p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
            n = np.cross(p1 - p0, p2 - p0)
            ln = np.linalg.norm(n, axis=1, keepdims=True)
            ln[ln == 0] = 1.0
            n = n / ln
            self.meshes.append((name, v, f, np.array(color, dtype=np.float64) / 255.0, n, uv))
            minv = np.minimum(minv, v.min(axis=0))
            maxv = np.maximum(maxv, v.max(axis=0))

        if not self.meshes:
            self.redraw()
            return
        self.center = (minv + maxv) / 2.0
        self.radius = float(np.linalg.norm(maxv - minv)) / 2.0 or 1.0
        self.dist = self.radius * 2.0
        self.redraw()

    # ---------------- 事件
    def _on_press(self, e):
        self._drag = (e.x, e.y, "rot")
        self.dragging = True

    def _on_drag(self, e):
        if not self._drag:
            return
        mode = self._drag[2]
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self._drag = (e.x, e.y, mode)
        if mode == "rot":
            self.yaw += dx * 0.6
            self.pitch = max(-89, min(89, self.pitch + dy * 0.6))
        else:
            self.pan[0] += dx
            self.pan[1] += dy
        self.redraw()

    def _on_release(self, e):
        self._drag = None
        self.dragging = False
        self.redraw()

    def _on_pan_press(self, e):
        self._drag = (e.x, e.y, "pan")
        self.dragging = True

    def _on_pan(self, e):
        if not self._drag:
            return
        mode = self._drag[2]
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self._drag = (e.x, e.y, mode)
        if mode == "pan":
            self.pan[0] += dx
            self.pan[1] += dy
            self.redraw()

    def _on_wheel(self, e):
        self.dist *= 0.88 if e.delta > 0 else 1.14
        self.dist = max(self.radius * 0.5, min(self.radius * 20, self.dist))
        self.redraw()

    # ---------------- 渲染（z-buffer 软件光栅化）
    def render_image(self, W, H, scale_factor=1):
        """纯计算渲染，返回 (img, rw, rh)，不依赖 Canvas。"""
        rw = max(80, W // scale_factor)
        rh = max(80, H // scale_factor)

        cz, sz = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        Rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
        Rx = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
        R = Rx @ Rz

        fov = 1.2
        scale = min(rw, rh) * 0.9
        cx = rw / 2.0 + self.pan[0] / scale_factor
        cyy = rh * 0.5 + self.pan[1] / scale_factor
        cam_z = self.dist

        light = np.array([0.45, 0.6, 0.66])
        light = light / np.linalg.norm(light)

        img = np.zeros((rh, rw, 3), dtype=np.uint8)
        zbuf = np.full((rh, rw), np.inf, dtype=np.float32)

        n_tri = 0
        for item in self.meshes:
            name, v, f, rgb, nrm = item[0], item[1], item[2], item[3], item[4]
            uv = item[5] if len(item) > 5 else None
            if uv is not None and len(uv) != len(f):
                uv = None

            vv = (v - self.center) @ R.T
            z = cam_z - vv[:, 1]
            zc = np.clip(z, 0.05, None)
            px = cx + vv[:, 0] / zc * scale * fov
            py = cyy - vv[:, 2] / zc * scale * fov
            shade = np.abs(nrm @ light) * 0.32 + 0.78

            sx0, sy0 = px[f[:, 0]], py[f[:, 0]]
            sx1, sy1 = px[f[:, 1]], py[f[:, 1]]
            sx2, sy2 = px[f[:, 2]], py[f[:, 2]]

            minx = np.maximum(0, np.floor(np.minimum(np.minimum(sx0, sx1), sx2))).astype(int)
            maxx = np.minimum(rw - 1, np.ceil(np.maximum(np.maximum(sx0, sx1), sx2))).astype(int)
            miny = np.maximum(0, np.floor(np.minimum(np.minimum(sy0, sy1), sy2))).astype(int)
            maxy = np.minimum(rh - 1, np.ceil(np.maximum(np.maximum(sy0, sy1), sy2))).astype(int)
            valid = (maxx > minx) & (maxy > miny)

            # LOD：按屏幕面积过滤
            area = (maxx - minx) * (maxy - miny)
            if self.dragging:
                nv = int(valid.sum())
                if nv > 60 and drag_keep < 0.99:
                    cut = int(round((1.0 - drag_keep) * 100))
                    thr = max(48, int(np.percentile(area[valid], min(99, cut))))
                    valid &= (area >= thr)
            else:
                valid &= (area >= 5)

            for i in np.nonzero(valid)[0]:
                x_lo, x_hi = int(minx[i]), int(maxx[i]) + 1
                y_lo, y_hi = int(miny[i]), int(maxy[i]) + 1
                if x_hi <= x_lo or y_hi <= y_lo:
                    continue

                x0, y0, z0_ = sx0[i], sy0[i], z[f[i, 0]]
                x1, y1, z1_ = sx1[i], sy1[i], z[f[i, 1]]
                x2, y2, z2_ = sx2[i], sy2[i], z[f[i, 2]]

                den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
                if abs(den) < 1e-9:
                    continue

                xs = np.arange(x_lo, x_hi, dtype=np.float32) + 0.5
                ys = np.arange(y_lo, y_hi, dtype=np.float32) + 0.5
                gx, gy = np.meshgrid(xs, ys)

                a = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / den
                b = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / den
                c = 1.0 - a - b
                mask = (a >= -1e-4) & (b >= -1e-4) & (c >= -1e-4)
                if not mask.any():
                    continue

                zz = a * z0_ + b * z1_ + c * z2_
                sub_z = zbuf[y_lo:y_hi, x_lo:x_hi]
                better = mask & (zz < sub_z)
                if not better.any():
                    continue
                sub_z[better] = zz[better]

                sub_img = img[y_lo:y_hi, x_lo:x_hi]

                if self.texture is not None and uv is not None:
                    tw, th, timg = self.texture
                    u0, v0 = uv[i, 0]
                    u1, v1 = uv[i, 1]
                    u2, v2 = uv[i, 2]
                    uu = a * u0 + b * u1 + c * u2
                    vv2 = a * v0 + b * v1 + c * v2
                    tx = np.clip((uu * (tw - 1)).astype(np.int32), 0, tw - 1)
                    ty = np.clip(((1.0 - vv2) * (th - 1)).astype(np.int32), 0, th - 1)
                    col = (timg[ty, tx].astype(np.float32) * shade[i]).astype(np.uint8)
                    sub_img[better] = col[better]
                else:
                    col = (rgb * shade[i] * 255.0).clip(0, 255).astype(np.uint8)
                    sub_img[better] = col

                n_tri += 1

        self.last_tri_count = n_tri
        return img, rw, rh

    def redraw(self, force=False):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10 or not self.meshes:
            self.delete("all")
            return

        # 相机状态没变就跳过重绘（同一帧重复调用时省掉整次渲染）
        sig = (round(self.yaw, 2), round(self.pitch, 2), round(self.dist, 3),
               round(self.pan[0], 1), round(self.pan[1], 1), w, h,
               id(self.texture), len(self.meshes), self.dragging)
        if not force and sig == getattr(self, "_last_sig", None):
            return
        self._last_sig = sig

        self.delete("all")

        drag_sf, drag_keep, static_sf = self.quality
        sf = drag_sf if self.dragging else static_sf
        img, rw, rh = self.render_image(w, h, sf)

        if rw != w or rh != h:
            ys_idx = (np.arange(h) * rh // h).clip(0, rh - 1)
            xs_idx = (np.arange(w) * rw // w).clip(0, rw - 1)
            big = img[ys_idx][:, xs_idx]
        else:
            big = img

        ppm = ("P6\n{} {}\n255\n".format(w, h)).encode("ascii") + big.tobytes()
        try:
            self._img = tk.PhotoImage(data=ppm, format="PPM")
            self.create_image(0, 0, anchor="nw", image=self._img)
        except Exception:  # noqa: BLE001
            pass

        cz = math.cos(math.radians(self.yaw))
        sz = math.sin(math.radians(self.yaw))
        cp = math.cos(math.radians(self.pitch))
        sp = math.sin(math.radians(self.pitch))
        R = (np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
             @ np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]]))

        ax = 46
        ox, oy = w - ax - 16, h - ax - 16
        for vec, col, label in (
                (np.array([1.0, 0, 0]), "#d05050", "X"),
                (np.array([0, 1.0, 0]), "#50c050", "Y"),
                (np.array([0, 0, 1.0]), "#5080d0", "Z")):
            d = R @ vec
            ex, ey = ox + d[0] * ax, oy - d[2] * ax
            self.create_line(ox, oy, ex, ey, fill=col, width=2)
            self.create_text(ex, ey, text=label, fill=col, font=("Consolas", 8))

        self.create_text(8, 8, anchor="nw", fill="#909090",
                         text="左键旋转 | 滚轮缩放 | 右键平移 | 三角形 {} | {}x{}".format(
                             getattr(self, "last_tri_count", 0), rw, rh),
                         font=("Consolas", 8))


# ================================================================ 主窗口
VERSION = "v6.18  (手柄只在移动/旋转/缩放工具下显示，选择与相连面工具不画)"

SPLIT_BG = "#dcdcdc"
SPLIT_HL = "#a8c8ec"

COLORS = [
    (150, 150, 150), (120, 150, 180), (170, 140, 110), (130, 170, 130),
    (170, 130, 150), (140, 140, 180), (180, 160, 120), (120, 170, 170),
]

# ---------------------------------------------------------------- 定位器定义
# 来自《闪电战》建模教程第 5 步（Maya MEL），与 blitzkrieg_helper 的 LOCATOR_DEFS 一致
#   key / 对象名 / 中文说明 / 应该挂到哪个部件 / 绕 X 轴旋转度数
LOCATOR_DEFS = (
    # —— 开火点 ——
    ("main_gun",       "LMainGun",                  "主炮 / 炸弹投放点", "MainBarrel", -90.0),
    ("coax",           "LMachineGun_Turret_COAX",   "同轴机枪",          "Turret",     -90.0),
    ("mg_turret_back", "LMachineGun_Turret_Back",   "炮塔后置机枪",      "Turret",     -90.0),
    ("mg_cupola",      "LMachineGun_Turret_Cupola", "指挥塔机枪",        "Turret",     -90.0),
    ("mg_basis",       "LMachineGun_Basis",         "车体航向机枪",      "Basis",      -90.0),
    ("cannon",         "LCannon",                   "机炮（飞机）",      "Basis",      -90.0),
    # —— 特效点 ——
    ("explosion",      "LExplosion01",              "爆炸点",            "Basis",        0.0),
    ("smoke",          "LSmoke01",                  "普通冒烟",          "Basis",        0.0),
    ("fatality_smoke", "LFatalitySmoke01",          "毁灭性损毁冒烟",    "Basis",        0.0),
    ("exhaust",        "LExhaust01",                "排气",              "Basis",       90.0),
    # —— 人员 / 挂点 ——
    ("people",         "LPeople",                   "人员点",            "Basis",        0.0),
    ("gunner",         "LGunner01",                 "炮手站位点",        "Basis",        0.0),
    ("ammo",           "LAmmo",                     "弹药箱点",          "Basis",        0.0),
    ("towing",         "LTowingPoint",              "牵引挂钩点",        "Basis",       90.0),
    # —— 轮式车辆 / 装甲列车 ——
    ("front_wheel",    "LFrontWheel",               "前轮轴心点",        "Basis",        0.0),
    ("back_wheel",     "LBackWheel",                "后轮轴心点",        "Basis",        0.0),
    ("hook",           "LHookPoint",                "前后挂钩点",        "Basis",        0.0),
)

LOCATOR_MAP = {k: (name, label, parent, rot)
               for k, name, label, parent, rot in LOCATOR_DEFS}


def find_obj_by_name(pool, key):
    """按名字找对象：先精确、再忽略 .001 后缀、最后包含匹配。"""
    k = key.lower()
    for o in pool:
        if (o.name or "").lower() == k:
            return o
    for o in pool:
        if (o.name or "").lower().split(".")[0] == k:
            return o
    for o in pool:
        if k in (o.name or "").lower():
            return o
    return None


def quat_from_x_deg(deg):
    """绕 X 轴 deg 度的四元数 (x, y, z, w)——.mod 的 Rotation 就是四元数。"""
    a = math.radians(float(deg)) * 0.5
    return np.array([math.sin(a), 0.0, 0.0, math.cos(a)], float)


def quat_from_mat3(R):
    """3x3 旋转矩阵 -> 四元数 (x, y, z, w)，用于把局部矩阵写回对象属性。"""
    m = np.asarray(R, float)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    return np.array([x, y, z, w], float)


def loose_face_groups(v, f):
    """把三角形按连通性分组（Blender 的 Separate by Loose Parts）。

    先按位置焊接顶点（.mod 接缝处常有重复顶点，不焊会碎成一堆小块），
    再用并查集把同一个三角形里的三个顶点并起来。
    返回 (face_group_ids, groups) —— groups 是按面数从大到小排好的组号列表。
    """
    v = np.asarray(v, float)
    f = np.asarray(f, np.int64)
    if len(f) == 0:
        return np.zeros(0, np.int64), []
    _, weld = np.unique(np.round(v, 5), axis=0, return_inverse=True)
    weld = np.asarray(weld).reshape(-1)
    n = int(weld.max()) + 1
    par = list(range(n))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    fw = weld[f.reshape(-1)].reshape(-1, 3)
    for tri in fw:
        a = find(int(tri[0]))
        for k in (1, 2):
            b = find(int(tri[k]))
            if b != a:
                par[b] = a
                a = find(a)
    face_root = np.array([find(int(tri[0])) for tri in fw], np.int64)

    uniq, inv, counts = np.unique(face_root, return_inverse=True, return_counts=True)
    order = [int(i) for i in np.argsort(-counts)]
    return np.asarray(inv, np.int64), order

# ---------------------------------------------------------------- 变换面板
def _fmt_num(v, nd=3):
    try:
        s = "{:.{}f}".format(float(v), nd)
    except (TypeError, ValueError):
        return "0"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", "", "-"):
        s = "0"
    return s


class TransformPanel(tk.Toplevel):
    """仿 Blender 的变换面板：位置 / 旋转 / 缩放 / 尺寸，逐轴可改可锁。"""

    BG = "#ececed"
    GROUPS = (("loc", "位置:", "m", False),
              ("rot", "旋转:", "°", False),
              ("scl", "缩放:", "", False),
              ("dim", "尺寸:", "m", False))

    def __init__(self, app):
        tk.Toplevel.__init__(self, app)
        self.app = app
        self.title("变换")
        self.configure(bg=self.BG)
        self.transient(app)
        self.resizable(False, False)
        self._updating = False
        self.vars = {}
        self.entries = {}
        self.locks = {}
        self.lock_btns = {}
        self._imgs = self._make_lock_images()
        self._build()
        self.refresh()

    # ---- 锁图标
    def _make_lock_images(self):
        S = getattr(self.app, "S", 1.0)
        b = max(13, int(round(14 * S)))
        t = max(1, int(round(S)))
        out = {}
        for opened in (False, True):
            img = tk.PhotoImage(width=b, height=b)
            col = "#9a9a9a" if opened else "#3a3a3a"
            bw = max(6, int(round(b * 0.60)))
            bh = max(5, int(round(b * 0.42)))
            x0 = (b - bw) // 2
            y1 = b - max(1, t)
            y0 = y1 - bh
            img.put(col, to=(x0, y0, x0 + bw, y1))      # 锁体
            r = bw // 2                                  # 锁梁（半圆弧）
            cxx = x0 + r + (max(2, int(b * 0.20)) if opened else 0)
            for k in range(0, 181, 5):
                a = math.radians(k)
                xx = cxx + r * math.cos(a)
                yy = y0 - r * math.sin(a)
                xx, yy = int(round(xx)), int(round(yy))
                if 0 <= xx < b and 0 <= yy < b:
                    img.put(col, to=(xx, yy, min(b, xx + t), min(b, yy + t)))
            out[opened] = img
        return out

    def _build(self):
        p = self.app.px
        head = tk.Frame(self, bg=self.BG)
        head.pack(fill="x", padx=p(8), pady=(p(6), p(2)))
        tk.Label(head, text="▾ " + tr("变换"), bg=self.BG, fg="#222222", anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")

        # 仅对原点生效：勾上后 G/R/S 与面板改数都只动原点，网格在场景里不动
        cbx = tk.Checkbutton(self, text=tr("仅对原点生效（网格不动）"),
                             variable=self.app.origin_only,
                             bg=self.BG, fg="#222222", activebackground=self.BG,
                             selectcolor="#ffffff", anchor="w", bd=0,
                             highlightthickness=0,
                             font=("Microsoft YaHei UI", 9))
        cbx.pack(fill="x", padx=p(8), pady=(0, p(2)))

        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=0, pady=(0, p(8)))

        last = None
        for grp, title, unit, ro in self.GROUPS:
            if last == "rot":
                cb = ttk.Combobox(body, values=[tr("XYZ 欧拉")], state="readonly",
                                  font=("Microsoft YaHei UI", 9), width=18)
                cb.current(0)
                cb.pack(fill="x", padx=p(8), pady=(p(1), p(5)))
            tk.Label(body, text=tr(title), bg=self.BG, fg="#333333", anchor="w",
                     font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=p(8),
                                                          pady=(p(4), p(1)))
            for ax in range(3):
                self._row(body, grp, ax, unit, ro)
            last = grp

    def _row(self, parent, grp, ax, unit, ro):
        p = self.app.px
        row = tk.Frame(parent, bg=self.BG)
        row.pack(fill="x", padx=p(8), pady=max(1, p(1)))
        tk.Label(row, text="XYZ"[ax], width=2, anchor="w", bg=self.BG,
                 fg="#4a4a4a", font=("Microsoft YaHei UI", 9)).pack(side="left")
        field = tk.Frame(row, bg="white", bd=1, relief="solid")
        field.pack(side="left", fill="x", expand=True, ipady=max(1, p(1)))
        var = tk.StringVar()
        ent = tk.Entry(field, textvariable=var, bd=0, relief="flat", bg="white",
                       justify="right", font=("Consolas", 10), width=8,
                       highlightthickness=0, insertwidth=1)
        ent.pack(side="left", fill="x", expand=True, padx=(p(3), 0))
        if unit:
            tk.Label(field, text=unit, bg="white", fg="#888888",
                     font=("Microsoft YaHei UI", 8)).pack(side="right", padx=(p(2), p(4)))
        if ro:
            ent.configure(state="readonly", readonlybackground="white",
                          fg="#333333")
        else:
            ent.bind("<Return>", lambda e, g=grp, a=ax: self._commit(g, a))
            ent.bind("<FocusOut>", lambda e, g=grp, a=ax: self._commit(g, a))
            ent.bind("<Escape>", lambda e: self.refresh())
        self.vars[(grp, ax)] = var
        self.entries[(grp, ax)] = ent

        lb = tk.Label(row, bg=self.BG, cursor="hand2")
        lb.pack(side="left", padx=(p(4), 0))
        self.lock_btns[(grp, ax)] = lb
        self.locks[(grp, ax)] = tk.BooleanVar(value=False)
        if ro:
            lb.configure(image=self._imgs[False], state="disabled")
        else:
            lb.configure(image=self._imgs[False])
            lb.bind("<Button-1>", lambda e, g=grp, a=ax: self._toggle_lock(g, a))

    def _toggle_lock(self, grp, ax):
        v = self.locks[(grp, ax)]
        v.set(not v.get())
        on = v.get()
        self.lock_btns[(grp, ax)].configure(image=self._imgs[on])
        ent = self.entries[(grp, ax)]
        if on:
            ent.configure(state="readonly", readonlybackground="#f2f2f2",
                          fg="#9a9a9a")
        else:
            ent.configure(state="normal", bg="white", fg="#000000")
        # 视口 gizmo 也跳过锁住的轴
        self.app.update_locked_axes()

    def locked_axes(self):
        return {ax for ax in range(3) if self.locks[("scl", ax)].get()
                or self.locks[("loc", ax)].get() or self.locks[("rot", ax)].get()}

    def refresh(self):
        app = self.app
        self._updating = True
        try:
            o = None
            if app.scene is not None and getattr(app, "sel_id", None) is not None:
                o = app._obj_by_id_map().get(app.sel_id)
            if o is None:
                box = self._aabb_box()
                if box is not None:
                    label, mn, mx = box
                    c = (mn + mx) * 0.5
                    d = mx - mn
                    for ax in range(3):
                        self.vars[("loc", ax)].set(_fmt_num(c[ax]))
                        self.vars[("rot", ax)].set("0")
                        self.vars[("scl", ax)].set("1")
                        self.vars[("dim", ax)].set(_fmt_num(d[ax]))
                    self.title(tr("变换") + " - {}  (AABB)".format(label))
                    return
                for var in self.vars.values():
                    var.set("")
                self.title(tr("变换"))
                return
            trs = app._trs_of(o.id)
            dims = app._dims_of(o.id)
            if trs is None:
                return
            loc, eul, scl = trs
            for ax in range(3):
                self.vars[("loc", ax)].set(_fmt_num(loc[ax]))
                self.vars[("rot", ax)].set(_fmt_num(eul[ax], 1))
                self.vars[("scl", ax)].set(_fmt_num(scl[ax]))
                self.vars[("dim", ax)].set(_fmt_num(dims[ax]))
            self.title(tr("变换") + " - {}  ({})".format(
                o.name, "定位器" if o.data is None else "网格"))
        finally:
            self._updating = False

    def _aabb_box(self):
        """当前选中的 AABB（label, mn, mx）；没选返回 None。"""
        app = self.app
        ai = getattr(app, "aabb_sel", None)
        items = getattr(app, "_aabb_items", [])
        if ai is None or not (0 <= ai < len(items)):
            return None
        it = items[ai]
        return (it[0], np.asarray(it[1], float), np.asarray(it[2], float))

    def _commit_aabb(self, grp, ax, val):
        """面板改数直接作用于 AABB：位置=中心，尺寸=边长。"""
        box = self._aabb_box()
        if box is None:
            return
        _label, mn, mx = box
        mn = mn.copy()
        mx = mx.copy()
        if grp == "loc":
            c = (mn + mx) * 0.5
            off = float(val) - c[ax]
            mn[ax] += off
            mx[ax] += off
        elif grp == "dim":
            d = float(val)
            if d < 0.01:
                d = 0.01
            c = (mn + mx) * 0.5
            mn[ax] = c[ax] - d * 0.5
            mx[ax] = c[ax] + d * 0.5
        else:
            return
        self.app._push_undo("修改 AABB")
        self.app._set_aabb_box(self.app.aabb_sel, mn, mx)

    def _commit(self, grp, ax):
        if self._updating:
            return
        txt = (self.vars[(grp, ax)].get() or "").strip()
        try:
            val = float(txt)
        except ValueError:
            self.refresh()
            return
        if getattr(self.app, "sel_id", None) is None and self._aabb_box() is not None:
            self._commit_aabb(grp, ax, val)
            self.refresh()
            return
        self.app.set_transform_value(grp, ax, val)
        self.refresh()

    def on_close(self):
        try:
            self.app._panel = None
            self.destroy()
        except Exception:  # noqa: BLE001
            pass


class _ColTable:
    """表格：列宽自适应 + 可拖分隔线 + 内容裁剪。

    布局完全自己算（place 逐格定位），不用 grid —— grid 的 minsize/自适应
    和定宽容器混用会互相打架（之前就是这么把表弄乱的）。

    宽度规则照搬 Blender 的 ui_litem_estimate_column：
      · 初始列宽 = 该列各单元格自然宽度的最大值
      · flex=True 的内容不参与撑宽（比如右侧很长的说明文字）
      · 拖分隔线改列宽，文字按列宽裁剪，不会压到隔壁列
    """

    def __init__(self, body, cols, app, rowh=26, headh=40):
        self.body = body
        self.app = app
        self.cols = cols                  # [(key, 中文标题, 英文字段名)]
        self.n = len(cols)
        s = getattr(app, "S", 1.0) or 1.0
        self.rowh = int(max(22, rowh * s))
        self.headh = int(max(30, headh * s))
        self.width = [0] * self.n
        self.minw = [0] * self.n
        self.ideal = [0] * self.n
        self.cells = []                   # (col, row, frame, content, flex)
        self.grips = []
        self._drag = None
        self._cw = 0
        self._rows = 0
        self._geom = {}          # frame/grip -> 上次摆放的几何（增量 place 用）
        self._last_bodyh = None
        self._fit_job = None     # resize 事件合并
        body.pack_propagate(False)
        body.grid_propagate(False)

    # ---------------- 基础
    def char_w(self):
        if not self._cw:
            try:
                import tkinter.font as tkfont
                self._cw = max(6, tkfont.Font(font=("Microsoft YaHei UI", 9)).measure("0"))
            except Exception:  # noqa: BLE001
                self._cw = 9
        return self._cw

    def new_row(self):
        self._rows += 1
        return self._rows

    def cell(self, col, row, flex=False):
        """开一个单元格（定宽容器），返回它，控件用它的 master 创建。"""
        f = tk.Frame(self.body, bg=self.body.cget("bg"))
        f.pack_propagate(False)
        f.grid_propagate(False)
        self.cells.append([col, row, f, None, flex])
        return f

    def set_content(self, frame, widget, anchor="w", padx=3):
        """把控件放进单元格，并登记用于测量。"""
        widget.pack(anchor=anchor, padx=padx)
        for c in self.cells:
            if c[2] is frame:
                c[3] = widget
                return widget
        return widget

    # ---------------- 表头
    def build_header(self):
        self._en_labels = []
        for c, (key, cn, en) in enumerate(self.cols):
            f = self.cell(c, 0)
            cn_lbl = tk.Label(f, text=tr(cn), bg=self.body.cget("bg"), fg="#333333",
                              font=("Microsoft YaHei UI", 9, "bold"))
            cn_lbl.pack(anchor="w", padx=self.app.px(4))
            en_lbl = None
            if en:
                en_lbl = tk.Label(f, text=en, bg=self.body.cget("bg"), fg="#8a8a8a",
                                  font=("Consolas", 8))
                en_lbl.pack(anchor="w", padx=self.app.px(4))
                self._en_labels.append(en_lbl)
            f.update_idletasks()
            cn_w = cn_lbl.winfo_reqwidth()
            en_w = en_lbl.winfo_reqwidth() if en_lbl is not None else 0
            self.ideal[c] = max(cn_w, en_w) + self.app.px(16)
            self.minw[c] = cn_w + self.app.px(6)

    # ---------------- 宽度
    def hide_en(self):
        """窄屏时收起英文小标题，避免列被挤成碎片。"""
        if not getattr(self, "_en_labels", None):
            return
        for lb in self._en_labels:
            try:
                lb.pack_forget()
            except Exception:  # noqa: BLE001
                pass
        self._en_labels = []
        self.headh = int(max(24, 26 * getattr(self.app, "S", 1.0)))
        self.apply()

    def measure(self):
        """列宽 = 该列内容的自然宽度最大值（flex 列不参与撑宽，但保一个可读下限）。"""
        for c in range(self.n):
            need = self.ideal[c]
            flex_need = 0
            for (cc, row, frame, content, flex) in self.cells:
                if cc != c or content is None:
                    continue
                try:
                    rq = content.winfo_reqwidth() + self.app.px(16)
                except Exception:  # noqa: BLE001
                    continue
                if flex:
                    flex_need = max(flex_need, rq)
                else:
                    need = max(need, rq)
            if flex_need:
                keep = min(flex_need, self.app.px(150))
                need = max(need, keep)
                self.minw[c] = max(self.minw[c], keep)
            self.width[c] = int(max(self.minw[c], need))

    def apply(self):
        """按当前列宽把每个单元格摆到位。"""
        cw = self.char_w()
        x = 0
        for c in range(self.n):
            w = int(self.width[c])
            for (cc, row, frame, content, flex) in self.cells:
                if cc != c:
                    continue
                h = self.headh if row == 0 else self.rowh
                y = 0 if row == 0 else self.headh + (row - 1) * self.rowh
                geo = (x, y, w, h)
                if self._geom.get(frame) != geo:
                    # 没变的格子不重摆——resize/拖动时这是最大的开销
                    frame.configure(width=w, height=h)
                    frame.place(x=x, y=y, width=w, height=h)
                    self._geom[frame] = geo
                if content is not None and isinstance(
                        content, (tk.Entry, ttk.Combobox)):
                    # 宽度按字符数给；列宽变化 1 字符以内不重算（缩放
                    # 窗口时这能省掉大半 winfo_reqwidth 几何同步）
                    chars = max(3, int((w - 20) / cw))
                    last = getattr(content, "_last_chars", None)
                    if last is not None and abs(chars - last) < 2:
                        continue
                    if last != chars:
                        try:
                            content.configure(width=chars)
                            for _ in range(8):
                                if content.winfo_reqwidth() <= w - 4:
                                    break
                                chars -= 1
                                content.configure(width=chars)
                            content._last_chars = chars
                        except Exception:  # noqa: BLE001
                            pass
            x += w
        # 宽度交给外层 grid（跟着窗口走），这里只固定高度
        th = self.table_h()
        if self._last_bodyh != th:
            self._last_bodyh = th
            self.body.configure(height=th)
        self.layout_grips()

    def table_h(self):
        """表格自身需要的高度（表头 + 数据行）。"""
        return self.headh + max(0, self._rows - 1) * self.rowh

    def layout_grips(self):
        h = self.table_h()
        x = 0
        for i in range(1, self.n):
            x += int(self.width[i - 1])
            gx = int(x) - 3
            if self._geom.get(("g", i)) != (gx, h):
                try:
                    self.grips[i - 1].place(x=gx, y=0, height=h)
                except Exception:  # noqa: BLE001
                    pass
                self._geom[("g", i)] = (gx, h)

    def install(self):
        """装上分隔线（可拖）。"""
        for i in range(1, self.n):
            g = tk.Frame(self.body, bg=self.body.cget("bg"), width=7,
                         cursor="sb_h_double_arrow")
            g.pack_propagate(False)
            line = tk.Frame(g, bg="#d8d8d8", width=1)
            line.place(x=3, y=0, relheight=1.0)
            g.bind("<Button-1>", lambda e, i=i: self._press(i, e))
            g.bind("<B1-Motion>", self._motion)
            g.bind("<ButtonRelease-1>", lambda e: self._release(e))
            g.bind("<Enter>", lambda e: line.configure(bg="#8f8f8f"))
            g.bind("<Leave>", lambda e: line.configure(bg="#d8d8d8"))
            self.grips.append(g)
        self.apply()
        self.body.bind("<Configure>", lambda e: self.on_resize(e))

    def fit_to(self, maxw):
        """内容比屏幕还宽时，从最宽的列开始压（压到各自下限为止）。"""
        over = sum(self.width) - int(maxw)
        if over <= 0:
            return
        for c in sorted(range(self.n), key=lambda i: -self.width[i]):
            if over <= 0:
                break
            room = self.width[c] - self.minw[c]
            if room <= 0:
                continue
            cut = min(room, over)
            self.width[c] -= int(cut)
            over -= int(cut)
        self.apply()

    def on_resize(self, event=None):
        """窗口大小变了就跟着调列宽（连续事件合并成每拍一次，防卡）。"""
        if self._fit_job is not None:
            return
        try:
            self._fit_job = self.body.after_idle(self._do_fit)
        except Exception:  # noqa: BLE001
            self._fit_job = None

    def _do_fit(self):
        self._fit_job = None
        try:
            if not self.body.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        self.fit_width(self.body.winfo_width())

    def flex_col(self):
        """优先吃多余宽度的列：有 flex 单元格的列（说明文字列），没有就最后一列。"""
        flexed = {cc for (cc, row, frame, content, flex) in self.cells if flex}
        if flexed:
            return max(flexed)
        return self.n - 1

    def fit_width(self, w):
        """按可用宽度重排列：变宽 flex 列吃掉，变窄从最宽的列开始压。"""
        if getattr(self, "_busy", False):
            return
        total = sum(self.width)
        if not self.n or w <= 0 or abs(w - total) < 6:
            return
        self._busy = True
        try:
            if w > total:
                self.width[self.flex_col()] = int(
                    self.width[self.flex_col()] + (w - total))
            else:
                over = total - w
                for c in sorted(range(self.n), key=lambda i: -self.width[i]):
                    if over <= 0:
                        break
                    room = self.width[c] - self.minw[c]
                    if room <= 0:
                        continue
                    cut = min(room, over)
                    self.width[c] -= int(cut)
                    over -= int(cut)
            self.apply()
        finally:
            self._busy = False

    # ---------------- 拖动
    def _press(self, i, e):
        # 按下分割线时先把总宽对齐可见宽度（追加行/重载后列宽可能已超出窗口）
        try:
            bw = self.body.winfo_width()
            if bw > sum(self.minw):
                self.fit_width(bw)
        except Exception:  # noqa: BLE001
            pass
        self._drag = (i, e.x_root, list(self.width))

    def _motion(self, e):
        if not self._drag:
            return
        i, x0, w0 = self._drag
        left = i - 1
        dx = e.x_root - x0
        want = max(self.minw[left], w0[left] + dx)
        new_w = list(w0)
        diff = want - new_w[left]
        if diff > 0:
            # 加宽：右边的列保持宽度、整体向右平移，让位空间优先从
            # flex 列（最右的说明列）出；flex 到底了才依次压右侧邻列
            fc = self.flex_col()
            order = []
            if fc > left:
                order.append(fc)
            order += [c for c in range(left + 1, self.n) if c != fc]
            avail = sum(new_w[c] - self.minw[c] for c in order)
            take = min(int(diff), int(avail))
            new_w[left] += take
            rem = take
            for c in order:
                if rem <= 0:
                    break
                cut = min(new_w[c] - self.minw[c], rem)
                new_w[c] -= cut
                rem -= cut
        elif diff < 0:
            # 收窄：右边的列整体左移，让出来的宽度先还给 flex 列
            # （最多还到按下时的宽度），再按原路给右侧邻列
            give = int(-diff)
            new_w[left] -= give
            fc = self.flex_col()
            if fc > left:
                add = min(max(0, w0[fc] - new_w[fc]), give)
                new_w[fc] += add
                give -= add
            for c in range(left + 1, self.n):
                if give <= 0:
                    break
                if c == fc:
                    continue
                add = min(max(0, self.ideal[c] - new_w[c]), give)
                new_w[c] += add
                give -= add
            if give > 0:
                new_w[fc if fc > left else self.n - 1] += give
        for c in range(self.n):
            self.width[c] = int(new_w[c])
        self.apply()

    def _release(self, e):
        self._drag = None

    def reset_rows(self):
        """清掉所有数据行（表头保留）。"""
        for (cc, row, frame, content, flex) in self.cells:
            if row > 0:
                try:
                    frame.destroy()
                except Exception:  # noqa: BLE001
                    pass
        self.cells = [c for c in self.cells if c[1] == 0]
        self._rows = 0
        # 旧格子已销毁，几何缓存作废（防止 id 复用导致跳过摆放）
        self._geom = {}


XML_TEMPLATE = (
    '<?xml version="1.0"?>\n'
    '<base>\n'
    '\t<History/>\n'
    '\t<RPG MaxHP="400" RepairCost="0.1" Sight="57" SightPower="1" Speed="14.8" Passability="0.57" Priority="2" Camouflage="30" Weight="36000" Price="1.02" UninstallRotate="0" UninstallTransport="0" RotateSpeed="40" TurnRadius="5" TowingForce="0" Crew="4" Passangers="10" BoundTileRadius="2" SmallAABBCoeff="0.85" TowPoint="-1" EntrancePoint="12" FatalitySmokePoint="11" ShootDustPoint="-1" LeavesTracks="1" TrackOffset="0.01" TrackWidth="0.18" TrackStart="0.3" TrackEnd="0.2" TrackIntensity="0.7" TrackLifetime="35000" MaxHeight="500" DivingAngle="20" ClimbAngle="20" TiltAngle="30" TiltRatio="2">\n'
    '\t\t<KeyName>USSR-T62</KeyName>\n'
    '\t\t<StatsType>Mech</StatsType>\n'
    '\t\t<DamagedHPs/>\n'
    '\t\t<Defence0 MinArmor="40" MaxArmor="90" Silhouette="1"/>\n'
    '\t\t<Defence1 MinArmor="40" MaxArmor="90" Silhouette="1"/>\n'
    '\t\t<Defence2 MinArmor="40" MaxArmor="90" Silhouette="1"/>\n'
    '\t\t<Defence3 MinArmor="40" MaxArmor="90" Silhouette="1"/>\n'
    '\t\t<Defence4 MinArmor="40" MaxArmor="90" Silhouette="1"/>\n'
    '\t\t<Defence5 MinArmor="40" MaxArmor="90" Silhouette="1"/>\n'
    '\t\t<Type>arm_medium</Type>\n'
    '\t\t<AIClass>track</AIClass>\n'
    '\t\t<Commands Size="51">\n'
    '\t\t\t<BitArray>\n'
    '\t\t\t\t<item data="47"/>\n'
    '\t\t\t\t<item data="67"/>\n'
    '\t\t\t\t<item data="0"/>\n'
    '\t\t\t\t<item data="0"/>\n'
    '\t\t\t\t<item data="128"/>\n'
    '\t\t\t\t<item data="32"/>\n'
    '\t\t\t\t<item data="4"/>\n'
    '\t\t\t</BitArray>\n'
    '\t\t</Commands>\n'
    '\t\t<Exposures Size="0">\n'
    '\t\t\t<BitArray/>\n'
    '\t\t</Exposures>\n'
    '\t\t<AnimDescs>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data>\n'
    '\t\t\t\t\t<item Length="562" Action="0" Index="0" AABB_A="-1" AABB_D="-1"/>\n'
    '\t\t\t\t\t<item Length="562" Action="0" Index="1" AABB_A="-1" AABB_D="-1"/>\n'
    '\t\t\t\t\t<item Length="562" Action="0" Index="2" AABB_A="-1" AABB_D="-1"/>\n'
    '\t\t\t\t\t<item Length="562" Action="0" Index="3" AABB_A="-1" AABB_D="-1"/>\n'
    '\t\t\t\t\t<item Length="562" Action="0" Index="4" AABB_A="-1" AABB_D="-1"/>\n'
    '\t\t\t\t\t<item Length="562" Action="0" Index="5" AABB_A="-1" AABB_D="-1"/>\n'
    '\t\t\t\t</data>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data>\n'
    '\t\t\t\t\t<item Length="1187" Action="0" Index="6" AABB_A="0" AABB_D="0"/>\n'
    '\t\t\t\t\t<item Length="1187" Action="0" Index="7" AABB_A="1" AABB_D="1"/>\n'
    '\t\t\t\t\t<item Length="1187" Action="0" Index="8" AABB_A="2" AABB_D="2"/>\n'
    '\t\t\t\t\t<item Length="1187" Action="0" Index="9" AABB_A="3" AABB_D="3"/>\n'
    '\t\t\t\t\t<item Length="1187" Action="0" Index="10" AABB_A="4" AABB_D="4"/>\n'
    '\t\t\t\t</data>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t</AnimDescs>\n'
    '\t\t<AABB_As>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="26.7143" y="17.1744"/>\n'
    '\t\t\t\t<HalfSize x="56.756" y="70.8063"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="6.44562" y="34.6361"/>\n'
    '\t\t\t\t<HalfSize x="37.105" y="89.1255"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="-32.9576" y="16.7486"/>\n'
    '\t\t\t\t<HalfSize x="63.017" y="72.1653"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="37.1136" y="-23.4945"/>\n'
    '\t\t\t\t<HalfSize x="66.6535" y="74.5963"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="-28.2062" y="34.7679"/>\n'
    '\t\t\t\t<HalfSize x="56.4126" y="88.2227"/>\n'
    '\t\t\t</item>\n'
    '\t\t</AABB_As>\n'
    '\t\t<AABB_Ds>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="-2.41481" y="8.08591"/>\n'
    '\t\t\t\t<HalfSize x="33.3189" y="62.8353"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="-0.0342264" y="27.4828"/>\n'
    '\t\t\t\t<HalfSize x="30.5865" y="81.2589"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="1.12239" y="15.8228"/>\n'
    '\t\t\t\t<HalfSize x="29.4498" y="70.3334"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="-24.4782" y="0.700397"/>\n'
    '\t\t\t\t<HalfSize x="54.4395" y="54.6168"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="0.0752153" y="1.9484"/>\n'
    '\t\t\t\t<HalfSize x="31.3488" y="59.5271"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<Center x="0.0166864" y="31.8544"/>\n'
    '\t\t\t\t<HalfSize x="30.0313" y="87.26"/>\n'
    '\t\t\t</item>\n'
    '\t\t</AABB_Ds>\n'
    '\t\t<AcksRefs>\n'
    '\t\t\t<item>sounds\\ack\\russian\\tank2</item>\n'
    '\t\t</AcksRefs>\n'
    '\t\t<AcksRef/>\n'
    '\t\t<Platforms>\n'
    '\t\t\t<item VerticalRotateSpeed="1" HorizontalRotationSpeed="1" ModelPart="6" GunCarriageParts="-1" FirstGun="0" NumGuns="0">\n'
    '\t\t\t\t<Constraint Min="0" Max="0"/>\n'
    '\t\t\t\t<ConstraintVertical Min="0" Max="0"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item VerticalRotateSpeed="0" HorizontalRotationSpeed="6" ModelPart="7" GunCarriageParts="-1" FirstGun="0" NumGuns="2">\n'
    '\t\t\t\t<Constraint Min="-6.28319" Max="6.28319"/>\n'
    '\t\t\t\t<ConstraintVertical Min="0" Max="0"/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item VerticalRotateSpeed="1" HorizontalRotationSpeed="1" ModelPart="9" GunCarriageParts="-1" FirstGun="2" NumGuns="1">\n'
    '\t\t\t\t<Constraint Min="-6.28319" Max="6.28319"/>\n'
    '\t\t\t\t<ConstraintVertical Min="0" Max="0"/>\n'
    '\t\t\t</item>\n'
    '\t\t</Platforms>\n'
    '\t\t<Guns>\n'
    '\t\t\t<item Priority="0" Ammo="40" Direction="65534" ReloadCost="400" ShootPoint="17" Recoil="1" RecoilLength="16.2157" RecoilTime="50" ModelPart="8" RecoilShakeTime="400" RecoilShakeAngle="0.0523599">\n'
    '\t\t\t\t<Weapon>USSR-115mm-ATGun</Weapon>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item Priority="1" Ammo="2500" Direction="65534" ReloadCost="1" ShootPoint="16" Recoil="0" RecoilLength="0" RecoilTime="50" ModelPart="7" RecoilShakeTime="400" RecoilShakeAngle="0.0523599">\n'
    '\t\t\t\t<Weapon>USSR-dt7_62A2</Weapon>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item Priority="1" Ammo="500" Direction="65534" ReloadCost="1" ShootPoint="18" Recoil="0" RecoilLength="0" RecoilTime="50" ModelPart="10" RecoilShakeTime="400" RecoilShakeAngle="0">\n'
    '\t\t\t\t<Weapon>USSR-12mm-HMG</Weapon>\n'
    '\t\t\t</item>\n'
    '\t\t</Guns>\n'
    '\t\t<ArmorLeft Min="80" Max="170"/>\n'
    '\t\t<ArmorRight Min="80" Max="170"/>\n'
    '\t\t<ArmorTop Min="30" Max="30"/>\n'
    '\t\t<ArmorBottom Min="30" Max="30"/>\n'
    '\t\t<ArmorFront Min="200" Max="240"/>\n'
    '\t\t<ArmorBack Min="45" Max="65"/>\n'
    '\t\t<AABBCenter x="-0.05128" y="-1.82242"/>\n'
    '\t\t<AABBHalfSize x="28.2577" y="52.6536"/>\n'
    '\t\t<ExhaustPoints>\n'
    '\t\t\t<item data="13"/>\n'
    '\t\t\t<item data="14"/>\n'
    '\t\t</ExhaustPoints>\n'
    '\t\t<DamagePoints>\n'
    '\t\t\t<item data="11"/>\n'
    '\t\t\t<item data="15"/>\n'
    '\t\t</DamagePoints>\n'
    '\t\t<PeoplePoints/>\n'
    '\t\t<TowPoint2D x="0" y="0"/>\n'
    '\t\t<HookPoint x="0" y="0"/>\n'
    '\t\t<FrontWheel x="0" y="0"/>\n'
    '\t\t<BackWheel x="0" y="0"/>\n'
    '\t\t<EntrancePoint2D x="30.6872" y="-3.77305"/>\n'
    '\t\t<PeoplePoints2D/>\n'
    '\t\t<AmmoPoint2D x="0" y="0"/>\n'
    '\t\t<Gunners>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t\t<item>\n'
    '\t\t\t\t<data/>\n'
    '\t\t\t</item>\n'
    '\t\t</Gunners>\n'
    '\t\t<EffectDiesel>smokedisel1</EffectDiesel>\n'
    '\t\t<EffectSmoke>smokeflame1</EffectSmoke>\n'
    '\t\t<EffectWheelDust>dustmove1</EffectWheelDust>\n'
    '\t\t<EffectShootDust>dustshoot</EffectShootDust>\n'
    '\t\t<EffectFatality>expfatality1</EffectFatality>\n'
    '\t\t<EffectEntrenching/>\n'
    '\t\t<EffectDisappear>brakedown2</EffectDisappear>\n'
    '\t\t<JoggingX Period1="1.8" Period2="0.8" Amp1="0.5" Amp2="0.4" Phase1="0.1" Phase2="2.2"/>\n'
    '\t\t<JoggingY Period1="1" Period2="0.4" Amp1="0.6" Amp2="0.3" Phase1="1.9" Phase2="0"/>\n'
    '\t\t<JoggingZ Period1="1" Period2="0.3" Amp1="1" Amp2="0.6" Phase1="0" Phase2="2.1"/>\n'
    '\t\t<SoundMoveStart>t62_start</SoundMoveStart>\n'
    '\t\t<SoundMove>t55_t62_cycle</SoundMove>\n'
    '\t\t<SoundMoveStop>t62_stop</SoundMoveStop>\n'
    '\t\t<DeathCraters>\n'
    '\t\t\t<item>objects\\terraobjects\\death_hole\\verybig\\01\\1</item>\n'
    '\t\t\t<item>objects\\terraobjects\\death_hole\\verybig\\02\\1</item>\n'
    '\t\t\t<item>objects\\terraobjects\\death_hole\\verybig\\03\\1</item>\n'
    '\t\t</DeathCraters>\n'
    '\t</RPG>\n'
    '</base>\n'
)


class _TableScroll(tk.Frame):
    """把 _ColTable 放进可横向滚动的容器：窄屏时滚动看，不压碎列。"""

    def __init__(self, parent, cols, app):
        tk.Frame.__init__(self, parent, bg="#ececed")
        self.app = app
        self.canvas = tk.Canvas(self, bg="#ececed", highlightthickness=0)
        self.hbar = ttk.Scrollbar(self, orient="horizontal",
                                 command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner = tk.Frame(self.canvas, bg="#ececed")
        self.win = self.canvas.create_window((0, 0), window=self.inner,
                                             anchor="nw")
        self.table = _ColTable(self.inner, cols, app)
        self.table.body.pack(fill="both", expand=True)
        self._job = None
        self.canvas.bind("<Configure>", self._on_canvas)
        self.table.body.bind("<Configure>", self._on_body)

    def _on_canvas(self, e):
        # 拖窗口时 Configure 很密：合并成每拍一次排版
        if self._job is None:
            self._job = self.after_idle(self._sync_after)

    def _on_body(self, e):
        if self._job is None:
            self._job = self.after_idle(self._sync_after)

    def _sync_after(self):
        self._job = None
        try:
            if self.winfo_exists():
                w = self.canvas.winfo_width()
                if w == getattr(self, "_last_vw", None):
                    return
                self._last_vw = w
                self._sync(w)
        except Exception:  # noqa: BLE001
            pass

    def _sync(self, vw):
        if vw <= 1:
            return
        t = self.table
        t.fit_width(vw)                 # 尽量填满视口（不够就压到 minw）
        total = max(sum(t.width), int(vw))
        if total == getattr(self, "_last_total", None):
            return                      # 没变化就别重复排版（否则 Configure 会自激）
        self._last_total = total
        t.body.configure(width=total)
        self.canvas.itemconfigure(self.win, width=total)
        self.canvas.configure(scrollregion=(0, 0, total, t.table_h()))
        if sum(t.width) > int(vw) + 2:
            self.hbar.grid(row=1, column=0, sticky="ew")
        else:
            self.hbar.grid_remove()




def _png_write_rgba(path, arr):
    """把 (h, w, 4) uint8 写成 PNG（只为了给色盘生成贴图，避免依赖 Pillow）。"""
    import zlib
    import struct

    h, w = arr.shape[:2]
    zero = bytes(1)
    rows = b"".join(zero + np.asarray(arr[y], np.uint8).tobytes() for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = bytes([137, 80, 78, 71, 13, 10, 26, 10])
    png = (sig
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


class _HsvWheelPicker(tk.Toplevel):
    """Blender 风格的色盘：色相/饱和度圆盘 + 明度条 + Hex + Alpha。"""

    WHEEL = 168
    BAR_W = 22

    def __init__(self, master, title, rgba, on_ok):
        super().__init__(master)
        self.title(title)
        self.configure(bg="#2b2b2b")
        self.transient(master)
        self.resizable(False, False)
        self._on_ok = on_ok
        self.h, self.s, self.v = _rgb_to_hsv(rgba[:3])
        self.a = float(rgba[3]) if len(rgba) > 3 else 1.0
        self._img = None
        self._drag = None

        pad = 10
        self.canvas = tk.Canvas(self, width=self.WHEEL, height=self.WHEEL,
                                bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, padx=(pad, 6), pady=(pad, 6))
        self.bar = tk.Canvas(self, width=self.BAR_W, height=self.WHEEL,
                             bg="#2b2b2b", highlightthickness=0, cursor="hand2")
        self.bar.grid(row=0, column=1, padx=(0, pad), pady=(pad, 6), sticky="n")

        row = tk.Frame(self, bg="#2b2b2b")
        row.grid(row=1, column=0, columnspan=2, sticky="we", padx=pad)
        tk.Label(row, text="Hex", bg="#2b2b2b", fg="#dddddd",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.hex_var = tk.StringVar()
        ent = tk.Entry(row, textvariable=self.hex_var, width=12,
                       font=("Consolas", 10), bg="#3c3c3c", fg="#eeeeee",
                       insertbackground="#eeeeee", relief="flat")
        ent.pack(side="left", padx=6, ipady=2)
        ent.bind("<Return>", lambda _e: self._from_hex())
        self.preview = tk.Label(row, width=4, bg="#ffffff", relief="solid", bd=1)
        self.preview.pack(side="left", padx=(6, 0))

        btns = tk.Frame(self, bg="#2b2b2b")
        btns.grid(row=2, column=0, columnspan=2, sticky="e", padx=pad, pady=(4, pad))
        tk.Button(btns, text="确定", width=8, command=self._ok).pack(side="left", padx=4)
        tk.Button(btns, text="取消", width=8, command=self.destroy).pack(side="left")

        self.canvas.bind("<Button-1>", lambda e: self._pick_hs(e.x, e.y, True))
        self.canvas.bind("<B1-Motion>", lambda e: self._pick_hs(e.x, e.y, True))
        self.bar.bind("<Button-1>", lambda e: self._pick_v(e.y, True))
        self.bar.bind("<B1-Motion>", lambda e: self._pick_v(e.y, True))

        self._rebuild()
        self.grab_set()

    # ---------------- 绘制
    def _wheel_png(self):
        n = self.WHEEL
        yy, xx = np.mgrid[0:n, 0:n]
        cx = cy = (n - 1) / 2.0
        dx = (xx - cx) / (n / 2.0)
        dy = (yy - cy) / (n / 2.0)
        r = np.hypot(dx, dy)
        hue = (np.degrees(np.arctan2(-dy, dx)) + 360.0) % 360.0 / 360.0
        sat = np.clip(r, 0.0, 1.0)
        arr = np.zeros((n, n, 4), np.uint8)
        hh = hue * 6.0
        i = np.floor(hh).astype(int) % 6
        f = hh - np.floor(hh)
        v = self.v
        p = v * (1 - sat)
        q = v * (1 - sat * f)
        t = v * (1 - sat * (1 - f))
        r_ = np.choose(i, [v, q, p, p, t, v])
        g_ = np.choose(i, [t, v, v, q, p, p])
        b_ = np.choose(i, [p, p, t, v, v, q])
        arr[:, :, 0] = np.clip(r_ * 255, 0, 255).astype(np.uint8)
        arr[:, :, 1] = np.clip(g_ * 255, 0, 255).astype(np.uint8)
        arr[:, :, 2] = np.clip(b_ * 255, 0, 255).astype(np.uint8)
        arr[:, :, 3] = np.where(r <= 1.0, 255, 0).astype(np.uint8)
        path = os.path.join(os.environ.get("TEMP", "."), "modview_uv_wheel.png")
        _png_write_rgba(path, arr)
        return path

    def _rebuild(self):
        self.canvas.delete("all")
        try:
            path = self._wheel_png()
            self._img = tk.PhotoImage(master=self, file=path)
            self.canvas.create_image(0, 0, image=self._img, anchor="nw")
        except Exception:  # noqa: BLE001
            pass
        # 饱和度/明度指示圈
        n = self.WHEEL
        rad = self.s * (n / 2.0)
        ang = math.radians(self.h * 360.0)
        px = n / 2.0 + math.cos(ang) * rad
        py = n / 2.0 - math.sin(ang) * rad
        self.canvas.create_oval(px - 5, py - 5, px + 5, py + 5,
                                outline="#ffffff", width=2)
        self.canvas.create_oval(px - 6, py - 6, px + 6, py + 6,
                                outline="#000000", width=1)

        # 明度条
        self.bar.delete("all")
        for yy in range(self.WHEEL):
            t = 1.0 - yy / float(self.WHEEL - 1)
            r, g, b = _hsv_to_rgb(self.h, self.s, t)
            self.bar.create_line(0, yy, self.BAR_W, yy,
                                 fill="#%02x%02x%02x" % (int(r * 255 + 0.5),
                                                         int(g * 255 + 0.5),
                                                         int(b * 255 + 0.5)))
        by = int((1.0 - self.v) * (self.WHEEL - 1))
        self.bar.create_rectangle(0, by - 2, self.BAR_W, by + 2,
                                  outline="#ffffff", width=2)

        r, g, b = _hsv_to_rgb(self.h, self.s, self.v)
        self.preview.configure(bg="#%02x%02x%02x" % (int(r * 255 + 0.5),
                                                    int(g * 255 + 0.5),
                                                    int(b * 255 + 0.5)))
        self.hex_var.set("#%02X%02X%02X%02X" % (int(r * 255 + 0.5), int(g * 255 + 0.5),
                                               int(b * 255 + 0.5), int(self.a * 255 + 0.5)))

    # ---------------- 交互
    def _pick_hs(self, x, y, redraw):
        n = self.WHEEL
        cx = cy = (n - 1) / 2.0
        dx = x - cx
        dy = cy - y
        self.s = min(1.0, math.hypot(dx, dy) / (n / 2.0))
        if abs(dx) > 1e-6 or abs(dy) > 1e-6:
            self.h = (math.degrees(math.atan2(dy, dx)) % 360.0) / 360.0
        if redraw:
            self._rebuild()

    def _pick_v(self, y, redraw):
        self.v = min(1.0, max(0.0, 1.0 - y / float(self.WHEEL - 1)))
        if redraw:
            self._rebuild()

    def _from_hex(self):
        txt = (self.hex_var.get() or "").strip().lstrip("#")
        try:
            if len(txt) == 6:
                r, g, b = (int(txt[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
                a = self.a
            elif len(txt) == 8:
                r, g, b, a = (int(txt[i:i + 2], 16) / 255.0 for i in (0, 2, 4, 6))
            else:
                return
        except ValueError:
            return
        self.h, self.s, self.v = _rgb_to_hsv((r, g, b))
        self.a = a
        self._rebuild()

    def _ok(self):
        r, g, b = _hsv_to_rgb(self.h, self.s, self.v)
        try:
            self._on_ok((r, g, b, self.a))
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def _hsv_to_rgb(h, s, v):
    h = (h % 1.0) * 6.0
    i = int(math.floor(h)) % 6
    f = h - math.floor(h)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


def _rgb_to_hsv(rgb):
    r, g, b = [max(0.0, min(1.0, float(x))) for x in rgb[:3]]
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 1e-9:
        h = 0.0
    elif mx == r:
        h = ((g - b) / d) % 6.0
    elif mx == g:
        h = (b - r) / d + 2.0
    else:
        h = (r - g) / d + 4.0
    return (h / 6.0) % 1.0, (0.0 if mx == 0 else d / mx), mx


class ModViewer(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        # 真实 DPI（感知之后才是物理值，否则永远返回 96）
        try:
            self.dpi = float(self.winfo_fpixels("1i"))
        except Exception:  # noqa: BLE001
            self.dpi = 96.0
        self.S = max(1.0, self.dpi / 96.0)
        try:
            # 让"点"单位的字号跟着 DPI 放大
            self.tk.call("tk", "scaling", self.dpi / 72.0)
        except Exception:  # noqa: BLE001
            pass

        self.title("闪电战 MOD 查看器  {}".format(VERSION))
        # 高 DPI 下窗口别超出屏幕（小屏 + 200% 的机器上会顶出去）
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(1180 * self.S), int(sw * 0.94))
        h = min(int(760 * self.S), int(sh * 0.90))
        self.geometry("{}x{}".format(w, h))
        self.minsize(min(int(900 * self.S), w), min(int(600 * self.S), h))
        # 首选项：撤销次数 + 删除权限（删除权限默认只开定位器）
        self.prefs = {"undo_max": 32, "del_mesh": False,
                      "del_locator": True, "del_aabb": False,
                      "join": False, "sep_loose": False, "lang": "zh",
                      "import_anim": False, "ask_import_anim": True, "edit_anim": False,
                      "link_uv": False, "link_angle": 30.0, "link_mode": "topo"}
        self._prefs_load()
        set_lang(self.prefs.get("lang", "zh"))
        self.configure(bg="#f0f0f0")

        self.scene = None
        self.mod_path = None
        self.tex_paths = []

        self._build_ui()
        self._init_file_drop()
        self._drop = None
        self.after(300, self._init_drop)      # 等窗口真正建出来再注册

    def _split_start(self, e):
        self._split_x = e.x_root
        self._split_w = self.left.winfo_width()
        # 拖动期间不改布局（GL 一 resize 就要重画，必然闪）。
        # 引导线用一个无边框置顶小窗口来画：移动由系统负责刷新，不会留残影；
        # 早先用 place 在 body 上移 Frame 会拖出一条条"鬼影"。
        self._guide = None
        try:
            g = tk.Toplevel(self)
            g.overrideredirect(True)
            g.configure(bg="#0078d7")
            try:
                g.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass
            self._guide = g
            self._sync_guide(self._split_w)
        except Exception:  # noqa: BLE001
            self._guide = None
        self.set_status("拖动分隔条：大纲宽 {} px（松手应用）".format(self._split_w))

    def _sync_guide(self, w):
        g = getattr(self, "_guide", None)
        if g is None:
            return
        try:
            body = self.left.master
            g.geometry("{}x{}+{}+{}".format(
                self.px(2), body.winfo_height(),
                body.winfo_rootx() + int(w), body.winfo_rooty()))
        except Exception:  # noqa: BLE001
            pass

    def _split_drag(self, e):
        if getattr(self, "_split_x", None) is None:
            return
        w = self._split_target(e)
        self._sync_guide(w)
        self.set_status("拖动分隔条：大纲宽 {} px（松手应用）".format(w))

    def _split_target(self, e):
        dx = e.x_root - self._split_x
        lo = self.px(200)
        hi = max(lo + self.px(80), self.winfo_width() - self.px(320))
        return max(lo, min(hi, self._split_w + dx))

    def _split_end(self, e):
        g = getattr(self, "_guide", None)
        self._guide = None
        if g is not None:
            try:
                g.destroy()
            except Exception:  # noqa: BLE001
                pass
        if getattr(self, "_split_x", None) is None:
            return
        self.left.config(width=self._split_target(e))
        self._split_x = None
        self.update_idletasks()          # 先让布局落定，再按新尺寸重绘
        self.view.redraw(force=True)
        self.set_status("大纲宽度 {} px".format(self.left.winfo_width()))

    def _split_reset(self):
        self.left.config(width=self.px(360))
        self.update_idletasks()
        self.view.redraw(force=True)

    # ---- 右侧 XML 面板分隔条（逻辑同左侧，方向相反）
    def _xml_split_start(self, e):
        self._xml_split_x = e.x_root
        self._xml_split_w = self.xml_panel.winfo_width()
        self._guide = None
        try:
            g = tk.Toplevel(self)
            g.overrideredirect(True)
            g.configure(bg="#0078d7")
            try:
                g.attributes("-topmost", True)
            except Exception:  # noqa: BLE001
                pass
            self._guide = g
            self._sync_xml_guide(self._xml_split_w)
        except Exception:  # noqa: BLE001
            self._guide = None
        self.set_status("拖动分隔条：XML 宽 {} px（松手应用）".format(self._xml_split_w))

    def _sync_xml_guide(self, w):
        g = getattr(self, "_guide", None)
        if g is None:
            return
        try:
            # 引导线预览 XML 面板左边缘的新位置
            x = self.xml_panel.winfo_rootx() + (self._xml_split_w - w)
            g.geometry("{}x{}+{}+{}".format(
                self.px(2), self.xml_panel.winfo_height(), x,
                self.xml_panel.winfo_rooty()))
        except Exception:  # noqa: BLE001
            pass

    def _xml_split_target(self, e):
        dx = e.x_root - self._xml_split_x
        lo = self.px(200)
        hi = max(lo + self.px(80), self.winfo_width() - self.px(480))
        return max(lo, min(hi, self._xml_split_w - dx))

    def _xml_split_drag(self, e):
        if getattr(self, "_xml_split_x", None) is None:
            return
        w = self._xml_split_target(e)
        self._sync_xml_guide(w)
        self.set_status("拖动分隔条：XML 宽 {} px（松手应用）".format(w))

    def _xml_split_end(self, e):
        g = getattr(self, "_guide", None)
        self._guide = None
        if g is not None:
            try:
                g.destroy()
            except Exception:  # noqa: BLE001
                pass
        if getattr(self, "_xml_split_x", None) is None:
            return
        self.xml_panel.config(width=self._xml_split_target(e))
        self._xml_split_x = None
        self.update_idletasks()
        self.view.redraw(force=True)
        self.set_status("XML 宽度 {} px".format(self.xml_panel.winfo_width()))

    def _xml_split_reset(self):
        self.xml_panel.config(width=self.px(300))
        self.update_idletasks()
        self.view.redraw(force=True)

    def px(self, v):
        """按 DPI 缩放比把设计稿像素换算成实际像素。"""
        return int(round(v * getattr(self, "S", 1.0)))

    def _draw_tick(self, img, color):
        """在图片上画一条加粗对勾（折线两段）。"""
        b = img.width()
        t = max(1, int(round(getattr(self, "S", 1.0))))
        p = [(int(b * 0.22), int(b * 0.52)),
             (int(b * 0.40), int(b * 0.70)),
             (int(b * 0.78), int(b * 0.28))]
        for (x0, y0), (x1, y1) in ((p[0], p[1]), (p[1], p[2])):
            n = max(abs(x1 - x0), abs(y1 - y0), 1)
            for i in range(n + 1):
                x = x0 + (x1 - x0) * i // n
                y = y0 + (y1 - y0) * i // n
                img.put(color, to=(x, y, min(x + t, b), min(y + t, b)))

    def _make_check_images(self, size=None):
        """DPI 自适应的复选框图标：未勾选（灰框白底）/ 已勾选（蓝底白勾）。"""
        S = getattr(self, "S", 1.0)
        b = max(12, int(round((size or 13) * S)))
        lw = max(1, int(round(S)))
        img_off = tk.PhotoImage(master=self, width=b, height=b)
        img_off.put("#7a7a7a", to=(0, 0, b, b))
        img_off.put("white", to=(lw, lw, b - lw, b - lw))
        img_on = tk.PhotoImage(master=self, width=b, height=b)
        img_on.put("#0078d7", to=(0, 0, b, b))
        self._draw_tick(img_on, "white")
        return img_off, img_on

    def _build_menubar(self):
        """菜单栏单独建：切语言时整块重建。"""
        old = getattr(self, "_menubar", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:  # noqa: BLE001
                pass
        q = self.quality_var.get() if hasattr(self, "quality_var") else "balanced"
        g = self.grid_var.get() if hasattr(self, "grid_var") else False
        o = self.ortho_var.get() if hasattr(self, "ortho_var") else False
        t = self.tool_var.get() if hasattr(self, "tool_var") else "select"
        menubar = tk.Menu(self)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label=tr("打开 .mod..."), command=self.open_mod)
        m_file.add_command(label=tr("打开 OBJ..."), command=self.open_obj_dialog)
        m_file.add_command(label=tr("打开贴图..."), command=self.open_texture)
        m_file.add_separator()
        m_file.add_command(label=tr("重置视图"), command=self.reset_view)
        m_file.add_command(label=tr("保存截图..."), command=self.save_screenshot)
        m_file.add_command(label=tr("导出 UV 布局图 (PNG，带透明)..."),
                           command=self.dlg_export_uv)
        m_file.add_command(label=tr("导出 OBJ..."), command=self.export_obj_dialog)
        m_file.add_command(label=tr("保存为 .mod...    Ctrl+S"), command=self.save_mod_as)
        m_file.add_command(label=tr("动画时间线"), command=self._anim_menu_open)
        self.anim_var = tk.BooleanVar(value=bool(self.prefs.get("import_anim", False)))
        m_file.add_checkbutton(label=tr("启用动画预览（保存始终保留动画）"),
                               variable=self.anim_var,
                               command=self._toggle_import_anim)
        m_file.add_separator()
        m_file.add_command(label=tr("首选项..."), command=self.dlg_prefs)
        m_file.add_separator()
        m_file.add_command(label=tr("退出"), command=self.destroy)
        menubar.add_cascade(label=tr("文件"), menu=m_file)

        m_view = tk.Menu(menubar, tearoff=0)
        self.quality_var = tk.StringVar(value=q)
        m_view.add_radiobutton(label=tr("高质量（清晰，旋转会卡）"),
                               variable=self.quality_var,
                               value="high", command=self.apply_quality)
        m_view.add_radiobutton(label=tr("均衡（默认）"), variable=self.quality_var,
                               value="balanced", command=self.apply_quality)
        m_view.add_radiobutton(label=tr("低（流畅，旋转模糊）"), variable=self.quality_var,
                               value="low", command=self.apply_quality)
        m_view.add_separator()
        self.grid_var = tk.BooleanVar(value=g)
        m_view.add_checkbutton(label=tr("基面网格"), variable=self.grid_var,
                               command=self.toggle_grid)
        self.ortho_var = tk.BooleanVar(value=o)
        m_view.add_checkbutton(label=tr("正交视图（点右下角坐标球也能切）"),
                               variable=self.ortho_var, command=self.toggle_ortho)
        m_view.add_separator()
        self.tool_var = tk.StringVar(value=t)
        mt = tk.Menu(m_view, tearoff=0)
        for label, val, key in (("选择", "select", "Esc"), ("移动", "move", "G"),
                                ("旋转", "rotate", "R"), ("缩放", "scale", "S")):
            mt.add_radiobutton(label="{}    {}".format(tr(label), key),
                               variable=self.tool_var, value=val,
                               command=lambda v=val: self.set_tool(v))
        m_view.add_cascade(label=tr("变换工具"), menu=mt)
        m_view.add_command(label=tr("变换面板    N"), command=self.toggle_transform_panel)
        menubar.add_cascade(label=tr("视图"), menu=m_view)

        m_part = tk.Menu(menubar, tearoff=0)
        m_part.add_command(label=tr("设置范围（旋转 / 位移）…"), command=self.dlg_rotation_limit)
        m_part.add_command(label=tr("清除限制"), command=self.clear_limit)
        m_part.add_separator()
        m_part.add_command(label=tr("生成 AABB（选中部件）"),
                           command=lambda: self.make_aabb(True))
        m_part.add_command(label=tr("生成 AABB（全部网格）"),
                           command=lambda: self.make_aabb(False))
        m_part.add_separator()
        m_part.add_command(label=tr("生成定位器…"), command=self.dlg_locator)
        m_part.add_command(label=tr("批量生成定位器"),
                           command=lambda: self.add_all_locators("CENTER"))
        m_part.add_separator()
        m_part.add_command(label=tr("平台 / 武器（XML）…"), command=self.dlg_platforms)
        m_part.add_separator()
        m_part.add_command(label=tr("合并网格    Ctrl+J"), command=self.join_meshes)
        m_part.add_command(label=tr("按松散块分离    Shift+P"), command=self.separate_loose)
        m_part.add_command(label=tr("选择相连面工具（点面圈选）"),
                           command=lambda: self.set_tool("link"))
        self.link_uv_var = tk.BooleanVar(value=bool(self.prefs.get("link_uv", False)))
        m_part.add_checkbutton(label="　　划界：按 UV 断开（默认只按法向）",
                               variable=self.link_uv_var,
                               command=self._toggle_link_uv)
        mm = tk.Menu(m_part, tearoff=0)
        cur_mode = str(self.prefs.get("link_mode", "topo") or "topo")
        cur_val = cur_mode if cur_mode != "flat" else "flat:{}".format(
            int(self.prefs.get("link_angle", 30.0)))
        self._link_mode_var = tk.StringVar(value=cur_val)
        mm.add_radiobutton(label="拓扑（整块相连，同 Blender Ctrl+L）",
                           value="topo", variable=self._link_mode_var,
                           command=lambda: self._set_link_mode("topo"))
        mm.add_radiobutton(label="另外断开「朝向相反」的相邻面",
                           value="normal", variable=self._link_mode_var,
                           command=lambda: self._set_link_mode("normal"))
        mm.add_separator()
        for deg in (15, 30, 45, 60, 90):
            mm.add_radiobutton(label="平直面 ≤ {}°（Linked Flat Faces）".format(deg),
                               value="flat:{}".format(deg), variable=self._link_mode_var,
                               command=lambda d=float(deg): self._set_link_flat(d))
        m_part.add_cascade(label="　　划界方式（当前：{}）".format(
            {"topo": "拓扑", "normal": "拓扑+断开反向"}.get(
                cur_val, "平直面 ≤ {}°".format(int(self.prefs.get("link_angle", 30.0))))),
            menu=mm)
        m_part.add_separator()
        m_part.add_command(label=tr("删除选中部件（网格/定位器）    Del"),
                           command=self.delete_selected)
        m_part.add_command(label=tr("只删定位器"), command=self.remove_selected_locators)
        m_part.add_command(label=tr("删除选中 AABB"), command=self.delete_selected_aabb)
        menubar.add_cascade(label=tr("部件"), menu=m_part)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label=tr("关于"), command=self.show_about)
        menubar.add_cascade(label=tr("关于"), menu=m_help)
        self.config(menu=menubar)
        self._menubar = menubar

    def _build_ui(self):
        self._tr_widgets = []          # 需要跟着语言刷新的 (控件, 中文原文)
        self._build_menubar()

        # ---------------- 主体
        body = tk.Frame(self, bg="#f0f0f0")
        body.pack(side="top", fill="both", expand=True)

        # 左栏收起条（最左缘；点击展开/收起大纲栏）
        self.left_bar = tk.Frame(body, bg="#d8d8d8", width=self.px(16))
        self.left_bar.pack(side="left", fill="y")
        self.left_bar.pack_propagate(False)
        self.lb_left_toggle = tk.Label(self.left_bar, text="▶", bg="#d8d8d8", fg="#444444",
                                       font=("Microsoft YaHei UI", 8))
        self.lb_left_toggle.pack(expand=True)
        for w in (self.left_bar, self.lb_left_toggle):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self.toggle_left_panel())

        # 左栏（固定宽度）
        left = tk.Frame(body, bg="#f0f0f0", width=self.px(360))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.left = left
        self._left_open = True

        head = tk.Frame(left, bg="#f0f0f0")
        head.pack(fill="x", padx=6, pady=(4, 2))
        lb_parts = tk.Label(head, text=tr("模型部件："), anchor="w",
                            bg="#f0f0f0", fg="black")
        lb_parts.pack(side="left")
        for txt, flag in (("折叠", False), ("展开", True)):
            b = tk.Button(head, text=tr(txt), bd=1, relief="raised", padx=self.px(3), pady=0,
                          font=("Microsoft YaHei UI", 8),
                          command=lambda f=flag: self._set_open(f))
            b.pack(side="right", padx=(3, 0))
            self._tr_widgets.append((b, txt))

        # 搜索
        srow = tk.Frame(left, bg="#f0f0f0")
        srow.pack(fill="x", padx=6, pady=(0, 3))
        self.search_var = tk.StringVar()
        lb_search = tk.Label(srow, text=tr("搜索"), bg="#f0f0f0", fg="#444444",
                             font=("Microsoft YaHei UI", 9))
        lb_search.pack(side="left", padx=(0, self.px(4)))
        ent = tk.Entry(srow, textvariable=self.search_var, font=("Microsoft YaHei UI", 9),
                       relief="solid", bd=max(1, self.px(1)), bg="white",
                       highlightthickness=0, insertwidth=max(1, self.px(1)))
        ent.pack(side="left", fill="x", expand=True, ipady=self.px(2))
        ent.bind("<KeyRelease>", lambda e: self.on_search())
        ent.bind("<Escape>", lambda e: (self.search_var.set(""), self.on_search()))

        # 类型勾选：全部 / 网格 / 定位器 / AABB，决定大纲和视口里显不显示
        # 经典 tk.Checkbutton 的勾选框不跟 DPI，改用自绘 image/selectimage
        bar = tk.Frame(left, bg="#f0f0f0")
        bar.pack(fill="x", padx=6, pady=(0, 3))
        self.auto_aabb = tk.BooleanVar(value=False)   # 变换后自动重算主 AABB
        self.show_all = tk.BooleanVar(value=True)
        self.show_mesh = tk.BooleanVar(value=True)
        self.show_loc = tk.BooleanVar(value=True)
        self.show_aabb = tk.BooleanVar(value=True)
        img_off, img_on = self._make_check_images()
        self._chk_imgs = (img_off, img_on)          # 持引用防止被 GC
        self._tr_widgets.extend([(lb_parts, "模型部件："), (lb_search, "搜索")])
        for txt, var, cmd in (("全部", self.show_all, self.on_all_toggle),
                              ("网格", self.show_mesh, self.on_filter_change),
                              ("定位器", self.show_loc, self.on_filter_change),
                              ("AABB", self.show_aabb, self.on_filter_change),
                              ("自动AABB", self.auto_aabb,
                               lambda: self.set_status("变换后自动重算主 AABB：{}".format(
                                   "开" if self.auto_aabb.get() else "关")))):
            cb = tk.Checkbutton(bar, text=tr(txt), variable=var, onvalue=True, offvalue=False,
                                image=img_off, selectimage=img_on, compound="left",
                                indicatoron=False, relief="flat",
                                bg="#f0f0f0", activebackground="#f0f0f0",
                                highlightthickness=0, bd=0, padx=1, pady=0,
                                font=("Microsoft YaHei UI", 9), command=cmd)
            cb.pack(side="left", padx=(0, 3))

        f1 = tk.Frame(left, bg="white", bd=1, relief="sunken")

        # 贴图区先贴到底部，剩下的高度全给大纲树
        tex_box = tk.Frame(left, bg="#f0f0f0")
        tex_box.pack(side="bottom", fill="x", pady=(0, 4))
        tk.Label(tex_box, text="Recent Textures:", anchor="w",
                 bg="#f0f0f0", fg="black").pack(fill="x", padx=6, pady=(4, 2))
        f2 = tk.Frame(tex_box, bg="white", bd=1, relief="sunken")
        f2.pack(fill="x", padx=6)
        self.lb_tex = tk.Listbox(f2, bg="white", fg="black", bd=0, height=7,
                                 highlightthickness=0, activestyle="none",
                                 selectbackground="#0078d7", selectforeground="white",
                                 exportselection=False)
        sb2 = tk.Scrollbar(f2, orient="vertical", command=self.lb_tex.yview)
        self.lb_tex.config(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        self.lb_tex.pack(side="left", fill="both", expand=True)
        self.lb_tex.bind("<<ListboxSelect>>", self.on_tex_select)
        self.lb_tex.bind("<Double-Button-1>", self.on_tex_dbl)

        f1.pack(fill="both", expand=True, padx=6)

        # ---- 大纲：自绘 Canvas（眼睛 / 折叠三角 / 文字，命中判定完全可控）
        self.outline = tk.Canvas(f1, bg="white", highlightthickness=0, bd=0,
                                 width=self.px(300), yscrollincrement=self.px(20),
                                 xscrollincrement=self.px(16))
        sb1 = tk.Scrollbar(f1, orient="vertical", command=self.outline.yview)
        hbar = tk.Scrollbar(f1, orient="horizontal", command=self.outline.xview)
        self.outline.config(yscrollcommand=sb1.set, xscrollcommand=hbar.set)
        hbar.pack(side="bottom", fill="x")
        sb1.pack(side="right", fill="y")
        self.outline.pack(side="left", fill="both", expand=True)
        self.outline.bind("<Button-1>", self._on_outline_click)
        self.outline.bind("<Double-Button-1>", self._on_outline_dbl)
        self.outline.bind("<Alt-Button-1>", self._on_outline_alt_click)
        self.outline.bind("<Button-3>", self._on_outline_menu)
        self.outline.bind("<MouseWheel>", self._on_outline_wheel)
        self.outline.bind("<Shift-MouseWheel>", self._on_outline_wheel)
        self.outline.bind("<Configure>", self._on_outline_resize)

        self._xml_root = None
        self._xml_path = None
        self._xml_meta = {}
        self._xml_dirty = False

        self.hidden = set()
        self.expanded = set()
        self.sel_id = None            # 活动对象（最后点中的那个；父子级里它当父级）
        self.sel_ids = set()          # 多选集合（含 sel_id）
        # 勾上后：移动/旋转/缩放只改对象原点，网格几何在世界里保持不动
        self.origin_only = tk.BooleanVar(value=False)
        self._aabb_items = []
        self.aabb_on = set()
        self.aabb_sel = None
        self.solo_id = None
        self.rows = []
        self.ROW_H = self.px(24)
        self.INDENT = self.px(18)

        # 分隔条：按住左右拖，调整大纲 / 视口占比；双击复位
        self.splitter = tk.Frame(body, bg=SPLIT_BG, width=self.px(6),
                                 cursor="sb_h_double_arrow")
        self.splitter.pack(side="left", fill="y")
        self.splitter.bind("<Button-1>", self._split_start)
        self.splitter.bind("<B1-Motion>", self._split_drag)
        self.splitter.bind("<ButtonRelease-1>", self._split_end)
        self.splitter.bind("<Double-Button-1>", lambda e: self._split_reset())
        self.splitter.bind("<Enter>", lambda e: self.splitter.config(bg=SPLIT_HL))
        self.splitter.bind("<Leave>", lambda e: self.splitter.config(bg=SPLIT_BG))

        # ---- 右侧 XML 面板（可收起）
        self.xml_bar = tk.Frame(body, bg="#d8d8d8", width=self.px(16))
        self.xml_bar.pack(side="right", fill="y")
        self.xml_bar.pack_propagate(False)
        self.lb_xml_toggle = tk.Label(self.xml_bar, text="▶", bg="#d8d8d8", fg="#444444",
                                      font=("Microsoft YaHei UI", 8))
        self.lb_xml_toggle.pack(expand=True)
        for w in (self.xml_bar, self.lb_xml_toggle):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self.toggle_xml_panel())

        self.xml_panel = tk.Frame(body, bg="#f0f0f0", width=self.px(300))
        self.xml_panel.pack(side="right", fill="y")
        self.xml_panel.pack_propagate(False)
        self._xml_open = True

        # ---- XML 面板左缘分隔条：按住拖动调整 XML / 视口占比，双击复位
        self.xml_splitter = tk.Frame(body, bg=SPLIT_BG, width=self.px(6),
                                     cursor="sb_h_double_arrow")
        self.xml_splitter.pack(side="right", fill="y")
        self.xml_splitter.bind("<Button-1>", self._xml_split_start)
        self.xml_splitter.bind("<B1-Motion>", self._xml_split_drag)
        self.xml_splitter.bind("<ButtonRelease-1>", self._xml_split_end)
        self.xml_splitter.bind("<Double-Button-1>", lambda e: self._xml_split_reset())
        self.xml_splitter.bind("<Enter>", lambda e: self.xml_splitter.config(bg=SPLIT_HL))
        self.xml_splitter.bind("<Leave>", lambda e: self.xml_splitter.config(bg=SPLIT_BG))

        xhead = tk.Frame(self.xml_panel, bg="#f0f0f0")
        xhead.pack(fill="x", padx=6, pady=(4, 2))
        tk.Label(xhead, text="XML", anchor="w", bg="#f0f0f0", fg="black",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.xml_title = tk.Label(xhead, text="", anchor="e", bg="#f0f0f0", fg="#555555",
                                  font=("Microsoft YaHei UI", 8))
        self.xml_title.pack(side="right")

        xsrow = tk.Frame(self.xml_panel, bg="#f0f0f0")
        xsrow.pack(fill="x", padx=6, pady=(0, 2))
        tk.Label(xsrow, text="搜索", bg="#f0f0f0", fg="#444444",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, self.px(4)))
        self.xml_search = tk.StringVar()
        self.ent_xml_search = tk.Entry(xsrow, textvariable=self.xml_search,
                                       font=("Microsoft YaHei UI", 9), relief="solid",
                                       bg="white", bd=max(1, self.px(1)),
                                       highlightthickness=0)
        self.ent_xml_search.pack(side="left", fill="x", expand=True, ipady=self.px(1))
        self.ent_xml_search.bind("<KeyRelease>", lambda e: self.on_xml_search())
        self.ent_xml_search.bind("<Escape>",
                                 lambda e: (self.xml_search.set(""), self.on_xml_search()))

        xf = tk.Frame(self.xml_panel, bg="white", bd=1, relief="sunken")
        xf.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        st_x = ttk.Style(self)
        st_x.configure("Xml.Treeview", background="white", fieldbackground="white",
                       foreground="black", rowheight=self.px(22), borderwidth=0,
                       font=("Consolas", 9))
        # 展开指示器：Tk 8.6.6+ 的 treeview 由内部 C 代码绘制条目，
        # indicatorsize/原生 vista 元素都无法缩放。改为自绘 +/− 图片，
        # 通过 element_create 注册到内部 Item 布局引用的 Treeitem.indicator 元素。
        # 状态位（ttkTreeview.c）：open=TTK_STATE_USER1，leaf=TTK_STATE_USER2，
        # 图片尺寸即显示尺寸，天然跟随 DPI 缩放。
        try:
            S = self.S
            b = max(11, int(round(13 * S)))
            if b % 2 == 0:
                b += 1                      # 奇数边长，加号才能居中
            lw = max(1, int(round(S)))      # 边框线宽
            t = max(1, int(round(S)))       # +/− 符号线宽
            c = b // 2
            bar_y = c - t // 2
            img_open = tk.PhotoImage(master=self, width=b, height=b)   # [-] 展开态
            img_open.put("#7a7a7a", to=(0, 0, b, b))
            img_open.put("white", to=(lw, lw, b - lw, b - lw))
            img_open.put("#1a1a1a", to=(lw + 1, bar_y, b - lw - 1, bar_y + t))
            img_closed = tk.PhotoImage(master=self, width=b, height=b) # [+] 收起态
            img_closed.put("#7a7a7a", to=(0, 0, b, b))
            img_closed.put("white", to=(lw, lw, b - lw, b - lw))
            img_closed.put("#1a1a1a", to=(lw + 1, bar_y, b - lw - 1, bar_y + t))
            img_closed.put("#1a1a1a", to=(c - t // 2, lw + 1, c - t // 2 + t, b - lw - 1))
            img_blank = tk.PhotoImage(master=self, width=b, height=b)  # 叶子节点不画
            # 匹配顺序：open → 展开图标；非叶子且收起 → 收起图标；叶子 → 透明
            st_x.element_create("Treeitem.indicator", "image", img_blank,
                                ("user1", img_open), ("!user2", img_closed),
                                sticky="")
            self._xml_ind_imgs = (img_open, img_closed, img_blank)  # 防 GC
        except Exception:  # noqa: BLE001
            pass
        st_x.map("Xml.Treeview", background=[("selected", "#0078d7")],
                 foreground=[("selected", "white")])
        self.xml_tree = ttk.Treeview(xf, show="tree", style="Xml.Treeview")
        sx = tk.Scrollbar(xf, orient="vertical", command=self.xml_tree.yview)
        hx = tk.Scrollbar(xf, orient="horizontal", command=self.xml_tree.xview)
        self.xml_tree.config(yscrollcommand=sx.set, xscrollcommand=hx.set)
        hx.pack(side="bottom", fill="x")
        sx.pack(side="right", fill="y")
        self.xml_tree.pack(side="left", fill="both", expand=True)
        self.xml_tree.column("#0", width=self.px(280), stretch=False)
        # 层级缩进也按 DPI 放大（Tk >= 8.6.6 支持 indent 选项）
        try:
            self.xml_tree.configure(indent=int(round(20 * self.S)))
        except Exception:  # noqa: BLE001
            pass
        self.xml_tree.bind("<<TreeviewSelect>>", self._on_xml_select)

        xedit = tk.Frame(self.xml_panel, bg="#f0f0f0")
        xedit.pack(fill="x", padx=6, pady=(0, 2))
        tk.Label(xedit, text="值", bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.xml_value = tk.StringVar()
        self.ent_xml = tk.Entry(xedit, textvariable=self.xml_value,
                                font=("Consolas", 9), relief="solid", bg="white",
                                bd=max(1, self.px(1)), highlightthickness=0)
        self.ent_xml.pack(side="left", fill="x", expand=True, padx=self.px(4),
                          ipady=self.px(1))
        self.ent_xml.bind("<Return>", lambda e: self._xml_apply())

        xbar = tk.Frame(self.xml_panel, bg="#f0f0f0")
        xbar.pack(fill="x", padx=6, pady=(0, 6))
        tk.Button(xbar, text="应用", width=5, font=("Microsoft YaHei UI", 9),
                  command=self._xml_apply).pack(side="left")
        tk.Button(xbar, text="保存", width=5, font=("Microsoft YaHei UI", 9),
                  command=self._xml_save).pack(side="left", padx=self.px(4))
        self.lb_xml_hint = tk.Label(xbar, text="", bg="#f0f0f0", fg="#666666",
                                    font=("Microsoft YaHei UI", 8))
        self.lb_xml_hint.pack(side="left")

        # 右：3D 视口（纯黑，无边框）
        self.tool = "select"
        self._face_sel = None          # 选择相连面：{"name", "tris"}
        self._panel = None
        self._tb = None
        # 视口上方一条细工具栏：视图下拉（3D / UV）。Tk 控件直接叠在 GL 上会
        # 让 SwapBuffers 偶发黑屏，所以单独占一行，不覆盖 GL 区域。
        # 视口上方一条细工具条：视图下拉（3D / UV）。Tk 控件直接叠在 GL 上会
        # 让 SwapBuffers 偶发黑屏，所以单独占一行，不覆盖 GL 区域。
        vwrap = tk.Frame(body, bg="#f0f0f0")
        vwrap.pack(side="left", fill="both", expand=True)
        vbar = tk.Frame(vwrap, bg="#f0f0f0")
        vbar.pack(side="top", fill="x")
        tk.Label(vbar, text=tr("视图"), bg="#f0f0f0", fg="#444444",
                 font=("Microsoft YaHei UI", 9)).pack(
            side="left", padx=(self.px(8), self.px(4)), pady=self.px(2))
        self.view_mode_var = tk.StringVar(value=tr("3D 视图"))
        self._view_mode_box = ttk.Combobox(
            vbar, textvariable=self.view_mode_var, state="readonly", width=10,
            values=[tr("3D 视图"), tr("UV 视图")],
            font=("Microsoft YaHei UI", 9))
        self._view_mode_box.pack(side="left", pady=self.px(2))
        self._view_mode_box.bind("<<ComboboxSelected>>", self._on_view_mode)
        self.view = GLViewport(vwrap)
        self.view.S = self.S          # 描边环宽随 DPI 缩放
        self.view.on_error = self.set_status
        self.view.on_pick = self.on_view_pick
        self.view.on_view_hint = self.set_status
        self.view.on_ortho_changed = self._on_ortho_changed
        self.view.on_files_dropped = self._on_files_dropped
        self.view.on_gizmo_start = self._on_gizmo_start
        self.view.on_gizmo = self._on_gizmo
        self.view.on_gizmo_end = self._on_gizmo_end
        self.view.on_aabb_face = self._on_aabb_face
        self.view.on_aabb_face_start = self._on_aabb_face_start
        self.view.on_aabb_face_end = self._on_aabb_face_end
        self.view.on_face_pick = self._on_face_pick
        self.view.on_context_menu = self._viewport_menu
        self.view.on_tool_click = self.set_tool
        self.view.pack(side="top", fill="both", expand=True)
        self._dropped_paths = []
        self.after(150, self._poll_drops)
        # 工具条直接画在 GL 视口里（Tk 控件叠在 GL 窗口上会导致 SwapBuffers 偶发黑屏）

        # ---------------- 状态栏
        self.status = tk.Label(self, text="Ready   |   " + VERSION + "   |   可直接拖入 .mod / .dds", anchor="w", bg="#f0f0f0",
                               fg="black", bd=1, relief="sunken", padx=6)
        self.status.pack(side="bottom", fill="x")

        # ---------------- 动画时间线（导入动画后才显示）
        self.anim_bar = tk.Frame(self, bg="#e4e4e6")
        self._anim_sync = False
        tk.Label(self.anim_bar, text=tr("动画"), bg="#e4e4e6", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 4))
        self.anim_clip = ttk.Combobox(self.anim_bar, state="readonly", width=18,
                                      font=("Microsoft YaHei UI", 9))
        self.anim_clip.pack(side="left")
        self.anim_clip.bind("<<ComboboxSelected>>", self._anim_on_clip)
        self.anim_play_btn = tk.Button(self.anim_bar, text="▶", width=3,
                                       command=self._anim_toggle_play,
                                       font=("Microsoft YaHei UI", 9))
        self.anim_play_btn.pack(side="left", padx=(6, 2))
        self.anim_del_btn = tk.Button(self.anim_bar, text=tr("删除段"), width=6,
                                      command=self._anim_delete_clip,
                                      font=("Microsoft YaHei UI", 9), state="disabled")
        self.anim_del_btn.pack(side="left", padx=(0, 2))
        self.anim_scale = ttk.Scale(self.anim_bar, from_=0, to=1, orient="horizontal",
                                    command=self._anim_on_scale)
        self.anim_scale.pack(side="left", fill="x", expand=True, padx=6, pady=3)
        self.anim_lbl = tk.Label(self.anim_bar, text="0 / 0", bg="#e4e4e6", fg="#333333",
                                 width=11, font=("Microsoft YaHei UI", 9))
        self.anim_lbl.pack(side="left")
        tk.Button(self.anim_bar, text="✕", width=3, command=self._anim_close,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(2, 8))

        # 启动时两边面板默认收起，3D 视口最大化
        self.toggle_left_panel()
        self.toggle_xml_panel()
        self._bind_hotkeys()

    # ================================================================ 变换工具
    def _bind_hotkeys(self):
        """G / R / S 切换工具，N 开关变换面板，Esc 退回选择。"""
        for key, tool in (("g", "move"), ("r", "rotate"), ("s", "scale")):
            for k in (key, key.upper()):
                self.bind("<KeyPress-{}>".format(k),
                          lambda e, t=tool: self._hotkey_tool(t), add="+")
        self.bind("<KeyPress-n>", lambda e: self._hotkey_panel(), add="+")
        # 松散分离 Shift+P（大写 P 就是 Shift 状态下的 keysym）
        self.bind("<KeyPress-P>", self._hotkey_separate_loose, add="+")
        self.bind("<Shift-P>", self._hotkey_separate_loose, add="+")
        self.bind("<Shift-p>", self._hotkey_separate_loose, add="+")
        self.bind("<KeyPress-N>", lambda e: self._hotkey_panel(), add="+")
        self.bind("<Escape>", lambda e: self._hotkey_escape(), add="+")
        self.bind("<Control-z>", lambda e: self._hotkey_undo(), add="+")
        self.bind("<Control-Z>", lambda e: self._hotkey_undo(), add="+")
        # 父子级：Blender 的 Ctrl+P / Alt+P
        self.bind("<Control-p>", self._hotkey_parent, add="+")
        self.bind("<Control-P>", self._hotkey_parent, add="+")
        self.bind("<Alt-p>", self._hotkey_unparent, add="+")
        self.bind("<Alt-P>", self._hotkey_unparent, add="+")
        self.bind("<Delete>", self._hotkey_delete, add="+")
        self.bind("<Control-j>", self._hotkey_join, add="+")
        self.bind("<Control-J>", self._hotkey_join, add="+")
        self.bind("<Control-s>", self._hotkey_save, add="+")
        self.bind("<Control-S>", self._hotkey_save, add="+")
        self.bind("<KeyPress-p>", self._hotkey_separate, add="+")
        self.bind("<KeyPress-P>", self._hotkey_separate, add="+")
        # 中文输入法激活时 keysym 会变成 ??/Process，用 VK 键码兜底
        self.bind("<KeyPress>", self._hotkey_fallback, add="+")

    def _typing(self):
        """焦点在输入框里时，快捷键要让位给打字。"""
        try:
            w = self.focus_get()
        except Exception:  # noqa: BLE001
            return False
        return isinstance(w, (tk.Entry, tk.Text, tk.Spinbox,
                               ttk.Entry, ttk.Combobox))

    # Windows 虚拟键码（中文输入法下 keysym 不可靠，键码仍准确）
    VK_G, VK_R, VK_S, VK_N, VK_P = 71, 82, 83, 78, 80
    VK_ESC, VK_DEL = 27, 46

    def _hotkey_fallback(self, e):
        """输入法冲突兜底：keysym 变成 ?? 时按 VK 键码重放一次热键。"""
        if self._typing():
            return
        ks = (getattr(e, "keysym", "") or "").strip()
        # keysym 正常说明专用绑定已经处理过了，别重复触发（G/R/S 是会来回切的）
        if ks and ks not in ("??", "Process", "None", "??") and len(ks) <= 2:
            if ks.lower() in ("g", "r", "s", "n", "p"):
                return
        if ks in ("Escape", "Delete"):
            return
        st = int(getattr(e, "state", 0) or 0)
        if st & 0x0004 or st & 0x0008:      # Ctrl / Alt 组合交给专用绑定
            return
        vk = int(getattr(e, "keycode", 0) or 0)
        if vk in (self.VK_G, self.VK_R, self.VK_S):
            self._hotkey_tool({self.VK_G: "move", self.VK_R: "rotate",
                               self.VK_S: "scale"}[vk])
        elif vk == self.VK_N:
            self._hotkey_panel()
        elif vk == self.VK_P:
            self._hotkey_separate()
        elif vk == self.VK_ESC:
            self._hotkey_escape()
        elif vk == self.VK_DEL:
            self._hotkey_delete()

    def _hotkey_tool(self, tool):
        if self._typing():
            return
        # UV 视图里 G/R/S 也是切工具（拖动 gizmo 手柄改造）
        self.set_tool("select" if self.tool == tool else tool)

    def _hotkey_panel(self):
        if self._typing():
            return
        self.toggle_transform_panel()

    def _hotkey_undo(self):
        if self._typing():
            return
        self.undo()

    def _hotkey_escape(self):

        if self._typing():
            return
        if self.tool != "select":
            self.set_tool("select")

    def set_tool(self, name):
        if name not in ("select", "move", "rotate", "scale", "link"):
            name = "select"
        if name != "link" and getattr(self, "_face_sel", None):
            self._clear_face_sel()
        self.tool = name
        if getattr(self, "view", None) is not None:
            self.view.tool = name
        if self._tb is not None:
            self._tb.set_active(name)
        try:
            self.tool_var.set(name)
        except Exception:  # noqa: BLE001
            pass
        hint = {"select": "选择工具",
                "move": "移动工具：拖轴 / 拖平面方块（G）",
                "rotate": "旋转工具：拖圆环（R）",
                "scale": "缩放工具：拖轴端方块 / 中心方块（S）",
                "link": "选择相连面：点一下选整片，Shift+左键追加多选；P 分离"}.get(name, "")
        if name in ("move", "rotate", "scale") and getattr(self, "sel_id", None) is None:
            hint += "   —— 先选一个部件"
        self.set_status("工具：" + hint)
        self.view.redraw(force=True)

    def toggle_transform_panel(self):
        if self._panel is not None and self._panel.winfo_exists():
            self._panel.on_close()
            return
        self._panel = TransformPanel(self)
        self._panel.protocol("WM_DELETE_WINDOW", self._panel.on_close)
        self._place_panel()
        self._panel.refresh()

    def _place_panel(self):
        """面板默认贴在视口右侧，别挡住模型。"""
        try:
            self.update_idletasks()
            w = self._panel.winfo_reqwidth()
            vx = self.view.winfo_rootx() + self.view.winfo_width() - w - self.px(24)
            vy = self.view.winfo_rooty() + self.px(16)
            self._panel.geometry("+{}+{}".format(int(vx), int(vy)))
        except Exception:  # noqa: BLE001
            pass

    def refresh_transform_panel(self):
        p = getattr(self, "_panel", None)
        if p is not None and p.winfo_exists():
            try:
                p.refresh()
            except Exception:  # noqa: BLE001
                pass

    def update_locked_axes(self):
        """面板上的锁 -> 视口 gizmo 隐藏对应轴。"""
        p = getattr(self, "_panel", None)
        locked = p.locked_axes() if (p is not None and p.winfo_exists()) else set()
        self.view.locked_axes = locked
        self.view.redraw(force=True)

    def _sync_pivot(self):
        v = getattr(self, "view", None)
        if v is None:
            return
        # 选中 AABB（且没选部件）时旋转 gizmo 禁用
        v.gz_no_rotate = (getattr(self, "sel_id", None) is None
                          and getattr(self, "aabb_sel", None) is not None)
        # AABB 被选中时（且没有选部件），支点放到框中心
        ai = getattr(self, "aabb_sel", None)
        if getattr(self, "sel_id", None) is None and ai is not None:
            items = getattr(self, "_aabb_items", [])
            if 0 <= ai < len(items):
                mn = np.asarray(items[ai][1], float)
                mx = np.asarray(items[ai][2], float)
                v.pivot = (mn + mx) * 0.5
                return
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is None:
            v.pivot = None
            return
        # 一定要现算：_world 是缓存，分离/撤销/改层级后可能还是旧的，
        # 用它会导致 gizmo 停在原地不跟随部件
        M = self._world_of(o.id)
        v.pivot = np.asarray(M[:3, 3], dtype=float).copy()

    # ---- 矩阵 <-> TRS
    @staticmethod
    def _euler_mat(deg):
        x, y, z = [math.radians(float(v)) for v in deg]
        cx, sx = math.cos(x), math.sin(x)
        cy, sy = math.cos(y), math.sin(y)
        cz, sz = math.cos(z), math.sin(z)
        Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], float)
        Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], float)
        Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], float)
        return Rz @ Ry @ Rx

    @staticmethod
    def _mat_euler(R):
        """3x3 -> XYZ 欧拉角（度），与 _euler_mat 互逆。"""
        sy = max(-1.0, min(1.0, -R[2, 0]))
        y = math.asin(sy)
        if abs(sy) < 0.999999:
            x = math.atan2(R[2, 1], R[2, 2])
            z = math.atan2(R[1, 0], R[0, 0])
        else:
            x = math.atan2(-R[1, 2], R[1, 1])
            z = 0.0
        return np.degrees([x, y, z])

    @staticmethod
    def _axis_rot(axis, theta):
        x, y, z = axis
        c, s = math.cos(theta), math.sin(theta)
        C = 1.0 - c
        return np.array([
            [c + x*x*C,     x*y*C - z*s, x*z*C + y*s],
            [y*x*C + z*s,   c + y*y*C,   y*z*C - x*s],
            [z*x*C - y*s,   z*y*C + x*s, c + z*z*C],
        ], float)

    def _trs_of(self, oid):
        M = self._xform.get(oid)
        if M is None:
            return None
        M = np.asarray(M, float)
        loc = M[:3, 3].copy()
        R = M[:3, :3].copy()
        scl = np.array([np.linalg.norm(R[:, i]) for i in range(3)])
        for i in range(3):
            if scl[i] < 1e-9:
                scl[i] = 1.0
        Rn = R / scl
        if np.linalg.det(Rn) < 0:
            scl[0] = -scl[0]
            Rn[:, 0] = -Rn[:, 0]
        return loc, self._mat_euler(Rn), scl

    def _mat_from_trs(self, loc, eul, scl):
        M = np.eye(4)
        M[:3, :3] = self._euler_mat(eul) @ np.diag(np.asarray(scl, float))
        M[:3, 3] = np.asarray(loc, float)
        return M

    def _dims_of(self, oid):
        v = getattr(self, "_obj_local", {}).get(oid)
        M = getattr(self, "_world", {}).get(oid)
        if v is None or M is None or len(v) == 0:
            return np.zeros(3)
        vw = np.asarray(v, float) @ M[:3, :3].T + M[:3, 3]
        return vw.max(axis=0) - vw.min(axis=0)

    def set_transform_value(self, grp, axis, value):
        """面板里改数字：写回该部件的局部矩阵。"""
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is None:
            return
        trs = self._trs_of(o.id)
        if trs is None:
            return
        loc, eul, scl = [np.array(x, float) for x in trs]
        if grp == "loc":
            loc[axis] = value
        elif grp == "rot":
            eul[axis] = value
        elif grp == "scl":
            if abs(value) < 1e-4:
                value = 1e-4 if value >= 0 else -1e-4
            scl[axis] = value
        elif grp == "dim":
            cur = self._dims_of(o.id)
            if cur is None:
                return
            cur_dim = max(float(cur[axis]), 1e-6)
            if abs(value) < 1e-4:
                value = 1e-4
            scl[axis] = scl[axis] * (float(value) / cur_dim)
        else:
            return
        M = self._mat_from_trs(loc, eul, scl)
        oldL = np.asarray(self._xform.get(o.id), float)
        if np.allclose(M, oldL):
            return
        self._push_undo("面板改{}".format(
            {"loc": "位置", "rot": "旋转", "scl": "缩放", "dim": "尺寸"}[grp]))
        if self._origin_only_on():
            self._origin_only_compensate(o.id, oldL, M)
        self._xform[o.id] = M
        self._apply_transforms(ids=[o.id] + self._descendants(o.id))
        if self.view.ready:
            try:
                self.view._rebuild_outline()
            except Exception:  # noqa: BLE001
                pass

    def _descendants(self, oid):
        by_id = self._obj_by_id_map()
        out, stack = [], [oid]
        while stack:
            o = by_id.get(stack.pop())
            if o is None:
                continue
            for c in o.children_ids:
                if c in by_id and c not in out:
                    out.append(c)
                    stack.append(c)
        return out

    def _parent_rot(self, o):
        pid = getattr(self, "_parent", {}).get(o.id)
        if pid is None:
            return np.eye(3)
        M = getattr(self, "_world", {}).get(pid)
        if M is None:
            M = self._world_of(pid)
        R = np.asarray(M[:3, :3], float)
        for c in range(3):
            n = np.linalg.norm(R[:, c])
            if n > 1e-9:
                R[:, c] = R[:, c] / n
        return R

    def _apply_transforms(self, ids=None, redraw=True):
        """按 _xform 重算世界矩阵，并把受影响部件的顶点刷进 GPU。"""
        if not self.scene or not hasattr(self, "_xform"):
            return
        objs = self.scene.objects
        by_id = {o.id: o for o in objs}
        self._world = {o.id: self._world_of(o.id) for o in objs}
        if ids is None:
            ids = list(getattr(self, "_obj_local", {}).keys())
        idx_of = {}
        for i, it in enumerate(self.view.meshes):
            idx_of.setdefault(it[0], i)
        for oid in ids:
            o = by_id.get(oid)
            v = getattr(self, "_obj_local", {}).get(oid)
            if o is None or v is None:
                continue
            M = self._world.get(oid)
            if M is None:
                continue
            vw = np.asarray(v, float) @ M[:3, :3].T + M[:3, 3]
            i = idx_of.get(o.name)
            if i is not None:
                it = self.view.meshes[i]
                self.view.meshes[i] = (it[0], vw, it[2], it[3], it[4], it[5])
                self.view.update_mesh_verts(o.name, vw, M[:3, :3])
            for j, ai in enumerate(getattr(self, "_all_meshes", [])):
                if ai[0] == o.name:
                    self._all_meshes[j] = (ai[0], vw, ai[2], ai[3], ai[4])
                    break
        # 定位器跟着父链走
        locs = []
        moved = False
        for item in getattr(self, "_all_locators", []):
            M = self._world.get(item[3])
            if M is None:
                locs.append(item)
                continue
            locs.append((item[0], M[:3, 3].copy(), M[:3, :3].copy(), item[3]))
            moved = True
        if moved:
            self._all_locators = locs
            self.view.locators = list(locs)
            if self.view.ready:
                try:
                    self.view._upload_locators()
                except Exception:  # noqa: BLE001
                    pass
        self._sync_pivot()
        if redraw:
            self.view._last_sig = None
            self.view.redraw(force=True)
        self.refresh_transform_panel()

    # ---- 撤销：整份文档快照（Blender 的思路，不只是变换）
    def _push_undo(self, label=""):
        """快照：变换 + 层级 + 限制 + 每块网格的顶点/面/loops + 对象列表 + AABB。

        数组一律按引用存：所有改动都是「整块换新数组」，不会原地改老数据，
        所以浅拷贝就够，内存也只有几个 MB。
        """

        objs = list(self.scene.objects) if self.scene else []
        xf = dict(getattr(self, "_xform", {}))
        lo = dict(getattr(self, "_obj_local", {}))
        par = dict(getattr(self, "_parent", {}))
        kids = {o.id: list(o.children_ids) for o in objs}
        lim = {o.id: dict(getattr(o, "limits", {}) or {}) for o in objs}
        mesh = {}
        for o in objs:
            d = getattr(o, "data", None)
            if d is not None:
                mesh[o.id] = (d.vertices, d.faces,
                              getattr(d, "loops", None),
                              getattr(d, "normals", None),
                              getattr(d, "uvs", None))
        ab = None
        if self.scene is not None:
            ab = (getattr(self.scene, "aabb", None),
                  getattr(self.scene, "aabb_as", None),
                  getattr(self.scene, "aabb_ds", None),
                  [(getattr(d, "aabb_a", -1), getattr(d, "aabb_d", -1))
                   for d in (getattr(self.scene, "anim_descs", []) or [])])
        stack = getattr(self, "_undo", None)
        if stack is None:
            stack = []
        stack.append((xf, lo, par, kids, lim, mesh, objs, ab, label))
        try:
            limit = int(self.prefs.get("undo_max", 32))
        except Exception:  # noqa: BLE001
            limit = 32
        limit = max(1, min(500, limit))
        del stack[:-limit]
        self._undo = stack

    def undo(self):
        stack = getattr(self, "_undo", None)
        if not stack:
            self.set_status("没有可撤销的操作")
            return
        xf, lo, par, kids, lim, mesh, objs, ab, label = stack.pop()
        self._xform = dict(xf or {})
        self._obj_local = dict(lo or {})
        if par is not None:
            self._parent = dict(par)
        if self.scene is not None and objs is not None:
            self.scene.objects = list(objs)      # 分离/删除新增的对象要能回滚
        for o in (self.scene.objects if self.scene else []):
            if o.id in (kids or {}):
                o.children_ids = list(kids[o.id])
            if o.id in (lim or {}):
                o.limits = dict(lim[o.id])
            m = (mesh or {}).get(o.id)
            if m is not None and getattr(o, "data", None) is not None:
                d = o.data
                d.vertices, d.faces, d.loops, d.normals, d.uvs = m
                if d.faces is None:
                    d.faces = np.empty((0, 3), np.int64)
        if self.scene is not None and ab is not None:
            self.scene.aabb, self.scene.aabb_as, self.scene.aabb_ds, refs = ab
            for d, (aa, ad) in zip(getattr(self.scene, "anim_descs", []) or [], refs):
                d.aabb_a, d.aabb_d = aa, ad
        # 拓扑可能变了：定位器 / 网格 / AABB / 大纲全部重建
        self._rebuild_locators()
        self._rebuild_meshes_from_scene()
        prev_on = self._aabb_on_labels()
        self._aabb_items = self._collect_aabbs()
        self._apply_aabb_on_labels(prev_on)
        self.aabb_sel = None
        self._apply_aabbs()
        self._rebuild_rows()
        self._draw_outline()
        self._sync_pivot()
        self.refresh_transform_panel()
        # 支点是在重建网格之后才同步的，必须再画一帧，否则 gizmo 要等下次操作才动
        if self.view.ready:
            self.view._last_sig = None
            self.view.redraw(force=True)
        self.set_status("已撤销" + ("：" + label if label else ""))

    # ---- gizmo 回调
    def _on_gizmo_start(self):
        if getattr(self, "_anim_frame", -1) >= 0:
            # 动画只做预览（只读）：不改任何数据，也不让编辑落在动画姿态上
            self.set_status("动画预览是只读的：点时间线右侧 ✕ 退出预览后再编辑")
            return
        self._gz_aabb = None
        self._gz_group = []
        # AABB 优先：选中框时 G/S 作用在框上（旋转禁用：框没有旋转字段）
        if getattr(self, "sel_id", None) is None and getattr(self, "aabb_sel", None) is not None:
            items = getattr(self, "_aabb_items", [])
            ai = self.aabb_sel
            if 0 <= ai < len(items) and self.tool != "rotate":
                self._gz_aabb = (ai, items[ai][0],
                                 np.asarray(items[ai][1], float).copy(),
                                 np.asarray(items[ai][2], float).copy())
                self._gz_label = {"move": "移动", "rotate": "旋转",
                                  "scale": "缩放"}.get(self.tool, "变换")
                self._gz_undo_done = False
                self._gz_oid = None
                self._gz_children = []
                return
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is None:
            return
        # ---- 多选 + 父子级（照 Blender 的做法）
        # Blender 只在「选中集里的层级根」上做变换，子孙跟着父级走，
        # 所以不会出现「父级移动一次、子级自己又移动一次」的叠加。
        sel = set(getattr(self, "sel_ids", set()) or set())
        parent = getattr(self, "_parent", {})

        def top_selected_ancestor(oid):
            """返回最上面的“被选中的祖先”，没有就 None。"""
            best = None
            p = parent.get(oid)
            guard = 0
            while p is not None and guard < 64:
                if p in sel:
                    best = p
                p = parent.get(p)
                guard += 1
            return best

        # 活动对象若有被选中的祖先，改由那个祖先（的层级根）来驱动
        target_id = top_selected_ancestor(o.id) or o.id
        o = self._obj_by_id_map().get(target_id) or o
        self._gz_label = {"move": "移动", "rotate": "旋转", "scale": "缩放"}.get(self.tool, "变换")
        self._gz_undo_done = False
        self._gz_oid = o.id
        self._gz_M0 = np.array(self._xform.get(o.id, self._base_mat(o)), float)
        self._gz_children = self._descendants(o.id)
        self._gz_W0 = self._world_of(o.id)
        pid = parent.get(o.id)
        self._gz_Wp = self._world_of(pid) if pid is not None else np.eye(4)
        # 其余“层级根”一起吃同一份世界增量；它们内部的孩子自动跟随
        self._gz_group = []
        by_id = self._obj_by_id_map()
        for oid2 in sel:
            if oid2 == o.id:
                continue
            if top_selected_ancestor(oid2) is not None:
                continue                     # 有被选中的祖先 → 由祖先带着走
            o2 = by_id.get(oid2)
            if o2 is None:
                continue
            W0 = self._world_of(oid2)
            pid2 = parent.get(oid2)
            Wp = self._world_of(pid2) if pid2 is not None else np.eye(4)
            try:
                pinv = np.linalg.inv(Wp)
            except np.linalg.LinAlgError:
                continue
            self._gz_group.append((oid2, W0, pinv,
                                   np.array(self._xform.get(oid2), float)))

    def _gz_apply_group(self, M):
        """把主动对象这次的世界增量套到其它选中对象上。"""
        grp = getattr(self, "_gz_group", None)
        if not grp:
            return []
        try:
            Dw = (self._gz_Wp @ np.asarray(M, float)) @ np.linalg.inv(self._gz_W0)
        except np.linalg.LinAlgError:
            return []
        ids = []
        for gid, Gw0, Gpinv, Lold in grp:
            newL = Gpinv @ Dw @ Gw0
            if self._origin_only_on():
                self._origin_only_compensate(gid, Lold, newL)
            self._xform[gid] = newL
            ids.append(gid)
            ids.extend(self._descendants(gid))
        return ids

    # ---- AABB 单面拖动（视图里的面手柄）
    def _on_aabb_face_start(self):
        self._push_undo("移动 AABB 面")

    def _on_aabb_face(self, axis, sign, value):
        ai = getattr(self, "aabb_sel", None)
        items = getattr(self, "_aabb_items", [])
        if ai is None or not (0 <= ai < len(items)):
            return
        mn = np.asarray(items[ai][1], float).copy()
        mx = np.asarray(items[ai][2], float).copy()
        if sign > 0:
            mx[axis] = max(float(value), mn[axis] + 0.01)
        else:
            mn[axis] = min(float(value), mx[axis] - 0.01)
        self._set_aabb_box(ai, mn, mx)

    def _on_aabb_face_end(self):
        try:
            self._draw_outline()
            self._sync_pivot()
            self.refresh_transform_panel()
            self.view.redraw(force=True)
        except Exception:  # noqa: BLE001
            pass
        ai = getattr(self, "aabb_sel", None)
        items = getattr(self, "_aabb_items", [])
        if ai is not None and 0 <= ai < len(items):
            it = items[ai]
            d = np.asarray(it[2], float) - np.asarray(it[1], float)
            self.set_status("{}：尺寸 ({:.3f}, {:.3f}, {:.3f})".format(
                it[0], d[0], d[1], d[2]))

    def _gz_aabb_update(self, kind, axis, value):
        """G/S 作用在 AABB 上：移动中心 / 缩放半尺寸。
        旋转禁用：.mod 的 AABB 只有中心+半尺寸，没有旋转字段。"""
        if kind == "rotate":
            return
        ai, label, mn0, mx0 = self._gz_aabb
        if not getattr(self, "_gz_undo_done", True):
            self._gz_undo_done = True
            self._push_undo(getattr(self, "_gz_label", "变换") + " AABB")
        c0 = (mn0 + mx0) * 0.5
        h0 = (mx0 - mn0) * 0.5
        if kind == "move":
            c = c0 + np.asarray(value, float)
            h = h0
        elif kind == "rotate":
            ax = np.asarray(axis, float)
            n = float(np.linalg.norm(ax))
            if n < 1e-9:
                return
            R = self._axis_rot(ax / n, float(value))
            corners = np.array([[h0[0] if i & 1 else -h0[0],
                                 h0[1] if i & 2 else -h0[1],
                                 h0[2] if i & 4 else -h0[2]] for i in range(8)])
            rc = corners @ R.T
            c = c0                              # 绕自身中心转
            h = (rc.max(axis=0) - rc.min(axis=0)) * 0.5
        else:
            s = np.asarray(value, float)
            c = c0
            h = np.abs(h0 * s)
        self._set_aabb_box(ai, c - h, c + h)

    def _set_aabb_box(self, ai, mn, mx):
        """把 (min, max) 写回 .mod 的 AABB 数据，并刷新大纲/视口。"""
        items = getattr(self, "_aabb_items", [])
        if self.scene is None or not (0 <= ai < len(items)):
            return
        label = items[ai][0]
        mn = np.asarray(mn, float)
        mx = np.asarray(mx, float)
        center = (mn + mx) * 0.5
        half = np.maximum((mx - mn) * 0.5, 1e-4)      # 别缩成 0
        six = np.concatenate([center, half])
        if label.startswith("AABB 模型"):
            self.scene.aabb = six
        else:
            tag, _sp, anim = label.partition(" ")
            field = {"AABB_A": "aabb_a", "AABB_D": "aabb_d"}.get(tag.strip())
            if field:
                arr = getattr(self.scene,
                              "aabb_as" if field == "aabb_a" else "aabb_ds", None)
                for d in getattr(self.scene, "anim_descs", []) or []:
                    if d.name != anim:
                        continue
                    idx = getattr(d, field, -1)
                    if arr is not None and idx is not None and 0 <= idx < len(arr):
                        # 拷贝写回，别原地改（撤销快照按引用存着）
                        new_arr = np.array(arr, copy=True)
                        new_arr[int(idx)] = six
                        if field == "aabb_a":
                            self.scene.aabb_as = new_arr
                        else:
                            self.scene.aabb_ds = new_arr
                    break
        prev_on = self._aabb_on_labels()
        self._aabb_items = self._collect_aabbs()
        self.aabb_sel = None
        for i, it in enumerate(self._aabb_items):
            if it[0] == label:
                self.aabb_sel = i
                break
        self._apply_aabb_on_labels(prev_on, also=label)
        self._apply_aabbs()
        # 面拖动时每个事件都会走到这里：大纲/面板/整帧重绘留到松手时再做
        if getattr(self.view, "_aabb_face_drag", None) is None:
            self._draw_outline()
            self._sync_pivot()
            self.refresh_transform_panel()   # 面板里的中心/尺寸跟着变
            self.view.redraw(force=True)

    def _on_gizmo(self, kind, axis, value):
        if getattr(self, "_gz_aabb", None) is not None:
            self._gz_aabb_update(kind, axis, value)
            return
        oid = getattr(self, "_gz_oid", None)
        if oid is None or not hasattr(self, "_gz_M0"):
            return
        o = self._obj_by_id_map().get(oid)
        if o is None:
            return
        if not getattr(self, "_gz_undo_done", True):
            self._gz_undo_done = True
            self._push_undo(getattr(self, "_gz_label", "变换"))
        M0 = self._gz_M0
        Ri = np.linalg.inv(self._parent_rot(o))
        M = np.array(M0, float)
        if kind == "move":
            off = Ri @ np.asarray(value, float)
            M[:3, 3] = M0[:3, 3] + off
        elif kind == "rotate":
            a = Ri @ np.asarray(axis, float)
            n = np.linalg.norm(a)
            if n < 1e-9:
                return
            M[:3, :3] = self._axis_rot(a / n, float(value)) @ M0[:3, :3]
            M[:3, 3] = M0[:3, 3]
        else:
            s = np.asarray(value, float)
            if np.allclose(s, s[0]):
                M[:3, :3] = M0[:3, :3] * float(s[0])
            else:
                S = np.eye(3)
                for i in range(3):
                    if abs(s[i] - 1.0) > 1e-9:
                        e = np.zeros(3)
                        e[i] = 1.0
                        al = Ri @ e
                        S = S + (s[i] - 1.0) * np.outer(al, al)
                M[:3, :3] = S @ M0[:3, :3]
            M[:3, 3] = M0[:3, 3]
        oldL = np.asarray(self._xform.get(oid, M0), float)
        if self._origin_only_on():
            self._origin_only_compensate(oid, oldL, M)
        self._xform[oid] = M
        ids = [oid] + getattr(self, "_gz_children", [])
        ids += self._gz_apply_group(M)         # 其余选中对象一起变
        self._apply_transforms(ids=ids)

    def _on_gizmo_end(self):
        if getattr(self, "_gz_aabb", None) is not None:
            self._gz_aabb = None
            items = getattr(self, "_aabb_items", [])
            i = getattr(self, "aabb_sel", None)
            if i is not None and 0 <= i < len(items):
                label, mn, mx = items[i][0], items[i][1], items[i][2]
                c = (np.asarray(mn) + np.asarray(mx)) * 0.5
                d = np.asarray(mx) - np.asarray(mn)
                self.set_status("{}：中心 ({:.3f}, {:.3f}, {:.3f})  尺寸 ({:.3f}, "
                                "{:.3f}, {:.3f})".format(label, c[0], c[1], c[2],
                                                         d[0], d[1], d[2]))
            self.view._last_sig = None
            self.view.redraw(force=True)
            return
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is not None and self.view.ready:
            try:
                self.view._rebuild_outline()   # 描边外壳是按旧顶点建的吗，重建一次
            except Exception:  # noqa: BLE001
                pass
        self._sync_pivot()          # 支点先同步（仅改原点时会移动），再重画
        self.view._last_sig = None
        self.view.redraw(force=True)
        self.refresh_transform_panel()
        # 变换完成后主 AABB 跟着重算，否则旋转/缩放部件后框还是旧的
        self._refresh_model_aabb_from_world()
        if o is not None:
            trs = self._trs_of(o.id)
            if trs:
                loc, eul, scl = trs
                self.set_status("{}   位置 ({:.3f}, {:.3f}, {:.3f})   缩放 ({:.2f}, {:.2f}, {:.2f})".format(
                    o.name, loc[0], loc[1], loc[2], scl[0], scl[1], scl[2]))

    # ---- 右键菜单 / 应用变换
    def _viewport_menu(self, x, y):
        self._rename_cancel()              # 在视口右键也要把大纲的改名框收掉
        m = tk.Menu(self, tearoff=0)
        self._fill_part_menu(m)
        try:
            m.tk_popup(int(x), int(y))
        finally:
            m.grab_release()

    def _fill_part_menu(self, m):
        """把「变换 / 中心点 / 部件 / 工具」这些项填进给定菜单（视口和大纲共用）。"""
        sel = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        has_mesh = (sel is not None and sel.id in getattr(self, "_obj_local", {}))
        sub = tk.Menu(m, tearoff=0)
        for label, what in (("应用位置", "loc"), ("应用旋转", "rot"),
                            ("应用缩放", "scale")):
            sub.add_command(label=label, command=lambda w=what: self.apply_transform(w),
                            state="normal" if has_mesh else "disabled")
        sub.add_separator()
        sub.add_command(label="应用全部变换", command=lambda: self.apply_transform("all"),
                        state="normal" if has_mesh else "disabled")
        m.add_cascade(label="应用变换", menu=sub)

        sub2 = tk.Menu(m, tearoff=0)
        for label, what in (("位置归零", "loc"), ("旋转归零", "rot"),
                            ("缩放归 1", "scale"), ("全部重置", "all")):
            sub2.add_command(label=label, command=lambda w=what: self.reset_transform(w),
                             state="normal" if sel is not None else "disabled")
        m.add_cascade(label="重置变换", menu=sub2)

        # 原点（中心点）挪到几何中心：网格在世界里不动，只换旋转/缩放中心
        sub3 = tk.Menu(m, tearoff=0)
        sub3.add_command(label="几何中心（顶点平均）",
                         command=lambda: self.origin_to_geometry("mean"),
                         state="normal" if has_mesh else "disabled")
        sub3.add_command(label="包围盒中心",
                         command=lambda: self.origin_to_geometry("bounds"),
                         state="normal" if has_mesh else "disabled")
        m.add_cascade(label="中心点设为", menu=sub3)

        sub4 = tk.Menu(m, tearoff=0)
        on = "normal" if sel is not None else "disabled"
        sub4.add_command(label="设置范围（旋转 / 位移）…", command=self.dlg_rotation_limit, state=on)
        sub4.add_command(label="清除限制", command=self.clear_limit, state=on)
        sub4.add_separator()
        sub4.add_command(label="生成 AABB（选中部件）", command=lambda: self.make_aabb(True))
        sub4.add_command(label="生成 AABB（全部网格）", command=lambda: self.make_aabb(False))
        sub4.add_separator()
        sub4.add_command(label="生成定位器…", command=self.dlg_locator, state=on)
        sub4.add_command(label="批量生成定位器",
                         command=lambda: self.add_all_locators("CENTER"),
                         state="normal" if has_mesh else "disabled")
        sub4.add_command(label="删除选中部件（网格/定位器）", command=self.delete_selected,
                         state=on)
        sub4.add_command(label="只删定位器", command=self.remove_selected_locators, state=on)
        sub4.add_command(label="删除选中 AABB", command=self.delete_selected_aabb)
        sub4.add_separator()
        sub4.add_command(label="平台 / 武器（XML）…", command=self.dlg_platforms)
        sub4.add_command(label="合并网格    Ctrl+J", command=self.join_meshes,
                         state="normal" if has_mesh else "disabled")
        n_face = len(self._face_sel["tris"]) if getattr(self, "_face_sel", None) else 0
        sub4.add_command(
            label=("分离选中面（{} 个面）    P".format(n_face) if n_face
                   else "分离选中面（先用相连面工具选面）    P"),
            command=self.separate_faces,
            state="normal" if n_face else "disabled")
        sub4.add_command(label="按松散块分离    Shift+P", command=self.separate_loose,
                         state="normal" if has_mesh else "disabled")
        sub4.add_command(label="删除选中项    Del", command=self.delete_selected)
        m.add_cascade(label="部件", menu=sub4)

        m.add_separator()
        m.add_checkbutton(label="仅对原点生效（网格不动）", variable=self.origin_only)
        if hasattr(self, "ortho_var"):
            m.add_checkbutton(label="正交视图", variable=self.ortho_var,
                              command=self.toggle_ortho)
        for label, tool, key in (("移动工具", "move", "G"), ("旋转工具", "rotate", "R"),
                                 ("缩放工具", "scale", "S")):
            m.add_command(label="{}    {}".format(label, key),
                          command=lambda t=tool: self.set_tool(t))
        m.add_command(label="选择工具    Esc", command=lambda: self.set_tool("select"))
        m.add_separator()
        m.add_command(label="变换面板    N", command=self.toggle_transform_panel)

    def apply_transform(self, what="all"):
        """把当前变换烘进网格顶点，并把对象变换复位（Blender 的 Ctrl+A）。"""
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is None or o.id not in getattr(self, "_obj_local", {}):
            self.set_status("先选一个网格部件")
            return
        L = np.asarray(self._xform.get(o.id), float)
        loc = L[:3, 3].copy()
        R = L[:3, :3].copy()
        scl = np.array([np.linalg.norm(R[:, i]) for i in range(3)])
        for i in range(3):
            if scl[i] < 1e-9:
                scl[i] = 1.0
        Rn = R / scl
        T = np.eye(4)
        T[:3, 3] = loc
        Rm = np.eye(4)
        Rm[:3, :3] = Rn
        Sm = np.eye(4)
        Sm[:3, :3] = np.diag(scl)
        if what == "loc":
            bake, newL = T, Rm @ Sm
        elif what == "rot":
            bake, newL = Rm, T @ Sm
        elif what == "scale":
            bake, newL = Sm, T @ Rm
        else:
            bake, newL = L, np.eye(4)
        self._push_undo("应用变换")
        v = self._obj_local[o.id]
        self._obj_local[o.id] = np.asarray(v, float) @ bake[:3, :3].T + bake[:3, 3]
        comp = np.linalg.inv(newL) @ L
        for cid in o.children_ids:
            if cid in self._xform:
                self._xform[cid] = comp @ self._xform[cid]
        self._xform[o.id] = newL
        self._apply_transforms()
        if self.view.ready:
            try:
                self.view._rebuild_outline()
            except Exception:  # noqa: BLE001
                pass
        names = {"loc": "位置", "rot": "旋转", "scale": "缩放", "all": "全部变换"}
        self.set_status("{}：已应用{}（顶点已烘焙，对象变换已复位）".format(
            o.name, names.get(what, "")))

    def _origin_only_on(self):
        try:
            return bool(self.origin_only.get())
        except Exception:  # noqa: BLE001
            return False

    def _origin_only_compensate(self, oid, oldL, newL):
        """「仅对原点生效」：把顶点和子级反向补偿，网格与子级在世界里不动。

        局部矩阵从 oldL 变到 newL 时，comp = inv(newL)·oldL 就是"抵消量"：
          顶点：v' = comp·v        -> newL·v' = oldL·v（世界坐标不变）
          子级：Lc' = comp·Lc      -> newL·Lc' = oldL·Lc（子级世界矩阵不变）
        每次都按"当前状态"补偿，多次拖动会自然抵消，不会累积漂移。
        """
        try:
            comp = np.linalg.inv(np.asarray(newL, float)) @ np.asarray(oldL, float)
        except np.linalg.LinAlgError:
            return
        v = getattr(self, "_obj_local", {}).get(oid)
        if v is not None:
            self._obj_local[oid] = np.asarray(v, float) @ comp[:3, :3].T + comp[:3, 3]
        o = self._obj_by_id_map().get(oid)
        if o is not None:
            for cid in list(o.children_ids):
                Lc = getattr(self, "_xform", {}).get(cid)
                if Lc is not None:
                    self._xform[cid] = comp @ np.asarray(Lc, float)

    def origin_to_geometry(self, mode="mean"):
        """把对象原点挪到几何中心（Blender 的 Origin to Geometry）。

        做法：局部顶点整体平移 -c，原点平移 +L·c。
        两者相乘后世界坐标完全不变，只是旋转/缩放/gizmo 的支点换到了几何中心。
        """
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is None or o.id not in getattr(self, "_obj_local", {}):
            self.set_status("先选一个网格部件")
            return
        v = np.asarray(self._obj_local[o.id], float)
        if len(v) == 0:
            self.set_status("{} 没有顶点".format(o.name))
            return
        if mode == "bounds":                       # 包围盒中心
            c = (v.min(axis=0) + v.max(axis=0)) / 2.0
        else:                                      # 顶点平均（Blender 的重心）
            c = v.mean(axis=0)
        if float(np.abs(c).max()) < 1e-9:
            self.set_status("{} 的原点已经在几何中心".format(o.name))
            return

        L = np.asarray(self._xform.get(o.id, self._base_mat(o)), float)
        self._push_undo("原点移到几何中心")
        self._obj_local[o.id] = v - c
        Ln = np.array(L, copy=True)
        Ln[:3, 3] = L[:3, 3] + L[:3, :3] @ c
        self._xform[o.id] = Ln
        # 父级的局部平移变了，子级的局部矩阵要反向补偿，否则子级会跟着漂
        kids = [cid for cid in o.children_ids if cid in getattr(self, "_xform", {})]
        for cid in kids:
            Lc = np.array(self._xform[cid], copy=True)
            Lc[:3, 3] = Lc[:3, 3] - c
            self._xform[cid] = Lc
        self._apply_transforms(ids=[o.id] + self._descendants(o.id))
        self._sync_pivot()                         # gizmo 支点跟着挪
        if self.view.ready:
            try:
                self.view._rebuild_outline()
            except Exception:  # noqa: BLE001
                pass
            self.view.redraw(force=True)
        self.refresh_transform_panel()
        trs = self._trs_of(o.id)
        where = "包围盒中心" if mode == "bounds" else "几何中心"
        if trs:
            loc = trs[0]
            self.set_status("{}：原点已移到{} ({:.3f}, {:.3f}, {:.3f})｜网格位置不变".format(
                o.name, where, loc[0], loc[1], loc[2]))
        else:
            self.set_status("{}：原点已移到{}".format(o.name, where))

    def reset_transform(self, what="all"):
        o = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if o is None:
            return
        trs = self._trs_of(o.id)
        if trs is None:
            return
        loc, eul, scl = [np.array(x, float) for x in trs]
        if what in ("loc", "all"):
            loc = np.zeros(3)
        if what in ("rot", "all"):
            eul = np.zeros(3)
        if what in ("scale", "all"):
            scl = np.ones(3)
        self._push_undo("重置变换")
        oldL = np.asarray(self._xform.get(o.id, self._base_mat(o)), float)
        Mnew = self._mat_from_trs(loc, eul, scl)
        if self._origin_only_on():
            self._origin_only_compensate(o.id, oldL, Mnew)
        self._xform[o.id] = Mnew
        self._apply_transforms(ids=[o.id] + self._descendants(o.id))
        if self.view.ready:
            try:
                self.view._rebuild_outline()
            except Exception:  # noqa: BLE001
                pass
        self.set_status("{}：已重置{}".format(o.name, {"loc": "位置", "rot": "旋转",
                                                      "scale": "缩放"}.get(what, "变换")))

    def _on_ortho_changed(self, val):
        """视口里点坐标球改了投影方式，菜单勾选状态要跟着走。"""
        try:
            self.ortho_var.set(bool(val))
        except Exception:  # noqa: BLE001
            pass

    def toggle_ortho(self):
        self.view.ortho = bool(self.ortho_var.get())
        self.view._last_sig = None
        self.view.redraw(force=True)
        self.set_status("正交视图：开" if self.view.ortho else "正交视图：关（透视）")

    def toggle_grid(self):
        """View 菜单：基面网格开关。"""
        self.view.show_grid = bool(self.grid_var.get())
        self.view.redraw(force=True)

    def apply_quality(self):
        """高/中/低三档渲染质量。"""
        q = self.quality_var.get()
        if q == "high":
            self.view.quality = (1, 1.0, 1)      # 拖动也不降级
        elif q == "low":
            self.view.quality = (3, 0.03, 2)     # 拖动 1/3 + 只留 3% 面，静止也降 1/2
        else:
            self.view.quality = (2, 0.08, 1)     # 默认：拖动 1/2 + 留 8%
        self.view._last_sig = None
        self.view.redraw(force=True)

    def set_status(self, text):
        try:
            self.status.config(text=text)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- 打开
    def open_mod(self):
        p = filedialog.askopenfilename(
            title="Open Blitzkrieg MOD file",
            filetypes=[("Blitzkrieg model files", "*.mod"), ("All files", "*.*")])
        if not p:
            return
        self.open_mod_path(p, ask=True)

    def open_mod_path(self, p, ask=False):
        """按路径打开 .mod（菜单和拖放共用）。"""
        _install_mod_scene_fastpath()
        self.set_status("Loading {} ...".format(os.path.basename(p)))
        self.update_idletasks()
        try:
            # 不再缓存解析结果：缓存里存的是 ModScene 对象本身，编辑（限位/改名/分离…）
            # 会直接改到它，重新导入就会拿到「被改过的」场景而不是磁盘上的内容。
            s = ModScene()
            s.from_file(p)
            self.scene = s
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open failed",
                                 "Failed to parse .mod: " + str(exc))
            self.set_status("Ready")
            return

        self.scene = s
        self.mod_path = p
        # 动画默认不导入：解析器仍会读出来（AABB 名称要用），但保存时不写回
        self._anim_avail = len(getattr(s, "anim_descs", []) or [])
        self._anim_on = bool(self.prefs.get("import_anim", False))
        if ask and self._anim_avail and self.prefs.get("ask_import_anim", True):
            choice, remember = self._ask_anim_dialog(self._anim_avail)
            self._anim_on = bool(choice)
            if remember:
                self.prefs["import_anim"] = bool(choice)
                self.prefs["ask_import_anim"] = False
                self._prefs_save()
                if hasattr(self, "anim_var"):
                    try:
                        self.anim_var.set(bool(choice))
                    except Exception:  # noqa: BLE001
                        pass
        self._anim_sel = None
        self._anim_frame = -1
        self._anim_local = {}
        self.title("{}  -  {}".format(VERSION, os.path.basename(p)))

        # debug 报告开头标明本次会话是哪一个单位（优先取 xml 的 KeyName）
        try:
            from glviewport import dlog_banner
            base = os.path.splitext(os.path.basename(p))[0]
            unit = base
            xpath = os.path.join(os.path.dirname(p), base + ".xml")
            if not os.path.exists(xpath):
                try:
                    xs = sorted(f for f in os.listdir(os.path.dirname(p))
                                if f.lower().endswith(".xml"))
                    if xs:
                        xpath = os.path.join(os.path.dirname(p), xs[0])
                except OSError:
                    pass
            if os.path.exists(xpath):
                k = ET.parse(xpath).getroot().find(".//KeyName")
                if k is not None and k.text and k.text.strip():
                    unit = k.text.strip()
            dlog_banner("新会话 / 打开单位", [
                "单位: {}".format(unit),
                ".mod: {}".format(os.path.abspath(p)),
                "版本: {}".format(VERSION),
                "对象: {} 个（网格 {}）".format(
                    len(s.objects), sum(1 for o in s.objects if o.data is not None)),
            ])
        except Exception:  # noqa: BLE001
            pass

        self._load_meshes()
        self._load_textures()
        if self._anim_on and self._anim_avail:
            self._anim_show_bar()
        else:
            self._anim_hide_bar()

        n_mesh = sum(1 for o in s.objects if o.data is not None)
        n_loc = len(s.objects) - n_mesh
        anim_txt = ""
        if self._anim_avail:
            anim_txt = "  |  动画 {} 段{}".format(
                self._anim_avail, "" if self._anim_on else tr("（未导入）"))
        self.set_status("{}  |  对象 {} 个（网格 {} + 定位器 {}）{}".format(
            os.path.basename(p), len(s.objects), n_mesh, n_loc, anim_txt))

        # 平台/武器对话框开着的话，跟着换成新模型的 xml
        cb = getattr(self, "_plat_reload", None)
        if cb is not None:
            try:
                cb(silent=True)
            except Exception:  # noqa: BLE001
                self._plat_reload = None

    def open_obj_dialog(self):
        p = filedialog.askopenfilename(
            title="Open OBJ model",
            filetypes=[("Wavefront OBJ", "*.obj"), ("All files", "*.*")])
        if not p:
            return
        self.load_obj_preview(p)

    def _ask_obj_axes(self, def_fwd, def_up):
        """OBJ 导入轴向选择（仿 Blender 导入器），返回 (前进轴, 向上轴) 或 None。"""
        win = tk.Toplevel(self)
        win.title("OBJ 导入设置")
        win.configure(bg="#f0f0f0")
        win.transient(self)
        win.resizable(False, False)
        axes = ("X", "Y", "Z", "-X", "-Y", "-Z")
        frm = tk.Frame(win, bg="#f0f0f0")
        frm.pack(padx=14, pady=(10, 4))
        tk.Label(frm, text="前进轴", bg="#f0f0f0", anchor="w",
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w",
                                                      pady=3, padx=(0, 8))
        cb_fwd = ttk.Combobox(frm, values=list(axes), state="readonly", width=6,
                              font=("Microsoft YaHei UI", 9))
        cb_fwd.current(axes.index(def_fwd))
        cb_fwd.grid(row=0, column=1, pady=3)
        tk.Label(frm, text="向上轴", bg="#f0f0f0", anchor="w",
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w",
                                                      pady=3, padx=(0, 8))
        cb_up = ttk.Combobox(frm, values=list(axes), state="readonly", width=6,
                             font=("Microsoft YaHei UI", 9))
        cb_up.current(axes.index(def_up))
        cb_up.grid(row=1, column=1, pady=3)
        tk.Label(win, text="惯例 OBJ（Blender 导出）= 前进 -Z / 向上 Y；\n"
                           "modview 导出的 Z-up 文件 = 前进 -Y / 向上 Z",
                 bg="#f0f0f0", fg="#777777", justify="left",
                 font=("Microsoft YaHei UI", 8)).pack(padx=14, anchor="w")
        res = {"v": None}

        def ok():
            res["v"] = (cb_fwd.get(), cb_up.get())
            win.destroy()

        btns = tk.Frame(win, bg="#f0f0f0")
        btns.pack(pady=(4, 10))
        tk.Button(btns, text="确定", width=6, command=ok).pack(side="left", padx=4)
        tk.Button(btns, text="取消", width=6,
                  command=win.destroy).pack(side="left", padx=4)
        win.grab_set()
        win.wait_window()
        return res["v"]

    def load_obj_preview(self, p):
        """打开 .obj 做预览（含 MTL 贴图/材质色）。
        轴向仿 Blender 导入器：弹窗选前进轴/向上轴，默认按文件标记或 OBJ 惯例。"""
        self.set_status("Loading {} ...".format(os.path.basename(p)))
        self.update_idletasks()
        try:
            parts, tex_path, up_axis = parse_obj(p)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Open failed", "Failed to parse OBJ:\n{}".format(exc))
            self.set_status("Ready")
            return
        if not parts:
            messagebox.showerror("Open failed", "OBJ 里没有可显示的面")
            return

        def axis_vec(s):
            v = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}[s[-1]]
            v = np.asarray(v)
            return -v if s.startswith("-") else v

        if up_axis == "z":
            def_fwd, def_up = "-Y", "Z"       # modview 导出的 Z-up 文件
        else:
            def_fwd, def_up = "-Z", "Y"       # OBJ 惯例（Blender 默认导出）
        axes = self._ask_obj_axes(def_fwd, def_up)
        if axes is None:
            return
        fwd = axis_vec(axes[0])
        up = axis_vec(axes[1])
        right = np.cross(fwd, up)
        if np.linalg.norm(right) < 1e-6:
            messagebox.showerror("Open failed", "前进轴和向上轴不能平行")
            return
        # 文件坐标 -> 内部 Z-up：内部 X/Y/Z 基在文件系里的方向 = right/fwd/up
        rot = np.stack([right, fwd, up])

        from types import SimpleNamespace
        meshes = []
        objs = []
        for i, (name, v, f, col, uv) in enumerate(parts):
            v = v @ rot.T
            name = name or "part_{}".format(i + 1)
            meshes.append((name, v, f, col, uv))
            # 伪场景对象：复用大纲树 / 选中 / 眼睛 / AABB 整套逻辑
            objs.append(SimpleNamespace(
                id=i + 1, name=name, children_ids=[],
                data=SimpleNamespace(vertices=v, faces=f),
                location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0, 1.0)))

        self.scene = SimpleNamespace(objects=objs)
        self.mod_path = p
        self.title("{}  -  {}".format(VERSION, os.path.basename(p)))
        # OBJ 进来的部件没有节点变换，位置全在顶点里
        self._init_xform()
        self._world = {o.id: self._world_of(o.id) for o in objs}
        self._obj_local = {o.id: np.asarray(o.data.vertices, dtype=float) for o in objs}
        self._all_meshes = meshes
        self._all_locators = []
        self._aabb_items = []
        self.aabb_on = set()
        self.aabb_sel = None

        tex = None
        if tex_path:
            r = load_image(tex_path)
            if r:
                tw, th, rgb = r
                tex = _shrink_tex(np.frombuffer(rgb, np.uint8).reshape(th, tw, 3))
        self.view.texture = tex
        self.view.show_meshes = self.show_mesh.get() if hasattr(self, "show_mesh") else True
        self.view.show_locators = self.show_loc.get() if hasattr(self, "show_loc") else True
        self.view.set_meshes(meshes)
        self.view.set_locators([])
        self._apply_aabbs()
        self.hidden = set()
        self.sel_id = None
        self.sel_ids = set()
        self.expanded = set()
        self.view.set_hidden(set())
        self.view.set_highlight(None)
        self.solo_id = None
        self.view.solo = None
        self.view.mode = "solid"
        self._fill_list()
        self._load_xml()
        self._world = {o.id: self._world_of(o.id) for o in self.scene.objects}
        self._sync_pivot()
        self.refresh_transform_panel()
        self._load_textures()
        self.reset_view()
        self.set_status("{}  |  OBJ 预览：{} 个部件{}（前进 {} / 向上 {}）".format(
            os.path.basename(p), len(meshes),
            "，贴图 " + os.path.basename(tex_path) if tex_path else "",
            axes[0], axes[1]))

    # ---------------- 右侧 XML 面板
    def toggle_left_panel(self):
        """收起/展开左栏（显式状态位，不依赖 winfo_ismapped）。"""
        if getattr(self, "_left_open", True):
            self.left.pack_forget()
            self.splitter.pack_forget()
            self.lb_left_toggle.config(text="▶")
            self._left_open = False
        else:
            # after= 锁定 packing 顺序（左→右：收起条、面板、分隔条）
            self.left.pack(side="left", fill="y", after=self.left_bar)
            self.splitter.pack(side="left", fill="y", after=self.left)
            self.lb_left_toggle.config(text="◀")
            self._left_open = True
        self.update_idletasks()
        self.view.redraw(force=True)

    def toggle_xml_panel(self):
        """收起/展开右栏 XML 面板（显式状态位）。"""
        if getattr(self, "_xml_open", True):
            self.xml_panel.pack_forget()
            self.xml_splitter.pack_forget()
            self.lb_xml_toggle.config(text="◀")
            self._xml_open = False
        else:
            # 必须用 after= 锁定 packing 顺序（右→左：收缩条、面板、分隔条），
            # 否则重 pack 会追加到队尾，分隔条被挤到收缩条旁边
            self.xml_panel.pack(side="right", fill="y", after=self.xml_bar)
            self.xml_splitter.pack(side="right", fill="y", after=self.xml_panel)
            self.lb_xml_toggle.config(text="▶")
            self._xml_open = True
        self.update_idletasks()
        self.view.redraw(force=True)

    def _load_xml(self):
        """读同目录的 xml（优先同名），铺到右面板的树里。"""
        t = self.xml_tree
        if self._xml_dirty and self._xml_path:
            r = messagebox.askyesnocancel(
                "未保存的修改", "{} 有未保存的修改，要先保存吗？".format(
                    os.path.basename(self._xml_path)))
            if r is None:
                return
            if r:
                self._xml_save()
        self._xml_dirty = False
        self._xml_root = None
        self._xml_path = None
        self._xml_meta = {}
        self.xml_value.set("")
        self.lb_xml_hint.config(text="")
        t.delete(*t.get_children())
        if not self.mod_path:
            self.xml_title.config(text="未加载模型")
            return
        folder = os.path.dirname(self.mod_path)
        base = os.path.splitext(os.path.basename(self.mod_path))[0]
        path = None
        same = os.path.join(folder, base + ".xml")
        if os.path.exists(same):
            path = same
        else:
            try:
                xs = sorted(f for f in os.listdir(folder) if f.lower().endswith(".xml"))
            except OSError:
                xs = []
            if xs:
                path = os.path.join(folder, xs[0])
        if path is None:
            self.xml_title.config(text="同目录没有 xml")
            return
        try:
            root = ET.parse(path).getroot()
        except Exception as exc:  # noqa: BLE001
            self.xml_title.config(text="xml 解析失败")
            t.insert("", "end", text="解析失败: {}".format(exc))
            return

        self._xml_root = root
        self._xml_path = path
        self._xml_meta = {}
        self.xml_title.config(text=os.path.basename(path))
        q = (self.xml_search.get() if hasattr(self, "xml_search") else "").strip().lower()
        self._fill_xml_tree(q)

    def _fit_xml_column(self):
        """按最长的一行调整 #0 列宽，配合横向滚动条做到不截断。"""
        import tkinter.font as tkfont
        t = self.xml_tree
        fnt = tkfont.Font(root=self, family="Consolas", size=9)
        indent = self.px(14) + 10
        maxw = self.px(280)

        def measure(parent, depth):
            nonlocal maxw
            for iid in t.get_children(parent):
                w = fnt.measure(t.item(iid, "text")) + depth * indent + self.px(30)
                if w > maxw:
                    maxw = w
                measure(iid, depth + 1)

        measure("", 0)
        t.column("#0", width=maxw, stretch=False)

    def on_xml_search(self):
        """搜索框变化：按标签/属性名/中文注释过滤（中英文都能搜）。"""
        if not self._xml_root:
            return
        self._fill_xml_tree((self.xml_search.get() or "").strip().lower())

    @staticmethod
    def _xml_row_label(tag, azh, key, value):
        # tag 已经带好元素自己的中文括注，这里不要再包一层
        if azh:
            return "{}  {}（{}）: {}".format(tag, key, azh, value)
        return "{}  {}: {}".format(tag, key, value)

    def _fill_xml_tree(self, q=""):
        t = self.xml_tree
        t.delete(*t.get_children())
        self._xml_meta = {}
        if self._xml_root is None:
            return
        budget = [5000]

        def attr_label(tag, azh, k, v):
            return "{}（{}）: {}".format(k, azh, v) if azh else "{}: {}".format(k, v)

        def add_full(elem, parent, depth):
            if budget[0] <= 0:
                return
            zh = _xml_zh_tag(elem.tag)
            for ch, lbl in _xml_child_items(elem):
                if budget[0] <= 0:
                    return
                budget[0] -= 1
                chzh = _xml_zh_tag(ch.tag)
                text = lbl + ("（{}）".format(chzh) if chzh else "")
                node = t.insert(parent, "end", text=text, open=(depth < 2))
                for k, v in ch.attrib.items():
                    if budget[0] <= 0:
                        return
                    budget[0] -= 1
                    azh = _xml_zh_attr(ch.tag, k)
                    iid = t.insert(node, "end", text=attr_label(ch.tag, azh, k, v))
                    self._xml_meta[iid] = ("attr", ch, k)
                txt = ch.text
                if txt is not None and txt.strip():
                    budget[0] -= 1
                    iid = t.insert(node, "end",
                                   text="#text（文本内容）: {}".format(txt.strip()))
                    self._xml_meta[iid] = ("text", ch, None)
                add_full(ch, node, depth + 1)

        def add_filtered(elem, zh, path):
            """搜索模式：命中的元素/属性/文本平铺列出，带路径区分同名条目。"""
            if budget[0] <= 0:
                return
            tag_hit = (q in elem.tag.lower() or (zh and q in zh.lower()))
            attrs = []
            for k, v in elem.attrib.items():
                azh = _xml_zh_attr(elem.tag, k)
                if q in k.lower() or (azh and q in azh.lower()):
                    attrs.append((k, v, azh))
            txt = (elem.text or "").strip()
            txt_hit = bool(txt) and q in txt.lower()
            if not tag_hit and not attrs and not txt_hit:
                return
            budget[0] -= 1
            head = (path + " ▸ ") if path else ""
            if tag_hit:
                node = t.insert("", "end",
                                text=head + elem.tag + ("（{}）".format(zh) if zh else ""),
                                open=True)
                for k, v, azh in attrs:
                    if budget[0] <= 0:
                        return
                    budget[0] -= 1
                    iid = t.insert(node, "end", text=attr_label(elem.tag, azh, k, v))
                    self._xml_meta[iid] = ("attr", elem, k)
                if txt_hit:
                    iid = t.insert(node, "end",
                                   text="#text（文本内容）: {}".format(txt))
                    self._xml_meta[iid] = ("text", elem, None)
            else:
                for k, v, azh in attrs:
                    if budget[0] <= 0:
                        return
                    budget[0] -= 1
                    ez = elem.tag + ("（{}）".format(zh) if zh else "")
                    iid = t.insert("", "end",
                                   text="{} ▸ {}".format(head + ez,
                                                         attr_label(elem.tag, azh, k, v)))
                    self._xml_meta[iid] = ("attr", elem, k)
                if txt_hit:
                    iid = t.insert("", "end",
                                   text="{} ▸ #text（文本内容）: {}".format(head + ez, txt))
                    self._xml_meta[iid] = ("text", elem, None)

        def walk(elem, path):
            zh = _xml_zh_tag(elem.tag)
            add_filtered(elem, zh, path)
            for ch, lbl in _xml_child_items(elem):
                sub = (path + " ▸ " + lbl) if path else lbl
                walk(ch, sub)

        if not q:
            add_full(self._xml_root, "", 0)
            if budget[0] <= 0:
                t.insert("", "end", text="…（节点太多，已截断）")
        else:
            walk(self._xml_root, "")
            if budget[0] <= 0:
                t.insert("", "end", text="…（结果太多，已截断）")
            if not t.get_children():
                t.insert("", "end", text="（没有匹配的字段）")
        self._fit_xml_column()

    def _on_xml_select(self, _evt=None):
        sel = self.xml_tree.selection()
        meta = self._xml_meta.get(sel[0]) if sel else None
        if not meta:
            self.xml_value.set("")
            self.lb_xml_hint.config(text="选中属性或 #text 才能改")
            return
        kind, elem, key = meta
        if kind == "attr":
            self.xml_value.set(elem.get(key, ""))
            self.lb_xml_hint.config(text="<{}> 属性 {}".format(elem.tag, key))
        else:
            self.xml_value.set((elem.text or "").strip())
            self.lb_xml_hint.config(text="<{}> 文本".format(elem.tag))

    def _xml_apply(self):
        sel = self.xml_tree.selection()
        meta = self._xml_meta.get(sel[0]) if sel else None
        if not meta:
            self.set_status("先在 XML 树里选中一个属性或 #text")
            return
        kind, elem, key = meta
        val = self.xml_value.get()
        if kind == "attr":
            elem.set(key, val)
            self.xml_tree.item(sel[0], text="{}: {}".format(key, val))
        else:
            elem.text = val
            self.xml_tree.item(sel[0], text="#text: {}".format(val))
        self._xml_dirty = True
        self._refresh_xml_title()
        self.set_status("已修改（记得点保存）")

# 无 xml 时新建用的完整模板（USSR-T62 结构，KeyName 会被替换成新单位名）
    def _xml_create_new(self):
        """同目录没有 xml 时新建一个骨架文件并载入。返回是否成功。"""
        if not self.mod_path:
            return False
        folder = os.path.dirname(self.mod_path)
        base = os.path.splitext(os.path.basename(self.mod_path))[0]
        path = os.path.join(folder, base + ".xml")
        if os.path.exists(path):
            return False
        if not messagebox.askyesno(
                "平台编辑器",
                "同目录没有找到可编辑的 xml。" + chr(10) + chr(10)
                + "要在本目录新建 {} 吗？".format(base + ".xml")):
            return False
        try:
            nl = chr(10)
            crlf = chr(13) + chr(10)
            txt = XML_TEMPLATE
            k0 = txt.find("<KeyName>")
            if k0 >= 0:
                k1 = txt.find("</KeyName>", k0)
                if k1 >= 0:
                    txt = txt[:k0 + 9] + base + txt[k1:]
            with open(path, "w", encoding="utf-8", newline=crlf) as f:
                f.write(txt)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("新建失败", str(exc))
            return False
        self._xml_root = ET.parse(path).getroot()
        self._xml_path = path
        self._xml_dirty = False
        try:
            self._fill_xml_tree("")
        except Exception:  # noqa: BLE001
            pass
        self.set_status("已新建 {}".format(os.path.basename(path)))
        return True
    def _xml_save(self):
        if not self._xml_root or not self._xml_path:
            self.set_status("没有可保存的 xml")
            return
        # 保存前跑一次结构检查：<Guns> 顺序错位之类的会让游戏直接闪退
        try:
            e2, _w2 = self._xml_check_guns(self._xml_root)
            e2 = list(e2) + list(self._xml_check_platforms(self._xml_root))
        except Exception:  # noqa: BLE001
            e2 = []
        if e2:
            msg = "发现 {} 处会导致游戏闪退的问题：\n\n{}\n\n仍要保存吗？".format(
                len(e2), "\n".join(e2[:4]))
            if not messagebox.askyesno("XML 检查", msg, parent=self):
                self.set_status("已取消保存（先修 XML）")
                return
        bak = self._xml_path + ".bak"
        try:
            if not os.path.exists(bak):
                import shutil
                shutil.copy2(self._xml_path, bak)
        except Exception:  # noqa: BLE001
            pass
        try:
            _indent_xml(self._xml_root)
            # 手写而不是 ET.write：保留原来的声明写法和 <Tag/> 风格（ET 会写成 <Tag />）
            body = ET.tostring(self._xml_root, encoding="unicode").replace(" />", "/>")
            head = "<?xml version=\"1.0\"?>\n"
            with open(self._xml_path, "w", encoding="utf-8", newline="\r\n") as fh:
                fh.write(head + body + "\n")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", str(exc))
            return
        self._xml_dirty = False
        self._refresh_xml_title()
        self.set_status("已保存 {}".format(os.path.basename(self._xml_path)))

    def _xml_check_platforms(self, root):
        """<Platforms> 的顺序检查：父平台必须排在子平台之前。

        引擎按这个顺序建立旋转链（车体 → 炮塔 → 指挥塔…），
        把子平台提到父平台前面，轻则转轴挂错，重则加载闪退。
        """
        errors = []
        plats_el = root.find(".//Platforms")
        if plats_el is None:
            return errors
        parts = []
        for i, pl in enumerate(list(plats_el)):
            try:
                parts.append((i, int((pl.get("ModelPart") or "-1").strip())))
            except ValueError:
                errors.append("第 {} 个平台的 ModelPart 不是整数".format(i + 1))
                parts.append((i, -1))
        idx = {mp: i for i, mp in parts if mp >= 0}

        parent = {}
        for o in (self.scene.objects if self.scene else []):
            for c in (getattr(o, "children_ids", None) or []):
                try:
                    parent[int(c)] = int(o.id)
                except (TypeError, ValueError):
                    pass

        for i, mp in parts:
            if mp < 0:
                continue
            cur, seen = parent.get(mp), set()
            while cur is not None and cur not in seen:
                seen.add(cur)
                if cur in idx and idx[cur] > i:
                    errors.append(
                        "平台 {}（ModelPart={}）排在了它的父平台 ModelPart={}（第 {} 个）之后 —— "
                        "平台必须按层级「父在前、子在后」排列".format(
                            i + 1, mp, cur, idx[cur] + 1))
                    break
                cur = parent.get(cur)
        return errors

    def _xml_check_guns(self, root):
        """<Guns> / <Platforms> 的语义检查（顺序错位会让引擎直接闪退）。

        引擎按「平台顺序 → 枪顺序」线性分配枪口归属：
        plat 声明的 [FirstGun, FirstGun+NumGuns) 必须和模型里的父子关系对得上，
        而且 <Guns> 里必须按平台分组排列，不能交叉。
        """
        errors, warnings = [], []
        plats_el = root.find(".//Platforms")
        guns_el = root.find(".//Guns")
        if plats_el is None or guns_el is None:
            return errors, warnings

        plats = []
        for i, pl in enumerate(list(plats_el)):
            try:
                mp = int((pl.get("ModelPart") or "-1").strip())
                fg = int((pl.get("FirstGun") or "0").strip())
                ng = int((pl.get("NumGuns") or "0").strip())
            except ValueError:
                errors.append("第 {} 个平台：ModelPart / FirstGun / NumGuns 不是整数".format(i + 1))
                continue
            plats.append((i, mp, fg, ng))

        guns = []
        for j, g in enumerate(list(guns_el)):
            try:
                mp = int((g.get("ModelPart") or "-1").strip())
            except ValueError:
                mp = -1
            name = (g.findtext("Weapon") or "").strip()
            guns.append((j, mp, name))
        n = len(guns)

        # 1) 范围
        for i, mp, fg, ng in plats:
            if fg < 0 or ng < 0:
                errors.append("平台 {}（ModelPart={}）：FirstGun={} / NumGuns={} 不能是负数"
                              .format(i + 1, mp, fg, ng))
            elif fg + ng > n:
                errors.append("平台 {}（ModelPart={}）的武器范围 {}..{} 超出武器总数 {}"
                              .format(i + 1, mp, fg, fg + ng - 1, n))

        # 2) 枪归哪个平台（从零件往上找，第一个匹配到的平台）
        parent = {}
        for o in (self.scene.objects if self.scene else []):
            for c in (getattr(o, "children_ids", None) or []):
                try:
                    parent[int(c)] = int(o.id)
                except (TypeError, ValueError):
                    pass

        def owner_of(part):
            cur, seen = part, set()
            while cur is not None and cur not in seen:
                seen.add(cur)
                for i, mp, fg, ng in plats:
                    if mp == cur:
                        return i
                cur = parent.get(cur)
            return None

        owners = [owner_of(mp) for (_j, mp, _nm) in guns]

        # 3) 覆盖：每门枪至少要被某个平台的范围覆盖
        cover = [0] * n
        for i, mp, fg, ng in plats:
            for k in range(max(0, fg), min(fg + ng, n)):
                cover[k] += 1
        for k, c in enumerate(cover):
            if c == 0:
                warnings.append("第 {} 门武器（{}）没有任何平台的 FirstGun/NumGuns 覆盖到"
                                .format(k + 1, guns[k][2] or "?"))

        # 3.5) 引擎 SMechUnitRPGStats::Validate() 的断言：各平台武器区间要首尾相接
        if plats:
            if plats[0][2] != 0:
                errors.append("第一个平台的 FirstGun 必须是 0（引擎断言）")
            for k in range(1, len(plats)):
                _i0, _mp0, fg0, ng0 = plats[k - 1]
                _i1, _mp1, fg1, ng1 = plats[k]
                expect = fg0 + ng0
                if fg1 != expect:
                    warnings.append(
                        "平台 {} 的 FirstGun={} 与引擎断言不一致（应为 {} = 上一平台 FirstGun {} + NumGuns {}）；"
                        "游戏里可能仍能跑，但属于非标准写法".format(k + 1, fg1, expect, fg0, ng0))

        # 4) 顺序：必须按所属平台分组，且与 <Platforms> 的顺序一致
        last = -1
        for k, ow in enumerate(owners):
            if ow is None:
                continue
            if ow < last:
                errors.append(
                    "第 {} 门武器（{}）属于平台 {}，却排在属于平台 {} 的武器之后 —— "
                    "<Guns> 必须按平台分组排列，否则引擎加载时会闪退"
                    .format(k + 1, guns[k][2] or "?", ow + 1, last + 1))
                break
            last = max(last, ow)

        return errors, warnings

    def _xml_check_report(self):
        """检查磁盘上的 xml：语法（带行列）+ 常见结构问题。返回 (errors, warnings, info)。"""
        errors, warnings = [], []
        path = self._xml_path
        if not path or not os.path.exists(path):
            return ["找不到 xml 文件：{}".format(path)], [], ""
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            line, col = getattr(exc, "position", (0, 0))
            return ["第 {} 行 第 {} 列：{}".format(line, col, exc)], [], ""
        root = tree.getroot()

        n_lines = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                n_lines = sum(1 for _ in fh)
        except OSError:
            pass

        known = set()
        for o in (self.scene.objects if self.scene else []):
            try:
                known.add(int(o.id))
            except (TypeError, ValueError):
                pass

        guns_el = root.find(".//Guns")
        if guns_el is None:
            warnings.append("缺少 <Guns> 节点（武器列表为空）")
        else:
            names = {}
            for i, g in enumerate(list(guns_el)):
                w = g.find("Weapon")
                nm = (w.text or "").strip() if w is not None else ""
                if not nm:
                    warnings.append("第 {} 门武器没有 <Weapon> 名字".format(i + 1))
                else:
                    names[nm] = names.get(nm, 0) + 1
                for attr in ("ModelPart", "ShootPoint"):
                    val = (g.get(attr) or "").strip()
                    if known and val.lstrip("-").isdigit() and int(val) not in known:
                        warnings.append(
                            "第 {} 门武器 {}={} 在模型里不存在".format(i + 1, attr, val))
            for nm, cnt in names.items():
                if cnt > 1:
                    warnings.append("武器名重复 {} 次：{}".format(cnt, nm))

        for pl in root.findall(".//Platform"):
            nm = pl.get("Id") or pl.get("Name") or "?"
            for lo_a, hi_a in (("Constraint.Min", "Constraint.Max"),
                               ("ConstraintVertical.Min", "ConstraintVertical.Max")):
                try:
                    lo = float(pl.get(lo_a, "nan"))
                    hi = float(pl.get(hi_a, "nan"))
                except (TypeError, ValueError):
                    continue
                if lo == lo and hi == hi and lo > hi:
                    warnings.append("平台 {}：{} > {}".format(nm, lo_a, hi_a))

        # 引擎 SMechUnitRPGStats::Validate() 里会「拒绝加载」的硬性条件
        rpg_el = root.find(".//RPG")
        if rpg_el is not None:
            def _num(name, default=None):
                v = (rpg_el.get(name) or "").strip()
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default
            prio = _num("Priority")
            if prio is not None and prio <= 0:
                errors.append("Priority={} —— 引擎要求非步兵单位 Priority > 0，否则拒绝加载"
                              .format(prio))
            btr = _num("BoundTileRadius")
            if btr is not None and btr > 5:
                errors.append("BoundTileRadius={} —— 引擎要求 <= 5，否则拒绝加载"
                              .format(btr))
            hp = _num("MaxHP")
            if hp is not None and hp <= 0:
                errors.append("MaxHP={} —— 血量必须 > 0".format(hp))

        # 目录名要和 <KeyName> 一致（引擎按目录名找单位，不一致会加载失败）
        key_el = root.find(".//KeyName")
        key = (key_el.text or "").strip() if key_el is not None else ""
        folder = os.path.basename(os.path.dirname(self._xml_path or ""))
        # 目录名常带作者备注（"USSR-T80B-1982    （空梦制作定稿…）"），只比对开头那段
        base = re.split(r"\s{2,}|（|\(", folder, maxsplit=1)[0].strip()
        if key and base and base.lower() != key.lower():
            errors.append("目录名「{}」和 <KeyName>「{}」不一致 —— 引擎按目录名加载，会找不到单位"
                          .format(folder, key))
        try:
            e2, w2 = self._xml_check_guns(root)
            errors.extend(e2)
            warnings.extend(w2)
        except Exception:  # noqa: BLE001
            pass
        try:
            errors.extend(self._xml_check_platforms(root))
        except Exception:  # noqa: BLE001
            pass

        return errors, warnings, "{} 行".format(n_lines)

    def dlg_xml_check(self, parent=None):
        """「校验 XML」：语法错误给行列，结构问题给提示。"""
        name = os.path.basename(self._xml_path) if self._xml_path else "(没有 xml)"
        errors, warnings, info = self._xml_check_report()
        ok = not errors

        top = tk.Toplevel(parent or self)
        top.title(tr("XML 校验") + " - " + name)
        top.configure(bg="#ececed")
        top.transient(parent or self)
        wrap = tk.Frame(top, bg="#ececed")
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        head = ("✔ 语法检查通过" if ok else "✘ 发现 {} 处错误".format(len(errors)))
        tk.Label(wrap, text=head, bg="#ececed",
                 fg=("#1c7c3c" if ok else "#b02a2a"),
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Label(wrap, text="{}  ·  {} 条提示".format(info, len(warnings)),
                 bg="#ececed", fg="#666666").pack(anchor="w", pady=(0, 6))

        box = tk.Text(wrap, width=88, height=14, wrap="none", bg="#ffffff",
                      fg="#222222", relief="solid", borderwidth=1)
        sb = tk.Scrollbar(wrap, command=box.yview)
        box.configure(yscrollcommand=sb.set)
        box.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        lines = ["[错误] " + e for e in errors] + ["[提示] " + w for w in warnings]
        box.insert("1.0", "\n".join(lines) if lines else "没有发现问题。")
        box.configure(state="disabled")

        tk.Button(wrap, text="关闭", width=10,
                  command=top.destroy).pack(side="bottom", anchor="e", pady=(8, 0))
        try:
            top.grab_set()
        except Exception:  # noqa: BLE001
            pass
        self.set_status(("XML 校验通过：" if ok else "XML 校验失败：") + info)

    def _refresh_xml_title(self):
        if not self._xml_path:
            return
        name = os.path.basename(self._xml_path)
        self.xml_title.config(text=name + ("  *" if self._xml_dirty else ""))
    # ---------------- 拖放
    def _init_drop(self):
        try:
            self._drop = FileDrop(self, self.on_files_dropped)
        except Exception as exc:  # noqa: BLE001
            self.set_status("拖放初始化失败: {}".format(exc))

    def on_files_dropped(self, paths):
        """窗口里拖进文件：.mod / .obj 直接打开，.dds 换贴图（可同时拖入）。"""
        mods = [p for p in paths if p.lower().endswith(".mod")]
        objs = [p for p in paths if p.lower().endswith(".obj")]
        dds = [p for p in paths if p.lower().endswith(IMAGE_EXTS)]
        if not mods and not objs and not dds:
            self.set_status("只支持拖入 .mod / .obj 或图片（收到 {} 个其它文件）".format(
                len(paths)))
            return
        self.deiconify()
        self.lift()
        if mods:
            self.open_mod_path(mods[0])
        elif objs:
            self.load_obj_preview(objs[0])
        if dds:
            self.apply_texture_path(dds[0])
        if len(mods) + len(objs) > 1:
            self.set_status("一次只打开第一个模型：{}".format(
                os.path.basename((mods or objs)[0])))

    # ---------------------------------------------------------------- 拖放
    def _init_file_drop(self):
        """让主窗口也接受资源管理器拖入的文件（视口那边在 glviewport 里处理）。"""
        try:
            import ctypes
            u = ctypes.windll.user32
            shell = ctypes.windll.shell32
            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p,
                                         ctypes.c_uint, ctypes.c_ulonglong,
                                         ctypes.c_longlong)
            u.CallWindowProcW.restype = ctypes.c_longlong
            u.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.c_uint, ctypes.c_ulonglong,
                                          ctypes.c_longlong]
            u.SetWindowLongPtrW.restype = ctypes.c_void_p
            u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_void_p]
            self._dnd_old = {}
            self._dnd_procs = []
            self._dnd_alive = True
            self.bind("<Destroy>", self._unhook_file_drop, add="+")
            hwnds = []
            try:
                h = int(self.winfo_id())
                hwnds.append(h)
                par = u.GetParent(ctypes.c_void_p(h))
                if par:
                    hwnds.append(int(par))
            except Exception:  # noqa: BLE001
                pass
            for hwnd in hwnds:
                try:
                    shell.DragAcceptFiles(ctypes.c_void_p(hwnd), True)
                    u.GetWindowLongPtrW.restype = ctypes.c_void_p
                    old = u.GetWindowLongPtrW(ctypes.c_void_p(hwnd), -4)
                    self._dnd_old[hwnd] = old
                    proc = WNDPROC(self._make_dnd_proc(hwnd, old, WNDPROC, u))
                    self._dnd_procs.append(proc)
                    u.SetWindowLongPtrW(ctypes.c_void_p(hwnd), -4,
                                        ctypes.cast(proc, ctypes.c_void_p))
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            try:
                import glviewport as _gv
                _gv.dlog("拖放初始化失败: {}".format(exc))
            except Exception:  # noqa: BLE001
                pass

    def _unhook_file_drop(self, _e=None):
        """关窗时把窗口过程还回去，别让系统回调到已销毁的 Python 对象。"""
        self._dnd_alive = False
        try:
            import ctypes
            u = ctypes.windll.user32
            for hwnd, old in list(getattr(self, "_dnd_old", {}).items()):
                try:
                    u.SetWindowLongPtrW(ctypes.c_void_p(hwnd), -4,
                                        ctypes.c_void_p(old))
                except Exception:  # noqa: BLE001
                    pass
            self._dnd_old = {}
        except Exception:  # noqa: BLE001
            pass

    def _make_dnd_proc(self, hwnd, old, wndproc_type, u):
        import ctypes
        shell = ctypes.windll.shell32

        def proc(h, msg, wp, lp):
            if not getattr(self, "_dnd_alive", False):
                return 0
            if msg == 0x0233:            # WM_DROPFILES
                try:
                    paths = []
                    n = shell.DragQueryFileW(ctypes.c_void_p(wp), 0xFFFFFFFF, None, 0)
                    for i in range(n):
                        ln = shell.DragQueryFileW(ctypes.c_void_p(wp), i, None, 0)
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        shell.DragQueryFileW(ctypes.c_void_p(wp), i, buf, ln + 1)
                        paths.append(buf.value)
                    shell.DragFinish(ctypes.c_void_p(wp))
                    if paths:
                        # 纯 Python 入队，不调用任何 Tk 接口（重入会崩）
                        q = getattr(self, "_dropped_paths", None)
                        if q is None:
                            q = self._dropped_paths = []
                        q.extend(paths)
                except Exception:  # noqa: BLE001
                    pass
                return 0
            try:
                return u.CallWindowProcW(ctypes.c_void_p(old), ctypes.c_void_p(h),
                                         msg, wp, lp)
            except Exception:  # noqa: BLE001
                return 0

        return proc

    def _poll_drops(self):
        """定时取走拖入队列（窗口过程只入队，这里在 Tk 循环里处理）。"""
        paths = list(getattr(self, "_dropped_paths", None) or [])
        if paths:
            self._dropped_paths = []
        try:
            pend = list(getattr(self.view, "_drop_pending", None) or [])
            if pend:
                self.view._drop_pending = []
                paths.extend(pend)
        except Exception:  # noqa: BLE001
            pass
        if paths:
            try:
                self._on_files_dropped(paths)
            except Exception as exc:  # noqa: BLE001
                self.set_status("拖入处理失败: {}".format(exc))
        try:
            self.after(150, self._poll_drops)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- UV 编辑
    def _uv_mesh_of(self, name):
        for o in (self.scene.objects if self.scene else []):
            if o.name == name and o.data is not None:
                return o
        return None

    def _uv_loop_index(self, o, face, corner):
        """(face, corner) -> data.uvs 下标（.mod 的 loops[:,2] 就是 UV 索引）。"""
        try:
            return int(np.asarray(o.data.loops)[face * 3 + corner, 2])
        except Exception:  # noqa: BLE001
            return None

    def _uv_sync_view(self, names=None):
        """把改过的 data.uvs 同步到视口的 UV 显示数组。"""
        names = set(names or {n for n, _ in getattr(self, "_uv_sel", set())})
        for name in names:
            o = self._uv_mesh_of(name)
            if o is None:
                continue
            d = o.data
            try:
                uv_idx = np.asarray(d.loops)[:, 2]
                uv_face = np.asarray(d.uvs, float)[uv_idx].reshape(-1, 3, 2)
            except Exception:  # noqa: BLE001
                continue
            for i, it in enumerate(self.view.meshes):
                if it[0] == name:
                    self.view.meshes[i] = tuple(it[:5]) + (uv_face,)
                    break
        self.view._last_sig = None
        if self.view.ready:
            self.view.redraw(force=True)

    def _uv_sync_sel(self):
        pts = []
        for o, i in self._uv_sel_points():
            try:
                pts.append((float(o.data.uvs[i][0]), float(o.data.uvs[i][1])))
            except Exception:  # noqa: BLE001
                pass
        self.view.uv_sel_pts = pts
        self.view.uv_pivot = self._uv_pivot()
        self.view._last_sig = None
        if self.view.ready:
            self.view.redraw(force=True)

    def _uv_sel_points(self):
        """选中点 -> [(obj, uv_index)]（去重，忽略失效项）。"""
        out = []
        for name, idx in sorted(getattr(self, "_uv_sel", set())):
            o = self._uv_mesh_of(name)
            if o is None:
                continue
            if 0 <= idx < len(o.data.uvs):
                out.append((o, idx))
        return out

    def _uv_snapshot(self):
        snap = {}
        for o, _i in self._uv_sel_points():
            if o.name not in snap:
                snap[o.name] = np.asarray(o.data.uvs, float).copy()
        return snap

    def _uv_restore(self, snap):
        for name, arr in (snap or {}).items():
            o = self._uv_mesh_of(name)
            if o is not None:
                o.data.uvs = arr.copy()
        self._uv_sync_view(set((snap or {}).keys()))

    def _uv_pivot(self):
        pts = [(o.data.uvs[i], o, i) for o, i in self._uv_sel_points()]
        if not pts:
            return None
        arr = np.array([p[0] for p in pts], float)
        return arr.mean(axis=0)

    def _uv_apply(self, fn, delta_only=False):
        """对选中 UV 应用一次变换（基于快照，避免累积误差）。"""
        snap = getattr(self, "_uv_snap", None)
        if not snap:
            return
        for o, idx in self._uv_sel_points():
            base = snap.get(o.name)
            if base is None or idx >= len(base):
                continue
            # 必须整块换新数组：撤销快照按引用存老数组，原地改会把它一起改掉
            cur = np.array(o.data.uvs, float, copy=True)
            cur[idx] = fn(base[idx])
            o.data.uvs = cur
        self._uv_sync_view()
        self._uv_sync_sel()

    def _on_uv_pick(self, kind, name, face, corner, x, y, shift):
        """UV 视图点击：点/面选择（Shift 加选，空白处清空）。"""
        if getattr(self, "_uv_modal", None) is not None:
            self._uv_modal_confirm()
            return
        if kind == "none":
            if not shift:
                self._uv_sel = set()
                self.set_status("UV：已清空选择")
                self.view.redraw(force=True)
            return
        o = self._uv_mesh_of(name)
        if o is None:
            return
        idxs = set()
        if kind == "vert":
            i = self._uv_loop_index(o, face, corner)
            if i is not None:
                idxs.add((name, i))
        else:
            for j in range(3):
                i = self._uv_loop_index(o, face, j)
                if i is not None:
                    idxs.add((name, i))
        if shift:
            self._uv_sel = set(getattr(self, "_uv_sel", set())) | idxs
        else:
            self._uv_sel = idxs
        self._uv_snap = None
        self._uv_undo_dirty = False
        self.set_status("UV：选中 {} 个点".format(len(self._uv_sel)))
        self._uv_sync_sel()

    def _uv_push_undo(self, label):
        if getattr(self, "_uv_undo_dirty", False):
            return
        self._uv_undo_dirty = True
        self._push_undo(label)

    def _on_uv_drag(self, dx, dy, x, y):
        """左键拖动 = 移动选中 UV（像素 -> UV 增量）。"""
        modal = getattr(self, "_uv_modal", None)
        if modal is not None:
            self._uv_modal_update(x, y)
            return
        if not getattr(self, "_uv_sel", None):
            return
        scale, _m, _c = self.view.uv_scale()
        if scale <= 1e-9:
            return
        if getattr(self, "_uv_snap", None) is None:
            self._uv_snap = self._uv_snapshot()
            self._uv_undo_dirty = False
            self._uv_push_undo("UV 移动")
            self._uv_gesture = True
        du, dv = dx / scale, -dy / scale
        self._uv_apply(lambda p: (p[0] + du, p[1] + dv))

    def _on_uv_release(self):
        if getattr(self, "_uv_modal", None) is None:
            self._uv_snap = None
            self._uv_undo_dirty = False

    def _uv_gz_start(self, kind):
        self._uv_gz_kind = kind
        self._uv_snap = self._uv_snapshot()
        self._uv_undo_dirty = False
        self._uv_push_undo({"u": "UV 移动", "v": "UV 移动", "center": "UV 变换",
                            "ring": "UV 旋转"}.get(kind, "UV 变换"))
        self._uv_gz_pivot = self._uv_pivot()

    def _uv_gz(self, kind, dx, dy, x, y, geom):
        """拖 gizmo 手柄：U/V 轴、中心（自由移动或等比缩放）、旋转环。"""
        if not getattr(self, "_uv_snap", None) or getattr(self, "_uv_gz_pivot", None) is None:
            return
        scale = float(geom.get("k") or 1.0)
        piv = self._uv_gz_pivot
        tool = getattr(self.view, "tool", "move")
        cx, cy = geom["c"]
        x0, y0 = x - dx, y - dy          # 按下时的鼠标位置
        if kind in ("u", "v"):
            du, dv = dx / scale, -dy / scale
            if kind == "u":
                self._uv_apply(lambda p: (p[0] + du, p[1]))
            else:
                self._uv_apply(lambda p: (p[0], p[1] + dv))
        elif kind == "center":
            if tool == "scale":
                d0 = math.hypot(x0 - cx, y0 - cy) or 1.0
                d1 = math.hypot(x - cx, y - cy)
                k = max(0.05, d1 / d0)
                self._uv_apply(lambda p: (piv[0] + (p[0] - piv[0]) * k,
                                          piv[1] + (p[1] - piv[1]) * k))
            else:
                du, dv = dx / scale, -dy / scale
                self._uv_apply(lambda p: (p[0] + du, p[1] + dv))
        elif kind == "ring":
            a0 = math.atan2(y0 - cy, x0 - cx)
            a1 = math.atan2(y - cy, x - cx)
            ang = a1 - a0
            ca, sa = math.cos(ang), math.sin(ang)
            self._uv_apply(lambda p: (
                piv[0] + (p[0] - piv[0]) * ca - (p[1] - piv[1]) * sa,
                piv[1] + (p[0] - piv[0]) * sa + (p[1] - piv[1]) * ca))

    def _uv_gz_end(self):
        self._uv_snap = None
        self._uv_undo_dirty = False
        self.set_status("UV 变换已应用")

    def _on_uv_motion(self, x, y):
        if getattr(self, "_uv_modal", None) is not None:
            self._uv_modal_update(x, y)

    def _uv_modal_start(self, kind):
        """G/R/S：开始 UV 变换（跟随鼠标，点击或回车确认，Esc 取消）。"""
        if self.view.view_mode != "uv":
            return False
        if not getattr(self, "_uv_sel", None):
            self.set_status("UV：先点选 UV 点/面")
            return True
        scale, (mx, my), (cx, cy) = self.view.uv_scale()
        piv = self._uv_pivot()
        if piv is None:
            return True
        px = mx + (piv[0] - cx) * scale
        py = my + (piv[1] - cy) * scale
        self._uv_snap = self._uv_snapshot()
        self._uv_undo_dirty = False
        self._uv_push_undo({"move": "UV 移动", "rotate": "UV 旋转", "scale": "UV 缩放"}[kind])
        self._uv_modal = {"kind": kind, "pivot": piv, "px": px, "py": py,
                          "x0": px, "y0": py, "scale": scale}
        self.view.uv_modal = True
        self.set_status("UV {}：移动鼠标调整，左键/回车确认，Esc 取消".format(
            {"move": "移动", "rotate": "旋转", "scale": "缩放"}[kind]))
        return True

    def _uv_modal_update(self, x, y):
        m = getattr(self, "_uv_modal", None)
        if not m:
            return
        piv = m["pivot"]
        kind = m["kind"]
        if kind == "move":
            du = (x - m["x0"]) / m["scale"]
            dv = -(y - m["y0"]) / m["scale"]
            self._uv_apply(lambda p: (p[0] + du, p[1] + dv))
        elif kind == "rotate":
            a0 = math.atan2(m["y0"] - m["py"], m["x0"] - m["px"])
            a1 = math.atan2(y - m["py"], x - m["px"])
            ang = a1 - a0
            ca, sa = math.cos(ang), math.sin(ang)
            self._uv_apply(lambda p: (
                piv[0] + (p[0] - piv[0]) * ca - (p[1] - piv[1]) * sa,
                piv[1] + (p[0] - piv[0]) * sa + (p[1] - piv[1]) * ca))
        else:
            d0 = math.hypot(m["x0"] - m["px"], m["y0"] - m["py"]) or 1.0
            d1 = math.hypot(x - m["px"], y - m["py"])
            k = max(0.02, d1 / d0)
            self._uv_apply(lambda p: (piv[0] + (p[0] - piv[0]) * k,
                                      piv[1] + (p[1] - piv[1]) * k))

    def _uv_modal_confirm(self):
        if getattr(self, "_uv_modal", None) is None:
            return False
        self._uv_modal = None
        self.view.uv_modal = False
        self._uv_snap = None
        self._uv_undo_dirty = False
        self.set_status("UV 变换已应用")
        return True

    def _uv_modal_cancel(self):
        if getattr(self, "_uv_modal", None) is None:
            return False
        snap = getattr(self, "_uv_snap", None)
        self._uv_modal = None
        self.view.uv_modal = False
        self._uv_restore(snap)
        self._uv_snap = None
        self._uv_undo_dirty = False
        self.set_status("UV 变换已取消")
        return True

    def _on_view_mode(self, _e=None):
        """切换 3D / UV 预览（UV 只读，不提供编辑）。"""
        val = (self.view_mode_var.get() or "").strip()
        mode = "uv" if ("UV" in val.upper()) else "3d"
        try:
            self.view.set_view_mode(mode)
        except Exception as exc:  # noqa: BLE001
            self.set_status("切换视图失败: {}".format(exc))
            return
        if mode == "uv":
            n = len(self.view.uv_visible_meshes())
            self.set_status("UV 视图：{} 个可见部件".format(n))
        else:
            self.set_status("3D 视图")

    def _on_files_dropped(self, paths):
        """拖入文件：.mod 直接打开；图片加进 Recent Textures 并套到视口上。"""
        mods = [p for p in paths if str(p).lower().endswith(".mod")]
        if mods:
            try:
                self.open_mod_path(os.path.abspath(mods[0]), ask=True)
                self.update_idletasks()
            except Exception as exc:  # noqa: BLE001
                self.set_status("打开 {} 失败: {}".format(
                    os.path.basename(mods[0]), exc))
                return
        imgs = [p for p in paths if str(p).lower().endswith(IMAGE_EXTS)]
        if not imgs:
            if mods:
                self.set_status("已打开 {}".format(os.path.basename(mods[0])))
            else:
                self.set_status("拖入的文件里没有 .mod 模型或图片"
                                "（图片支持 {}）".format(", ".join(IMAGE_EXTS)))
            return
        if not hasattr(self, "tex_paths"):
            self.tex_paths = []
        added = 0
        for p in imgs:
            p = os.path.abspath(p)
            if p not in self.tex_paths:
                self.tex_paths.append(p)
                self.lb_tex.insert("end", os.path.basename(p))
                added += 1
        try:
            idx = self.tex_paths.index(os.path.abspath(imgs[-1]))
            self.lb_tex.selection_clear(0, "end")
            self.lb_tex.selection_set(idx)
            self.lb_tex.see(idx)
        except ValueError:
            pass
        self.apply_texture_path(imgs[-1])
        if added:
            self.set_status("拖入 {} 张图片，已加入贴图列表".format(added))

    def open_texture(self):
        p = filedialog.askopenfilename(
            title="Open texture file",
            filetypes=[("贴图", "*.dds *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                       ("DDS texture", "*.dds"),
                       ("Images", "*.png *.jpg *.jpeg *.bmp *.gif"),
                       ("All files", "*.*")])
        if not p:
            return
        self._show_texture_window(p)

    @staticmethod
    def _write_png_rgba(path, arr):
        """arr 形状 (h, w, 4) uint8 -> 带 alpha 的 PNG（不依赖 Pillow）。"""
        h, w = int(arr.shape[0]), int(arr.shape[1])
        a = np.ascontiguousarray(arr, dtype=np.uint8)
        raw = b"".join(b"\x00" + a[y].tobytes() for y in range(h))

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(raw, 6))
        png += chunk(b"IEND", b"")
        with open(path, "wb") as f:
            f.write(png)
        return path

    def dlg_export_uv(self):
        """导出 UV 布局图：透明背景 PNG，可选分辨率 / 不透明度 / 线宽。"""
        if not getattr(self.view, "meshes", None):
            messagebox.showinfo("导出 UV 布局图", "还没有加载模型")
            return
        if not self.view.uv_visible_meshes():
            messagebox.showinfo("导出 UV 布局图",
                                "当前没有可见的 UV（检查眼睛隐藏/模型是否带 UV）")
            return
        p = self.px
        win = tk.Toplevel(self)
        win.title(tr("导出 UV 布局图"))
        win.configure(bg="#f0f0f0")
        win.transient(self)
        win.resizable(False, False)

        body = tk.Frame(win, bg="#f0f0f0")
        body.pack(padx=p(14), pady=(p(10), p(4)))

        tk.Label(body, text=tr("分辨率"), bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
        presets = ["1024 x 1024", "2048 x 2048", "4096 x 4096",
                   "2048 x 1024", "1024 x 2048"]
        preset_var = tk.StringVar(value=presets[1])
        cb = ttk.Combobox(body, textvariable=preset_var, values=presets,
                          state="readonly", width=14, font=("Microsoft YaHei UI", 9))
        cb.grid(row=0, column=1, columnspan=3, sticky="w", padx=p(6))

        w_var = tk.StringVar(value="2048")
        h_var = tk.StringVar(value="2048")
        tk.Label(body, text=tr("宽"), bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=0, sticky="w", pady=p(4))
        tk.Entry(body, textvariable=w_var, width=8,
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=1, sticky="w")
        tk.Label(body, text=tr("高"), bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=2, sticky="e", padx=(p(8), p(4)))
        tk.Entry(body, textvariable=h_var, width=8,
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=3, sticky="w")

        def _apply_preset(_e=None):
            try:
                a, b = [x.strip() for x in preset_var.get().lower().split("x")]
                w_var.set(str(int(a)))
                h_var.set(str(int(b)))
            except Exception:  # noqa: BLE001
                pass
        cb.bind("<<ComboboxSelected>>", _apply_preset)

        line_rgba = [1.0, 1.0, 1.0, 1.0]
        fill_rgba = [0.72, 0.74, 0.80, 1.0]

        def _swatch(row, text, rgba, title):
            tk.Label(body, text=text, bg="#f0f0f0", fg="#333333",
                     font=("Microsoft YaHei UI", 9)).grid(row=row, column=0, sticky="w", pady=p(2))
            btn = tk.Button(body, width=9, relief="solid", bd=1, cursor="hand2")
            btn.grid(row=row, column=1, sticky="w")

            def paint():
                btn.configure(bg="#%02x%02x%02x" % tuple(
                    int(max(0.0, min(1.0, c)) * 255 + 0.5) for c in rgba[:3]))

            def open_picker():
                _HsvWheelPicker(self, title, list(rgba),
                                lambda new: (rgba.__setitem__(slice(None), list(new)), paint()))

            btn.configure(command=open_picker)
            paint()
            return btn

        _swatch(2, tr("线框颜色"), line_rgba, tr("线框颜色"))
        tk.Label(body, text=tr("线框不透明度"), bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).grid(row=3, column=0, sticky="w")
        line_alpha = tk.IntVar(value=100)
        tk.Scale(body, from_=0, to=100, orient="horizontal", variable=line_alpha,
                 bg="#f0f0f0", length=p(150), showvalue=True, sliderlength=p(14),
                 font=("Microsoft YaHei UI", 8)).grid(row=3, column=1, columnspan=3, sticky="we")

        _swatch(4, tr("底色填充"), fill_rgba, tr("底色填充"))
        tk.Label(body, text=tr("填充不透明度"), bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).grid(row=5, column=0, sticky="w")
        fill_alpha = tk.IntVar(value=100)
        tk.Scale(body, from_=0, to=100, orient="horizontal", variable=fill_alpha,
                 bg="#f0f0f0", length=p(150), showvalue=True, sliderlength=p(14),
                 font=("Microsoft YaHei UI", 8)).grid(row=5, column=1, columnspan=3, sticky="we")

        tk.Label(body, text=tr("线宽 px"), bg="#f0f0f0", fg="#333333",
                 font=("Microsoft YaHei UI", 9)).grid(row=6, column=0, sticky="w", pady=p(2))
        lw_var = tk.StringVar(value="0")
        tk.Entry(body, textvariable=lw_var, width=6,
                 font=("Microsoft YaHei UI", 9)).grid(row=6, column=1, sticky="w")
        tk.Label(body, text=tr("（0 = 按分辨率自动）"), bg="#f0f0f0", fg="#777777",
                 font=("Microsoft YaHei UI", 8)).grid(row=6, column=2, columnspan=2, sticky="w")

        fill_var = tk.BooleanVar(value=True)
        tk.Checkbutton(body, text=tr("UV 岛内填充"), variable=fill_var,
                       bg="#f0f0f0", activebackground="#f0f0f0",
                       font=("Microsoft YaHei UI", 9)).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=p(4))

        align_var = tk.BooleanVar(value=True)
        tk.Checkbutton(body, text=tr("按 0~1 UV 排布（与贴图对齐）"), variable=align_var,
                       bg="#f0f0f0", activebackground="#f0f0f0",
                       font=("Microsoft YaHei UI", 9)).grid(
            row=8, column=0, columnspan=4, sticky="w", pady=p(2))

        hint = tk.Label(body, text=tr("背景透明；隐藏的部件不会导出"),
                        bg="#f0f0f0", fg="#777777", font=("Microsoft YaHei UI", 8))
        hint.grid(row=9, column=0, columnspan=4, sticky="w", pady=(p(6), 0))

        def do_export():
            try:
                tw = int(float(w_var.get()))
                th = int(float(h_var.get()))
            except ValueError:
                messagebox.showwarning("导出 UV 布局图", "宽/高要填整数", parent=win)
                return
            if not (16 <= tw <= 16384 and 16 <= th <= 16384):
                messagebox.showwarning("导出 UV 布局图",
                                       "分辨率请填 16 ~ 16384", parent=win)
                return
            try:
                lw = float(lw_var.get() or 0)
            except ValueError:
                lw = 0.0
            path = filedialog.asksaveasfilename(
                parent=win, title="导出 UV 布局图", defaultextension=".png",
                initialfile="uv_layout.png", filetypes=[("PNG image", "*.png")])
            if not path:
                return
            try:
                arr = self.view.uv_layout_rgba(
                    tw, th, 1.0, lw or None,
                    tuple(line_rgba[:3]), bool(fill_var.get()),
                    fill_color=tuple(fill_rgba[:3]),
                    line_alpha=line_alpha.get() / 100.0,
                    fill_alpha=fill_alpha.get() / 100.0,
                    fit=not bool(align_var.get()))
                self._write_png_rgba(path, arr)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("导出失败", str(exc), parent=win)
                return
            win.destroy()
            self.set_status("已导出 UV 布局图: {}   ({}x{}, 线 {}% / 填充 {}%)".format(
                os.path.basename(path), tw, th,
                line_alpha.get(), fill_alpha.get()))

        btns = tk.Frame(win, bg="#f0f0f0")
        btns.pack(fill="x", padx=p(14), pady=(p(4), p(10)))
        tk.Button(btns, text=tr("导出..."), command=do_export,
                  font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Button(btns, text=tr("取消"), command=win.destroy,
                  font=("Microsoft YaHei UI", 9)).pack(side="left", padx=p(8))

    def save_screenshot(self):
        """把当前 3D 视口渲染结果存成 PNG（纯 Python，不依赖 Pillow）。"""
        if not self.view.meshes:
            messagebox.showinfo("Save Screenshot", "还没有加载模型")
            return
        p = filedialog.asksaveasfilename(
            title="Save Screenshot",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")])
        if not p:
            return
        from OpenGL import GL
        import ctypes as _ct
        v = self.view
        w = max(64, v.winfo_width())
        h = max(64, v.winfo_height())
        # 直接从帧缓冲读像素（OpenGL 的坐标原点在左下，要垂直翻转）
        buf = GL.glReadPixels(0, 0, w, h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)[::-1]
        rw, rh = w, h
        raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(rh))

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", rw, rh, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(raw, 6))
        png += chunk(b"IEND", b"")
        try:
            with open(p, "wb") as f:
                f.write(png)
            self.set_status("已保存 {}   ({}x{})".format(p, rw, rh))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Save failed", str(exc))

    # ================================================================ 动画时间线
    @staticmethod
    def _mat_from_quat_loc(loc, quat):
        """(位置, 四元数 x,y,z,w) -> 4x4 局部矩阵。"""
        x, y, z, w = [float(v) for v in quat]
        M = np.eye(4)
        M[:3, :3] = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ])
        M[:3, 3] = np.asarray(loc, float)
        return M

    def _anim_active(self):
        return getattr(self, "_anim_frame", -1) >= 0 and bool(getattr(self, "_anim_local", None))

    def _ask_anim_dialog(self, count):
        """导入 .mod 时问一句：要不要连动画一起导入。返回 (choice, remember)。"""
        win = tk.Toplevel(self)
        win.title(tr("导入动画？"))
        win.configure(bg="#ececed")
        win.transient(self)
        win.resizable(False, False)
        p = self.px
        tk.Label(win, text="这个模型带有 {} 段动画，要一起导入吗？".format(count),
                 bg="#ececed", fg="#222222", anchor="w",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", padx=p(16), pady=(p(14), p(4)))
        tk.Label(win, text="导入：多一条时间线，可以拖时间指针逐帧预览\n"
                           "不导入：只看／编辑静止姿态（导出也不写动画）",
                 bg="#ececed", fg="#555555", anchor="w", justify="left",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=p(16))
        v_remember = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text=tr("记住这个选择，以后不再询问"), variable=v_remember,
                       onvalue=True, offvalue=False, bg="#ececed",
                       activebackground="#ececed", anchor="w", bd=0,
                       highlightthickness=0, font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=p(14), pady=(p(10), 0))
        res = {"choice": False, "done": False}

        def choose(val):
            res["choice"] = bool(val)
            res["done"] = True
            win.destroy()

        btns = tk.Frame(win, bg="#ececed")
        btns.pack(fill="x", padx=p(16), pady=(p(10), p(12)))
        tk.Button(btns, text=tr("只导入静态"), width=12,
                  command=lambda: choose(False)).pack(side="right")
        tk.Button(btns, text=tr("导入动画"), width=12, default="active",
                  command=lambda: choose(True)).pack(side="right", padx=p(8))
        win.protocol("WM_DELETE_WINDOW", lambda: choose(False))
        win.update_idletasks()
        try:
            x = self.winfo_rootx() + (self.winfo_width() - win.winfo_width()) // 2
            y = self.winfo_rooty() + (self.winfo_height() - win.winfo_height()) // 3
            win.geometry("+{}+{}".format(max(0, x), max(0, y)))
        except Exception:  # noqa: BLE001
            pass
        win.grab_set()
        win.wait_window()
        return res["choice"], bool(v_remember.get())

    def _anim_menu_open(self):
        """菜单入口：打开时间线（前提是当前文件导入了动画）。"""
        if not getattr(self, "_anim_avail", 0):
            self.set_status("当前文件没有动画数据")
            return
        if not getattr(self, "_anim_on", False):
            self.set_status("动画未导入：用「文件 > 导入动画」开启后重新打开这个 .mod")
            return
        self._anim_show_bar()

    def _sync_anim_edit_ui(self):
        """按首选项决定「删除段」按钮能不能点。"""
        btn = getattr(self, "anim_del_btn", None)
        if btn is None:
            return
        ok = bool(self.prefs.get("edit_anim", False))
        btn.configure(state=("normal" if ok else "disabled"),
                      fg=("#b03030" if ok else "#9a9a9a"))

    def _anim_delete_clip(self):
        """删掉当前选中的动画段；只有首选项里勾了「允许编辑动画」才给用。"""
        if not self.prefs.get("edit_anim", False):
            messagebox.showinfo(
                "删除动画段",
                "动画当前是「只预览」模式。\n\n"
                "要删除请先到「文件 → 首选项 → 操作权限」勾选「允许编辑动画」。")
            return
        clips = list(getattr(self.scene, "anim_descs", []) or [])
        if not clips:
            return
        try:
            idx = int(self.anim_clip.current())
        except Exception:  # noqa: BLE001
            idx = 0
        idx = max(0, min(idx, len(clips) - 1))
        d = clips[idx]
        name = getattr(d, "name", "?")
        if not messagebox.askyesno(
                "删除动画段",
                "确定删除动画段「{}」吗？\n\n"
                "导出 .mod 时不会再写出这一段（保存后才影响文件，"
                "未保存前重新打开文件即可恢复）。".format(name),
                parent=self):
            return
        self._anim_stop()
        clips.pop(idx)
        self.scene.anim_descs = clips
        self._anim_sel = None
        self._anim_frame = -1
        self._anim_local = {}
        if clips:
            self._anim_show_bar()
        else:
            self._anim_hide_bar()
            self.set_status("动画段已全部删除（导出时不会写出动画）")
            return
        self.set_status("已删除动画段：{}（还剩 {} 段）".format(name, len(clips)))

    def _anim_show_bar(self):
        clips = list(getattr(self.scene, "anim_descs", []) or [])
        if not clips or not hasattr(self, "anim_bar"):
            return
        self.anim_clip["values"] = [d.name for d in clips]
        sel = getattr(self, "_anim_sel", None)
        if sel not in clips:
            sel = clips[0]
        self._anim_sel = sel
        try:
            self.anim_clip.current(clips.index(sel))
        except ValueError:
            self.anim_clip.current(0)
        if not self.anim_bar.winfo_ismapped():
            self.anim_bar.pack(side="bottom", fill="x")
        self._sync_anim_edit_ui()
        self._anim_goto(0)
        self.set_status("动画：{} 段｜第 1 帧（拖动滑块或点 ▶ 预览）".format(len(clips)))

    def _anim_hide_bar(self):
        self._anim_stop()
        self._anim_clear()
        if hasattr(self, "anim_bar") and self.anim_bar.winfo_ismapped():
            self.anim_bar.pack_forget()

    def _anim_sync_scale(self):
        d = getattr(self, "_anim_sel", None)
        top = max(1, int(getattr(d, "length", 1)) - 1) if d is not None else 1
        self._anim_sync = True
        try:
            self.anim_scale.configure(to=top)
            self.anim_scale.set(max(0, min(getattr(self, "_anim_frame", 0), top)))
        finally:
            self._anim_sync = False
        self._anim_update_label()

    def _anim_update_label(self):
        d = getattr(self, "_anim_sel", None)
        if d is None or not hasattr(self, "anim_lbl"):
            return
        self.anim_lbl.config(text="{} / {}".format(
            max(0, getattr(self, "_anim_frame", 0)) + 1, int(getattr(d, "length", 1))))

    def _anim_clear(self):
        """清掉帧覆盖，回到静止姿态。"""
        self._anim_frame = -1
        self._anim_local = {}
        if getattr(self, "scene", None) is not None:
            self._apply_transforms()

    def _anim_exit_preview(self):
        if getattr(self, "_anim_frame", -1) >= 0:
            self._anim_stop()
            self._anim_clear()

    def _anim_goto(self, frame, redraw=True):
        """把场景摆到某一帧（只影响显示，不动 _xform，保存/编辑仍是静止姿态）。"""
        d = getattr(self, "_anim_sel", None)
        if d is None:
            return
        frames = np.asarray(getattr(d, "frames", None))
        if frames.ndim != 3 or frames.shape[0] == 0:
            return
        length = int(getattr(d, "length", frames.shape[1])) or frames.shape[1]
        f = max(0, min(int(frame), length - 1, frames.shape[1] - 1))
        prev = getattr(self, "_anim_local", {}) or {}
        new = {}
        for oid in range(frames.shape[0]):
            rec = frames[oid, f]
            new[int(oid)] = self._mat_from_quat_loc(rec[:3], rec[3:7])
        changed = [oid for oid, M in new.items()
                   if oid not in prev or not np.allclose(prev[oid], M, atol=1e-6)]
        if changed:
            # 父级动了，子级的世界矩阵也变了（子级自身可能一帧都没变），必须一起刷
            extra = set()
            for oid in changed:
                extra.update(self._descendants(oid))
            changed = sorted(set(changed) | extra)
        self._anim_local = new
        self._anim_frame = f
        if getattr(self, "scene", None) is not None:
            self._apply_transforms(ids=changed, redraw=redraw)
        self._anim_sync_scale()

    def _anim_on_scale(self, val):
        if getattr(self, "_anim_sync", False):
            return
        try:
            f = int(round(float(val)))
        except (TypeError, ValueError):
            return
        self._anim_stop()
        self._anim_goto(f)

    def _anim_on_clip(self, _e=None):
        clips = list(getattr(self.scene, "anim_descs", []) or [])
        i = self.anim_clip.current()
        if 0 <= i < len(clips):
            self._anim_stop()
            self._anim_sel = clips[i]
            self._anim_frame = -1
            self._anim_local = {}
            self._anim_goto(0)

    def _anim_toggle_play(self):
        if getattr(self, "_anim_after", None):
            self._anim_stop()
            return
        if getattr(self, "_anim_sel", None) is None:
            return
        self.anim_play_btn.config(text="⏸")
        self._anim_tick()

    def _anim_tick(self):
        d = getattr(self, "_anim_sel", None)
        if d is None:
            self._anim_stop()
            return
        n = max(1, int(getattr(d, "length", 1)))
        self._anim_goto((max(0, getattr(self, "_anim_frame", 0)) + 1) % n)
        self._anim_after = self.after(50, self._anim_tick)

    def _anim_stop(self):
        aid = getattr(self, "_anim_after", None)
        if aid:
            try:
                self.after_cancel(aid)
            except Exception:  # noqa: BLE001
                pass
        self._anim_after = None
        if hasattr(self, "anim_play_btn"):
            try:
                self.anim_play_btn.config(text="▶")
            except Exception:  # noqa: BLE001
                pass

    def _anim_close(self):
        self._anim_stop()
        self._anim_clear()
        if hasattr(self, "anim_bar"):
            self.anim_bar.pack_forget()
        self.set_status("已关闭动画时间线（文件菜单里可以再打开）")

    # ---------------- 保存为 .mod ----------------
    @staticmethod
    def _rebuild_mesh_loops(data, uv_face=None):
        """按当前 vertices/faces 重建 loops / normals / uvs。

        .mod 的几何是「顶点表 + 法线表 + UV 表 + 每面角的 (v,n,uv) 三元组」，
        分离、烘焙之类改了拓扑以后必须重算，否则写出来是坏网格。
        """
        f = np.asarray(getattr(data, "faces", np.empty((0, 3))), np.int64).reshape(-1, 3)
        v = np.asarray(getattr(data, "vertices", np.empty((0, 3))), float).reshape(-1, 3)
        if len(f) == 0 or len(v) == 0:
            data.loops = np.empty((0, 3), np.int64)
            data.normals = np.empty((0, 3), float)
            data.uvs = np.empty((0, 2), float)
            return
        vn = np.zeros((len(v), 3), float)
        p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        fn = np.cross(p1 - p0, p2 - p0)
        for k in range(3):
            np.add.at(vn, f[:, k], fn)
        ln = np.linalg.norm(vn, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        vn = vn / ln
        _nk, _nidx, ninv = np.unique(np.round(vn, 5), axis=0,
                                     return_index=True, return_inverse=True)
        ninv = np.asarray(ninv).reshape(-1)
        nuniq = vn[np.asarray(_nidx).reshape(-1)]
        # loops 是「每个面角」一条，法线索引要按面角展开
        nidx = ninv[f.reshape(-1)]
        if uv_face is not None and len(uv_face) == len(f):
            uva = np.asarray(uv_face, float).reshape(-1, 2)
            # 用取整后的键做去重，但表里保留原始精确值，避免 UV 被量化
            _keys, _idx, uinv = np.unique(np.round(uva, 6), axis=0,
                                          return_index=True, return_inverse=True)
            uinv = np.asarray(uinv).reshape(-1)
            uuniq = uva[np.asarray(_idx).reshape(-1)]
        else:
            uuniq = np.zeros((1, 2), float)
            uinv = np.zeros(3 * len(f), np.int64)
        data.loops = np.stack([f.reshape(-1), nidx, uinv], axis=1).astype(np.int64)
        data.normals = nuniq
        data.uvs = uuniq

    def _build_export_scene(self):
        """把编辑器里的状态（局部矩阵 / 顶点 / 限制 / AABB）整理成一份可写的副本。"""
        import copy
        # 动画数据**永远**原样带进导出副本：
        # 以前这里会在「未勾选导入动画」时清空 anim_descs，导致另存后动画全丢。
        # 预览开关（_anim_on）现在只影响时间线是否显示，不再影响保存内容。
        exp = copy.deepcopy(self.scene)
        uv_by = {}
        for it in self.view.meshes:
            uv_by[it[0]] = it[5] if len(it) > 5 else (it[4] if len(it) > 4 else None)

        for o in exp.objects:
            L = self._xform.get(o.id)
            if L is not None:
                L = np.asarray(L, float)
                o.location = np.asarray(L[:3, 3], float).copy()
                q = quat_from_mat3(L[:3, :3])
                n = float(np.linalg.norm(q))
                o.rotation = q / n if n > 1e-12 else np.array([0.0, 0.0, 0.0, 1.0])
            v = self._obj_local.get(o.id)
            if o.data is None or v is None:
                continue
            o.data.vertices = np.asarray(v, float).copy()
            f = np.asarray(o.data.faces, np.int64)
            loops = getattr(o.data, "loops", None)
            broken = (loops is None or len(loops) != 3 * len(f)
                      or (len(f) and len(loops) and int(np.max(loops[:, 0])) >= len(v)))
            if broken:
                self._rebuild_mesh_loops(o.data, uv_by.get(o.name))

        # .mod 必须有 AABB 节点，缺了回读会直接报错：没有就按整体包围盒补一个
        if getattr(exp, "aabb", None) is None:
            bbox = self._world_bbox([o for o in self.scene.objects if o.data is not None])
            if bbox is not None:
                mn, mx = bbox
                exp.aabb = np.concatenate([(mn + mx) * 0.5, (mx - mn) * 0.5])

        # 游戏是按「第 k 个 Geometry ↔ ID 为 k 的部件」配对的，所以导出前必须：
        #   1) 网格部件排在前面（顺序 = Geometry 顺序），定位器排在最后
        #   2) ID 重新编号成 0..N-1
        #   3) 动画帧按新 ID 重排，obj_count 改成部件总数
        # 不这么做的话，分离/新增出来的部件（ID 比定位器还大）会让几何整体错位，
        # 游戏里表现为轮子/碎块乱飞。
        meshes = [o for o in exp.objects if o.data is not None]
        locs = [o for o in exp.objects if o.data is None]
        meshes.sort(key=lambda o: o.id)
        locs.sort(key=lambda o: o.id)
        ordered = meshes + locs
        remap = {o.id: i for i, o in enumerate(ordered)}
        if not ordered:
            # 一个部件都不剩：动画没有可挂的轨道，直接丢掉（否则写出 obj_count=0 的坏轨道）
            exp.anim_descs = []

        for d in (getattr(exp, "anim_descs", None) or []):
            fr = np.asarray(getattr(d, "frames", None))
            if fr.ndim != 3 or fr.shape[0] == 0:
                d.obj_count = len(ordered)
                continue
            length = int(fr.shape[1])
            new = np.zeros((len(ordered), length, 7))
            # 默认行用部件自己的静止姿态，没有对应动画数据的部件才不会跳到原点
            for i, o in enumerate(ordered):
                new[i, :, :3] = np.asarray(o.location, float)
                q = np.asarray(o.rotation, float)
                new[i, :, 3:] = q if q.shape == (4,) else np.array([0.0, 0.0, 0.0, 1.0])
            for old_id, new_id in remap.items():
                if old_id < fr.shape[0]:
                    new[new_id] = fr[old_id]
            d.frames = new
            d.obj_count = len(ordered)

        for o in ordered:
            kids = getattr(o, "children_ids", []) or []
            o.children_ids = [remap[c] for c in kids if c in remap]
        if getattr(exp, "root_id", None) in remap:
            exp.root_id = remap[exp.root_id]
        elif ordered:
            exp.root_id = 0
        for i, o in enumerate(ordered):
            o.id = i
        exp.objects = ordered
        return exp

    def _verify_written_mod(self, path, exp):
        """回读刚写出的文件，比对对象数 / 面数 / 包围盒。"""
        try:
            chk = ModScene()
            chk.from_file(path)
        except Exception as exc:  # noqa: BLE001
            return False, "回读失败：{}".format(exc)
        if len(chk.objects) != len(exp.objects):
            return False, "对象数不一致：写 {} / 读回 {}".format(len(exp.objects),
                                                                len(chk.objects))
        f0 = sum(len(o.data.faces) for o in exp.objects if o.data is not None)
        f1 = sum(len(o.data.faces) for o in chk.objects if o.data is not None)
        if f0 != f1:
            return False, "面数不一致：写 {} / 读回 {}".format(f0, f1)

        # 动画段数也要对得上：以前未勾选「导入动画」时会把动画整体丢掉，
        # 现在保存一定写回，这里做一次回归校验，避免以后又被改回「静默丢数据」
        d0 = list(getattr(exp, "anim_descs", []) or [])
        d1 = list(getattr(chk, "anim_descs", []) or [])
        if len(d0) != len(d1):
            return False, "动画段数不一致：写 {} / 读回 {}".format(len(d0), len(d1))

        def bounds(sc):
            mn = np.array([1e18] * 3)
            mx = np.array([-1e18] * 3)
            for o in sc.objects:
                if o.data is None or len(o.data.vertices) == 0:
                    continue
                v = np.asarray(o.data.vertices, float)
                mn = np.minimum(mn, v.min(axis=0))
                mx = np.maximum(mx, v.max(axis=0))
            return mn, mx

        a0, b0 = bounds(exp)
        a1, b1 = bounds(chk)
        if not (np.allclose(a0, a1, atol=1e-3) and np.allclose(b0, b1, atol=1e-3)):
            return False, "包围盒不一致"
        return True, "{} 个对象，{} 个面".format(len(chk.objects), f1)

    def save_mod_as(self):
        if not self.scene:
            self.set_status("先打开一个 .mod")
            return
        src = self.mod_path or ""
        base = os.path.splitext(os.path.basename(src))[0] if src else "model"
        p = filedialog.asksaveasfilename(
            title="保存为 .mod", defaultextension=".mod",
            initialfile=base + "_edited.mod",
            initialdir=os.path.dirname(src) if src else None,
            filetypes=[("Blitzkrieg model", "*.mod"), ("All files", "*.*")])
        if not p:
            return
        self.set_status("正在写入 {} ...".format(os.path.basename(p)))
        self.update_idletasks()
        try:
            exp = self._build_export_scene()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", "整理场景时出错：\n{}".format(exc))
            self.set_status("保存失败")
            return
        tmp = p + ".tmp"
        try:
            exp.to_file(tmp)
        except Exception as exc:  # noqa: BLE001
            try:
                os.remove(tmp)
            except Exception:  # noqa: BLE001
                pass
            messagebox.showerror("保存失败", "写文件出错：\n{}".format(exc))
            self.set_status("保存失败")
            return
        ok, detail = self._verify_written_mod(tmp, exp)
        if not ok:
            messagebox.showerror(
                "写入校验失败", "写出的文件读回来对不上：\n{}\n\n"
                "临时文件保留在：\n{}".format(detail, tmp))
            self.set_status("校验失败：" + detail)
            return
        try:
            os.replace(tmp, p)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", "替换目标文件出错：\n{}".format(exc))
            return
        self.set_status("已保存 {}   ({})".format(p, detail))

    # ---------------- 导出 OBJ ----------------
    def export_obj_dialog(self):
        if not self.scene or not getattr(self, "_all_meshes", None):
            messagebox.showinfo("Export OBJ", "先打开一个 .mod")
            return
        base = os.path.splitext(os.path.basename(self.mod_path))[0]
        p = filedialog.asksaveasfilename(
            title="Export OBJ", defaultextension=".obj",
            initialfile=base + ".obj",
            initialdir=os.path.dirname(self.mod_path),
            filetypes=[("Wavefront OBJ", "*.obj")])
        if not p:
            return
        y_up = self._ask_up_axis()
        if y_up is None:
            return
        try:
            n_v, n_f, mtl = self.export_obj(p, y_up=y_up)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc))
            return
        self.set_status("已导出 {} ：{} 个顶点 / {} 个面{}（坐标 {}-up）".format(
            os.path.basename(p), n_v, n_f,
            "，材质 " + mtl if mtl else "", "Y" if y_up else "Z"))

    def _ask_up_axis(self):
        """问一下用 Y-up 还是保持 Z-up，返回 True(Y) / False(Z) / None(取消)。"""
        win = tk.Toplevel(self)
        win.title("导出坐标轴")
        win.configure(bg="#f0f0f0")
        win.transient(self)
        win.resizable(False, False)
        var = tk.BooleanVar(value=True)
        tk.Label(win, text="导出的坐标轴：", bg="#f0f0f0").pack(anchor="w", padx=12, pady=(10, 2))
        tk.Radiobutton(win, text="Y-up（推荐：Blender 默认导入设置直接直立）", variable=var,
                       value=True, bg="#f0f0f0").pack(anchor="w", padx=18)
        tk.Radiobutton(win, text="Z-up（.mod 原始坐标；Blender 导入时需把 Up 设为 Z，"
                                 "否则会躺平）", variable=var,
                       value=False, bg="#f0f0f0").pack(anchor="w", padx=18)
        res = {"v": None}

        def ok():
            res["v"] = var.get()
            win.destroy()

        def cancel():
            res["v"] = None
            win.destroy()

        bar = tk.Frame(win, bg="#f0f0f0")
        bar.pack(fill="x", padx=12, pady=10)
        tk.Button(bar, text="导出", width=8, command=ok).pack(side="right")
        tk.Button(bar, text="取消", width=8, command=cancel).pack(side="right", padx=6)
        win.bind("<Return>", lambda e: ok())
        win.bind("<Escape>", lambda e: cancel())
        self.update_idletasks()
        try:
            x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
            y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
            win.geometry("+{}+{}".format(x, y))
        except Exception:  # noqa: BLE001
            pass
        win.grab_set()
        win.wait_window()
        return res["v"]

    def export_obj(self, path, y_up=True):
        """把当前场景写成 .obj（+ 同名 .mtl），返回 (顶点数, 面数, mtl 文件名)。"""
        meshes = getattr(self, "_all_meshes", [])
        if not meshes:
            raise RuntimeError("没有可导出的网格")

        folder = os.path.dirname(path)
        base = os.path.splitext(os.path.basename(path))[0]

        # 贴图：用视口当前用的那张（通常就是同目录的 1_h.dds）
        tex = None
        if self.view.texture is not None and self.mod_path:
            cand = ["1_h.dds", "1a_h.dds", "1+++_h.dds", "1++_h.dds", "1+_h.dds",
                    "1.dds", "2_h.dds",
                    "1_h.png", "1a_h.png", "1.png", "1_h.jpg", "1_h.jpeg", "1.jpg"]
            for c in cand:
                fp = os.path.join(os.path.dirname(self.mod_path), c)
                if os.path.exists(fp):
                    tex = c
                    break

        mtl_name = base + ".mtl"
        lines = ["# exported by MODVIEW (Blitzkrieg .mod viewer)",
                 "# modview-export: {}".format("y-up" if y_up else "z-up"), ""]
        if tex:
            lines.append("mtllib {}".format(mtl_name))
            lines.append("")
        lines.append("# {} objects".format(len(meshes)))

        v_off = 0
        t_off = 0
        n_v = 0
        n_f = 0

        for name, verts, faces, color, uv in meshes:
            v = np.asarray(verts, dtype=float)
            f = np.asarray(faces, dtype=np.int64)
            if y_up:
                # .mod 是 Z-up：转成 Y-up，Blender 默认导入就是立的
                v = np.stack([v[:, 0], v[:, 2], -v[:, 1]], axis=1)

            lines.append("o {}".format(name))
            if tex:
                lines.append("usemtl mod_tex")
            for p in v:
                lines.append("v {:.6f} {:.6f} {:.6f}".format(p[0], p[1], p[2]))

            has_uv = uv is not None and len(uv) == len(f)
            if has_uv:
                uva = np.asarray(uv, dtype=float)
                for k in range(len(f)):
                    for c in range(3):
                        u, vv = uva[k, c]
                        # 不要再翻 V：.mod 的 UV 已经是 Blender 约定（mod_scene 导入时翻过一次），
                        # 和 Blender 自己导出的 .obj 逐值相同，原样写出才对得上
                        lines.append("vt {:.6f} {:.6f}".format(u, vv))

            for k in range(len(f)):
                a, b, c = f[k]
                if has_uv:
                    lines.append("f {}/{} {}/{} {}/{}".format(
                        v_off + a + 1, t_off + k * 3 + 1,
                        v_off + b + 1, t_off + k * 3 + 2,
                        v_off + c + 1, t_off + k * 3 + 3))
                else:
                    lines.append("f {} {} {}".format(
                        v_off + a + 1, v_off + b + 1, v_off + c + 1))

            v_off += len(v)
            if has_uv:
                t_off += len(f) * 3
            n_v += len(v)
            n_f += len(f)
            lines.append("")

        with open(path, "w", encoding="ascii", errors="replace", newline="\n") as fh:
            fh.write("\n".join(lines))

        if tex:
            mtl = ["# exported by MODVIEW (Blitzkrieg .mod viewer)",
                   "newmtl mod_tex",
                   "Ka 1.000000 1.000000 1.000000",
                   "Kd 1.000000 1.000000 1.000000",
                   "Ks 0.000000 0.000000 0.000000",
                   "d 1.000000",
                   "illum 2",
                   "map_Kd {}".format(tex), ""]
            with open(os.path.join(folder, mtl_name), "w",
                      encoding="ascii", errors="replace", newline="\n") as fh:
                fh.write("\n".join(mtl))
            return n_v, n_f, mtl_name
        return n_v, n_f, None

    def show_about(self):
        messagebox.showinfo(
            "About",
            "闪电战 .mod 查看器 v1.1\n\n"
            "· 解析 Blitzkrieg .mod（mod_scene）\n"
            "· z-buffer 软件渲染\n"
            "· 内置 DXT1/DXT5 解码\n\n"
            "Python 3.8 + tkinter + numpy")

    def _fill_list(self):
        self._rebuild_rows()
        self._draw_outline()

    @staticmethod
    def _base_mat(o):
        """对象在 .mod 里的初始局部变换：location + 四元数。"""
        q = getattr(o, "rotation", None)
        M = np.eye(4)
        if q is None or len(q) != 4:
            R = np.eye(3)
        else:
            x, y, z, w = [float(v) for v in q]
            R = np.array([
                [1 - 2*(y*y + z*z), 2*(x*y - z*w),     2*(x*z + y*w)],
                [2*(x*y + z*w),     1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                [2*(x*z - y*w),     2*(y*z + x*w),     1 - 2*(x*x + y*y)],
            ])
        M[:3, :3] = R
        M[:3, 3] = np.asarray(getattr(o, "location", (0.0, 0.0, 0.0)), float)
        return M

    def _init_xform(self):
        """每个部件一份可编辑的局部矩阵（初始值来自 .mod 节点）。"""
        self._xform = {}
        self._parent = {}
        objs = self.scene.objects if self.scene else []
        by_id = {o.id: o for o in objs}
        for po in objs:
            for cid in po.children_ids:
                if cid in by_id and cid != po.id:
                    self._parent.setdefault(cid, po.id)
        for o in objs:
            self._xform[o.id] = self._base_mat(o)

    def _world_of(self, oid, depth=0):
        """沿父链累乘得到世界矩阵（时间线预览时用动画帧的局部矩阵）。"""
        M = None
        anim = getattr(self, "_anim_local", None)
        if anim:
            M = anim.get(oid)
        if M is None:
            M = self._xform.get(oid)
        if M is None:
            o = self._obj_by_id_map().get(oid)
            M = self._base_mat(o) if o is not None else np.eye(4)
        pid = getattr(self, "_parent", {}).get(oid)
        if pid is None or depth > 32:
            return np.array(M, dtype=float)
        return self._world_of(pid, depth + 1) @ M

    def _world_mat(self, o):
        return self._world_of(o.id)
    # ---------------- AABB
    def _toggle_import_anim(self):
        """菜单勾选：动画是否参与编辑与保存（默认不导入）。"""
        on = bool(self.anim_var.get())
        self.prefs["import_anim"] = on
        self._prefs_save()
        self._anim_on = on
        if self.scene is not None:
            # 时间线跟着开关走：开了显示，关了收起并退回静止姿态
            if on and getattr(self, "_anim_avail", 0):
                self._anim_show_bar()
            else:
                self._anim_hide_bar()
            prev = self._aabb_on_labels()
            self._aabb_items = self._collect_aabbs()
            self._apply_aabb_on_labels(prev)     # 动画 AABB 条目跟着增减
            self._apply_aabbs()
            self._draw_outline()
        n = getattr(self, "_anim_avail", 0)
        if not n:
            self.set_status("这个文件没有动画数据")
        elif on:
            self.set_status("动画已启用：{} 段（可预览，保存时原样写回）".format(n))
        else:
            self.set_status("动画预览已关闭：{} 段仍会原样写回文件（只是时间线不显示）".format(n))

    def _collect_aabbs(self):
        """.mod 里的 AABB 是 (中心xyz, 半长xyz) 共 6 个数；模型一个 + 动画各一组。"""
        items = []
        s = self.scene
        if s is None:
            return items

        def box_of(six):
            a = np.asarray(six, dtype=float).reshape(-1)
            if a.size < 6:
                return None
            return (a[:3] - a[3:6], a[:3] + a[3:6])

        def size_text(mn, mx):
            d = mx - mn
            return "{:.2f} × {:.2f} × {:.2f}".format(d[0], d[1], d[2])

        seen = set()

        def add(label, six):
            r = box_of(six)
            if r is None:
                return
            mn, mx = r
            key = tuple(np.round(np.concatenate([mn, mx]), 3))
            if key in seen:
                return                     # 完全相同的框不重复列
            seen.add(key)
            items.append((label, mn, mx, size_text(mn, mx)))

        if getattr(s, "aabb", None) is not None:
            add("AABB 模型", s.aabb)

        packs = (("AABB_A", getattr(s, "aabb_as", None)),
                 ("AABB_D", getattr(s, "aabb_ds", None)))
        for tag, arr in packs:
            if arr is None:
                continue
            arr = np.asarray(arr, dtype=float)
            if arr.ndim != 2 or arr.shape[1] < 6:
                continue
            for d in getattr(s, "anim_descs", []) or []:
                idx = getattr(d, "aabb_a" if tag == "AABB_A" else "aabb_d", -1)
                if idx is None or idx < 0 or idx >= len(arr):
                    continue
                add("{} {}".format(tag, d.name), arr[int(idx)])
        return items

    def _apply_aabbs(self):
        if hasattr(self, "show_aabb") and not self.show_aabb.get():
            self.view.set_aabbs([])
            self.view.highlight_aabb(None)
            return
        boxes = []
        for i, it in enumerate(getattr(self, "_aabb_items", [])):
            if i in self.aabb_on:
                boxes.append((it[0], it[1], it[2]))
        self.view.set_aabbs(boxes)
        sel = getattr(self, "aabb_sel", None)
        name = None
        if sel is not None and sel < len(self._aabb_items) and sel in self.aabb_on:
            name = self._aabb_items[sel][0]
        self.view.highlight_aabb(name)
        if name is not None:
            it = self._aabb_items[sel]
            self.view.set_aabb_sel(it[0], it[1], it[2])
        else:
            self.view.set_aabb_sel(None)

    def _toggle_aabb_on(self, ai):
        if ai in self.aabb_on:
            self.aabb_on.discard(ai)
        else:
            self.aabb_on.add(ai)
        self._draw_outline()
        self._apply_aabbs()

    def _aabb_on_labels(self):
        """当前显示的 AABB 用 label 记下来：重建列表后索引会变，label 不会。"""
        items = getattr(self, "_aabb_items", [])
        return {(items[i][0]) for i in getattr(self, "aabb_on", set())
                if 0 <= i < len(items)}

    def _apply_aabb_on_labels(self, labels, also=None):
        """按 label 恢复显示状态；also 指定的那个强制打开（新生成的框）。"""
        items = getattr(self, "_aabb_items", [])
        self.aabb_on = {i for i, it in enumerate(items) if it[0] in (labels or set())}
        if also:
            for i, it in enumerate(items):
                if it[0] == also:
                    self.aabb_on.add(i)
                    break

    # ---------------- 大纲（自绘）
    def _obj_by_id_map(self):
        return {o.id: o for o in (self.scene.objects if self.scene else [])}

    def _rebuild_rows(self):
        """按展开状态 + 过滤器算出要显示的行：[(obj, depth, has_kids)]"""
        self.rows = []
        s = self.scene
        if s is None:
            return
        by_id = {o.id: o for o in s.objects}
        parent_of = {}
        for po in s.objects:
            for cid in po.children_ids:
                if cid in by_id and cid != po.id:
                    parent_of.setdefault(cid, po.id)

        want_mesh = self.show_mesh.get() if hasattr(self, "show_mesh") else True
        want_loc = self.show_loc.get() if hasattr(self, "show_loc") else True

        q = ""
        if hasattr(self, "search_var"):
            q = (self.search_var.get() or "").strip().lower()

        def visible(o):
            if o.data is None:
                if not want_loc:
                    return False
            elif not want_mesh:
                return False
            if q and q not in o.name.lower() and q not in str(o.id):
                return False
            return True

        if q:
            # 搜索时平铺列出命中项，不按层级走
            for o in sorted(s.objects, key=lambda x: x.id):
                if visible(o):
                    self.rows.append((o, 0, False))
            return

        def any_visible(oid, depth=0):
            o = by_id.get(oid)
            if o is None or depth > 6:
                return False
            if visible(o):
                return True
            return any(any_visible(c, depth + 1) for c in o.children_ids if c in by_id)

        seen = set()

        def walk(oid, depth):
            if oid in seen or oid not in by_id:
                return
            seen.add(oid)
            o = by_id[oid]
            kids = [c for c in sorted(o.children_ids) if c in by_id and c != oid]
            has_kids = any(any_visible(c) for c in kids)
            vis = visible(o)
            if vis:
                self.rows.append((o, depth, has_kids))
            if oid in self.expanded:
                for c in kids:
                    walk(c, depth + (1 if vis else 0))

        for root in [o.id for o in sorted(s.objects, key=lambda x: x.id)
                     if o.id not in parent_of]:
            walk(root, 0)

    def _label(self, o):
        axes = "XYZ"
        lim = ""
        try:
            t = int(o.limits["type"])
            if t:
                # .mod 里旋转限制存的是弧度，显示成角度才对得上教程
                k = 180.0 / math.pi if t == 1 else 1.0
                lim = "  [{} {}{}~{}]".format(
                    {1: "旋转", 2: "位移"}.get(t, "?"),
                    axes[int(o.limits["axis"])],
                    round(o.limits["min"] * k, 1), round(o.limits["max"] * k, 1))
        except Exception:  # noqa: BLE001
            lim = ""
        return "{:>3}  {}  ({}){}".format(
            o.id, o.name, "定位器" if o.data is None else "网格", lim)

    def _draw_outline(self):
        c = self.outline
        c.delete("all")
        w = max(c.winfo_width(), 40)
        font = ("Microsoft YaHei UI", 9)
        max_right = 0
        y = 0
        wide = self.px(4000)          # 选中条铺满（超出会被 scrollregion 裁掉）
        for o, depth, has_kids in self.rows:
            sel = (o.id == self.sel_id)
            marked = (o.id in getattr(self, "sel_ids", ()))
            if sel:
                c.create_rectangle(0, y, wide, y + self.ROW_H, fill="#0078d7", width=0)
            elif marked:
                c.create_rectangle(0, y, wide, y + self.ROW_H, fill="#d3e3f5", width=0)
            fg = "#ffffff" if sel else "#000000"
            ex = self.px(4) + depth * self.INDENT
            cy = y + self.ROW_H // 2
            self._draw_eye(ex, cy, o.id not in self.hidden, sel,
                           solo=(o.id == getattr(self, "solo_id", None)))
            tx = ex + self.px(20)
            if has_kids:
                self._draw_tri(tx, cy, o.id in self.expanded, fg)
            tx += self.px(16)
            tid = c.create_text(tx, cy, anchor="w", font=font, fill=fg, text=self._label(o))
            bb = c.bbox(tid)
            if bb:
                max_right = max(max_right, bb[2])
            y += self.ROW_H

        # ---- AABB 段 ----
        if hasattr(self, "show_aabb") and not self.show_aabb.get():
            c.config(scrollregion=(0, 0, max(w, max_right + self.px(24)), max(y, 1)))
            return
        q = (self.search_var.get() or "").strip().lower() if hasattr(self, "search_var") else ""
        self._aabb_view = [i for i, it in enumerate(getattr(self, "_aabb_items", []))
                           if not q or q in str(i) or q in it[0].lower()]
        if self._aabb_view:
            c.create_line(0, y + 1, w, y + 1, fill="#c8d8e0")
            y += 3
        for i in self._aabb_view:
            label, mn, mx, size = self._aabb_items[i]
            sel = (i == getattr(self, "aabb_sel", None))
            if sel:
                c.create_rectangle(0, y, wide, y + self.ROW_H, fill="#0078d7", width=0)
            fg = "#ffffff" if sel else "#0a5c78"
            cy = y + self.ROW_H // 2
            self._draw_eye(self.px(4), cy, i in self.aabb_on, sel)
            tid = c.create_text(self.px(21), cy, anchor="w", font=font, fill=fg,
                                text="{}   {}".format(label, size))
            bb = c.bbox(tid)
            if bb:
                max_right = max(max_right, bb[2])
            y += self.ROW_H
        c.config(scrollregion=(0, 0, max(w, max_right + self.px(24)), max(y, 1)))

    def _draw_eye(self, x, cy, on, sel, solo=False):
        c = self.outline
        p = self.px
        col = "#ffffff" if sel else ("#333333" if on else "#bdbdbd")
        w, h = p(16), p(11)
        if solo:
            c.create_oval(x - p(2), cy - p(7), x + w + p(2), cy + p(7),
                          outline="#e08800", width=1)
        c.create_oval(x, cy - h // 2, x + w, cy + h // 2, outline=col, width=1)
        if on:
            r = p(5)
            c.create_oval(x + w // 2 - r // 2, cy - r // 2,
                          x + w // 2 + r // 2, cy + r // 2, fill=col, outline="")
        else:
            c.create_line(x + p(2), cy, x + w - p(2), cy, fill=col, width=1)

    def _draw_tri(self, x, cy, open_, fg):
        c = self.outline
        p = self.px
        col = fg if fg != "#000000" else "#606060"
        if open_:
            c.create_polygon(x, cy - p(4), x + p(9), cy - p(4), x + p(4.5), cy + p(3),
                             fill=col, outline="")
        else:
            c.create_polygon(x, cy - p(5), x + p(8), cy, x, cy + p(5), fill=col, outline="")

    def _on_outline_resize(self, evt):
        """只是变宽变窄，不用重排几百个条目；补一下 scrollregion 宽度即可。"""
        sr = str(self.outline.cget("scrollregion"))
        if not sr:
            self._draw_outline()
            return
        try:
            x0, y0, x1, y1 = [float(v) for v in sr.split()]
        except ValueError:
            self._draw_outline()
            return
        if evt.width > x1:
            self.outline.config(scrollregion=(x0, y0, evt.width, y1))

    def _on_outline_wheel(self, evt):
        if evt.state & 0x1:            # Shift 按住 -> 横向
            self.outline.xview_scroll(-1 if evt.delta > 0 else 1, "units")
        else:
            self.outline.yview_scroll(-1 if evt.delta > 0 else 1, "units")

    def _on_outline_click(self, evt):
        self._finish_rename(True)
        try:
            self.view.focus_set()      # 焦点离开搜索框，快捷键才不会被输入法吃掉
        except Exception:  # noqa: BLE001
            pass          # 点别处就当确认，输入框别赖着不走
        x = self.outline.canvasx(evt.x)
        y = self.outline.canvasy(evt.y)
        idx = int(y // self.ROW_H)
        n_obj = len(self.rows)
        if idx >= n_obj:
            view = getattr(self, "_aabb_view", [])
            k = idx - n_obj
            if k < 0 or k >= len(view):
                return
            ai = view[k]
            if 0 <= x <= self.px(16):                  # 行首眼睛
                if ai in self.aabb_on:
                    self.aabb_on.discard(ai)
                else:
                    self.aabb_on.add(ai)
                self._draw_outline()
                self._apply_aabbs()
                return
            self.aabb_sel = ai
            self.sel_id = None                 # 选框时取消部件选中，支点跟到框中心
            self.sel_ids = set()
            self._sel_anchor = None            # 框选行不参与对象连选
            self.on_select()                   # 互斥：清掉网格/定位器的高亮
            self._draw_outline()
            self._apply_aabbs()
            self.view.redraw(force=True)
            it = self._aabb_items[ai]
            self.set_status("{}   min({:.2f}, {:.2f}, {:.2f})  max({:.2f}, {:.2f}, {:.2f})".format(
                it[0], it[1][0], it[1][1], it[1][2], it[2][0], it[2][1], it[2][2]))
            return
        if idx < 0:
            return
        self.aabb_sel = None            # 点对象行就取消 AABB 段的选中，Delete 才不会删错
        self._apply_aabbs()
        o, depth, has_kids = self.rows[idx]
        ex = self.px(4) + depth * self.INDENT
        if ex - self.px(4) <= x <= ex + self.px(19):
            self._toggle_hidden(o.id)
            return
        if has_kids and ex + self.px(19) <= x <= ex + self.px(33):
            self._toggle_expand(o.id)
            return
        if getattr(self, "solo_id", None) is not None and o.id != self.solo_id:
            self.exit_solo()
        ctrl = bool(evt.state & 0x0004)
        shift = bool(evt.state & 0x0001)
        # Shift：从上次点的行连选到这一行（Windows/Blender 的资源管理器习惯）
        if shift and getattr(self, "_sel_anchor", None) is not None:
            a = self._sel_anchor
            if 0 <= a < len(self.rows):
                lo, hi = (a, idx) if a <= idx else (idx, a)
                ids = {r[0].id for r in self.rows[lo:hi + 1]}
                self.sel_ids = set(ids)
                self.sel_id = o.id
                self._draw_outline()
                self.on_select()
                act = self._obj_by_id_map().get(self.sel_id)
                self.set_status("连选 {} 项｜活动：{}".format(
                    len(ids), act.name if act is not None else "-"))
                return
        if ctrl:
            # Ctrl：逐个加选 / 再点取消
            if o.id in self.sel_ids:
                self.sel_ids.discard(o.id)
                if self.sel_id == o.id:
                    self.sel_id = next(iter(self.sel_ids), None)
            else:
                self.sel_ids.add(o.id)
                self.sel_id = o.id
            self._sel_anchor = idx
            self._draw_outline()
            self.on_select()
            n = len(self.sel_ids)
            if n > 1:
                act = self._obj_by_id_map().get(self.sel_id)
                self.set_status("已选 {} 项｜活动：{}（Ctrl 继续加选，Shift 连选）".format(
                    n, act.name if act is not None else "-"))
            return
        self.sel_ids = {o.id}
        self.sel_id = o.id
        self._sel_anchor = idx
        self._draw_outline()
        self.on_select()

    # ---------------- 大纲里双击改名
    def _on_outline_dbl(self, evt):
        x = self.outline.canvasx(evt.x)
        y = self.outline.canvasy(evt.y)
        idx = int(y // self.ROW_H)
        if idx < 0 or idx >= len(self.rows):
            return
        o, depth, has_kids = self.rows[idx]
        ex = self.px(4) + depth * self.INDENT
        if x < ex + self.px(34):        # 眼睛 / 折叠三角区不进入改名
            return
        self._begin_rename(o, depth, x, y)

    def _begin_rename(self, o, depth, cx, cy):
        self._rename_cancel()
        top = cy - self.outline.canvasy(0)
        tx = self.px(4) + depth * self.INDENT + self.px(36)
        p = self.px
        ent = tk.Entry(self.outline, font=("Microsoft YaHei UI", 9),
                       relief="solid", bd=1, bg="#ffffff", fg="#000000",
                       highlightthickness=0, insertwidth=1)
        ent.insert(0, o.name)
        ent.select_range(0, "end")
        w = max(p(96), min(p(360), p(9) * max(len(o.name) + 2, 8)))
        ent.place(x=tx, y=top + 1, width=w, height=self.ROW_H - 2)
        ent.focus_set()
        ent.bind("<Return>", lambda e: self._commit_rename(o, ent))
        ent.bind("<Escape>", lambda e: self._rename_cancel())
        ent.bind("<FocusOut>", lambda e: self._commit_rename(o, ent))
        # 右键 = 取消改名（不保存）；否则输入框会一直挂在行上
        ent.bind("<Button-3>", lambda e: (self._rename_cancel(), "break")[1])
        self._renaming = ent
        self._renaming_obj = o

    def _finish_rename(self, commit=True):
        """收尾正在进行的改名：commit=True 保存，False 直接取消。"""
        ent = getattr(self, "_renaming", None)
        o = getattr(self, "_renaming_obj", None)
        if ent is None:
            self._renaming_obj = None
            return
        if commit and o is not None:
            self._commit_rename(o, ent)
        else:
            self._rename_cancel()

    def _rename_cancel(self):
        ent = getattr(self, "_renaming", None)
        self._renaming = None
        self._renaming_obj = None
        if ent is not None:
            try:
                ent.destroy()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._draw_outline()      # 把被输入框盖住的那一行补画回来
            except Exception:  # noqa: BLE001
                pass

    def _commit_rename(self, o, ent):
        if ent is None or not ent.winfo_exists():
            return
        name = (ent.get() or "").strip()
        self._renaming = None
        self._renaming_obj = None
        try:
            ent.destroy()
        except Exception:  # noqa: BLE001
            pass
        if not name or name == o.name:
            self._draw_outline()
            return
        used = {x.name for x in (self.scene.objects if self.scene else []) if x.id != o.id}
        base, k = name, 1
        while name in used:
            name = "{}_{}".format(base, k)
            k += 1
        self._rename_object(o, name)

    def _rename_object(self, o, name):
        old = o.name
        o.name = name
        v = self.view
        for i, it in enumerate(v.meshes):
            if it[0] == old:
                v.meshes[i] = (name,) + tuple(it[1:])
        for i, it in enumerate(getattr(self, "_all_meshes", [])):
            if it[0] == old:
                self._all_meshes[i] = (name,) + tuple(it[1:])
        for gm in v.gl_meshes:
            if gm.name == old:
                gm.name = name
        v._gm_by_name = {gm.name: gm for gm in v.gl_meshes}
        for i, it in enumerate(getattr(self, "_all_locators", [])):
            if it[0] == old:
                self._all_locators[i] = (name,) + tuple(it[1:])
        for i, it in enumerate(v.locators):
            if it[0] == old:
                v.locators[i] = (name,) + tuple(it[1:])
        if v.hl_name == old:
            v.hl_name = name
        if v.solo == old:
            v.solo = name
        if v._loc_hl == old:
            v._loc_hl = name
        self._draw_outline()
        v._last_sig = None
        v.redraw(force=True)
        self.refresh_transform_panel()
        self.set_status("已重命名为 {}（只改内存里的名字，不改 .mod 文件）".format(name))

    def _on_outline_alt_click(self, evt):
        """Alt+点行首眼睛：独显该部件；再 Alt+点一次恢复。"""
        x = self.outline.canvasx(evt.x)
        y = self.outline.canvasy(evt.y)
        idx = int(y // self.ROW_H)
        n_obj = len(self.rows)
        if idx < 0 or idx >= n_obj + len(getattr(self, "_aabb_items", [])):
            return
        if idx >= n_obj:                     # AABB 行没有独显概念，按普通点击处理
            self._on_outline_click(evt)
            return
        o, depth, has_kids = self.rows[idx]
        ex = self.px(4) + depth * self.INDENT
        if not (ex - self.px(3) <= x <= ex + self.px(15)):      # 没点到眼睛 -> 当普通点击
            self._on_outline_click(evt)
            return
        if self.solo_id == o.id:
            self.exit_solo()
        else:
            self.solo_id = o.id
            self.view.solo = o.name
            self.set_status("独显：{}   （Alt+再点一次眼睛恢复）".format(o.name))
        self._draw_outline()
        self.view.redraw(force=True)

    def exit_solo(self):
        self.solo_id = None
        self.view.solo = None
        self.set_status("已恢复全部显示")
        self._draw_outline()
        self.view.redraw(force=True)

    def _toggle_expand(self, oid):
        if oid in self.expanded:
            self.expanded.discard(oid)
        else:
            self.expanded.add(oid)
        self._rebuild_rows()
        self._draw_outline()

    def expand_all(self):
        self.expanded = {o.id for o in (self.scene.objects if self.scene else [])
                         if o.children_ids}
        self._rebuild_rows()
        self._draw_outline()

    def collapse_all(self):
        self.expanded.clear()
        self._rebuild_rows()
        self._draw_outline()

    # ---------------------------------------------------------------- 父子级
    # 语义照 Blender：多选里「最后点中的对象」是父级，其余挂到它下面；
    # Ctrl+P 建立父子级（保持世界位置），Alt+P 解除。
    def _sel_obj_list(self):
        """按「活动对象排最后」的顺序返回当前选中对象。"""
        by_id = self._obj_by_id_map()
        objs = [by_id[i] for i in getattr(self, "sel_ids", set()) if i in by_id]
        objs.sort(key=lambda o: (o.id == getattr(self, "sel_id", None), -o.id))
        return objs

    def _detach_from_parent(self, cid):
        """把 cid 从所有父级的 children_ids 里摘掉，并清掉父级映射。"""
        for po in (self.scene.objects if self.scene else []):
            if cid in po.children_ids:
                po.children_ids = [c for c in po.children_ids if c != cid]
        getattr(self, "_parent", {}).pop(cid, None)

    def _refresh_hierarchy(self, ids):
        """重算指定部件的世界矩阵 -> 刷 GPU -> 同步大纲。"""
        by_id = self._obj_by_id_map()
        all_ids = []
        for oid in ids:
            if oid in by_id and oid not in all_ids:
                all_ids.append(oid)
            for d in self._descendants(oid):
                if d not in all_ids:
                    all_ids.append(d)
        self._apply_transforms(ids=all_ids)
        self._rebuild_rows()
        self._draw_outline()
        if self.view.ready:
            try:
                self.view._rebuild_outline()     # 描边网格跟着新世界矩阵重建
            except Exception:  # noqa: BLE001
                pass
            self.view.redraw(force=True)

    def set_parent_keep(self):
        """Ctrl+P：选中的对象挂到活动对象下面，世界位置保持不变。"""
        if not self.scene:
            return
        by_id = self._obj_by_id_map()
        parent = by_id.get(getattr(self, "sel_id", None))
        if parent is None:
            self.set_status("先选子级对象，再按住 Ctrl 加选父级（最后选中的当父级）")
            return
        kids = [o for o in self._sel_obj_list()
                if o.id != parent.id and o.id not in parent.children_ids]
        if not kids:
            if len(getattr(self, "sel_ids", ())) < 2:
                self.set_status("父级需要至少一个子级：Ctrl+点再加选一个对象，最后选中的当父级")
            else:
                self.set_status("{} 已经是这些对象的父级了".format(parent.name))
            return

        self._push_undo("设置父级")
        PW = self._world_of(parent.id)
        try:
            PW_inv = np.linalg.inv(PW)
        except np.linalg.LinAlgError:
            self.set_status("父级矩阵不可逆，无法建立父子级")
            return

        done, skipped = [], []
        for o in kids:
            # 成环保护：父级不能是子级的后代
            if parent.id in self._descendants(o.id):
                skipped.append(o.name)
                continue
            W = self._world_of(o.id)
            self._detach_from_parent(o.id)
            if o.id not in parent.children_ids:
                parent.children_ids.append(o.id)
            self._parent[o.id] = parent.id
            self._xform[o.id] = PW_inv @ W      # 局部矩阵 = 父级逆 × 原世界矩阵
            done.append(o)

        if not done:
            self.set_status("没有建立任何父子级（可能会成环）")
            return
        self.expanded.add(parent.id)            # 大纲里自动展开父级，层级立刻可见
        self._refresh_hierarchy([o.id for o in done])
        msg = "{} 个子级已挂到 {}".format(len(done), parent.name)
        if skipped:
            msg += "（{} 会成环，已跳过）".format("、".join(skipped))
        self.set_status(msg + "｜Alt+P 可解除")

    def clear_parent(self):
        """Alt+P：断开选中对象的父级，世界位置保持不变。"""
        by_id = self._obj_by_id_map()
        targets = [by_id[i] for i in getattr(self, "sel_ids", set())
                   if i in by_id and getattr(self, "_parent", {}).get(i) is not None]
        if not targets:
            o = by_id.get(getattr(self, "sel_id", None))
            if o is not None and getattr(self, "_parent", {}).get(o.id) is not None:
                targets = [o]
        if not targets:
            self.set_status("选中对象没有父级")
            return
        self._push_undo("解除父子级")
        ids = []
        for o in targets:
            W = self._world_of(o.id)
            self._detach_from_parent(o.id)
            self._xform[o.id] = W               # 世界矩阵直接变回局部矩阵
            ids.append(o.id)
        self._refresh_hierarchy(ids)
        self.set_status("已解除 {} 个对象的父子级".format(len(targets)))

    def _hotkey_parent(self, _e=None):
        if self._typing():
            return
        self.set_parent_keep()
        return "break"

    def _hotkey_unparent(self, _e=None):
        if self._typing():
            return
        self.clear_parent()
        return "break"

    def _on_outline_menu(self, evt):
        """大纲右键：父子级 / 展开折叠。右键同时取消正在进行的改名。"""
        self._rename_cancel()
        x = self.outline.canvasx(evt.x)
        y = self.outline.canvasy(evt.y)
        idx = int(y // self.ROW_H)
        n_obj = len(self.rows)
        if idx >= n_obj:
            # AABB 段右键：删除 / 显示隐藏
            view = getattr(self, "_aabb_view", [])
            k = idx - n_obj
            if 0 <= k < len(view):
                ai = view[k]
                self.aabb_sel = ai
                self._draw_outline()
                self._apply_aabbs()
                m = tk.Menu(self, tearoff=0)
                m.add_command(label="删除这个 AABB    Del",
                              command=self.delete_selected_aabb)
                m.add_command(label=("隐藏这个 AABB" if ai in self.aabb_on
                                     else "显示这个 AABB"),
                              command=lambda i=ai: self._toggle_aabb_on(i))
                try:
                    m.tk_popup(int(evt.x_root), int(evt.y_root))
                finally:
                    m.grab_release()
            return
        if 0 <= idx < len(self.rows):
            o = self.rows[idx][0]
            if o.id not in getattr(self, "sel_ids", ()):
                self.sel_ids = {o.id}
                self.sel_id = o.id
                self._draw_outline()
                self.on_select()
        by_id = self._obj_by_id_map()
        act = by_id.get(getattr(self, "sel_id", None))
        n = len(getattr(self, "sel_ids", ()))
        has_parent = bool(act is not None
                          and getattr(self, "_parent", {}).get(act.id) is not None)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="设置父级（最后选中者为父）    Ctrl+P",
                      command=self.set_parent_keep,
                      state="normal" if n >= 2 else "disabled")
        m.add_command(label="解除父子级    Alt+P",
                      command=self.clear_parent,
                      state="normal" if has_parent or n >= 2 else "disabled")
        m.add_separator()
        m.add_command(label="展开全部", command=self.expand_all)
        m.add_command(label="折叠全部", command=self.collapse_all)
        m.add_separator()
        self._fill_part_menu(m)            # 和视口右键同一套：变换/中心点/部件/工具
        try:
            m.tk_popup(int(evt.x_root), int(evt.y_root))
        finally:
            m.grab_release()

    # ---------------------------------------------------------------- 部件工具
    # 对应 blitzkrieg_helper 第 4~6 步：旋转限制 / AABB 碰撞盒 / 坐标点 Locator
    def _target_objs(self):
        by_id = self._obj_by_id_map()
        objs = [by_id[i] for i in getattr(self, "sel_ids", set()) if i in by_id]
        if not objs:
            o = by_id.get(getattr(self, "sel_id", None))
            if o is not None:
                objs = [o]
        return objs

    def _after_parts_change(self):
        self._rebuild_rows()
        self._draw_outline()
        self._refresh_model_aabb_from_world()   # 合并/删除/分离后框也要跟上
        if self.view.ready:
            self.view.redraw(force=True)

    def _sync_xml_platform_constraint(self, objs, axis, lo, hi, clear=False):
        """部件在 xml <Platforms> 里有对应平台时，把旋转范围同步到
        Constraint（水平=绕Z）或 ConstraintVertical（俯仰=绕Y）。
        游戏里炮塔的实际转动范围是从这里读的，只改 .mod 的部件
        limits 不会生效。返回同步的平台数。"""
        if not self.scene:
            return 0
        if int(axis) not in (1, 2) and not clear:
            return 0                     # X 轴在平台约束里没有对应项
        if self._xml_root is None:
            try:
                self._load_xml()
            except Exception:  # noqa: BLE001
                return 0
        if self._xml_root is None or self._xml_path is None:
            return 0
        plats = self._xml_root.find(".//Platforms")
        if plats is None:
            return 0
        want = {o.id for o in objs}
        touched = 0
        tags = ("Constraint", "ConstraintVertical") if clear else (
            ("ConstraintVertical",) if int(axis) == 1 else ("Constraint",))
        for it in plats.findall("item"):
            try:
                mp = int(float(it.get("ModelPart", "-1")))
            except ValueError:
                continue
            if mp not in want:
                continue
            for tag in tags:
                c = it.find(tag)
                if c is None:
                    c = ET.SubElement(it, tag)
                if clear:
                    c.set("Min", "0")
                    c.set("Max", "0")
                else:
                    c.set("Min", _fmt_num(math.radians(lo), 6))
                    c.set("Max", _fmt_num(math.radians(hi), 6))
            touched += 1
        if touched:
            self._xml_dirty = True
            self._xml_save()
            try:
                self._fill_xml_tree("")
            except Exception:  # noqa: BLE001
                pass
        return touched

    def set_rotation_limit(self, axis=2, lo=-360.0, hi=360.0):
        """给选中部件设旋转限制。界面用角度，.mod 里按弧度存（导出插件 np.radians）。"""
        objs = self._target_objs()
        if not objs:
            self.set_status("先选中要设限制的部件")
            return
        axis = int(axis) % 3
        lo, hi = float(lo), float(hi)
        if lo > hi:
            lo, hi = hi, lo
        self._push_undo("设置旋转范围")
        for o in objs:
            o.limits = {"type": 1, "axis": axis,
                        "min": math.radians(lo), "max": math.radians(hi)}
        n_xml = self._sync_xml_platform_constraint(objs, axis, lo, hi)
        self._after_parts_change()
        self.set_status("{} 个部件：旋转限制 {} 轴 {}° ~ {}°{}".format(
            len(objs), "XYZ"[axis], _fmt_num(lo, 1), _fmt_num(hi, 1),
            "（已同步 {} 个平台约束到 xml）".format(n_xml) if n_xml else ""))

    def set_translate_limit(self, axis=1, lo=2.9, hi=3.9):
        """给选中部件设位移限制（单位米，存盘时由 ModScene 换算成引擎单位）。"""
        objs = self._target_objs()
        if not objs:
            self.set_status("先选中要设限制的部件")
            return
        axis = int(axis) % 3
        lo, hi = float(lo), float(hi)
        if lo > hi:
            lo, hi = hi, lo
        self._push_undo("设置位移范围")
        for o in objs:
            o.limits = {"type": 2, "axis": axis, "min": lo, "max": hi}
        self._after_parts_change()
        self.set_status("{} 个部件：位移限制 {} 轴 {} m ~ {} m".format(
            len(objs), "XYZ"[axis], _fmt_num(lo, 2), _fmt_num(hi, 2)))

    def clear_limit(self):
        objs = self._target_objs()
        if not objs:
            self.set_status("先选中部件")
            return
        self._push_undo("清除限制")
        for o in objs:
            o.limits = {"type": 0, "axis": 0, "min": 0.0, "max": 0.0}
        n_xml = self._sync_xml_platform_constraint(objs, 2, 0.0, 0.0, clear=True)
        self._after_parts_change()
        self.set_status("已清除 {} 个部件的限制{}".format(
            len(objs), "（已同步清除 {} 个平台约束）".format(n_xml) if n_xml else ""))

    # ---- AABB
    def _world_bbox(self, objs):
        mn = np.array([1e18] * 3)
        mx = np.array([-1e18] * 3)
        found = False
        for o in objs:
            v = getattr(self, "_obj_local", {}).get(o.id)
            M = getattr(self, "_world", {}).get(o.id)
            if v is None or M is None or len(v) == 0:
                continue
            vw = np.asarray(v, float) @ M[:3, :3].T + M[:3, 3]
            mn = np.minimum(mn, vw.min(axis=0))
            mx = np.maximum(mx, vw.max(axis=0))
            found = True
        return (mn, mx) if found else None

    def _refresh_model_aabb_from_world(self):
        """按当前世界坐标重算主 AABB（部件被移动/旋转/缩放后调用）。
        只改数据，不动显示开关；受「自动AABB」开关控制（默认关）。"""
        if not getattr(self, "auto_aabb", None) or not self.auto_aabb.get():
            return
        if not self.scene:
            return
        objs = [o for o in self.scene.objects if o.data is not None]
        if not objs:
            return
        bbox = self._world_bbox(objs)
        if bbox is None:
            return
        mn, mx = bbox
        self.scene.aabb = np.concatenate([(mn + mx) * 0.5, (mx - mn) * 0.5])
        prev_on = self._aabb_on_labels()
        self._aabb_items = self._collect_aabbs()
        self._apply_aabb_on_labels(prev_on)     # 保持原来的显示状态，不强制点亮
        self._apply_aabbs()

    def make_aabb(self, selection_only=True):
        """按选中部件（含其全部子级）或整个场景算包围盒，写入 .mod 的主 AABB。"""
        if not self.scene:
            return
        if selection_only:
            objs = []
            for o in self._target_objs():
                for oid in [o.id] + self._descendants(o.id):
                    x = self._obj_by_id_map().get(oid)
                    if x is not None:
                        objs.append(x)
        else:
            objs = list(self.scene.objects)
        objs = [o for o in objs if o.data is not None]
        bbox = self._world_bbox(objs)
        if bbox is None:
            self.set_status("没有可统计的网格（先选部件，或改用「全部网格」）")
            return
        mn, mx = bbox
        center = (mn + mx) * 0.5
        half = (mx - mn) * 0.5
        prev_on = self._aabb_on_labels()          # 先记下别的框原来的显示状态
        self.scene.aabb = np.concatenate([center, half])
        self._aabb_items = self._collect_aabbs()
        # 只把新生成的主 AABB 打开，其余保持原样
        self._apply_aabb_on_labels(prev_on, also="AABB 模型")
        self._apply_aabbs()
        self._after_parts_change()
        self.set_status("AABB（{} 个网格）：中心 ({:.3f}, {:.3f}, {:.3f})  半尺寸 "
                        "({:.3f}, {:.3f}, {:.3f})".format(
                            len(objs), center[0], center[1], center[2],
                            half[0], half[1], half[2]))

    # ---- 定位器
    def _unique_name(self, base):
        used = {(o.name or "") for o in (self.scene.objects if self.scene else [])}
        if base not in used:
            return base
        i = len(base)
        while i > 0 and base[i - 1].isdigit():
            i -= 1
        head, num, width = base[:i], int(base[i:] or 0), len(base) - i
        start = num + 1 if width else 1
        for k in range(start, start + 999):
            cand = ("{}{:0{}d}".format(head, k, width) if width else
                    "{}{}".format(head, k))
            if cand not in used:
                return cand
        return base

    def _anchor_point(self, o, anchor):
        """坐标点该落在哪：部件包围盒的中心 / 顶面 / 底面 / 前缘（-Y），或世界原点。"""
        if anchor == "ORIGIN":
            return np.zeros(3)
        M = self._world_of(o.id)
        v = getattr(self, "_obj_local", {}).get(o.id)
        if v is None or len(v) == 0:
            return np.asarray(M[:3, 3], float).copy()
        vw = np.asarray(v, float) @ M[:3, :3].T + M[:3, 3]
        mn, mx = vw.min(axis=0), vw.max(axis=0)
        c = (mn + mx) * 0.5
        if anchor == "TOP":
            return np.array([c[0], c[1], mx[2]])
        if anchor == "BOTTOM":
            return np.array([c[0], c[1], mn[2]])
        if anchor == "FRONT":                       # .mod 是 Z-up，-Y 为前方
            return np.array([c[0], mn[1], c[2]])
        return np.asarray(c, float)

    def _rebuild_locators(self):
        locs = []
        for o in (self.scene.objects if self.scene else []):
            if o.data is None:
                M = self._world_of(o.id)
                locs.append((o.name, M[:3, 3].copy(), M[:3, :3].copy(), o.id))
        self._all_locators = locs
        self.view.set_locators(locs)
        return locs

    def add_locator(self, key="main_gun", anchor="CENTER", flip_x=False, parent=None,
                    quiet=False):
        if not self.scene:
            return None
        name, label, hint, rot = LOCATOR_MAP.get(key, LOCATOR_MAP["main_gun"])
        pobj = parent or self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if pobj is None:
            self.set_status("先选中一个部件，坐标点会挂到它下面")
            return None
        wp = self._anchor_point(pobj, anchor)
        PW = self._world_of(pobj.id)
        try:
            lp = np.linalg.inv(PW) @ np.array([wp[0], wp[1], wp[2], 1.0])
        except np.linalg.LinAlgError:
            return None
        obj = ModSceneObject()
        obj.id = max([o.id for o in self.scene.objects] + [0]) + 1
        obj.name = self._unique_name(name)
        obj.location = np.asarray(lp[:3], float)
        obj.rotation = quat_from_x_deg(-rot if flip_x else rot)
        obj.data = None
        obj.children_ids = []
        self.scene.objects.append(obj)
        if obj.id not in pobj.children_ids:
            pobj.children_ids.append(obj.id)
        self._parent[obj.id] = pobj.id
        self._xform[obj.id] = self._base_mat(obj)
        self.expanded.add(pobj.id)
        self._rebuild_locators()
        self._after_parts_change()
        if not quiet:
            self.sel_id, self.sel_ids = obj.id, {obj.id}
            self._draw_outline()
            self.set_status("已生成 {}（{}）挂到 {}｜X {:.0f}°".format(
                obj.name, label, pobj.name, rot))
        return obj

    def add_all_locators(self, anchor="CENTER", flip_x=False):
        """按挂载关系（Tracks/Basis/Turret/GunCarriage/MainBarrel）批量生成坐标点。"""
        if not self.scene:
            return
        meshes = [o for o in self.scene.objects if o.data is not None]
        if not meshes:
            self.set_status("场景里没有网格部件")
            return
        preferred = [o for o in self._target_objs() if o.data is not None] or meshes
        cache = {}

        def resolve(hint):
            if hint not in cache:
                cache[hint] = (find_obj_by_name(preferred, hint)
                               or find_obj_by_name(meshes, hint))
            return cache[hint]

        self._push_undo("批量生成定位器")
        made = []
        for key, _n, _l, hint, _r in LOCATOR_DEFS:
            target = resolve(hint)
            if target is None:
                continue
            obj = self.add_locator(key, anchor=anchor, flip_x=flip_x,
                                   parent=target, quiet=True)
            if obj is not None:
                made.append(obj.name)
        if not made:
            self.set_status("没找到 Tracks / Basis / Turret / GunCarriage / MainBarrel，"
                            "没法挂坐标点")
            return
        self._after_parts_change()
        self.set_status("已生成 {} 个坐标点：{}".format(len(made), "、".join(made)))

    def _delete_objects(self, objs):
        """从场景里删掉这些对象（连同它们的所有子级），返回被删掉的 id 集合。"""
        by_id = self._obj_by_id_map()
        drop = set()
        stack = [o.id for o in objs]
        while stack:
            oid = stack.pop()
            if oid in drop:
                continue
            drop.add(oid)
            o = by_id.get(oid)
            if o is not None:
                stack.extend(o.children_ids)
        for po in self.scene.objects:
            if po.id in drop:
                continue
            po.children_ids = [c for c in po.children_ids if c not in drop]
        for oid in drop:
            self._parent.pop(oid, None)
            self._xform.pop(oid, None)
            self._obj_local.pop(oid, None)
        self.scene.objects = [o for o in self.scene.objects if o.id not in drop]
        self.sel_ids -= drop
        if self.sel_id in drop:
            self.sel_id = next(iter(self.sel_ids), None)
        # 根节点被删了（比如删了 Tracks），要换一个还剩下的顶层对象当根
        if getattr(self.scene, "root_id", -1) in drop:
            parents = set(self._parent.values())
            roots = [o.id for o in self.scene.objects if o.id not in parents]
            self.scene.root_id = roots[0] if roots else -1
        return drop

    def _after_delete(self):
        """删东西之后把视口、大纲、AABB 全部刷新一遍。"""
        self._rebuild_locators()
        self._rebuild_meshes_from_scene()
        prev_on = self._aabb_on_labels()
        self._aabb_items = self._collect_aabbs()
        self._apply_aabb_on_labels(prev_on)
        self.aabb_sel = None
        self._apply_aabbs()
        self._rebuild_rows()
        self._draw_outline()
        self.on_select()

    def remove_selected_locators(self):
        """删掉选中的定位器（只处理 data 为空的节点，网格不动）。"""
        if not self.scene:
            return
        if not self.prefs.get("del_locator", True):
            self.set_status("首选项未开启「允许删除定位器」")
            return
        targets = [o for o in self._target_objs() if o.data is None]
        if not targets:
            self.set_status("选中的不是定位器（只有定位器能这样删）")
            return
        self._push_undo("删除定位器")
        gone = self._delete_objects(targets)
        self._after_delete()
        self.set_status("已删除 {} 个定位器".format(len(gone)))

    # ---- 删除
    def delete_selected_aabb(self):
        """删掉大纲 AABB 段里选中的那个框。

        主 AABB 直接置空；动画/死亡 AABB 是把对应动画的引用摘掉（-1），
        数组里的数据保留但不再列出来，导出时也不会被引用。
        """
        if self.scene is None:
            return
        items = getattr(self, "_aabb_items", [])
        ai = getattr(self, "aabb_sel", None)
        if ai is None or ai < 0 or ai >= len(items):
            self.set_status("先在大纲的 AABB 段点一个框，再删除")
            return
        if not self.prefs.get("del_aabb", False):
            self.set_status("首选项未开启「允许删除 AABB」")
            return
        label = items[ai][0]
        done = False
        if label.startswith("AABB 模型"):
            self.scene.aabb = None
            done = True
        else:
            tag, _sp, anim_name = label.partition(" ")
            field = {"AABB_A": "aabb_a", "AABB_D": "aabb_d"}.get(tag.strip())
            if field:
                for d in getattr(self.scene, "anim_descs", []) or []:
                    if d.name == anim_name and getattr(d, field, -1) is not None \
                            and getattr(d, field, -1) >= 0:
                        setattr(d, field, -1)
                        done = True
                        break
        if not done:
            self.set_status("没找到「{}」对应的数据源".format(label))
            return
        prev_on = self._aabb_on_labels()
        self._aabb_items = self._collect_aabbs()
        self.aabb_sel = None
        self._apply_aabb_on_labels(prev_on)      # 其余框的显示状态保持不变
        self._apply_aabbs()
        self._after_parts_change()
        self.set_status("已删除 AABB：{}".format(label))

    def delete_selected(self):
        """Delete 键：AABB 段选中的框优先，否则删选中对象（网格/定位器都行）。

        删网格会连它的子级一起删（和 Blender 的 X 一致），所以先弹确认。
        """
        if getattr(self, "aabb_sel", None) is not None:
            if not self.prefs.get("del_aabb", False):
                self.set_status("首选项未开启「允许删除 AABB」")
                return
            self.delete_selected_aabb()
            return
        objs = self._target_objs()
        if not objs:
            self.set_status("没有选中可删除的对象")
            return
        meshes = [o for o in objs if o.data is not None]
        locs = [o for o in objs if o.data is None]
        if meshes and not self.prefs.get("del_mesh", False):
            self.set_status("首选项未开启「允许删除网格」（网格删除可能父子级错乱）")
            return
        if locs and not self.prefs.get("del_locator", True):
            self.set_status("首选项未开启「允许删除定位器」")
            return
        if not meshes:
            self.remove_selected_locators()
            return
        by_id = self._obj_by_id_map()
        extra = 0
        for o in meshes:
            extra += len(self._descendants(o.id))
        msg = "将删除 {} 个部件".format(len(meshes))
        if extra:
            msg += "（连同它们的 {} 个子级）".format(extra)
        msg += "，此操作可 Ctrl+Z 撤销。\n\n确定删除吗？"
        if not messagebox.askyesno("删除部件", msg):
            self.set_status("已取消删除")
            return
        self._push_undo("删除部件")
        gone = self._delete_objects(objs)
        self._after_delete()
        self.set_status("已删除 {} 个部件".format(len(gone)))

    def _hotkey_delete(self, _e=None):
        if self._typing():
            return
        self.delete_selected()
        return "break"

    def _hotkey_save(self, _e=None):
        if self._typing():
            return "break"
        self.save_mod_as()
        return "break"

    # ---- 松散块分离
    @staticmethod
    def _face_uv_of(data):
        """从 .mod 的 loops/uvs 还原「每面 3 个角」的 UV，形状 (M, 3, 2)。"""
        try:
            f = np.asarray(getattr(data, "faces", None), np.int64)
            loops = np.asarray(getattr(data, "loops", None), np.int64)
            uvs = np.asarray(getattr(data, "uvs", None), float)
            if f is None or loops is None or uvs is None:
                return None
            if len(f) == 0 or len(loops) != 3 * len(f) or len(uvs) == 0:
                return None
            idx = loops[:, 2]
            if int(idx.min()) < 0 or int(idx.max()) >= len(uvs):
                return None
            return uvs[idx].reshape(-1, 3, 2)
        except Exception:  # noqa: BLE001
            return None

    def _rebuild_meshes_from_scene(self, uv_by=None):
        """场景改动后重建整个网格列表（含 GL 缓冲），并保住相机视角。"""
        uv_by = uv_by or {}
        cam = (self.view.yaw, self.view.pitch, self.view.dist, list(self.view.pan),
               np.asarray(self.view.center, float).copy(), self.view.radius)
        objs = self.scene.objects if self.scene else []
        # 顺手把世界矩阵缓存也刷新，别让 gizmo / 尺寸面板用到旧值
        self._world = {o.id: self._world_of(o.id) for o in objs}
        meshes = []
        for o in objs:
            v = getattr(self, "_obj_local", {}).get(o.id)
            if o.data is None or v is None or len(v) == 0:
                continue
            M = self._world.get(o.id)
            if M is None:
                continue
            vw = np.asarray(v, float) @ M[:3, :3].T + M[:3, 3]
            f = np.asarray(o.data.faces, np.int64)
            uv = uv_by.get(o.name)
            if uv is not None and len(uv) != len(f):
                uv = None
            if uv is None:
                uv = self._face_uv_of(o.data)      # 拓扑改过的，从 loops/uvs 还原
            if uv is not None and len(uv) != len(f):
                uv = None
            meshes.append((o.name, vw, f, (168, 168, 168), uv))
        self._all_meshes = meshes
        self.view.set_meshes(meshes)
        # set_meshes 会按新包围盒重设相机，这里按原样还原，免得视角跳走
        self.view.yaw, self.view.pitch, self.view.dist = cam[0], cam[1], cam[2]
        self.view.pan = cam[3]
        self.view.center = cam[4]
        self.view.radius = cam[5]
        self.view.redraw(force=True)

    # ---------------- 选择相连面（工具栏第 4 个按钮）
    def _toggle_link_uv(self):
        on = bool(self.link_uv_var.get())
        self.prefs["link_uv"] = on
        self._prefs_save()
        self.set_status("选择相连面：划界 {}按 UV 断开".format("" if on else "不"))

    def _set_link_mode(self, mode):
        self.prefs["link_mode"] = mode
        self._prefs_save()
        self.set_status("划界方式：{}".format(
            "拓扑（整块相连，同 Blender Ctrl+L）" if mode == "topo"
            else "拓扑 + 断开朝向相反的面"))

    def _set_link_flat(self, deg):
        self.prefs["link_mode"] = "flat"
        self.prefs["link_angle"] = float(deg)
        self._prefs_save()
        self.set_status("划界方式：平直面 ≤ {}°（同 Blender 的 Linked Flat Faces）".format(int(deg)))

    def _clear_face_sel(self):
        self._face_sel = None
        try:
            self.view.set_face_sel(None)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _same_uv_edge(f, uvf, i, j):
        """两个三角面在共享边上的 UV 是否连续（不连续 = UV 岛边界）。"""
        shared = [k for k in range(3) if f[i][k] in f[j]]
        if len(shared) < 2:
            return False
        for k in shared:
            vid = f[i][k]
            k2 = int(np.nonzero(f[j] == vid)[0][0])
            if float(np.abs(uvf[i][k] - uvf[j][k2]).max()) > 1e-4:
                return False
        return True

    def _linked_faces(self, o, tri, use_uv=True, angle=30.0, mode="normal"):
        """沿边泛洪选相连面。

        mode="topo"  ：纯拓扑，沿边连到整块（同 Blender 的 Ctrl+L 默认）—— 默认用这个
        mode="normal"：额外断开「朝向相反」的相邻面（法线点乘 < 0）
        mode="flat"  ：照 Linked Flat Faces——法线夹角超过 angle 断开
        """
        d = o.data
        v = np.asarray(self._obj_local.get(o.id, d.vertices), float)
        f = np.asarray(d.faces, np.int64)
        if len(f) == 0 or not (0 <= int(tri) < len(f)):
            return np.empty(0, np.int64)
        p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        n = np.cross(p1 - p0, p2 - p0)
        ln = np.linalg.norm(n, axis=1)
        ln[ln == 0] = 1.0
        n = n / ln[:, None]

        # 边 -> 面（只认共享边的相邻，顶点相碰不算相连）
        edge_faces = {}
        for i, ff in enumerate(f):
            for k in range(3):
                a, b = int(ff[k]), int(ff[(k + 1) % 3])
                edge_faces.setdefault((a, b) if a < b else (b, a), []).append(i)

        uvf = None
        if use_uv:
            try:
                uvf = self._face_uv_of(d)
            except Exception:  # noqa: BLE001
                uvf = None
            if uvf is not None and len(uvf) != len(f):
                uvf = None

        cos_thr = math.cos(math.radians(angle))
        seen = np.zeros(len(f), bool)
        seen[int(tri)] = True
        stack = [int(tri)]
        while stack:
            i = stack.pop()
            for k in range(3):
                a, b = int(f[i][k]), int(f[i][(k + 1) % 3])
                key = (a, b) if a < b else (b, a)
                for j in edge_faces.get(key, ()):
                    if j == i or seen[j]:
                        continue
                    if mode == "flat":
                        if float(np.dot(n[i], n[j])) < cos_thr:
                            continue
                    elif mode == "normal":
                        if float(np.dot(n[i], n[j])) < 0.0:   # 朝向相反 = 断开
                            continue
                    # mode == "topo"：纯拓扑，全部相连（同 Blender Ctrl+L 默认）
                    if uvf is not None and not self._same_uv_edge(f, uvf, i, j):
                        continue
                    seen[j] = True
                    stack.append(j)
        return np.nonzero(seen)[0]

    def _on_face_pick(self, name, tri, state=0):
        if self.scene is None:
            return
        o = next((x for x in self.scene.objects if x.name == name), None)
        if o is None or o.data is None:
            return
        if getattr(self, "sel_id", None) != o.id:
            self.on_view_pick("mesh", name, 0)      # 点面同时把这块设为选中部件
        shift = bool(int(state) & 0x0001)           # Shift+左键 = 追加到当前选区
        use_uv = bool(self.prefs.get("link_uv", False))
        angle = float(self.prefs.get("link_angle", 30.0))
        mode = str(self.prefs.get("link_mode", "topo") or "topo")
        sel = self._linked_faces(o, tri, use_uv=use_uv, angle=angle, mode=mode)
        if len(sel) == 0:
            return
        cur = getattr(self, "_face_sel", None)
        if shift and cur and cur.get("name") == name:
            sel = np.unique(np.concatenate([np.asarray(cur["tris"], np.int64), sel]))
        v = np.asarray(self._obj_local.get(o.id, o.data.vertices), float)
        f = np.asarray(o.data.faces, np.int64)
        W = self._world_of(o.id)
        vw = v @ W[:3, :3].T + W[:3, 3]
        self._face_sel = {"name": name, "tris": sel}
        self.view.set_face_sel(name, vw, f[sel])
        self.set_status("已选中 {} 的 {} 个面（整块 {} 个）—— Shift+左键可追加，P 分离，Esc 取消".format(
            name, len(sel), len(f)))

    def separate_faces(self):
        """把 _face_sel 选中的面拆成新对象（保持 UV 和父子级）。"""
        fs = getattr(self, "_face_sel", None)
        if not fs:
            return False
        o = next((x for x in self.scene.objects if x.name == fs["name"]), None)
        if o is None or o.data is None:
            return False
        if getattr(self, "sel_id", None) != o.id:
            self.set_status("分离只对当前选中的部件生效：先在视口里点一下 {} 再按 P".format(
                o.name))
            return False
        v = np.asarray(self._obj_local.get(o.id, o.data.vertices), float)
        f = np.asarray(o.data.faces, np.int64)
        uv = self._face_uv_of(o.data)
        if uv is not None and len(uv) != len(f):
            uv = None
        sel = np.zeros(len(f), bool)
        idx = np.asarray(fs["tris"], np.int64)
        idx = idx[(idx >= 0) & (idx < len(f))]
        sel[idx] = True
        if not sel.any():
            self._clear_face_sel()
            return False
        if sel.all():
            self.set_status("选中的是整块网格，换个面再点")
            return False

        self._push_undo("分离选中面")
        L = np.asarray(self._xform.get(o.id, self._base_mat(o)), float)
        pid = getattr(self, "_parent", {}).get(o.id)
        uv_by = {}

        sub_f = f[sel]
        sub_uv = uv[sel] if uv is not None else None
        used = np.unique(sub_f)
        remap = -np.ones(len(v), np.int64)
        remap[used] = np.arange(len(used))
        obj = ModSceneObject()
        obj.id = max([x.id for x in self.scene.objects] + [0]) + 1
        obj.name = self._unique_name("{}_faces".format(o.name))
        obj.data = ModSceneMeshData(name=obj.name, vertices=np.asarray(v[used], float),
                                    faces=remap[sub_f],
                                    loops=np.empty((0, 3), np.int64))
        self._rebuild_mesh_loops(obj.data, sub_uv)
        obj.location = np.asarray(L[:3, 3], float).copy()
        obj.rotation = quat_from_mat3(L[:3, :3])
        obj.children_ids = []
        self.scene.objects.append(obj)
        if pid is not None:
            po = self._obj_by_id_map().get(pid)
            if po is not None and obj.id not in po.children_ids:
                po.children_ids.append(obj.id)
            self._parent[obj.id] = pid
        self._xform[obj.id] = np.array(L, copy=True)
        self._obj_local[obj.id] = np.asarray(obj.data.vertices, float)
        uv_by[obj.name] = sub_uv

        keep = ~sel
        rest_f = f[keep]
        rest_uv = uv[keep] if uv is not None else None
        used2 = np.unique(rest_f)
        remap2 = -np.ones(len(v), np.int64)
        remap2[used2] = np.arange(len(used2))
        o.data.vertices = np.asarray(v[used2], float)
        o.data.faces = remap2[rest_f]
        self._rebuild_mesh_loops(o.data, rest_uv)
        self._obj_local[o.id] = o.data.vertices
        uv_by[o.name] = rest_uv

        self._clear_face_sel()
        self._rebuild_meshes_from_scene(uv_by)
        self._rebuild_rows()
        self._draw_outline()
        self.set_status("已把 {} 个面分离成 {}".format(len(idx), obj.name))
        return True

    def separate_loose(self, confirm=True):
        """P：把选中网格按松散块拆成多个对象（Blender 的 Separate by Loose Parts）。"""
        if not self.scene:
            return
        if not self.prefs.get("sep_loose", False):
            self.set_status("首选项未开启「允许松散分离」（可能导致父子级错乱）")
            return
        objs = []
        for o in self._target_objs():
            if o.data is None:
                continue
            f = getattr(o.data, "faces", None)
            if f is None or len(f) == 0:
                continue
            objs.append(o)
        if not objs:
            self.set_status("先选中要分离的网格部件")
            return
        # 先算一下会拆出多少块，给二次确认（P 是不可逆的大动作）
        if confirm:
            total_new = 0
            for o in objs:
                _gid, _order = loose_face_groups(
                    np.asarray(o.data.vertices, float),
                    np.asarray(o.data.faces, np.int64))
                total_new += max(0, len(_order) - 1)
            if total_new <= 0:
                self.set_status("选中的网格本身就是整体，没有可分离的松散块")
                return
            names = "、".join(o.name for o in objs[:3]) + (" 等" if len(objs) > 3 else "")
            if not messagebox.askyesno(
                    "按松散块分离",
                    "{} 将拆成更多对象，共新增 {} 个。\n\n继续吗？（可 Ctrl+Z 撤销）".format(
                        names, total_new)):
                self.set_status("已取消分离")
                return
        # 现有 UV 是按面存的，分离时要跟着切
        uv_by = {}
        for it in self.view.meshes:
            if len(it) > 5:
                uv_by[it[0]] = it[5]
            elif len(it) > 4:
                uv_by[it[0]] = it[4]

        self._push_undo("按松散块分离")
        made = []
        for o in objs:
            v = np.asarray(o.data.vertices, float)
            f = np.asarray(o.data.faces, np.int64)
            gid, order = loose_face_groups(v, f)
            if len(order) <= 1:
                continue                     # 本来就是整体
            uv = uv_by.get(o.name)
            if uv is not None and len(uv) != len(f):
                uv = None
            L = np.asarray(self._xform.get(o.id, self._base_mat(o)), float)
            pid = getattr(self, "_parent", {}).get(o.id)
            for k, gi in enumerate(order):
                mask = (gid == gi)
                sub_f = f[mask]
                used = np.unique(sub_f)
                remap = -np.ones(len(v), np.int64)
                remap[used] = np.arange(len(used))
                sub_v = np.asarray(v[used], float)
                sub_f = remap[sub_f]
                sub_uv = uv[mask] if uv is not None else None
                if k == 0:
                    # 最大块留在原对象里；loops/uvs 必须按新拓扑重建，
                    # 否则 data 里没有 UV，保存出去就丢了（视口只是显示层）
                    o.data.vertices = sub_v
                    o.data.faces = sub_f
                    self._rebuild_mesh_loops(o.data, sub_uv)
                    self._obj_local[o.id] = sub_v
                    uv_by[o.name] = sub_uv
                    continue
                obj = ModSceneObject()
                obj.id = max([x.id for x in self.scene.objects] + [0]) + 1
                obj.name = self._unique_name("{}_{}".format(o.name, k))
                obj.data = ModSceneMeshData(name=obj.name, vertices=sub_v, faces=sub_f,
                                            loops=np.empty((0, 3), np.int64))
                self._rebuild_mesh_loops(obj.data, sub_uv)      # 带上原 UV
                obj.location = np.asarray(L[:3, 3], float).copy()
                obj.rotation = quat_from_mat3(L[:3, :3])
                obj.children_ids = []
                self.scene.objects.append(obj)
                if pid is not None:
                    po = self._obj_by_id_map().get(pid)
                    if po is not None and obj.id not in po.children_ids:
                        po.children_ids.append(obj.id)
                    self._parent[obj.id] = pid
                self._xform[obj.id] = np.array(L, copy=True)
                self._obj_local[obj.id] = sub_v
                uv_by[obj.name] = sub_uv
                made.append(obj.name)

        if not made:
            self.set_status("选中的网格本身就是整体，没有可分离的松散块")
            return
        self._rebuild_meshes_from_scene(uv_by)
        self._rebuild_rows()
        self._draw_outline()
        self.set_status("分离完成：新生成 {} 个松散块（最大块留在原对象）".format(len(made)))

    def join_meshes(self):
        """Ctrl+J：把选中的多个网格合并进活动对象（Blender 的 Join）。

        其它对象的顶点先换算成世界坐标、再换算到活动对象的局部空间，
        所以合并后外观完全不变；UV 按面接过去，拓扑变了会重建 loops/normals。
        """
        if not self.scene:
            return
        if not self.prefs.get("join", False):
            self.set_status("首选项未开启「允许合并网格」（可能导致父子级错乱）")
            return
        # 空网格（0 顶点/0 面）也算数：合并时只把对象本身并掉
        objs = [o for o in self._target_objs() if o.data is not None]
        if len(objs) < 2:
            self.set_status("先选两个以上网格（Ctrl/Shift 加选），再按 Ctrl+J 合并")
            return
        by_id = self._obj_by_id_map()
        active = by_id.get(getattr(self, "sel_id", None))
        if active not in objs:
            active = objs[0]
        Wa = self._world_of(active.id)
        try:
            Wa_inv = np.linalg.inv(Wa)
        except np.linalg.LinAlgError:
            self.set_status("活动对象矩阵不可逆，无法合并")
            return

        # 显示用的 UV 是"每面 3 个角"，优先从视口网格取，其次从 loops/uvs 还原
        uv_by = {}
        for it in self.view.meshes:
            uv_by[it[0]] = it[5] if len(it) > 5 else (it[4] if len(it) > 4 else None)

        self._push_undo("合并网格")
        vs, fs, uvs = [], [], []
        v_off = 0
        gone = []
        n_empty = 0
        for o in objs:
            vv = self._obj_local.get(o.id)
            v = (np.asarray(vv, float) if vv is not None
                 else np.empty((0, 3), float))
            f = np.asarray(getattr(o.data, "faces", np.empty((0, 3))), np.int64)
            if len(v) == 0 or len(f) == 0:
                n_empty += 1
                if o.id != active.id:
                    gone.append((o.id, self._world_of(o.id)))
                continue
            M = self._world_of(o.id)
            vw = v @ M[:3, :3].T + M[:3, 3]
            vl = (Wa_inv[:3, :3] @ vw.T).T + Wa_inv[:3, 3]     # 世界 -> 活动对象局部
            uv = uv_by.get(o.name)
            if uv is not None and len(uv) != len(f):
                uv = None
            if uv is None:
                uv = self._face_uv_of(o.data)
            if uv is None or len(uv) != len(f):
                uv = np.zeros((len(f), 3, 2), float)
            vs.append(vl)
            fs.append(f + v_off)
            uvs.append(np.asarray(uv, float).reshape(-1, 3, 2))
            v_off += len(vl)
            if o.id != active.id:
                gone.append((o.id, M))

        if vs:
            V = np.vstack(vs)
            F = np.vstack(fs)
            UV = np.vstack(uvs)
        else:
            V = np.asarray(active.data.vertices, float)
            F = np.asarray(active.data.faces, np.int64)
            UV = None
        data = active.data
        data.vertices = V
        data.faces = F
        self._obj_local[active.id] = V
        if UV is not None:
            self._rebuild_mesh_loops(data, UV)      # 顶点/面变了，loops 必须重算

        # 被并掉的对象里有子级的，转挂到活动对象下（世界位置不变）
        for oid, M in gone:
            o = by_id.get(oid)
            if o is None:
                continue
            for cid in list(o.children_ids):
                if cid not in self._xform or cid == active.id:
                    continue
                Lc = np.asarray(self._xform[cid], float)
                self._xform[cid] = Wa_inv @ M @ Lc
                self._parent[cid] = active.id
                if cid not in active.children_ids:
                    active.children_ids.append(cid)
            for po in self.scene.objects:
                if oid in po.children_ids:
                    po.children_ids = [c for c in po.children_ids if c != oid]
            self._parent.pop(oid, None)
            self._xform.pop(oid, None)
            self._obj_local.pop(oid, None)

        drop = {oid for oid, _m in gone}
        self.scene.objects = [o for o in self.scene.objects if o.id not in drop]

        self.sel_id = active.id
        self.sel_ids = {active.id}
        self._rebuild_locators()
        self._rebuild_meshes_from_scene()
        self._aabb_items = self._collect_aabbs()
        self._apply_aabbs()
        self._rebuild_rows()
        self._draw_outline()
        self.on_select()
        self.set_status("已合并 {} 个网格 -> {}（{} 顶点 / {} 面）{}".format(
            len(objs), active.name, len(V), len(F),
            "，其中 {} 个空网格已并掉".format(n_empty) if n_empty else ""))

    def _hotkey_join(self, _e=None):
        if self._typing():
            return "break"
        self.join_meshes()
        return "break"

    def _hotkey_separate(self, _e=None):
        """P：把「选择相连面」选中的面分离出去。"""
        if self._typing():
            return
        if not getattr(self, "_face_sel", None):
            self.set_status("P 是分离选中的面：先用工具栏第 4 个「选择相连面」点一片面；"
                            "按松散块分离是 Shift+P")
            return "break"
        self.separate_faces()
        return "break"

    def _hotkey_separate_loose(self, _e=None):
        """Shift+P：按松散块分离。"""
        if self._typing():
            return
        self.separate_loose()
        return "break"

    # ---- XML 平台编辑器（Platforms / Guns）
    @staticmethod
    def _xml_float(el, attr, default=0.0):
        try:
            return float(el.get(attr, default))
        except Exception:  # noqa: BLE001
            return float(default)

    def _xml_model_part_label(self, mp):
        """ModelPart 的 id 换成模型里的部件名。"""
        try:
            oid = int(mp)
        except Exception:  # noqa: BLE001
            return "id {}".format(mp)
        if oid < 0:
            return "未指定部件（-1）"
        o = self._obj_by_id_map().get(oid)
        if o is None:
            return "id {}（模型里没有）".format(oid)
        return "{} {}".format(oid, o.name)

    def _xml_limit_text(self, mp):
        """对照 .mod 里该部件的旋转限制，返回 (文字, 轴/角度 或 None)。"""
        try:
            oid = int(mp)
        except Exception:  # noqa: BLE001
            return "部件 id 无效", None
        if oid < 0:
            return "XML 里没绑部件", None
        o = self._obj_by_id_map().get(oid)
        if o is None:
            return "模型里没有这个部件", None
        L = getattr(o, "limits", None) or {}
        try:
            t = int(L.get("type", 0))
        except Exception:  # noqa: BLE001
            t = 0
        if not t:
            return "模型未标注限制", None
        ax = "XYZ"[int(L.get("axis", 0)) % 3]
        mn = math.degrees(float(L.get("min", 0.0)))
        mx = math.degrees(float(L.get("max", 0.0)))
        return "旋转 {} {:.0f}~{:.0f}°".format(ax, mn, mx), (ax, mn, mx)

    def _xml_part_choices(self):
        """下拉列表内容：模型里所有对象（id + 名字），最前面是"未指定"。"""
        out = ["未指定部件（-1）"]
        for o in (self.scene.objects if self.scene else []):
            out.append("{} {}".format(o.id, o.name))
        return out

    @staticmethod
    def _xml_part_id(text):
        """从 "17 Turret" 这样的文本里取出 id；"未指定" -> -1。"""
        t = (text or "").strip()
        if t.startswith("未指定"):
            return -1
        try:
            return int(t.split(" ")[0])
        except Exception:  # noqa: BLE001
            return -1

    def _part_parent_map(self):
        """部件 id -> 父 id（优先用内存映射，没有就从场景的 children 反推）。"""
        par = dict(getattr(self, "_parent", {}) or {})
        if par:
            return par
        out = {}
        for o in (self.scene.objects if self.scene else []):
            for cid in (getattr(o, "children_ids", None) or []):
                out.setdefault(int(cid), o.id)
        return out

    def _part_ancestors(self, pid, par=None):
        par = par if par is not None else self._part_parent_map()
        out, seen, cur = [], set(), int(pid)
        while True:
            nxt = par.get(cur)
            if nxt is None or nxt in seen:
                break
            seen.add(nxt)
            out.append(int(nxt))
            cur = int(nxt)
        return out

    def _platform_maps(self):
        """返回 ({ModelPart: (FirstGun, NumGuns)}, [平台行])"""
        rows = getattr(self, "_plat_rows", None) or []
        plats = {}
        for rec in rows:
            try:
                mp = self._xml_part_id(rec["mp"].get())
                fg = int(str(rec["fg"].get()).strip() or 0)
                ng = int(str(rec["ng"].get()).strip() or 0)
            except (ValueError, KeyError, TypeError):
                continue
            # 车体那种「不驱动、角度也全是 0」的平台只是占位，不用管
            dummy = False
            if ng <= 0:
                try:
                    ang = (abs(float(rec["hmin"].get())) + abs(float(rec["hmax"].get()))
                           + abs(float(rec["vmin"].get())) + abs(float(rec["vmax"].get())))
                    dummy = ang < 1e-6
                except (ValueError, KeyError, TypeError):
                    dummy = False
            plats[int(mp)] = (fg, ng, dummy)
        return plats, rows

    def _platform_cover_warn(self):
        """嵌套炮塔的炮必须在每一级平台都登记。

        判定：一门炮的 ModelPart 挂在某平台的下级 → 该平台和它的所有上级平台
        的 [FirstGun, FirstGun+NumGuns) 都必须包含这门炮的序号。
        返回 {ModelPart: "⚠ ..."}。
        """
        plats, _rows = self._platform_maps()
        guns = list(getattr(self, "_gun_elems", []) or [])
        if not plats or not guns:
            return {}
        par = self._part_parent_map()
        bad = {}
        for i, g in enumerate(guns):
            try:
                m = int(g.get("ModelPart", "-1"))
            except (TypeError, ValueError):
                continue
            if m < 0:
                continue
            chain = [m] + self._part_ancestors(m, par)
            owners = [p for p in chain if p in plats]
            if not owners:
                continue
            for p in owners:
                fg, ng, dummy = plats[p]
                if dummy:
                    continue
                if not (ng > 0 and fg <= i < fg + ng):
                    w = g.find("Weapon")
                    name = (w.text or "").strip() if w is not None else ""
                    bad.setdefault(p, []).append(
                        "第{}号 {}".format(i + 1, name or "武器"))
        return {k: "⚠ 漏登记：" + "、".join(v) for k, v in bad.items()}

    def _update_platform_check(self, row):
        """按当前选中的部件和已填的范围，更新"和模型对照"那一列。"""
        label = row.get("chk")
        if label is None:
            return
        mp = self._xml_part_id(row["mp"].get())
        try:
            hmin = float(row["hmin"].get())
            hmax = float(row["hmax"].get())
            vmin = float(row["vmin"].get())
            vmax = float(row["vmax"].get())
        except ValueError:
            hmin = hmax = vmin = vmax = 0.0
        info, lim = self._xml_limit_text(mp)
        if lim is None:
            txt, color = info, "#8a6d00"
        else:
            ax, lmn, lmx = lim
            ok = False
            if ax == "Z":       # 水平 = 绕 Z 扫
                ok = abs(lmn - hmin) < 0.6 and abs(lmx - hmax) < 0.6
            elif ax == "Y":     # 俯仰 = 绕 Y 抬
                ok = abs(lmn - vmin) < 0.6 and abs(lmx - vmax) < 0.6
            txt = ("OK " if ok else "!! ") + info
            color = "#12731d" if ok else "#b02a2a"
        try:
            label.config(text=txt, fg=color)
        except tk.TclError:
            self._plat_rows = []

    def _gun_brief(self, g, idx):
        """一门武器的一行简介：#序号 武器名/后坐部件 (弹药)。"""
        w = g.find("Weapon")
        name = (w.text or "").strip() if w is not None else ""
        if not name:
            name = self._xml_model_part_label(g.get("ModelPart", "-1"))
        ammo = g.get("Ammo", "")
        return "[{}] {}{}".format(idx, name, " ({})".format(ammo) if ammo else "")

    def _gun_name(self, g, idx=None):
        """武器的短名（不带弹药），下拉里用。"""
        w = g.find("Weapon")
        name = (w.text or "").strip() if w is not None else ""
        if not name:
            name = self._xml_model_part_label(g.get("ModelPart", "-1"))
        return name

    def _gun_combos(self):
        """下拉选项：不驱动 + 所有 (起点, 数量) 组合，值就是 (FirstGun, NumGuns)。"""
        guns = list(getattr(self, "_gun_elems", []) or [])
        out = {"不驱动武器": (0, 0)}
        for start in range(len(guns)):
            names = []
            for cnt in range(1, len(guns) - start + 1):
                names.append(self._gun_name(guns[start + cnt - 1]))
                if cnt == 1:
                    txt = "[{}] {}".format(start, names[0])
                else:
                    txt = "[{}..{}] {}".format(start, start + cnt - 1, " + ".join(names))
                if len(txt) > 34:
                    txt = txt[:31] + "..."
                out[txt] = (start, cnt)
        return out

    def _gun_combo_text(self, row):
        """当前 (fg,ng) 对应的下拉文字；没有对应项时给一个提示项。"""
        try:
            fg = int(str(row["fg"].get()).strip() or 0)
            ng = int(str(row["ng"].get()).strip() or 0)
        except (ValueError, KeyError):
            return "不驱动武器"
        for txt, val in (row.get("gun_map") or {}).items():
            if val == (fg, ng):
                return txt
        return "当前 {}→{}（不在列表里）".format(fg, ng)

    def _on_gun_combo(self, row):
        txt = row["guns_sel"].get()
        val = (row.get("gun_map") or {}).get(txt)
        if val is not None:
            row["fg"].set(str(val[0]))
            row["ng"].set(str(val[1]))
        self._render_platform_guns()

    def _gun_map_text(self, row):
        """某一行的驱动炮说明（不含前缀）。"""
        guns = list(getattr(self, "_gun_elems", []) or [])
        try:
            fg = int(str(row["fg"].get()).strip() or 0)
            ng = int(str(row["ng"].get()).strip() or 0)
        except (ValueError, KeyError):
            return "要填整数"
        if ng <= 0:
            return "不驱动武器"
        if fg < 0 or fg + ng > len(guns):
            return "{}→{} 超出武器列表（共 {} 门）".format(fg, ng, len(guns))
        return "{}→{} = {}".format(fg, ng, " / ".join(
            self._gun_brief(guns[i], i) for i in range(fg, fg + ng)))

    def _render_platform_guns(self):
        """表格下方那块：每行平台驱动哪几门炮。"""
        lbl = getattr(self, "_plat_gun_info", None)
        if lbl is None:
            return
        try:
            if not lbl.winfo_exists():     # 上个对话框已经关掉了
                self._plat_gun_info = None
                self._plat_rows = []
                return
        except tk.TclError:
            self._plat_gun_info = None
            self._plat_rows = []
            return
        warns = self._platform_cover_warn()
        lines = []
        for rec in getattr(self, "_plat_rows", []) or []:
            line = "{}：{}".format(rec["mp"].get(), self._gun_map_text(rec))
            try:
                w = warns.get(int(self._xml_part_id(rec["mp"].get())))
            except Exception:  # noqa: BLE001
                w = None
            if w:
                line += "    " + w
            lines.append(line)
        try:
            lbl.config(text=chr(10).join(lines))
        except tk.TclError:
            self._plat_gun_info = None

    def _update_platform_guns(self, row=None):
        self._render_platform_guns()

    def _update_gun_hint(self, row):
        """更新武器行右边的"指向"提示：id → 部件名。"""
        label = row.get("hint")
        if label is None:
            return
        shot = self._xml_part_id(row["shot"].get())
        part = self._xml_part_id(row["part"].get())
        label.config(text="{}→{}｜{}→{}".format(
            shot, self._xml_model_part_label(shot).split(" ")[-1],
            part, self._xml_model_part_label(part).split(" ")[-1]))

    def _make_header(self, parent, col, cn, en="", tab=None):
        """表头：中文在上、英文原字段名在下（灰色等宽，方便对着 xml 看）。"""
        f = tk.Frame(parent, bg="#ececed", height=self.px(40))
        f.grid(row=0, column=col, padx=self.px(4), pady=(self.px(8), self.px(4)),
               sticky="w")
        f.pack_propagate(False)      # 宽度由表格管，文字超出就裁掉
        if tab is not None:
            tab.hdr[col] = f
        cn_lbl = tk.Label(f, text=cn, bg="#ececed", fg="#333333",
                           font=("Microsoft YaHei UI", 9, "bold"))
        cn_lbl.pack(anchor="w")
        en_lbl = None
        if en:
            en_lbl = tk.Label(f, text=en, bg="#ececed", fg="#8a8a8a",
                              font=("Consolas", 8))
            en_lbl.pack(anchor="w")
        f.update_idletasks()
        if tab is not None:
            cn_w = cn_lbl.winfo_reqwidth()
            en_w = en_lbl.winfo_reqwidth() if en_lbl is not None else 0
            tab.ideaw[col] = max(cn_w, en_w) + self.px(14)
            tab.minw_col[col] = cn_w + self.px(8)
        return f

    def dlg_platforms(self):
        """平台 / 武器编辑器：<Platforms> 与 <Guns> 都能改、能追加、能删行。"""
        if not self.mod_path:
            self.set_status("先打开一个 .mod")
            return
        if self._xml_root is None or self._xml_path is None:
            self._load_xml()
        if self._xml_root is None or self._xml_path is None:
            if not self._xml_create_new():
                return
        plats = self._xml_root.find(".//Platforms")   # 它嵌在 <RPG> 里，不能直接 find
        guns_el = self._xml_root.find(".//Guns")
        if plats is None and guns_el is None:
            if not messagebox.askyesno(
                    "平台编辑器",
                    "这份 xml 里没有 <Platforms>/<Guns>。" + chr(10) + chr(10)
                    + "要加上这两个节点吗？"):
                return
        if plats is None:
            plats = ET.SubElement(self._xml_root, "Platforms")
        if guns_el is None:
            guns_el = ET.SubElement(self._xml_root, "Guns")

        p = self.px
        # 上个对话框的控件可能已经被销毁，这里先断开旧引用
        self._plat_rows = []
        self._plat_gun_info = None
        win = tk.Toplevel(self)
        win.title(tr("平台 / 武器") + "（{}）".format(os.path.basename(self._xml_path)))
        win.configure(bg="#ececed")
        win.transient(self)
        win.resizable(True, True)

        nb = ttk.Notebook(win)
        nb.grid(row=0, column=0, sticky="nsew", padx=p(6), pady=(p(8), 0))
        win.rowconfigure(0, weight=1)     # 窗口拉大时 Notebook 跟着撑开
        win.columnconfigure(0, weight=1)
        tab1 = tk.Frame(nb, bg="#ececed")
        tab2 = tk.Frame(nb, bg="#ececed")
        tab3 = tk.Frame(nb, bg="#ececed")
        nb.add(tab1, text=tr("平台（Platforms）"))
        nb.add(tab2, text=tr("武器（Guns）"))
        nb.add(tab3, text=tr("车辆（RPG）"))
        tab1.rowconfigure(0, weight=1)
        tab1.columnconfigure(0, weight=1)
        tab2.rowconfigure(0, weight=1)
        tab2.columnconfigure(0, weight=1)

        pcols = [("mp", "部件", "ModelPart"),
                 ("hs", "水平转速", "HorizontalRotationSpeed"),
                 ("hmin", "水平 Min°", "Constraint.Min"),
                 ("hmax", "Max°", "Constraint.Max"),
                 ("vs", "垂直转速", "VerticalRotateSpeed"),
                 ("vmin", "俯仰 Min°", "ConstraintVertical.Min"),
                 ("vmax", "Max°", "ConstraintVertical.Max"),
                 ("guns", "驱动炮", "First / Num"),
                 ("chk", "和模型对照", ".mod limits"),
                 ("del", "", "")]
        gcols = [("ord", "序 ▲▼", ""), ("weapon", "武器", "Weapon"), ("ammo", "Ammo", "Ammo"),
                 ("reload", "装填", "ReloadCost"), ("shot", "开火点", "ShootPoint"),
                 ("part", "后坐部件", "ModelPart"), ("recoil", "Recoil", "Recoil"),
                 ("rlen", "后坐长", "RecoilLength"),
                 ("shake", "抖动°", "RecoilShakeAngle"),
                 ("prio", "优先级", "Priority"), ("hint", "指向", "id → 部件名"),
                 ("del", "", "")]
        pscroll = _TableScroll(tab1, pcols, self)
        gscroll = _TableScroll(tab2, gcols, self)
        pscroll.grid(row=0, column=0, sticky="nsew")
        gscroll.grid(row=0, column=0, sticky="nsew")
        ptab = pscroll.table
        gtab = gscroll.table
        ptab.build_header()
        gtab.build_header()
        choices = self._xml_part_choices()
        rows = []      # 平台行
        grows = []     # 武器行

        # ------------------------------------------------ 平台页
        def build_platform_rows():
            self._gun_elems = list(guns_el)      # 下拉选项要用，先备好
            ptab.reset_rows()
            del rows[:]
            for r, it in enumerate(list(plats), start=1):
                row = ptab.new_row()
                cst = it.find("Constraint")
                cvt = it.find("ConstraintVertical")
                mp = it.get("ModelPart", "-1")
                hmin = math.degrees(self._xml_float(cst, "Min")) if cst is not None else 0.0
                hmax = math.degrees(self._xml_float(cst, "Max")) if cst is not None else 0.0
                vmin = math.degrees(self._xml_float(cvt, "Min")) if cvt is not None else 0.0
                vmax = math.degrees(self._xml_float(cvt, "Max")) if cvt is not None else 0.0
                rec = {
                    "el": it,
                    "mp": tk.StringVar(value=self._xml_model_part_label(mp)),
                    "hs": tk.StringVar(value=it.get("HorizontalRotationSpeed", "0")),
                    "vs": tk.StringVar(value=it.get("VerticalRotateSpeed", "0")),
                    "hmin": tk.StringVar(value=_fmt_num(hmin, 1)),
                    "hmax": tk.StringVar(value=_fmt_num(hmax, 1)),
                    "vmin": tk.StringVar(value=_fmt_num(vmin, 1)),
                    "vmax": tk.StringVar(value=_fmt_num(vmax, 1)),
                    "fg": tk.StringVar(value=it.get("FirstGun", "0")),
                    "ng": tk.StringVar(value=it.get("NumGuns", "0")),
                }
                rows.append(rec)

                f = ptab.cell(0, row)
                mp_box = ttk.Combobox(f, textvariable=rec["mp"], values=choices,
                                      state="readonly", width=12,
                                      font=("Microsoft YaHei UI", 9))
                ptab.set_content(f, mp_box)
                for ci, key in ((1, "hs"), (2, "hmin"), (3, "hmax"),
                                (4, "vs"), (5, "vmin"), (6, "vmax")):
                    f = ptab.cell(ci, row)
                    ent = tk.Entry(f, textvariable=rec[key], width=7,
                                  font=("Microsoft YaHei UI", 9))
                    ptab.set_content(f, ent)
                f = ptab.cell(7, row, flex=True)
                # 驱动炮：下拉直接选「驱动哪几门」，选中后换算成 FirstGun/NumGuns
                rec["gun_map"] = self._gun_combos()
                rec["guns_sel"] = tk.StringVar(value=self._gun_combo_text(rec))
                combo = ttk.Combobox(f, textvariable=rec["guns_sel"],
                                     values=list(rec["gun_map"].keys()),
                                     state="readonly", width=18,
                                     font=("Microsoft YaHei UI", 8))
                combo.pack(anchor="w")
                ptab.set_content(f, combo)
                rec["combo"] = combo
                combo.bind("<<ComboboxSelected>>",
                           lambda e, rr=rec: self._on_gun_combo(rr))
                f = ptab.cell(8, row, flex=True)
                chk_lbl = tk.Label(f, text="", bg="#ececed",
                                   font=("Microsoft YaHei UI", 9))
                chk_lbl.pack(anchor="w")
                rec["chk"] = chk_lbl
                f = ptab.cell(9, row)
                tk.Button(f, text="×", width=2, relief="flat",
                          command=lambda e=it: del_platform(e)).pack(anchor="w")
                mp_box.bind("<<ComboboxSelected>>",
                            lambda e, rr=rec: self._update_platform_check(rr))
                for key in ("hmin", "hmax", "vmin", "vmax"):
                    rec[key].trace_add(
                        "write", lambda *a, rr=rec: self._update_platform_check(rr))

        def refresh_platform_guns():
            self._gun_elems = list(guns_el)
            for rec in rows:
                rec["gun_map"] = self._gun_combos()
                if rec.get("combo") is not None:
                    rec["combo"].configure(values=list(rec["gun_map"].keys()))
                    rec["guns_sel"].set(self._gun_combo_text(rec))
                self._update_platform_guns(rec)
                self._update_platform_check(rec)

        self._refresh_platform_guns = refresh_platform_guns

        def del_platform(el):
            try:
                plats.remove(el)
            except Exception:  # noqa: BLE001
                pass
            build_platform_rows()
            ptab.measure()
            ptab.apply()

        def add_platform():
            el = ET.SubElement(plats, "item", {
                "VerticalRotateSpeed": "0", "HorizontalRotationSpeed": "1",
                "ModelPart": "-1", "GunCarriageParts": "-1",
                "FirstGun": "0", "NumGuns": "0"})
            ET.SubElement(el, "Constraint", {"Min": "0", "Max": "0"})
            ET.SubElement(el, "ConstraintVertical", {"Min": "0", "Max": "0"})
            build_platform_rows()
            ptab.measure()
            ptab.apply()
            self.set_status("已追加一个平台，记得点「写入 XML 并保存」")

        # ------------------------------------------------ 武器页
        def build_gun_rows():
            gtab.reset_rows()
            del grows[:]
            for r, g in enumerate(list(guns_el), start=1):
                row = gtab.new_row()
                w = g.find("Weapon")
                shot, part = g.get("ShootPoint", "-1"), g.get("ModelPart", "-1")
                rec = {
                    "el": g,
                    "weapon": tk.StringVar(value=(w.text if w is not None and w.text else "")),
                    "ammo": tk.StringVar(value=g.get("Ammo", "0")),
                    "reload": tk.StringVar(value=g.get("ReloadCost", "0")),
                    "shot": tk.StringVar(value=self._xml_model_part_label(shot)),
                    "part": tk.StringVar(value=self._xml_model_part_label(part)),
                    "recoil": tk.StringVar(value=g.get("Recoil", "0")),
                    "rlen": tk.StringVar(value=g.get("RecoilLength", "0")),
                    "shake": tk.StringVar(value=_fmt_num(
                        math.degrees(self._xml_float(g, "RecoilShakeAngle")), 2)),
                    "prio": tk.StringVar(value=g.get("Priority", "0")),
                }
                grows.append(rec)
                # 序：↑↓ 调 <Guns> 顺序（驱动炮是按索引取连续区间的，顺序很关键）
                f = gtab.cell(0, row)
                obox = tk.Frame(f, bg="#ececed")
                tk.Button(obox, text="▲", width=1, relief="flat", bd=0,
                          padx=0, highlightthickness=0, takefocus=0,
                          font=("Microsoft YaHei UI", 9),
                          command=lambda e=g: move_gun(e, -1)).pack(side="left")
                tk.Button(obox, text="▼", width=1, relief="flat", bd=0,
                          padx=0, highlightthickness=0, takefocus=0,
                          font=("Microsoft YaHei UI", 9),
                          command=lambda e=g: move_gun(e, 1)).pack(side="left")
                gtab.set_content(f, obox)

                f = gtab.cell(1, row)
                gtab.set_content(f, tk.Entry(f, textvariable=rec["weapon"], width=20,
                                             font=("Microsoft YaHei UI", 9)))
                for ci, key in ((2, "ammo"), (3, "reload")):
                    f = gtab.cell(ci, row)
                    gtab.set_content(f, tk.Entry(f, textvariable=rec[key], width=7,
                                                 font=("Microsoft YaHei UI", 9)))
                f = gtab.cell(4, row)
                shot_box = ttk.Combobox(f, textvariable=rec["shot"], values=choices,
                                        state="readonly", width=12,
                                        font=("Microsoft YaHei UI", 9))
                gtab.set_content(f, shot_box)
                f = gtab.cell(5, row)
                part_box = ttk.Combobox(f, textvariable=rec["part"], values=choices,
                                        state="readonly", width=12,
                                        font=("Microsoft YaHei UI", 9))
                gtab.set_content(f, part_box)
                for ci, key in ((6, "recoil"), (7, "rlen"), (8, "shake"), (9, "prio")):
                    f = gtab.cell(ci, row)
                    gtab.set_content(f, tk.Entry(f, textvariable=rec[key], width=7,
                                                 font=("Microsoft YaHei UI", 9)))
                f = gtab.cell(10, row, flex=True)
                hint_lbl = tk.Label(f, text="", bg="#ececed", fg="#666666",
                                    font=("Microsoft YaHei UI", 8))
                hint_lbl.pack(anchor="w")
                rec["hint"] = hint_lbl
                f = gtab.cell(11, row)
                tk.Button(f, text="×", width=2, relief="flat",
                          command=lambda e=g: del_gun(e)).pack(anchor="w")
                shot_box.bind("<<ComboboxSelected>>",
                              lambda e, rr=rec: self._update_gun_hint(rr))
                part_box.bind("<<ComboboxSelected>>",
                              lambda e, rr=rec: self._update_gun_hint(rr))

        def move_gun(el, delta):
            """把某门炮上移/下移一位（改 <Guns> 里的元素顺序）。"""
            kids = list(guns_el)
            try:
                i = kids.index(el)
            except ValueError:
                return
            j = i + int(delta)
            if j < 0 or j >= len(kids):
                return
            guns_el.remove(el)
            guns_el.insert(j, el)
            build_gun_rows()
            gtab.measure()
            gtab.apply()
            try:
                self._refresh_platform_guns()
            except Exception:  # noqa: BLE001
                pass
            msg = "武器顺序：第 {} 位 ↔ 第 {} 位".format(i + 1, j + 1)
            # 驱动炮是按索引取连续区间，换了顺序可能让某个平台指到别的炮
            hit = []
            try:
                plats_el = self._xml_root.find(".//Platforms")
                for k, it in enumerate(list(plats_el) if plats_el is not None else []):
                    try:
                        fg = int(it.get("FirstGun", "0"))
                        ng = int(it.get("NumGuns", "0"))
                    except (TypeError, ValueError):
                        continue
                    if ng <= 0:
                        continue
                    in_i = fg <= i < fg + ng
                    in_j = fg <= j < fg + ng
                    if in_i != in_j:
                        hit.append("{}（ModelPart {}）".format(
                            self._xml_model_part_label(it.get("ModelPart", "-1")),
                            it.get("ModelPart", "?")))
            except Exception:  # noqa: BLE001
                pass
            if hit:
                msg += "；⚠ 这些平台的驱动范围变了，需要重新选：" + "、".join(hit)
            else:
                msg += "（各平台驱动范围未受影响）"
            self.set_status(msg)

        def del_gun(el):
            try:
                guns_el.remove(el)
            except Exception:  # noqa: BLE001
                pass
            build_gun_rows()
            gtab.measure()
            gtab.apply()
            self._refresh_platform_guns()

        def add_gun():
            g = ET.SubElement(guns_el, "item", {
                "Priority": "1", "Ammo": "0", "Direction": "65534",
                "ReloadCost": "1", "ShootPoint": "-1", "Recoil": "0",
                "RecoilLength": "0", "RecoilTime": "50", "ModelPart": "-1",
                "RecoilShakeTime": "400", "RecoilShakeAngle": "0"})
            ET.SubElement(g, "Weapon").text = "NewWeapon"
            build_gun_rows()
            gtab.measure()
            gtab.apply()
            self.set_status("已追加一门武器（默认 NewWeapon），填好后点「写入 XML 并保存」")

        build_platform_rows()
        build_gun_rows()
        ptab.measure()
        ptab.install()
        gtab.measure()
        gtab.install()
        self._plat_tab = ptab
        self._gun_tab = gtab
        self._gun_tab = gtab
        self._plat_rows = rows          # 供外部/测试引用
        self._gun_rows = grows
        self._render_platform_guns()
        for rec in rows:
            self._update_platform_check(rec)
        self._refresh_platform_guns()
        for rec in grows:
            self._update_gun_hint(rec)

        self._plat_gun_info = tk.Label(
            tab1, text="", bg="#ececed", fg="#555555", justify="left", anchor="nw",
            font=("Microsoft YaHei UI", 8))
        self._plat_gun_info.grid(row=1, column=0, sticky="nsew", padx=p(10),
                                 pady=(p(4), 0))
        self._render_platform_guns()

        pbar = tk.Frame(tab1, bg="#ececed")
        pbar.grid(row=2, column=0, sticky="w", padx=p(8), pady=(p(6), p(4)))
        tk.Button(pbar, text=tr("+ 追加平台"), command=add_platform).pack(side="left")
        tk.Label(pbar, text=tr("  范围填角度（写回自动转弧度）；水平 = 绕 Z 扫，俯仰 = 绕 Y 抬"),
                 bg="#ececed", fg="#666666",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")
        gbar = tk.Frame(tab2, bg="#ececed")
        gbar.grid(row=1, column=0, sticky="w", padx=p(8), pady=(p(8), p(4)))
        tk.Button(gbar, text=tr("+ 追加武器"), command=add_gun).pack(side="left")
        tk.Label(gbar, text="  抖动角填角度；开火点/后坐部件填模型里的定位器或网格 id",
                 bg="#ececed", fg="#666666",
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        # ------------------------------------------------ 车辆页（RPG 其他设置项）
        rpg = self._xml_root.find(".//RPG")
        if rpg is None:
            rpg = ET.SubElement(self._xml_root, "RPG")
        rpg_recs = []   # {"var","label","write","check"}；reload 时原地重建

        cv = tk.Canvas(tab3, bg="#ececed", highlightthickness=0)
        sb3 = ttk.Scrollbar(tab3, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb3.set)
        tab3.rowconfigure(0, weight=1)
        tab3.columnconfigure(0, weight=1)
        cv.grid(row=0, column=0, sticky="nsew")
        sb3.grid(row=0, column=1, sticky="ns")
        inner = tk.Frame(cv, bg="#ececed")
        cv.create_window((0, 0), window=inner, anchor="nw", tags="inner")
        # 缩放窗口时 Configure 很密，宽度同步和 scrollregion 都合并到每拍一次
        _cv_st = {"w": 0, "sync_job": None, "sr_job": None}

        def _do_sync_width():
            _cv_st["sync_job"] = None
            if not cv.winfo_exists():
                return
            w = cv.winfo_width()
            if w > 1 and w != _cv_st["w"]:
                _cv_st["w"] = w
                cv.itemconfigure("inner", width=w)

        def _do_sr():
            _cv_st["sr_job"] = None
            if not cv.winfo_exists():
                return
            cv.configure(scrollregion=cv.bbox("all"))

        def _on_cv_cfg(e):
            if _cv_st["sync_job"] is None:
                _cv_st["sync_job"] = cv.after_idle(_do_sync_width)

        def _on_inner_cfg(e):
            if _cv_st["sr_job"] is None:
                _cv_st["sr_job"] = cv.after_idle(_do_sr)

        inner.bind("<Configure>", _on_inner_cfg)
        cv.bind("<Configure>", _on_cv_cfg)
        # 输入框列吃掉多余宽度：窗口拉宽时 Entry 跟着撑开
        for c in (1, 4):
            inner.columnconfigure(c, weight=1)

        def _wheel(e):
            cv.yview_scroll(int(-e.delta / 120), "units")
        cv.bind_all("<MouseWheel>", _wheel)
        win.bind("<Destroy>", lambda e: cv.unbind_all("<MouseWheel>"))

        def _chk_num(s, label, is_int):
            if s == "":
                return
            try:
                f = float(s)
                if is_int:
                    int(round(f))
            except ValueError:
                raise ValueError("「{}」不是数字".format(label))

        def build_rpg_tab():
            for w in inner.winfo_children():
                w.destroy()
            del rpg_recs[:]
            prow = [0]
            pcol = [0]

            def sec(title):
                if pcol[0]:
                    prow[0] += 1
                    pcol[0] = 0
                tk.Label(inner, text=title, bg="#ececed", fg="#333333",
                         font=("Microsoft YaHei UI", 9, "bold")).grid(
                    row=prow[0], column=0, columnspan=6, sticky="w",
                    padx=p(8), pady=(p(10), p(2)))
                prow[0] += 1

            def prop(rec):
                c0 = pcol[0]
                tk.Label(inner, text=rec["label"], bg="#ececed", fg="#555555",
                         font=("Microsoft YaHei UI", 9)).grid(
                    row=prow[0], column=c0, sticky="e",
                    padx=(p(10), p(3)), pady=1)
                tk.Entry(inner, textvariable=rec["var"], width=12,
                         font=("Microsoft YaHei UI", 9)).grid(
                    row=prow[0], column=c0 + 1, sticky="ew",
                    padx=(0, p(4)), pady=1)
                en = rec.get("en")
                if en:
                    tk.Label(inner, text=en, bg="#ececed", fg="#8a8a8a",
                             font=("Consolas", 8)).grid(
                        row=prow[0], column=c0 + 2, sticky="w",
                        padx=(0, p(12)), pady=1)
                pcol[0] = (c0 + 3) % 6
                if pcol[0] == 0:
                    prow[0] += 1

            def rec_attr(get_el, attr, label, is_int=False, en=None):
                el0 = get_el(False)
                var = tk.StringVar(
                    value=(el0.get(attr, "") if el0 is not None else "") or "")
                def write(get_el=get_el, attr=attr, var=var):
                    s = var.get().strip()
                    if s == "":
                        return
                    el = get_el(True)
                    if el is None:
                        return
                    if el.get(attr) != s:
                        el.set(attr, s)
                rec = {"var": var, "label": label, "en": en or attr,
                       "write": write,
                       "check": lambda v=var, l=label, i=is_int:
                           _chk_num(v.get().strip(), l, i)}
                rpg_recs.append(rec)
                prop(rec)

            def rec_text(tag, label):
                e0 = rpg.find(tag)
                var = tk.StringVar(value=(e0.text or "")
                                   if e0 is not None and e0.text else "")
                def write(tag=tag, var=var):
                    s = var.get().strip()
                    if s == "":
                        return
                    e = rpg.find(tag)
                    if e is None:
                        e = ET.SubElement(rpg, tag)
                    if (e.text or "") != s:
                        e.text = s
                rec = {"var": var, "label": label, "en": tag, "write": write,
                       "check": lambda: None}
                rpg_recs.append(rec)
                prop(rec)

            def rec_sub_attr(tag, attr, label, is_int=False):
                def get_el(create):
                    e = rpg.find(tag)
                    if e is None and create:
                        e = ET.SubElement(rpg, tag)
                    return e
                rec_attr(get_el, attr, label, is_int,
                         en="{}.{}".format(tag, attr))

            def rec_pt2d(tag, label):
                e0 = rpg.find(tag)
                var = tk.StringVar(value=(
                    "{}, {}".format(e0.get("x", "0"), e0.get("y", "0"))
                    if e0 is not None else ""))
                def check(tag=tag, var=var):
                    s = var.get().strip()
                    if s == "":
                        return
                    parts = s.replace("，", ",").split(",")
                    if len(parts) != 2:
                        raise ValueError("「{}」要填 x, y 两个数".format(label))
                    float(parts[0]), float(parts[1])
                def write(tag=tag, var=var):
                    s = var.get().strip()
                    if s == "":
                        return
                    x, y = [float(t) for t in
                            s.replace("，", ",").split(",")]
                    e = rpg.find(tag)
                    if e is None:
                        e = ET.SubElement(rpg, tag)
                    e.set("x", _fmt_num(x, 6))
                    e.set("y", _fmt_num(y, 6))
                rec = {"var": var, "label": label, "en": tag + " x,y",
                       "write": write, "check": check}
                rpg_recs.append(rec)
                prop(rec)

            def rec_points(tag, label):
                e0 = rpg.find(tag)
                vals = ([it.get("data", "") for it in list(e0)]
                        if e0 is not None else [])
                var = tk.StringVar(value=", ".join(v for v in vals if v))
                def check(tag=tag, var=var):
                    s = var.get().strip()
                    if s == "":
                        return
                    for t in s.replace("，", ",").split(","):
                        if t.strip():
                            int(t.strip())
                def write(tag=tag, var=var):
                    s = var.get().strip()
                    if s == "":
                        return
                    ids = [t.strip() for t in s.replace("，", ",").split(",")
                           if t.strip()]
                    e = rpg.find(tag)
                    if e is None:
                        e = ET.SubElement(rpg, tag)
                    for it in list(e):
                        e.remove(it)
                    for pid in ids:
                        ET.SubElement(e, "item", {"data": pid})
                rec = {"var": var, "label": label, "en": tag,
                       "write": write, "check": check}
                rpg_recs.append(rec)
                prop(rec)

            # ---- 基础
            sec("基础")
            rec_text("KeyName", "名称")
            rec_text("StatsType", "StatsType")
            rec_text("Type", "类型")
            rec_text("AIClass", "AI 类别")
            for a, l, i in (("MaxHP", "最大生命", True),
                            ("RepairCost", "维修花费", False),
                            ("Price", "价格", False),
                            ("Priority", "优先级", True),
                            ("Crew", "乘员", True),
                            ("Passangers", "载员", True),
                            ("Weight", "重量 kg", True),
                            ("SmallAABBCoeff", "命中盒系数", False)):
                rec_attr(lambda c: rpg, a, l, i)
            # ---- 观瞄
            sec("观瞄与隐蔽")
            for a, l, i in (("Sight", "视野", False),
                            ("SightPower", "观察能力", False),
                            ("Camouflage", "隐蔽", False),
                            ("BoundTileRadius", "警戒半径", True)):
                rec_attr(lambda c: rpg, a, l, i)
            # ---- 机动
            sec("机动与车体")
            for a, l, i in (("Speed", "速度", False),
                            ("Passability", "通过率", False),
                            ("RotateSpeed", "转向速度", False),
                            ("TurnRadius", "转弯半径", False),
                            ("TowingForce", "牵引力", False),
                            ("MaxHeight", "最大高度", False),
                            ("DivingAngle", "俯角°", True),
                            ("ClimbAngle", "爬坡角°", True),
                            ("TiltAngle", "侧倾角°", True),
                            ("TiltRatio", "侧倾比", False),
                            ("UninstallRotate", "卸载转向", False),
                            ("UninstallTransport", "卸载运输", False)):
                rec_attr(lambda c: rpg, a, l, i)
            # ---- 点位
            sec("点位（模型定位器 id）")
            for a, l in (("TowPoint", "牵引点"),
                         ("EntrancePoint", "上车点"),
                         ("FatalitySmokePoint", "殉爆烟点"),
                         ("ShootDustPoint", "开火尘点")):
                rec_attr(lambda c: rpg, a, l, True)
            rec_points("ExhaustPoints", "排气点")
            rec_points("DamagePoints", "损坏点")
            # ---- 履带
            sec("履带痕迹")
            for a, l, i in (("LeavesTracks", "留痕 0/1", True),
                            ("TrackOffset", "轨迹偏移", False),
                            ("TrackWidth", "轨迹宽", False),
                            ("TrackStart", "轨迹起点", False),
                            ("TrackEnd", "轨迹终点", False),
                            ("TrackIntensity", "轨迹强度", False),
                            ("TrackLifetime", "存留 ms", True)):
                rec_attr(lambda c: rpg, a, l, i)
            # ---- 装甲
            sec("装甲（各面 Min/Max）")
            for tag, l in (("ArmorLeft", "左侧"), ("ArmorRight", "右侧"),
                           ("ArmorTop", "顶部"), ("ArmorBottom", "底部"),
                           ("ArmorFront", "正面"), ("ArmorBack", "背面")):
                rec_sub_attr(tag, "Min", l + " Min", True)
                rec_sub_attr(tag, "Max", l + " Max", True)
            # ---- 中弹区
            sec("中弹区 Defence")
            for i in range(6):
                tag = "Defence%d" % i
                rec_sub_attr(tag, "MinArmor", "区{} 装甲Min".format(i), True)
                rec_sub_attr(tag, "MaxArmor", "区{} 装甲Max".format(i), True)
                rec_sub_attr(tag, "Silhouette", "区{} 轮廓".format(i), True)
            # ---- 二维点位
            sec("二维点位（x, y）")
            for tag, l in (("AABBCenter", "命中盒中心"),
                           ("AABBHalfSize", "命中盒半宽"),
                           ("TowPoint2D", "牵引点2D"),
                           ("HookPoint", "挂钩点"),
                           ("FrontWheel", "前轮"),
                           ("BackWheel", "后轮"),
                           ("EntrancePoint2D", "上车点2D"),
                           ("AmmoPoint2D", "弹药点")):
                rec_pt2d(tag, l)
            # ---- 特效音效
            sec("特效 / 音效")
            for tag, l in (("EffectDiesel", "柴油烟特效"),
                           ("EffectSmoke", "损伤烟特效"),
                           ("EffectWheelDust", "行车尘特效"),
                           ("EffectShootDust", "开火尘特效"),
                           ("EffectFatality", "殉爆特效"),
                           ("EffectDisappear", "解体特效"),
                           ("SoundMoveStart", "起动音效"),
                           ("SoundMove", "行进音效"),
                           ("SoundMoveStop", "停车音效")):
                rec_text(tag, l)

        build_rpg_tab()
        self._rpg_recs = rpg_recs

        # ------------------------------------------------ 写回
        def set_num(el, attr, value, tol=1e-9, as_int=False):
            """只在值真的变了才写；角度往返有 1e-6 误差，容差放宽到 1e-4。"""
            txt = (str(int(round(value))) if as_int else _fmt_num(value, 6))
            if el.get(attr) is not None:
                try:
                    if abs(float(el.get(attr)) - float(value)) < tol:
                        return
                except ValueError:
                    pass
            el.set(attr, txt)

        def fnum(var, name):
            try:
                return float(var.get())
            except ValueError:
                raise ValueError("「{}」不是数字".format(name))

        def inum(var, name):
            return int(round(fnum(var, name)))

        def apply():
            try:
                for rec in rpg_recs:          # 先整体校验，再写
                    rec["check"]()
                for r, rec in enumerate(rows):
                    el = rec["el"]
                    hmin_ = fnum(rec["hmin"], "水平 Min")
                    hmax_ = fnum(rec["hmax"], "水平 Max")
                    vmin_ = fnum(rec["vmin"], "俯仰 Min")
                    vmax_ = fnum(rec["vmax"], "俯仰 Max")
                    if hmin_ > hmax_ or vmin_ > vmax_:
                        raise ValueError("平台第 {} 行 Min 大于 Max".format(r + 1))
                    set_num(el, "ModelPart", self._xml_part_id(rec["mp"].get()),
                            as_int=True)
                    set_num(el, "HorizontalRotationSpeed", fnum(rec["hs"], "水平转速"))
                    set_num(el, "VerticalRotateSpeed", fnum(rec["vs"], "垂直转速"))
                    fg_ = inum(rec["fg"], "首炮序号")
                    ng_ = inum(rec["ng"], "火炮数量")
                    if fg_ < 0 or ng_ < 0:
                        raise ValueError("平台第 {} 行：驱动炮不能填负数".format(r + 1))
                    if fg_ + ng_ > len(list(guns_el)):
                        raise ValueError(
                            "平台第 {} 行：驱动炮范围 {}+{} 超出武器列表（共 {} 门）".format(
                                r + 1, fg_, ng_, len(list(guns_el))))
                    set_num(el, "FirstGun", fg_, as_int=True)
                    set_num(el, "NumGuns", ng_, as_int=True)
                    c = el.find("Constraint")
                    if c is None:
                        c = ET.SubElement(el, "Constraint")
                    set_num(c, "Min", math.radians(hmin_), tol=1e-4)
                    set_num(c, "Max", math.radians(hmax_), tol=1e-4)
                    cv = el.find("ConstraintVertical")
                    if cv is None:
                        cv = ET.SubElement(el, "ConstraintVertical")
                    set_num(cv, "Min", math.radians(vmin_), tol=1e-4)
                    set_num(cv, "Max", math.radians(vmax_), tol=1e-4)
                for r, rec in enumerate(grows):
                    g = rec["el"]
                    set_num(g, "Ammo", inum(rec["ammo"], "Ammo"), as_int=True)
                    set_num(g, "ReloadCost", inum(rec["reload"], "装填"), as_int=True)
                    set_num(g, "ShootPoint", self._xml_part_id(rec["shot"].get()),
                            as_int=True)
                    set_num(g, "ModelPart", self._xml_part_id(rec["part"].get()),
                            as_int=True)
                    set_num(g, "Recoil", inum(rec["recoil"], "Recoil"), as_int=True)
                    set_num(g, "RecoilLength", fnum(rec["rlen"], "后坐长"))
                    set_num(g, "RecoilShakeAngle",
                            math.radians(fnum(rec["shake"], "抖动角")), tol=1e-4)
                    set_num(g, "Priority", inum(rec["prio"], "优先级"), as_int=True)
                    name = (rec["weapon"].get() or "").strip()
                    if name:
                        wel = g.find("Weapon")
                        if wel is None:
                            wel = ET.SubElement(g, "Weapon")
                        if wel.text != name:
                            wel.text = name
                for rec in rpg_recs:          # 车辆页：空 = 保持原值不动
                    rec["write"]()
            except ValueError as exc:
                messagebox.showwarning("数值不对", str(exc), parent=win)
                return
            if not messagebox.askyesno(
                    "写入 XML",
                    "确定把修改写入 {} 吗？\n\n（会先备份成 {}.bak）".format(
                        os.path.basename(self._xml_path),
                        os.path.basename(self._xml_path)),
                    parent=win):
                self.set_status("已取消写入")
                return
            self._xml_dirty = True
            self._xml_save()
            try:
                self._fill_xml_tree("")
            except Exception:  # noqa: BLE001
                pass
            self.set_status("已写入并保存（窗口保持打开，可继续编辑）")

        def reload_xml(silent=False):
            """从磁盘重新读一遍 xml；silent=True 时用在“拖入新模型”后自动刷新。"""
            nonlocal plats, guns_el, rpg
            if self._xml_dirty and not silent:
                if not messagebox.askyesno(
                        "重新加载", "有未保存的修改，重新载入会丢弃。\n继续吗？",
                        parent=win):
                    return
            if silent:
                # 换模型了：丢掉旧 xml，让 _load_xml 按新的 mod 路径重新找
                self._xml_dirty = False
                self._xml_root = None
                self._xml_path = None
            if self._xml_root is None or not self._xml_path:
                try:
                    self._load_xml()
                except Exception as exc:  # noqa: BLE001
                    if not silent:
                        messagebox.showerror("重载失败", str(exc), parent=win)
                    return
                if self._xml_root is None:
                    if not silent:
                        messagebox.showerror("重载失败", "同目录没找到 xml", parent=win)
                    return
            self._xml_dirty = False
            plats = self._xml_root.find(".//Platforms")
            guns_el = self._xml_root.find(".//Guns")
            if plats is None:
                plats = ET.SubElement(self._xml_root, "Platforms")
            if guns_el is None:
                guns_el = ET.SubElement(self._xml_root, "Guns")
            rpg = self._xml_root.find(".//RPG")
            if rpg is None:
                rpg = ET.SubElement(self._xml_root, "RPG")
            build_platform_rows()
            build_gun_rows()
            build_rpg_tab()
            ptab.measure()
            ptab.apply()
            gtab.measure()
            gtab.apply()
            self._refresh_platform_guns()
            for rec in rows:
                self._update_platform_check(rec)
            for rec in grows:
                self._update_gun_hint(rec)
            try:
                self._fill_xml_tree("")
            except Exception:  # noqa: BLE001
                pass
            self.set_status("已重新加载 {}".format(os.path.basename(self._xml_path)))

        btns = tk.Frame(win, bg="#ececed")
        btns.grid(row=1, column=0, sticky="ew", padx=p(8), pady=p(8))
        tk.Button(btns, text=tr("写入 XML 并保存"), command=apply).pack(side="left")
        tk.Button(btns, text=tr("校验 XML"),
                  command=lambda: self.dlg_xml_check(win)).pack(side="left", padx=(p(8), 0))
        tk.Button(btns, text=tr("重新加载"), command=reload_xml).pack(side="left", padx=p(8))
        def close_win():
            self._plat_rows = []
            self._plat_gun_info = None
            self._plat_reload = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_win)
        tk.Button(btns, text="取消", command=close_win).pack(side="right")

        self._plat_reload = reload_xml      # 拖入新模型后由 open_mod_path 调用

        # 窗口按表格宽度收紧；比屏幕还宽就压列宽
        try:
            win.update_idletasks()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            maxw = max(640, sw - 60)
            maxh = max(320, sh - 100)
            ptab.fit_to(maxw)
            gtab.fit_to(maxw)
            # 窄屏压得太狠时，把英文小标题收起来（中文表头才看得清）
            if (sum(getattr(ptab, "ideal", ptab.width)) + p(40) > maxw
                    or sum(getattr(gtab, "ideal", gtab.width)) + p(40) > maxw):
                ptab.hide_en()
                gtab.hide_en()
                ptab.measure()
                gtab.measure()
                ptab.fit_to(maxw)
                gtab.fit_to(maxw)
            tw = max(sum(ptab.width), sum(gtab.width)) + p(40)
            # 表格 + 标签页头 + 工具条 + 按钮行；高 DPI 下这些控件也跟着放大，留够余量
            th = max(ptab.table_h(), gtab.table_h()) + p(130)
            info_lbl = getattr(self, "_plat_gun_info", None)
            if info_lbl is not None:
                win.update_idletasks()
                th += info_lbl.winfo_reqheight()      # 表格下方那块驱动炮说明
            win.geometry("%dx%d" % (min(int(tw), maxw), min(int(th), maxh)))
            win.minsize(min(int(tw), maxw), 240)
        except Exception:  # noqa: BLE001
            pass

    # ---- 对话框
    def _xml_platform_range(self, part_id):
        """读 xml <Platforms> 里绑在这个部件上的当前约束范围。
        返回 (axis, (lo, hi)) 或 None。水平 Constraint -> Z(2)，
        垂直 ConstraintVertical -> Y(1)，0/0 视为没设。"""
        if self._xml_root is None:
            try:
                self._load_xml()
            except Exception:  # noqa: BLE001
                return None
        if self._xml_root is None:
            return None
        plats = self._xml_root.find(".//Platforms")
        if plats is None:
            return None
        for it in plats.findall("item"):
            try:
                mp = int(float(it.get("ModelPart", "-1")))
            except ValueError:
                continue
            if mp != part_id:
                continue
            for tag, ax in (("Constraint", 2), ("ConstraintVertical", 1)):
                c = it.find(tag)
                if c is None:
                    continue
                try:
                    lo = math.degrees(float(c.get("Min", "0")))
                    hi = math.degrees(float(c.get("Max", "0")))
                except ValueError:
                    continue
                if abs(lo) > 1e-6 or abs(hi) > 1e-6:
                    return ax, (lo, hi)
            return 2, (-360.0, 360.0)
        return None

    # ---------------------------------------------------------------- 首选项
    def _prefs_path(self):
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "modview_prefs.json")

    def _prefs_load(self):
        try:
            with open(self._prefs_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k in ("undo_max", "del_mesh", "del_locator", "del_aabb",
                          "join", "sep_loose", "lang", "import_anim",
                          "ask_import_anim", "edit_anim", "link_uv", "link_angle", "link_mode"):
                    if k in d:
                        self.prefs[k] = d[k]
                self.prefs["undo_max"] = int(self.prefs["undo_max"])
                self.prefs["import_anim"] = bool(self.prefs.get("import_anim", False))
                self.prefs["ask_import_anim"] = bool(
                    self.prefs.get("ask_import_anim", True))
                self.prefs["edit_anim"] = bool(self.prefs.get("edit_anim", False))
                self.prefs["link_uv"] = bool(self.prefs.get("link_uv", False))
                self.prefs["link_angle"] = float(self.prefs.get("link_angle", 30.0))
                self.prefs["link_mode"] = str(self.prefs.get("link_mode", "topo") or "topo")
        except Exception:  # noqa: BLE001
            pass

    def _prefs_save(self):
        try:
            with open(self._prefs_path(), "w", encoding="utf-8") as f:
                json.dump(self.prefs, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    def _prefs_trim_undo(self):
        stack = getattr(self, "_undo", None)
        if not stack:
            return
        try:
            limit = max(1, min(500, int(self.prefs.get("undo_max", 32))))
        except Exception:  # noqa: BLE001
            limit = 32
        if len(stack) > limit:
            del stack[:-limit]

    def _relang(self):
        """切语言后立即刷新界面（菜单重建、左栏文案、大纲行）。"""
        set_lang(self.prefs.get("lang", "zh"))
        self._build_menubar()
        for w, key in getattr(self, "_tr_widgets", []):
            try:
                w.configure(text=tr(key))
            except Exception:  # noqa: BLE001
                pass
        try:
            self._rebuild_rows()
            self._draw_outline()
        except Exception:  # noqa: BLE001
            pass
        pan = getattr(self, "_panel", None)
        if pan is not None:
            try:
                pan.destroy()
            except Exception:  # noqa: BLE001
                pass
            self._panel = None
        self.set_status("语言 / Language: {}".format(
            LANG_NAMES.get(self.prefs.get("lang"), "")))

    def dlg_prefs(self):
        p = self.px
        win = tk.Toplevel(self)
        win.title(tr("首选项"))
        win.configure(bg="#ececed")
        win.transient(self)
        win.resizable(False, False)
        win.grab_set()

        undo_var = tk.StringVar(value=str(self.prefs.get("undo_max", 32)))
        v_mesh = tk.BooleanVar(value=bool(self.prefs.get("del_mesh", False)))
        v_loc = tk.BooleanVar(value=bool(self.prefs.get("del_locator", True)))
        v_aabb = tk.BooleanVar(value=bool(self.prefs.get("del_aabb", False)))
        v_join = tk.BooleanVar(value=bool(self.prefs.get("join", False)))
        v_sep = tk.BooleanVar(value=bool(self.prefs.get("sep_loose", False)))
        v_anim = tk.BooleanVar(value=bool(self.prefs.get("import_anim", False)))
        v_edit = tk.BooleanVar(value=bool(self.prefs.get("edit_anim", False)))

        tk.Label(win, text=tr("撤销"), bg="#ececed", fg="#333333", anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=p(12), pady=(p(10), p(2)))
        tk.Label(win, text=tr("可撤回次数（1~500）"), bg="#ececed", fg="#555555",
                 anchor="w", font=("Microsoft YaHei UI", 9)).grid(
            row=1, column=0, sticky="w", padx=p(12))
        tk.Entry(win, textvariable=undo_var, width=8, justify="right",
                 font=("Consolas", 10)).grid(row=1, column=1, sticky="w",
                                             padx=(0, p(12)), pady=p(2))

        tk.Label(win, text=tr("语言"), bg="#ececed", fg="#333333", anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=2, column=0, sticky="w", padx=p(12), pady=(p(10), p(2)))
        lang_names = [LANG_NAMES[c] for c in ("zh", "ja", "en", "ko")]
        lang_cb = ttk.Combobox(win, values=lang_names, state="readonly",
                               width=14, font=("Microsoft YaHei UI", 9))
        try:
            lang_cb.current(("zh", "ja", "en", "ko").index(
                self.prefs.get("lang", "zh")))
        except ValueError:
            lang_cb.current(0)
        lang_cb.grid(row=2, column=1, sticky="w", padx=p(6), pady=(p(10), p(2)))

        tk.Label(win, text=tr("操作权限"), bg="#ececed", fg="#333333", anchor="w",
                 font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=p(12), pady=(p(12), p(2)))
        img_off, img_on = self._make_check_images(15)   # 和顶部筛选栏同款图标，稍大一点
        self._pref_chk_imgs = (img_off, img_on)         # 持引用防 GC
        for i, (txt, var) in enumerate((
                ("允许删除定位器", v_loc),
                ("允许删除 AABB", v_aabb),
                ("允许删除网格（可能导致父子级错乱，默认关闭）", v_mesh),
                ("允许合并网格 Ctrl+J（可能打乱父子级，默认关闭）", v_join),
                ("允许松散分离 P（可能打乱父子级，默认关闭）", v_sep))):
            tk.Checkbutton(win, text=tr(txt), variable=var,
                           onvalue=True, offvalue=False,
                           image=img_off, selectimage=img_on, compound="left",
                           indicatoron=False, relief="flat",
                           bg="#ececed", activebackground="#ececed",
                           anchor="w", bd=0, highlightthickness=0, padx=p(2),
                           font=("Microsoft YaHei UI", 9)).grid(
                row=4 + i, column=0, columnspan=2, sticky="w", padx=p(12), pady=p(1))

        tk.Checkbutton(win, text=tr("导入动画（保存时一并写回）"), variable=v_anim,
                       onvalue=True, offvalue=False,
                       image=img_off, selectimage=img_on, compound="left",
                       indicatoron=False, relief="flat",
                       bg="#ececed", activebackground="#ececed",
                       anchor="w", bd=0, highlightthickness=0, padx=p(2),
                       font=("Microsoft YaHei UI", 9)).grid(
            row=4 + 5, column=0, columnspan=2, sticky="w", padx=p(12), pady=(p(6), p(1)))

        tk.Checkbutton(win, text=tr("允许编辑动画（可删除动画段，导出时不再写出）"),
                       variable=v_edit,
                       onvalue=True, offvalue=False,
                       image=img_off, selectimage=img_on, compound="left",
                       indicatoron=False, relief="flat",
                       bg="#ececed", activebackground="#ececed",
                       anchor="w", bd=0, highlightthickness=0, padx=p(2),
                       font=("Microsoft YaHei UI", 9)).grid(
            row=4 + 6, column=0, columnspan=2, sticky="w", padx=p(12), pady=p(1))

        def reset():
            undo_var.set("32")
            v_mesh.set(False)
            v_loc.set(True)
            v_aabb.set(False)
            v_join.set(False)
            v_sep.set(False)
            v_anim.set(False)
            v_edit.set(False)

        def ok():
            try:
                n = int(str(undo_var.get()).strip())
            except ValueError:
                messagebox.showwarning("数值不对", "可撤回次数要填整数（1~500）",
                                       parent=win)
                return
            n = max(1, min(500, n))
            self.prefs["undo_max"] = n
            self.prefs["del_mesh"] = bool(v_mesh.get())
            self.prefs["del_locator"] = bool(v_loc.get())
            self.prefs["del_aabb"] = bool(v_aabb.get())
            self.prefs["join"] = bool(v_join.get())
            self.prefs["sep_loose"] = bool(v_sep.get())
            anim_changed = bool(v_anim.get()) != bool(self.prefs.get("import_anim", False))
            self.prefs["import_anim"] = bool(v_anim.get())
            self.prefs["edit_anim"] = bool(v_edit.get())
            old_lang = self.prefs.get("lang", "zh")
            try:
                self.prefs["lang"] = ("zh", "ja", "en", "ko")[lang_cb.current()]
            except Exception:  # noqa: BLE001
                self.prefs["lang"] = old_lang
            self._prefs_save()
            self._prefs_trim_undo()
            win.destroy()
            self._sync_anim_edit_ui()
            if anim_changed:
                if hasattr(self, "anim_var"):
                    self.anim_var.set(bool(self.prefs.get("import_anim", False)))
                self._toggle_import_anim()
            if self.prefs["lang"] != old_lang:
                self._relang()
            self.set_status(
                "首选项已保存：撤销 {} 次；权限 定位器={} AABB={} 网格={} 合并={} 分离={}".format(
                    n, "开" if v_loc.get() else "关",
                    "开" if v_aabb.get() else "关",
                    "开" if v_mesh.get() else "关",
                    "开" if v_join.get() else "关",
                    "开" if v_sep.get() else "关"))

        btns = tk.Frame(win, bg="#ececed")
        btns.grid(row=4 + 7, column=0, columnspan=2, sticky="ew", padx=p(12),
                  pady=(p(10), p(10)))
        tk.Button(btns, text=tr("恢复默认"), command=reset).pack(side="left")
        tk.Button(btns, text=tr("取消"), command=win.destroy).pack(side="right")
        tk.Button(btns, text=tr("确定"), width=8, command=ok).pack(side="right", padx=p(6))

    def dlg_rotation_limit(self):
        objs = self._target_objs()
        if not objs:
            self.set_status("先选中要设限制的部件")
            return
        o0 = objs[0]
        try:
            cur_t = int(o0.limits["type"])
            cur_ax = int(o0.limits["axis"])
            cur = (math.degrees(o0.limits["min"]), math.degrees(o0.limits["max"]))
        except Exception:  # noqa: BLE001
            cur_t, cur_ax, cur = 1, 2, (-360.0, 360.0)
        if cur_t == 0:
            # 部件没标限制时，从 xml 平台约束预填（炮塔的真实范围在那里）
            from_xml = self._xml_platform_range(o0.id)
            if from_xml is not None:
                cur_ax, cur = from_xml
            else:
                cur_ax, cur = 2, (-360.0, 360.0)

        p = self.px
        win = tk.Toplevel(self)
        win.title(tr("设置范围（旋转 / 位移）"))
        win.configure(bg="#ececed")
        win.transient(self)
        win.resizable(False, False)
        axis_var = tk.IntVar(value=cur_ax)
        type_var = tk.IntVar(value=2 if cur_t == 2 else 1)
        if cur_t == 2:
            lo0, hi0, nd = float(o0.limits["min"]), float(o0.limits["max"]), 2
        else:
            lo0, hi0, nd = cur[0], cur[1], 1
        lo_var = tk.StringVar(value=_fmt_num(lo0, nd))
        hi_var = tk.StringVar(value=_fmt_num(hi0, nd))

        tk.Label(win, text=tr("对选中的 {} 个部件生效").format(len(objs)), bg="#ececed",
                 fg="#444444", font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=p(12), pady=(p(10), p(4)))
        fr0 = tk.Frame(win, bg="#ececed")
        fr0.pack(anchor="w", padx=p(12), pady=(0, p(6)))
        tk.Label(fr0, text=tr("类型:"), bg="#ececed",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Radiobutton(fr0, text=tr("旋转（度）"), value=1, variable=type_var,
                       bg="#ececed", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=p(3))
        tk.Radiobutton(fr0, text=tr("位移（米）"), value=2, variable=type_var,
                       bg="#ececed", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=p(3))
        unit_lbl = tk.Label(fr0, text=tr("单位：度") if cur_t != 2 else tr("单位：米"),
                            bg="#ececed", fg="#777777",
                            font=("Microsoft YaHei UI", 8))
        unit_lbl.pack(side="left", padx=p(8))

        fr = tk.Frame(win, bg="#ececed")
        fr.pack(anchor="w", padx=p(12), pady=(0, p(6)))
        tk.Label(fr, text=tr("沿轴:"), bg="#ececed",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        for i, ax in enumerate("XYZ"):
            tk.Radiobutton(fr, text=ax, value=i, variable=axis_var, bg="#ececed",
                           font=("Microsoft YaHei UI", 9)).pack(side="left", padx=p(3))
        fr2 = tk.Frame(win, bg="#ececed")
        fr2.pack(anchor="w", padx=p(12), pady=(0, p(6)))
        lo_lbl = tk.Label(fr2, text=tr("最小角度:"), bg="#ececed",
                          font=("Microsoft YaHei UI", 9))
        lo_lbl.pack(side="left")
        tk.Entry(fr2, textvariable=lo_var, width=9).pack(side="left", padx=(p(4), p(12)))
        hi_lbl = tk.Label(fr2, text=tr("最大角度:"), bg="#ececed",
                          font=("Microsoft YaHei UI", 9))
        hi_lbl.pack(side="left")
        tk.Entry(fr2, textvariable=hi_var, width=9).pack(side="left", padx=p(4))

        def refill(*_a):
            """切换类型时，把数值换成该类型的现有值（旋转=度，位移=米）。"""
            t = type_var.get()
            try:
                if t == 2:
                    lo_var.set(_fmt_num(float(o0.limits["min"]), 2))
                    hi_var.set(_fmt_num(float(o0.limits["max"]), 2))
                else:
                    lo_var.set(_fmt_num(math.degrees(float(o0.limits["min"])), 1))
                    hi_var.set(_fmt_num(math.degrees(float(o0.limits["max"])), 1))
            except Exception:  # noqa: BLE001
                pass
            unit_lbl.config(text=tr("单位：度") if t == 1 else tr("单位：米"))
            lo_lbl.config(text=tr("最小角度:") if t == 1 else tr("最小位移:"))
            hi_lbl.config(text=tr("最大角度:") if t == 1 else tr("最大位移:"))

        type_var.trace_add("write", refill)
        refill()                      # 初始化标签（角度/位移）和单位

        def apply():
            try:
                lo, hi = float(lo_var.get()), float(hi_var.get())
            except ValueError:
                messagebox.showwarning("数值不对", "最小/最大值要填数字", parent=win)
                return
            if type_var.get() == 2:
                self.set_translate_limit(axis_var.get(), lo, hi)
            else:
                self.set_rotation_limit(axis_var.get(), lo, hi)
            win.destroy()

        btns = tk.Frame(win, bg="#ececed")
        btns.pack(fill="x", padx=p(12), pady=(p(4), p(10)))
        tk.Button(btns, text=tr("清除限制"), command=lambda: (self.clear_limit(),
                                                          win.destroy())).pack(side="left",
                                                                              padx=p(6))
        tk.Button(btns, text=tr("取消"), command=win.destroy).pack(side="right")
        tk.Button(btns, text=tr("确定"), command=apply, width=8).pack(side="right", padx=p(6))

    def dlg_locator(self):
        if not self.scene:
            return
        parent0 = self._obj_by_id_map().get(getattr(self, "sel_id", None))
        if parent0 is None:
            self.set_status("先选中一个部件，坐标点会挂到它下面")
            return
        p = self.px
        win = tk.Toplevel(self)
        win.title("生成定位器")
        win.configure(bg="#ececed")
        win.transient(self)
        win.resizable(False, False)

        tk.Label(win, text="挂到：{}".format(parent0.name), bg="#ececed",
                 fg="#444444", font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=p(12), pady=(p(10), p(6)))

        items = ["{} — {}（挂 {}）".format(n, l, pa) for _k, n, l, pa, _r in LOCATOR_DEFS]
        keys = [k for k, _n, _l, _pa, _r in LOCATOR_DEFS]
        tk.Label(win, text="坐标点类型:", bg="#ececed",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=p(12))
        cb = ttk.Combobox(win, values=items, state="readonly", width=42,
                          font=("Microsoft YaHei UI", 9))
        cb.current(0)
        cb.pack(padx=p(12), pady=(0, p(6)))

        anchors = [("CENTER", "部件中心"), ("TOP", "顶面中心"), ("BOTTOM", "底面中心"),
                   ("FRONT", "前缘中心(-Y)"), ("ORIGIN", "世界原点")]
        tk.Label(win, text="位置:", bg="#ececed",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=p(12))
        cb2 = ttk.Combobox(win, values=[a[1] for a in anchors], state="readonly",
                           width=42, font=("Microsoft YaHei UI", 9))
        cb2.current(0)
        cb2.pack(padx=p(12), pady=(0, p(6)))

        flip = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text="X 旋转取反（对照官方 .mod 发现朝向反了再勾）",
                       variable=flip, bg="#ececed",
                       font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=p(10))
        info = tk.Label(win, text="", bg="#ececed", fg="#0a5c78",
                        font=("Microsoft YaHei UI", 9), justify="left", anchor="w")
        info.pack(fill="x", padx=p(12))

        def do_one():
            k = keys[max(0, cb.current())]
            obj = self.add_locator(k, anchor=anchors[max(0, cb2.current())][0],
                                   flip_x=flip.get())
            info.config(text=self.status.cget("text"))

        def do_all():
            self.add_all_locators(anchor=anchors[max(0, cb2.current())][0],
                                  flip_x=flip.get())
            info.config(text=self.status.cget("text"))

        btns = tk.Frame(win, bg="#ececed")
        btns.pack(fill="x", padx=p(12), pady=(p(6), p(10)))
        tk.Button(btns, text="生成", command=do_one, width=8).pack(side="left")
        tk.Button(btns, text="批量生成全部", command=do_all).pack(side="left", padx=p(6))
        tk.Button(btns, text="关闭", command=win.destroy).pack(side="right")

    def _toggle_hidden(self, oid):
        if oid in self.hidden:
            self.hidden.discard(oid)
        else:
            self.hidden.add(oid)
        self._draw_outline()
        self._apply_hidden()

    def _apply_hidden(self):
        by_id = self._obj_by_id_map()
        names = set()
        for oid in self.hidden:
            o = by_id.get(oid)
            if o is not None:
                names.add(o.name)
        self.view.set_hidden(names)

    def _set_open(self, flag):
        if flag:
            self.expanded = {o.id for o in (self.scene.objects if self.scene else [])
                             if o.children_ids}
        else:
            self.expanded = set()
        self._rebuild_rows()
        self._draw_outline()

    @staticmethod
    def _base_mat(o):
        """对象在 .mod 里的初始局部变换：location + 四元数。"""
        q = getattr(o, "rotation", None)
        M = np.eye(4)
        if q is None or len(q) != 4:
            R = np.eye(3)
        else:
            x, y, z, w = [float(v) for v in q]
            R = np.array([
                [1 - 2*(y*y + z*z), 2*(x*y - z*w),     2*(x*z + y*w)],
                [2*(x*y + z*w),     1 - 2*(x*x + z*z), 2*(y*z - x*w)],
                [2*(x*z - y*w),     2*(y*z + x*w),     1 - 2*(x*x + y*y)],
            ])
        M[:3, :3] = R
        M[:3, 3] = np.asarray(getattr(o, "location", (0.0, 0.0, 0.0)), float)
        return M

    def _init_xform(self):
        """每个部件一份可编辑的局部矩阵（初始值来自 .mod 节点）。"""
        self._xform = {}
        self._parent = {}
        objs = self.scene.objects if self.scene else []
        by_id = {o.id: o for o in objs}
        for po in objs:
            for cid in po.children_ids:
                if cid in by_id and cid != po.id:
                    self._parent.setdefault(cid, po.id)
        for o in objs:
            self._xform[o.id] = self._base_mat(o)

    def _world_of(self, oid, depth=0):
        """沿父链累乘得到世界矩阵（时间线预览时用动画帧的局部矩阵）。"""
        M = None
        anim = getattr(self, "_anim_local", None)
        if anim:
            M = anim.get(oid)
        if M is None:
            M = self._xform.get(oid)
        if M is None:
            o = self._obj_by_id_map().get(oid)
            M = self._base_mat(o) if o is not None else np.eye(4)
        pid = getattr(self, "_parent", {}).get(oid)
        if pid is None or depth > 32:
            return np.array(M, dtype=float)
        return self._world_of(pid, depth + 1) @ M

    def _world_mat(self, o):
        return self._world_of(o.id)

    def _load_meshes(self):
        uv_map = parse_uv(self.mod_path, getattr(self, "scene", None))
        self._uv_cache = uv_map

        self._init_xform()

        meshes = []
        locators = []
        self._obj_local = {}
        for i, o in enumerate(self.scene.objects):
            M = self._world_of(o.id)
            if o.data is None or len(o.data.vertices) == 0:
                # 定位器：没有网格，只有位置 + 朝向，交给视口画成坐标三叉
                if o.data is None:
                    locators.append((o.name, M[:3, 3].copy(), M[:3, :3].copy(), o.id))
                continue
            v_local = np.asarray(o.data.vertices, dtype=float)
            self._obj_local[o.id] = v_local
            # .mod 顶点是部件局部坐标，要过完整的父级链变换
            v = v_local @ M[:3, :3].T + M[:3, 3]

            f = np.asarray(o.data.faces, dtype=np.int64)
            uv = None
            if o.name in uv_map:
                f2, uv2 = uv_map[o.name]
                if f2 is None:
                    # .obj 来的 UV：faces 沿用 ModScene 的
                    if uv2 is not None and len(uv2) == len(f):
                        uv = uv2
                elif len(f2) > 0 and int(f2.max()) < len(v):
                    f = f2.astype(np.int64)
                    uv = uv2
            # 无贴图时统一用中性灰（COLORS 的多色在整机白模下太花）
            meshes.append((o.name, v, f, (168, 168, 168), uv))

        self._all_meshes = meshes
        self._all_locators = locators
        self._aabb_items = self._collect_aabbs()
        # 打开 .mod 默认不显示 AABB（列表在大纲里备好，点眼睛才显示）
        self.aabb_on = set()
        self.aabb_sel = None
        self.view.texture = find_texture(self.mod_path)
        self.view.show_meshes = self.show_mesh.get() if hasattr(self, "show_mesh") else True
        self.view.show_locators = self.show_loc.get() if hasattr(self, "show_loc") else True
        self.view.set_meshes(meshes)
        self.view.set_locators(locators)
        self._apply_aabbs()
        self.hidden = set()
        self.sel_id = None
        self.sel_ids = set()
        self.expanded = {o.id for o in self.scene.objects if o.children_ids}
        self.view.set_hidden(set())
        self.view.set_highlight(None)
        self.solo_id = None
        self.view.solo = None
        self.view.mode = "solid"
        self._fill_list()
        self._load_xml()
        self._world = {o.id: self._world_of(o.id) for o in self.scene.objects}
        self._sync_pivot()
        self.refresh_transform_panel()

    # ---------------- 大纲交互
    def on_search(self):
        """按名字过滤大纲（命中项平铺显示）。"""
        self._fill_list()

    def on_view_pick(self, kind, name, state=0):
        """视口里点中东西：网格/定位器选中对应部件，AABB 选中对应的框。

        Ctrl/Shift+点 = 加选（和 Blender 一致），这样在视口里也能多选后 Ctrl+P 建父子级。
        """
        ctrl = bool(state & 0x0004)
        shift = bool(state & 0x0001)
        if kind == "aabb":
            for i, it in enumerate(getattr(self, "_aabb_items", [])):
                if it[0] == name:
                    if i not in self.aabb_on:        # 没显示就先打开
                        self.aabb_on.add(i)
                    self.aabb_sel = i
                    self.sel_id = None               # 框和部件互斥，支点要跟到框上
                    self.sel_ids = set()
                    self.on_select()                 # 互斥：清掉网格/定位器的高亮
                    self._apply_aabbs()
                    self._draw_outline()
                    self.view.redraw(force=True)
                    self._scroll_to_aabb(i)
                    self.set_status("{}   min({:.2f}, {:.2f}, {:.2f})  max({:.2f}, {:.2f}, {:.2f})".format(
                        it[0], it[1][0], it[1][1], it[1][2], it[2][0], it[2][1], it[2][2]))
                    return
            return
        if not name or not self.scene:
            return
        obj = None
        for o in self.scene.objects:
            if o.name == name:
                obj = o
                break
        if obj is None:
            return
        fs = getattr(self, "_face_sel", None)
        if fs and fs.get("name") != name:
            self._clear_face_sel()          # 换部件了，旧的面选区作废
        if getattr(self, "solo_id", None) is not None and obj.id != self.solo_id:
            self.exit_solo()
        if ctrl or shift:
            if ctrl and obj.id in self.sel_ids:
                self.sel_ids.discard(obj.id)
                if self.sel_id == obj.id:
                    self.sel_id = next(iter(self.sel_ids), None)
            else:
                self.sel_ids.add(obj.id)
                self.sel_id = obj.id
            self.aabb_sel = None
            self._apply_aabbs()
            self._draw_outline()
            self.on_select()
            n = len(self.sel_ids)
            if n > 1:
                act = self._obj_by_id_map().get(self.sel_id)
                self.set_status("已选 {} 项｜活动：{}（继续 Ctrl/Shift+点加选，Ctrl+P 设为父级）".format(
                    n, act.name if act is not None else "-"))
            return
        self.sel_id = obj.id
        self.sel_ids = {obj.id}
        self.aabb_sel = None                   # 选部件就取消 AABB 选中
        self._apply_aabbs()
        self._draw_outline()
        self._scroll_to(obj.id)
        self.on_select()

    def _scroll_to_aabb(self, ai):
        try:
            k = self._aabb_view.index(ai)
        except (AttributeError, ValueError):
            return
        total = len(self.rows) + max(1, len(getattr(self, "_aabb_view", [])))
        i = len(self.rows) + k
        self.outline.update_idletasks()
        vis = max(1, self.outline.winfo_height() // self.ROW_H)
        top = max(0, min(total - vis, i - vis // 2))
        self.outline.yview_moveto(top / float(total))

    def _scroll_to(self, oid):
        total = len(self.rows) + len(getattr(self, "_aabb_items", []))
        if total <= 0:
            return
        for i, (o, d, hk) in enumerate(self.rows):
            if o.id != oid:
                continue
            self.outline.update_idletasks()
            vis = max(1, self.outline.winfo_height() // self.ROW_H)
            top = max(0, min(total - vis, i - vis // 2))
            self.outline.yview_moveto(top / float(total))
            return

    def on_all_toggle(self, _evt=None):
        """"全部"勾上/取消 = 三类一起开关。"""
        v = self.show_all.get()
        self.show_mesh.set(v)
        self.show_loc.set(v)
        self.show_aabb.set(v)
        self.on_filter_change()

    def on_filter_change(self, _evt=None):
        """勾选框变化：大纲和 3D 视口同步只显示勾上的类型。"""
        self.view.show_meshes = self.show_mesh.get()
        self.view.show_locators = self.show_loc.get()
        # 三个都勾上时，"全部"自动点亮（set 不会触发 command，安全）
        self.show_all.set(self.show_mesh.get() and self.show_loc.get()
                          and self.show_aabb.get())
        self._apply_aabbs()
        # 切换时自动展开，避免筛出来的部件藏在折叠节点里
        self.expanded = {o.id for o in (self.scene.objects if self.scene else [])
                         if o.children_ids}
        self._fill_list()
        self.view.redraw(force=True)
        if self.scene:
            n_mesh = sum(1 for o in self.scene.objects if o.data is not None)
            n_loc = len(self.scene.objects) - n_mesh
            on = []
            if self.show_mesh.get():
                on.append("网格 {}".format(n_mesh))
            if self.show_loc.get():
                on.append("定位器 {}".format(n_loc))
            if self.show_aabb.get():
                on.append("AABB {}".format(len(getattr(self, "_aabb_items", []))))
            self.set_status("显示：" + ("  ".join(on) if on else "无（都没勾）"))

    def _load_textures(self):
        self.lb_tex.delete(0, "end")
        self.tex_paths = []
        if not self.mod_path:
            return
        folder = os.path.dirname(self.mod_path)
        texs = sorted([f for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTS)])
        self.tex_paths = [os.path.join(folder, t) for t in texs]
        for t in texs:
            self.lb_tex.insert("end", t)
        if texs:
            self.set_status("{} 贴图".format(len(texs)) if not self.scene else
                            self.status.cget("text"))

    def _show_texture_window(self, path):
        """用一个独立窗口显示贴图。"""
        ppm = dds_to_ppm(path, 512)
        if not ppm:
            messagebox.showwarning("无法显示", "该图片格式不支持")
            return
        win = tk.Toplevel(self)
        win.title(os.path.basename(path))
        win.configure(bg="#1e1e1e")
        try:
            img = tk.PhotoImage(data=ppm, format="PPM")
        except Exception:  # noqa: BLE001
            messagebox.showwarning("无法显示", "图像解码失败")
            win.destroy()
            return
        lb = tk.Label(win, image=img, bg="#1e1e1e")
        lb.image = img
        lb.pack(padx=8, pady=8)
        tk.Label(win, text="{}   {}x{}".format(path, img.width(), img.height()),
                 bg="#1e1e1e", fg="#999999").pack(pady=(0, 8))

    # ---------------- 交互
    def on_select(self, _evt=None):
        """活动对象描橙边，其余选中对象描淡蓝边（多选时一眼能看出选了哪些）。"""
        if not self.scene or self.sel_id is None:
            self.view.set_highlights(None, None)
            self._sync_pivot()
            self.refresh_transform_panel()
            return
        obj = self._obj_by_id_map().get(self.sel_id)
        if obj is None:
            return
        self._sync_pivot()
        self.refresh_transform_panel()
        by_id = self._obj_by_id_map()
        others = [by_id[i].name for i in getattr(self, "sel_ids", ())
                  if i != obj.id and i in by_id]

        if obj.data is None or len(obj.data.vertices) == 0:
            self.view.set_highlights(None, others)
            loc_names = [obj.name]
            for i in getattr(self, "sel_ids", ()):
                x = by_id.get(i)
                if x is not None and x is not obj and x.data is None:
                    loc_names.append(x.name)
            self.view.highlight_locators(loc_names, active=obj.name)
            M = self._world_mat(obj) if hasattr(self, "_world_mat") else None
            pos = M[:3, 3] if M is not None else np.asarray(obj.location, dtype=float)
            self.set_status("{:>3}  {}  (定位器)   X {:.2f}  Y {:.2f}  Z {:.2f}".format(
                obj.id, obj.name, pos[0], pos[1], pos[2]))
            return

        loc_others = [by_id[i].name for i in getattr(self, "sel_ids", ())
                      if i != obj.id and i in by_id and by_id[i].data is None]
        self.view.highlight_locators(loc_others)   # 副选定位器也点亮
        self.view.set_highlights(obj.name, others)
        n = len(others)
        self.set_status("{:>3}  {}  (网格)   面 {}{}   点行首眼睛可隐藏".format(
            obj.id, obj.name, len(obj.data.faces),
            "   另有 {} 个副选".format(n) if n else ""))

    def on_tex_select(self, _evt=None):
        """单击贴图列表：切换 3D 视口使用的贴图。"""
        sel = self.lb_tex.curselection()
        if not sel or not self.tex_paths:
            return
        try:
            path = self.tex_paths[sel[0]]
        except IndexError:
            return
        self.apply_texture_path(path)

    def apply_texture_path(self, path):
        """按路径把贴图换到 3D 视口上（列表点击和拖放共用）。

        带缓存：解码/缩放结果按路径缓存，并且复用同一个 (w,h,arr) 元组对象 ——
        glviewport._upload_texture 用 id(self.texture) 判重，元组对象不变就不会重传显存，
        来回切贴图基本是零成本。
        """
        key = os.path.abspath(path)
        try:
            st = os.stat(key)
            sig = (int(st.st_mtime), st.st_size)
        except OSError:
            sig = None
        cache = getattr(self, "_tex_cache", None)
        if cache is None:
            cache = self._tex_cache = {}
            self._tex_order = []
        hit = cache.get(key)
        if hit is not None and hit[0] == sig:
            w, h, tex = hit[1], hit[2], hit[3]
            # LRU：挪到末尾
            try:
                self._tex_order.remove(key)
            except ValueError:
                pass
            self._tex_order.append(key)
        else:
            r = load_image(key)
            if not r:
                self.set_status("{}: 不支持的 DDS 格式（仅 DXT1/DXT5/未压缩）".format(
                    os.path.basename(path)))
                return
            w, h, rgb = r
            arr = np.frombuffer(rgb, np.uint8).reshape(h, w, 3)
            # 大图先缩到 2048 以内，省显存也省上传时间（贴图看效果够用了）
            w, h, arr = _shrink_tex(arr, 2048)
            tex = (w, h, np.ascontiguousarray(arr))
            cache[key] = (sig, w, h, tex)
            self._tex_order.append(key)
            while len(self._tex_order) > 8:          # 最多缓存 8 张
                old = self._tex_order.pop(0)
                cache.pop(old, None)
        self.view.texture = tex
        self.view.redraw(force=True)
        self.set_status("当前贴图: {}   ({}x{})".format(os.path.basename(path), w, h))

    def on_tex_dbl(self, _evt=None):
        """双击：弹出大图窗口。"""
        sel = self.lb_tex.curselection()
        if not sel:
            return
        try:
            self._show_texture_window(self.tex_paths[sel[0]])
        except (IndexError, AttributeError):
            pass

    def set_mode(self, m):
        self.view.mode = m
        self.view.redraw()

    def reset_view(self):
        self.view.yaw, self.view.pitch = 35.0, -22.0
        self.view.pan = [0.0, 0.0]
        self.view.dist = self.view.radius * 2.0
        self.view.redraw()


def main():
    app = ModViewer()
    # 命令行带 .mod / .obj 时直接打开
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith((".mod", ".obj")):
        p = sys.argv[1]
        if os.path.exists(p):
            if p.lower().endswith(".obj"):
                app.load_obj_preview(p)
                app.mainloop()
                return
            try:
                s = ModScene()
                s.from_file(p)
                app.scene = s
                app.mod_path = p
                app.set_status(os.path.basename(p))
                app.title("{}  -  {}".format(VERSION, os.path.basename(p)))
                app._load_meshes()
                app._load_textures()
            except Exception:  # noqa: BLE001
                # 以前这里 pass 掉，出错就只剩一个黑视口，什么也查不到
                import traceback as _tb
                msg = _tb.format_exc()
                with open(os.path.join(_here, "modview_error.log"), "a",
                          encoding="utf-8") as f:
                    f.write("=" * 60 + "\n" + msg + "\n")
                messagebox.showerror("打开失败", msg[-1200:])
    app.mainloop()


if __name__ == "__main__":
    main()
