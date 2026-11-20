# -*- coding: utf-8 -*-
"""OpenGL 3D 视口（tkinter 内嵌 WGL 上下文）。

用固定管线的 glDrawElements + VBO，硬件加速。
和原来的 numpy 软件光栅化相比，这里的渲染全在 GPU 上，2400 个三角形完全无压力。
"""

import ctypes
from ctypes import wintypes

import math
import os
import sys
import time
import traceback

import numpy as np
import tkinter as tk

# ---------------- 调试日志 ----------------
def _log_path():
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "modview_debug.log")


_LOG = _log_path()
DEBUG_FRAME = False        # 每帧重绘都写日志会拖慢拖动，默认关


def dlog(msg):
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), msg))
    except Exception:  # noqa: BLE001
        pass


def dlog_banner(title, lines=()):
    """会话横幅：打开新单位时在 modview_debug.log 里写一块显眼的分隔。"""
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write("[{}] === {} ===\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), title))
            for ln in lines:
                f.write("[{}] {}\n".format(time.strftime("%H:%M:%S"), ln))
            f.write("=" * 60 + "\n")
    except Exception:  # noqa: BLE001
        pass



from OpenGL import GL

# ---------------- WGL 相关 ----------------
PFD_TYPE_RGBA = 0
PFD_DOUBLEBUFFER = 0x00000001
PFD_DRAW_TO_WINDOW = 0x00000004
PFD_SUPPORT_OPENGL = 0x00000020

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32
opengl32 = ctypes.windll.opengl32

WM_PAINT = 0x000F
WM_DROPFILES = 0x0233
WM_ERASEBKGND = 0x0014
WM_SETCURSOR = 0x0020
HTCLIENT = 1
IDC_ARROW = 32512
IDC_HAND = 32649
GWLP_WNDPROC = -4

user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
user32.CallWindowProcW.restype = ctypes.c_longlong
user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_ulonglong, ctypes.c_longlong]
user32.BeginPaint.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.BeginPaint.restype = ctypes.c_void_p
user32.EndPaint.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
shell32 = ctypes.windll.shell32
shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, wintypes.BOOL]
shell32.DragQueryFileW.restype = ctypes.c_uint
shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_wchar_p, ctypes.c_uint]
shell32.DragFinish.argtypes = [ctypes.c_void_p]
user32.GetClientRect.restype = ctypes.c_int

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                              ctypes.c_ulonglong, ctypes.c_longlong)


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", ctypes.c_void_p), ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]



# ⚠ 关键：HDC / HWND 都是指针（64 位下 8 字节）。
# 不设置 restype/argtypes 时 ctypes 默认按 c_int 处理，会被截断成负数，导致 GL 上下文创建静默失败。
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetParent.restype = ctypes.c_void_p
user32.GetParent.argtypes = [ctypes.c_void_p]
user32.LoadCursorW.restype = ctypes.c_void_p
user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.SetCursor.restype = ctypes.c_void_p
user32.SetCursor.argtypes = [ctypes.c_void_p]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.WindowFromPoint.restype = ctypes.c_void_p
user32.WindowFromPoint.argtypes = [wintypes.POINT]

gdi32.ChoosePixelFormat.restype = ctypes.c_int
gdi32.ChoosePixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.SetPixelFormat.restype = ctypes.c_int
gdi32.SetPixelFormat.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
gdi32.SwapBuffers.restype = ctypes.c_int
gdi32.SwapBuffers.argtypes = [ctypes.c_void_p]

opengl32.wglCreateContext.restype = ctypes.c_void_p
opengl32.wglCreateContext.argtypes = [ctypes.c_void_p]
opengl32.wglMakeCurrent.restype = ctypes.c_bool
opengl32.wglMakeCurrent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
opengl32.wglDeleteContext.restype = ctypes.c_bool
opengl32.wglDeleteContext.argtypes = [ctypes.c_void_p]
opengl32.wglGetCurrentContext.restype = ctypes.c_void_p


class PIXELFORMATDESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("nSize", wintypes.WORD),
        ("nVersion", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("iPixelType", wintypes.BYTE),
        ("cColorBits", wintypes.BYTE),
        ("cRedBits", wintypes.BYTE),
        ("cRedShift", wintypes.BYTE),
        ("cGreenBits", wintypes.BYTE),
        ("cGreenShift", wintypes.BYTE),
        ("cBlueBits", wintypes.BYTE),
        ("cBlueShift", wintypes.BYTE),
        ("cAlphaBits", wintypes.BYTE),
        ("cAlphaShift", wintypes.BYTE),
        ("cAccumBits", wintypes.BYTE),
        ("cAccumRedBits", wintypes.BYTE),
        ("cAccumGreenBits", wintypes.BYTE),
        ("cAccumBlueBits", wintypes.BYTE),
        ("cAccumAlphaBits", wintypes.BYTE),
        ("cDepthBits", wintypes.BYTE),
        ("cStencilBits", wintypes.BYTE),
        ("cAuxBuffers", wintypes.BYTE),
        ("iLayerType", wintypes.BYTE),
        ("bReserved", wintypes.BYTE),
        ("dwLayerMask", wintypes.DWORD),
        ("dwVisibleMask", wintypes.DWORD),
        ("dwDamageMask", wintypes.DWORD),
    ]


class GLMesh:
    """一个部件的 GPU 资源。"""

    __slots__ = ("name", "vao", "vbo", "ebo", "n_index", "has_uv", "color",
                 "src", "vn", "data")

    def __init__(self):
        self.name = ""
        self.vao = 0
        self.vbo = 0
        self.ebo = 0
        self.n_index = 0
        self.has_uv = False
        self.color = (0.6, 0.6, 0.6)
        self.src = None      # 输出顶点 -> 原始顶点号（None = 1:1）
        self.vn = None       # 原始顶点的法线（变换时跟着转）
        self.data = None     # 顶点属性缓冲（pos3 + nrm3 + uv2），改位置时原地改



def _uv_mask_close(mask, rounds=2):
    """把 1~2 采样点的裂缝补上（先膨胀后腐蚀，只连不通的地方）。"""
    m = mask.copy()
    for _ in range(rounds):
        m2 = m.copy()
        m2[1:, :] |= m[:-1, :]
        m2[:-1, :] |= m[1:, :]
        m2[:, 1:] |= m[:, :-1]
        m2[:, :-1] |= m[:, 1:]
        m = m2
    for _ in range(rounds):
        m2 = m.copy()
        m2[1:, :] &= m[:-1, :]
        m2[:-1, :] &= m[1:, :]
        m2[:, 1:] &= m[:, :-1]
        m2[:, :-1] &= m[:, 1:]
        m = m2
    return m


def _uv_compose(cov_fill, cov_line, ss, line_rgb, fill_rgb, line_a, fill_a, opacity):
    """把两趟覆盖度（超采样分辨率）合成成目标尺寸的 RGBA。

    Blender 的 UV 导出就是「实心多边形 + 1px 描边」，这里把两层分开画再合成，
    所以线色 / 底色各自带不透明度也不会互相污染。
    """
    def _prep(cov, rounds):
        if cov is None:
            return None
        m = _uv_mask_close(cov > 0.0, rounds)
        return m.astype(np.float32)

    def _down(cov):
        if cov is None:
            return None
        h, w = cov.shape[0] // ss, cov.shape[1] // ss
        if ss == 1:
            return cov
        return cov.reshape(h, ss, w, ss).mean(axis=(1, 3))

    rounds = 2 if ss > 1 else 1
    cf = _down(_prep(cov_fill, rounds))
    cl = _down(_prep(cov_line, rounds))
    if cf is None:
        cf = np.zeros_like(cl)
    h, w = cl.shape

    af = np.clip(cf * float(fill_a), 0.0, 1.0)
    al = np.clip(cl * float(line_a), 0.0, 1.0)
    a = al + af * (1.0 - al)
    rgb = (line_rgb.reshape(1, 1, 3) * al[:, :, None]
           + fill_rgb.reshape(1, 1, 3) * (af * (1.0 - al))[:, :, None])
    safe = np.maximum(a, 1e-6)
    rgb = rgb / safe[:, :, None]

    out = np.zeros((h, w, 4), np.uint8)
    out[:, :, :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(a * max(0.0, float(opacity)) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[out[:, :, 3] == 0] = 0
    return out


class GLViewport(tk.Frame):
    """用 OpenGL 渲染的 3D 视口，接口和原来的 Viewport3D 保持兼容。"""

    def __init__(self, master, **kw):
        super().__init__(master, bg="black", highlightthickness=0, bd=0, **kw)

        # 相机
        self.center = np.zeros(3)
        self.radius = 1.0
        self.yaw = 35.0
        self.pitch = -22.0
        self.dist = 3.0
        self.pan = [0.0, 0.0]
        self.dragging = False

        # 数据
        self.meshes = []          # [(name, verts, faces, rgb, normals, uv)]
        self.gl_meshes = []       # [GLMesh]
        self._gm_by_name = {}     # name -> GLMesh（变换工具局部刷 VBO 用）
        self.texture = None       # (w, h, ndarray)
        self.view_mode = "3d"     # 3d / uv
        self._uv_zoom = 1.0
        self._uv_pan = (0.0, 0.0)
        self.on_uv_pick = None      # (kind, name, face, corner, x, y, shift)
        self.on_uv_drag = None      # (dx, dy, x, y)
        self.on_uv_release = None
        self.on_uv_motion = None
        self.uv_modal = False
        self.uv_sel_pts = []      # 选中 UV 点（u,v），app 同步过来用于高亮
        self.uv_pivot = None      # 选中点均值（UV），gizmo 画在这里
        self._mode_hover = None
        self.mode_buttons_enabled = False   # 视口内 3D/UV 按钮已移除（改用上方下拉）
        self.uv_edit_enabled = False        # UV 只读预览：不提供点/面编辑
        self.on_view_mode_change = None   # (mode) 由 app 同步下拉/状态栏
        self._uv_gz = None        # 正在拖 gizmo 手柄
        self.on_uv_gz_start = None
        self.on_uv_gz = None
        self.on_uv_gz_end = None
        self._uv_down = None
        self._tex_id = 0
        self._tex_key = None

        # 高亮 / 隐藏
        self.ortho = False             # 默认透视；点右下角坐标球可切正交
        self.hl_name = None            # 活动对象（橙色描边）
        self._hl_others = set()        # 其余选中对象（淡蓝描边）
        self._hl_other = []            # 上面这些对应的 GLMesh
        self._hl_gl = None
        self._hl_src = None
        self._stencil_ok = False
        self._stencil_bits = 0
        self.hidden = set()
        self.solo = None               # 独显的部件名（None = 正常显示）

        # AABB 碰撞箱
        self.aabbs = []            # [(name, mn(3), mx(3))]
        self._aabb_vbo = 0
        self._aabb_n = 0
        self._aabb_seg = {}        # name -> (first_vertex, count)
        self._aabb_hl = None

        # 定位器（没有网格的节点）
        self.locators = []        # [(name, pos(3), basis(3,3), id)]
        self.show_meshes = True        # 大纲/视口筛选：是否画网格
        self.show_locators = True      # 是否画定位器箭头
        self.show_grid = False         # 基面网格（默认关闭，View 菜单里开）
        self._loc_vbo = 0
        self._loc_n = 0           # 线段顶点数
        self._loc_tn = 0          # 三角形顶点数（箭头头部）
        self._loc_seg = {}        # name -> (line_first, line_cnt, tri_first, tri_cnt)
        self._loc_hl = None
        self._loc_hls = set()     # 副选定位器（淡蓝描边）
        # AABB 单面拖动：sel_aabb=(name,mn,mx) 时画 6 个面手柄
        self.sel_aabb = None
        self._aabb_face_drag = None
        self._aabb_face_hover = None
        self.on_aabb_face = None
        self.on_aabb_face_start = None
        self.on_aabb_face_end = None

        # GL 状态
        self.hwnd = None
        self.hdc = None
        self.glrc = None
        self.ready = False
        self._last_sig = None
        self.last_tri_count = 0
        self._old_proc = None
        self._proc = None
        self._dirty = False
        self.on_pick = None            # 回调：在视口点中某个部件时调用 on_pick(name)
        self._drag_moved = False

        # ---- 变换工具（Blender 式移动/旋转/缩放）
        self.tool = "select"           # select / move / rotate / scale
        self.pivot = None              # 选中部件原点的世界坐标 (3,)
        self.on_gizmo_start = None     # 按下手柄
        self.on_gizmo = None           # (kind, axis, value)
        self.on_gizmo_end = None       # 松开
        self.gz_no_rotate = False      # True 时旋转 gizmo 不画不命中（AABB 没有旋转字段）
        self.on_context_menu = None    # 右键空白处
        self.on_tool_hint = None
        self._gz_drag = None
        self._gz_hover = None
        self.locked_axes = set()       # 面板上锁住的轴，手柄不画也不响应
        self.tb_hover = None           # 工具条悬停的按钮序号
        self.on_tool_click = None      # 点了工具条按钮
        self.on_face_pick = None       # 选择相连面：点中三角面时回调 (name, tri, state)
        self._face_sel = None          # (name, verts_world, faces) 高亮用
        self._sel_gl = None
        self._sel_stale = False
        self.on_view_hint = None       # 切正交/透视后回传一句提示
        self.on_ortho_changed = None   # 正交开关变了，通知界面同步勾选状态
        self._tb_down = False

        # 事件
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-3>", self._on_press)
        self.bind("<B3-Motion>", self._on_drag)
        self.bind("<ButtonRelease-3>", self._on_release)
        # 中键：平移
        self.bind("<Button-2>", self._on_pan_press)
        self.bind("<B2-Motion>", self._on_drag)
        self.bind("<ButtonRelease-2>", self._on_release)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", self._on_resize)
        self.bind("<Motion>", self._on_motion)

        self._init_error = None
        self.on_error = None
        self.bind("<Destroy>", lambda e: self._unhook_wndproc() if e.widget is self else None)
        dlog("=" * 50)
        dlog("GLViewport 创建, pid={}".format(os.getpid()))
        dlog("日志文件: " + _LOG)
        self.after(120, self._init_gl)

    # ---------------------------------------------------------------- 初始化
    def _init_gl(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:  # noqa: BLE001
            return
        try:
            dlog("--- _init_gl 开始 ---")
            self.update_idletasks()
            self.hwnd = self.winfo_id()
            dlog("winfo: {}x{}  hwnd={}".format(self.winfo_width(), self.winfo_height(), self.hwnd))
            self.hdc = user32.GetDC(ctypes.c_void_p(self.hwnd))
            dlog("hdc={}".format(self.hdc))
            if not self.hdc:
                self._init_error = "GetDC 失败"
                self._report_error("GetDC 失败")
                return

            pfd = PIXELFORMATDESCRIPTOR()
            pfd.nSize = ctypes.sizeof(PIXELFORMATDESCRIPTOR)
            pfd.nVersion = 1
            pfd.dwFlags = PFD_DRAW_TO_WINDOW | PFD_SUPPORT_OPENGL | PFD_DOUBLEBUFFER
            pfd.iPixelType = PFD_TYPE_RGBA
            pfd.cColorBits = 32
            pfd.cDepthBits = 24
            pfd.cStencilBits = 8

            pf = gdi32.ChoosePixelFormat(self.hdc, ctypes.cast(ctypes.byref(pfd), ctypes.c_void_p))
            if not pf:
                self._init_error = "ChoosePixelFormat 失败"
                self._report_error("ChoosePixelFormat 失败（显卡驱动不支持 OpenGL？）")
                return
            gdi32.SetPixelFormat(self.hdc, pf, ctypes.cast(ctypes.byref(pfd), ctypes.c_void_p))

            self.glrc = opengl32.wglCreateContext(ctypes.c_void_p(self.hdc))
            if not self.glrc:
                self._init_error = "wglCreateContext 失败"
                self._report_error("wglCreateContext 失败（OpenGL 上下文创建不了）")
                return
            opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))

            GL.glClearColor(0.10, 0.10, 0.12, 1.0)
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_TEXTURE_2D)
            GL.glDisable(GL.GL_CULL_FACE)
            GL.glEnable(GL.GL_NORMALIZE)
            # 双面光照
            GL.glLightModeli(GL.GL_LIGHT_MODEL_TWO_SIDE, 1)

            GL.glEnable(GL.GL_LIGHTING)
            GL.glEnable(GL.GL_LIGHT0)
            GL.glEnable(GL.GL_COLOR_MATERIAL)
            GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE)

            # 光照总量控制在 1 以内：原来 ambient 1.0 + light ambient .85 + diffuse .65
            # 加起来 2.5，无贴图的网格直接钳成纯白（白模一片死白就是这个原因）
            GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, [-0.42, 0.72, 0.55, 0.0])
            GL.glLightfv(GL.GL_LIGHT0, GL.GL_AMBIENT, [0.26, 0.26, 0.26, 1.0])
            GL.glLightfv(GL.GL_LIGHT0, GL.GL_DIFFUSE, [0.78, 0.78, 0.78, 1.0])
            GL.glLightModelfv(GL.GL_LIGHT_MODEL_AMBIENT, [0.22, 0.22, 0.24, 1.0])
            # 用 GL_MODULATE 让纹理和光照相乘
            GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_MODULATE)

            self._stencil_bits = int(GL.glGetIntegerv(GL.GL_STENCIL_BITS) or 0)
            self._stencil_ok = self._stencil_bits > 0
            dlog("STENCIL_BITS = {}".format(self._stencil_bits))
            ver = GL.glGetString(GL.GL_VERSION)
            ren = GL.glGetString(GL.GL_RENDERER)
            dlog("GL_VERSION  = {}".format(ver))
            dlog("GL_RENDERER = {}".format(ren))
            dlog("glGetError  = {}".format(GL.glGetError()))

            self._hook_wndproc()
            self.ready = True
            self.after(15, self._dirty_tick)
            # GL 就绪前 set_meshes/set_locators 只存了数据没上传，这里补上
            if self.meshes:
                self._upload_meshes()
            if self.locators:
                self._upload_locators()
            if self.aabbs:
                self._upload_aabbs()
            dlog("ready=True, 调用 redraw")
            self.redraw(force=True)
        except Exception as exc:  # noqa: BLE001
            self._init_error = str(exc)
            dlog("!!! _init_gl 异常: " + str(exc))
            dlog(traceback.format_exc())
            if callable(self.on_error):
                try:
                    self.on_error("OpenGL 初始化失败: " + str(exc))
                except Exception:  # noqa: BLE001
                    pass

    def _hook_wndproc(self):
        """接管 GL 子窗口的绘制消息，避免 Tk 每次先刷一层背景导致闪烁。"""
        try:
            self._proc = _WNDPROC(self._wndproc)
            self._old_proc = user32.SetWindowLongPtrW(
                ctypes.c_void_p(self.hwnd), GWLP_WNDPROC,
                ctypes.cast(self._proc, ctypes.c_void_p))
            dlog("窗口过程已接管，old={}".format(self._old_proc))
            shell32.DragAcceptFiles(ctypes.c_void_p(self.hwnd), True)
        except Exception as exc:  # noqa: BLE001
            self._proc = None
            dlog("接管窗口过程失败: {}".format(exc))

    def _unhook_wndproc(self):
        try:
            if self._old_proc:
                user32.SetWindowLongPtrW(ctypes.c_void_p(self.hwnd), GWLP_WNDPROC,
                                         ctypes.c_void_p(self._old_proc))
                self._old_proc = None
                self._proc = None
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 光标
    # 光标绝不能用 Tk 的 configure(cursor=...)：那会让 GL 子窗口的呈现被吃掉，
    # 整个视口变成纯黑，直到下一次 SwapBuffers 才恢复 —— 鼠标划过工具条/gizmo
    # 时的"黑屏闪烁"就是这个。改成在 WM_SETCURSOR 里直接 SetCursor，
    # Tk 完全不参与，画面不再被打断。
    def _apply_cursor(self):
        if not getattr(self, "_cur_arrow", None):
            self._cur_arrow = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
            self._cur_hand = user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND))
        h = self._cur_hand if getattr(self, "_cursor", None) == "hand2" else self._cur_arrow
        if h:
            user32.SetCursor(ctypes.c_void_p(h))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            try:
                paths = []
                n = shell32.DragQueryFileW(ctypes.c_void_p(wparam), 0xFFFFFFFF, None, 0)
                for i in range(n):
                    ln = shell32.DragQueryFileW(ctypes.c_void_p(wparam), i, None, 0)
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    shell32.DragQueryFileW(ctypes.c_void_p(wparam), i, buf, ln + 1)
                    paths.append(buf.value)
                shell32.DragFinish(ctypes.c_void_p(wparam))
                if paths:
                    # 窗口过程里只做纯 Python 入队，绝不碰 Tk/GL（会重入崩溃）
                    pend = getattr(self, "_drop_pending", None)
                    if pend is None:
                        pend = self._drop_pending = []
                    pend.extend(paths)
            except Exception:  # noqa: BLE001
                pass
            return 0
        if self._old_proc is not None:
            if msg == WM_SETCURSOR:
                if (lparam & 0xFFFF) == HTCLIENT:
                    # 只接管客户区；边框、标题栏、缩放热点仍交给系统
                    self._apply_cursor()
                    return 1
            elif msg == WM_ERASEBKGND:
                return 1                     # 别抹黑，擦掉再画就会闪
            if msg == WM_PAINT:
                # 只验证区域、打脏标记。这里正处在 Tk 的布局调用里（SetWindowPos 内部），
                # 直接 SwapBuffers 呈现不上去，会留下一片黑。
                try:
                    ps = PAINTSTRUCT()
                    user32.BeginPaint(ctypes.c_void_p(hwnd), ctypes.byref(ps))
                    user32.EndPaint(ctypes.c_void_p(hwnd), ctypes.byref(ps))
                    self._dirty = True
                except Exception:  # noqa: BLE001
                    pass
                return 0
        return user32.CallWindowProcW(ctypes.c_void_p(self._old_proc),
                                      ctypes.c_void_p(hwnd), msg, wparam, lparam)

    def _kick(self):
        """标脏，让 15ms 的脏标记循环统一重绘（拖动时别每个事件整帧重画）。"""
        self._dirty = True

    def _dirty_tick(self):
        """窗口过程只打标记，真正的重绘放到事件循环里做。"""
        try:
            if not self.winfo_exists():
                return
            if self._dirty and self.ready:
                self._dirty = False
                self.redraw(force=True)
                # 双缓冲在窗口尺寸变化后前缓冲是废的：只有尺寸变过才需要连画两帧，
                # 内容变化（拖动 AABB 面等）画一帧就够，省一半时间
                if getattr(self, "_need_double", False):
                    self._need_double = False
                    self.redraw(force=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.after(15, self._dirty_tick)
        except Exception:  # noqa: BLE001
            pass

    def snapshot_ppm(self):
        """把当前画面读成 PPM 字节串（拖动分隔条时当静态预览用）。"""
        if not self.ready:
            return None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return None
        try:
            opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
            GL.glReadBuffer(GL.GL_FRONT)
            buf = GL.glReadPixels(0, 0, w, h, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
            arr = np.frombuffer(buf, np.uint8).reshape(h, w, 3)[::-1]
        except Exception:  # noqa: BLE001
            return None
        raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))
        header = "P6 {} {} 255 ".format(w, h).encode("ascii")
        return header + raw

    def _report_error(self, msg):
        if callable(self.on_error):
            try:
                self.on_error(msg)
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- 数据
    def set_meshes(self, meshes):
        self.meshes = []
        minv = np.array([1e18] * 3)
        maxv = np.array([-1e18] * 3)
        for item in meshes:
            name = item[0]
            v = np.asarray(item[1], dtype=np.float64)
            f = np.asarray(item[2], dtype=np.int64)
            color = item[3] if len(item) > 3 else (150, 150, 150)
            # _load_meshes 传的是 (name, verts, faces, color, uv) 五元组
            # uv 在索引 4；法线不传，由本类自己从面法线算
            uv = item[4] if len(item) > 4 else None
            nrm = item[5] if len(item) > 5 else None
            if len(v) == 0 or len(f) == 0:
                continue
            # 过滤越界索引时必须同步过滤 uv，否则三角形和 UV 错位（贴图会整片乱）
            keep = (f[:, 0] < len(v)) & (f[:, 1] < len(v)) & (f[:, 2] < len(v))
            if not keep.all():
                f = f[keep]
                if uv is not None and len(uv) == len(keep):
                    uv = np.asarray(uv)[keep]
            if len(f) == 0:
                continue
            if uv is not None and len(uv) != len(f):
                uv = None
            if nrm is not None and len(nrm) != len(f):
                nrm = None
            self.meshes.append((name, v, f, color, nrm, uv))
            minv = np.minimum(minv, v.min(axis=0))
            maxv = np.maximum(maxv, v.max(axis=0))

        if not self.meshes:
            self.gl_meshes = []
            return
        self.center = (minv + maxv) / 2.0
        self.radius = float(np.linalg.norm(maxv - minv)) / 2.0 or 1.0
        self._scene_zmin = float(minv[2])      # 模型最低点 = 基面网格高度
        # 记住整场模型的尺寸，单独看一个部件时定位器标记才不会跟着变小
        self._scene_radius = max(getattr(self, "_scene_radius", 0.0), self.radius)
        self.dist = self.radius / math.sin(math.radians(20.0)) * 0.92

        if self.ready:
            self._upload_meshes()
            self.redraw(force=True)

    def _build_glmesh(self, name, v, f, color, uv, inflate=0.0):
        """把一组三角形做成 GPU 资源。inflate>0 时沿法线外推，用于描边外壳。"""
        gm = GLMesh()
        gm.name = name
        gm.color = tuple(c / 255.0 for c in color) if color[0] > 1.0 else tuple(color)
        gm.has_uv = uv is not None

        v = np.asarray(v, dtype=np.float64)
        f = np.asarray(f, dtype=np.int64)
        nv = len(v)

        # ---- 顶点法线（按原始顶点累加面法线） ----
        vn = np.zeros((nv, 3), dtype=np.float64)
        p0, p1, p2 = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        fn = np.cross(p1 - p0, p2 - p0)
        for k in range(3):
            np.add.at(vn, f[:, k], fn)
        ln = np.linalg.norm(vn, axis=1, keepdims=True)
        ln[ln == 0] = 1.0
        vn = vn / ln
        if inflate:
            # 纯法线膨胀对零厚度双面薄片无效（正反面法线完全抵消，vn=0，
            # 顶点不动 -> 描边在这些区域断掉）。叠加径向分量兜底：
            # 每个顶点至少沿"网格中心 -> 顶点"方向外扩，保证投影必然超出原轮廓。
            lo, hi = v.min(axis=0), v.max(axis=0)
            radial = v - (lo + hi) * 0.5
            rn = np.linalg.norm(radial, axis=1, keepdims=True)
            rn[rn == 0] = 1.0
            v = v + vn * inflate + radial / rn * inflate

        # ---- ★ UV 缝拆点 ----
        # 同一个顶点在 UV 缝上会有多套 UV（T-55 的 Turret：124 个顶点里 76 个带 2~5 套）。
        # 若按顶点只存一套 UV，缝两侧的三角形会拿到隔壁 UV 岛的坐标，
        # 于是贴图在模型上被整片拉伸/扭曲。这里按 (顶点号 + UV) 去重展开成独立顶点。
        if gm.has_uv:
            uva = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
            corners = np.empty((len(f) * 3, 3), dtype=np.int64)
            corners[:, 0] = f.ravel()
            # 量化到 1/65535，避免浮点抖动把同一个缝点拆成两份
            corners[:, 1:] = np.rint(uva * 65535.0).astype(np.int64)
            uniq, inv = np.unique(corners, axis=0, return_inverse=True)
            src = uniq[:, 0]
            pos = v[src]
            tex_uv = uniq[:, 1:].astype(np.float32) / 65535.0
            tex_uv[:, 1] = 1.0 - tex_uv[:, 1]      # DDS 首行是上边 -> GL 里要翻 V
            norm = vn[src]
            idx = np.asarray(inv).reshape(-1).astype(np.uint32)
        else:
            pos = v
            src = None
            tex_uv = None
            norm = vn
            idx = f.astype(np.uint32).ravel()

        nv_out = len(pos)
        # 顶点属性：pos(3) + normal(3) + uv(2)
        data = np.zeros((nv_out, 8), dtype=np.float32)
        data[:, 0:3] = pos
        data[:, 3:6] = norm
        if tex_uv is not None:
            data[:, 6:8] = tex_uv
        # 变换工具改位置时只需重算 pos/nrm 两列，这里把映射关系留好
        gm.src = src
        gm.vn = vn
        gm.data = data

        gm.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(gm.vao)
        gm.vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, gm.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)

        stride = 8 * 4
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glVertexPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(0))
        GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
        GL.glNormalPointer(GL.GL_FLOAT, stride, ctypes.c_void_p(12))
        if gm.has_uv:
            GL.glEnableClientState(GL.GL_TEXTURE_COORD_ARRAY)
            GL.glTexCoordPointer(2, GL.GL_FLOAT, stride, ctypes.c_void_p(24))

        gm.ebo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, gm.ebo)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
        gm.n_index = len(idx)

        GL.glBindVertexArray(0)
        dlog("  上传 {}: verts={}->{} faces={} has_uv={}".format(
            name, nv, nv_out, gm.n_index // 3, gm.has_uv))
        return gm

    @staticmethod
    def _delete_glmesh(gm):
        if not gm:
            return
        try:
            if gm.vao:
                GL.glDeleteVertexArrays(1, [gm.vao])
            if gm.vbo:
                GL.glDeleteBuffers(1, [gm.vbo])
            if gm.ebo:
                GL.glDeleteBuffers(1, [gm.ebo])
        except Exception:  # noqa: BLE001
            pass

    def _upload_meshes(self):
        # 清旧的
        for m in self.gl_meshes:
            self._delete_glmesh(m)
        self.gl_meshes = []
        self._delete_glmesh(self._hl_gl)
        self._hl_gl = None

        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
        dlog("开始上传 {} 个网格".format(len(self.meshes)))

        for name, v, f, color, nrm, uv in self.meshes:
            self.gl_meshes.append(self._build_glmesh(name, v, f, color, uv))
        self._gm_by_name = {gm.name: gm for gm in self.gl_meshes}

        # 选中部件的高亮外壳（重建网格后要跟着重建）
        self._rebuild_outline()

    def update_mesh_verts(self, name, v, M3=None):
        """部件被移动/旋转/缩放后，只把位置和法线刷进 VBO（拓扑不变）。"""
        gm = getattr(self, "_gm_by_name", {}).get(name)
        if gm is None or gm.data is None:
            return False
        if not self.ready:
            return False
        v = np.asarray(v, dtype=np.float64)
        if gm.src is not None:
            if len(gm.src) == 0 or int(gm.src.max()) >= len(v):
                return False
            pos = v[gm.src]
            nrm = gm.vn[gm.src]
        else:
            if len(v) * 3 != gm.data.shape[0]:
                return False
            pos = v
            nrm = gm.vn
        if M3 is not None:
            try:
                N = np.linalg.inv(np.asarray(M3, dtype=np.float64)).T
            except np.linalg.LinAlgError:
                N = np.asarray(M3, dtype=np.float64)
            nrm = nrm @ N.T
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            ln[ln < 1e-12] = 1.0
            nrm = nrm / ln
        gm.data[:, 0:3] = pos
        gm.data[:, 3:6] = nrm
        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, gm.vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, gm.data.nbytes, gm.data, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        return True

    def set_highlight(self, name):
        """兼容旧接口：只高亮一个对象。"""
        self.set_highlights(name, None)

    def set_highlights(self, active=None, others=None):
        """活动对象橙色描边，其余选中对象淡蓝描边 —— 多选时才能看出选了哪些。"""
        others = {x for x in (others or ()) if x and x != active}
        if self.hl_name == active and self._hl_others == others:
            return
        self.hl_name = active
        self._hl_others = others
        if self.ready:
            opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
            self._rebuild_outline()
            self.redraw(force=True)

    def _rebuild_outline(self):
        self._delete_glmesh(self._hl_gl)
        self._hl_gl = None
        self._hl_src = None
        self._hl_other = []
        # 副选对象：直接复用已上传的网格做模板遮罩
        if self._hl_others:
            for gm in self.gl_meshes:
                if gm.name in self._hl_others:
                    self._hl_other.append(gm)
        if not self.hl_name:
            return
        # 描边用的原网格（模板遮罩用），直接复用已上传的那份
        for gm in self.gl_meshes:
            if gm.name == self.hl_name:
                self._hl_src = gm
                break
        for item in self.meshes:
            if item[0] == self.hl_name:
                # 外扩量再小一档：0.009 时约 4px，太粗了
                k = max(1e-5, getattr(self, "_scene_radius", self.radius) * 0.004)
                self._hl_gl = self._build_glmesh(item[0], item[1], item[2],
                                                 (1.0, 0.55, 0.10), None, inflate=k)
                break

    def _outline_stencil(self, gms, w, h, b0, b1):
        """把一组网格的原投影写进 b0 位、8 方向膨胀写进 b1 位。

        用两套不同的位给「活动对象」和「副选对象」分别打掩码，
        这样同屏能画出两种颜色的描边，互不覆盖。
        """
        if not gms:
            return
        o = max(1, int(round(1.5 * getattr(self, "S", 1.0))))
        GL.glEnable(GL.GL_STENCIL_TEST)
        GL.glStencilMask(0xFF)
        GL.glColorMask(0, 0, 0, 0)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glStencilOp(GL.GL_KEEP, GL.GL_KEEP, GL.GL_REPLACE)
        GL.glStencilFunc(GL.GL_ALWAYS, b0, b0)
        GL.glStencilMask(b0)
        for gm in gms:
            GL.glBindVertexArray(gm.vao)
            GL.glDrawElements(GL.GL_TRIANGLES, gm.n_index, GL.GL_UNSIGNED_INT, None)
        GL.glStencilFunc(GL.GL_ALWAYS, b1, b1)
        GL.glStencilMask(b1)
        for ddx, ddy in ((o, 0), (-o, 0), (0, o), (0, -o),
                         (o, o), (o, -o), (-o, o), (-o, -o)):
            GL.glViewport(ddx, ddy, w, h)
            for gm in gms:
                GL.glBindVertexArray(gm.vao)
                GL.glDrawElements(GL.GL_TRIANGLES, gm.n_index, GL.GL_UNSIGNED_INT, None)
        GL.glViewport(0, 0, w, h)
        GL.glBindVertexArray(0)
        GL.glColorMask(1, 1, 1, 1)
        GL.glDisable(GL.GL_STENCIL_TEST)   # 不能留到网格绘制阶段
        GL.glEnable(GL.GL_DEPTH_TEST)

    def _outline_fill(self, w, h, rgba, want, mask):
        """全屏矩形：模板值在 mask 位上等于 want 的像素上色。"""
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_DEPTH_TEST)         # 描边恒在最上层
        GL.glEnable(GL.GL_STENCIL_TEST)
        GL.glStencilFunc(GL.GL_EQUAL, want, mask)
        GL.glStencilOp(GL.GL_KEEP, GL.GL_KEEP, GL.GL_KEEP)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glColor4f(*rgba)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(0, w, 0, h, -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glBegin(GL.GL_QUADS)
        GL.glVertex2f(0.0, 0.0)
        GL.glVertex2f(w, 0.0)
        GL.glVertex2f(w, h)
        GL.glVertex2f(0.0, h)
        GL.glEnd()
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_STENCIL_TEST)
        GL.glStencilMask(0xFF)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def set_hidden(self, names):
        """眼睛图标控制的可见性。"""
        self.hidden = set(names or ())
        if self.ready:
            opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
            self._upload_locators()      # 隐藏的定位器要把箭头从缓冲里去掉
            self.redraw(force=True)

    # ------------------------------------------------------------ 基面网格 / 坐标 gizmo
    def _draw_grid(self):
        """基面网格（Blender 风格）：Z=模型最低点的暗色底板 + 细线网格，
        过原点画 X（红）/ Y（绿）轴线。默认关闭，View 菜单切换。"""
        if not self.show_grid or not self.meshes:
            return
        # 步长按场景半径取 1-2-5 整数级
        raw = max(self.radius / 5.0, 1e-6)
        mag = 10.0 ** math.floor(math.log10(raw))
        step = mag
        for mul in (1.0, 2.0, 5.0, 10.0):
            step = mag * mul
            if step >= raw:
                break
        ext = step * 20.0
        z0 = getattr(self, "_scene_zmin", self.center[2] - self.radius)
        z_line = z0 + max(1e-4, self.radius * 2e-4)     # 抬一点避免和底板 z-fight
        cx, cy = self.center[0], self.center[1]
        ix0 = int(math.floor((cx - ext) / step))
        ix1 = int(math.ceil((cx + ext) / step))
        iy0 = int(math.floor((cy - ext) / step))
        iy1 = int(math.ceil((cy + ext) / step))

        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        # 不写深度：相机在基面下方时网格也不会挡住模型（Blender 同款行为）
        GL.glDepthMask(0)
        # 暗色底板（比黑背景略亮）
        GL.glColor4f(0.10, 0.10, 0.11, 1.0)
        GL.glBegin(GL.GL_QUADS)
        GL.glVertex3f(ix0 * step, iy0 * step, z0)
        GL.glVertex3f(ix1 * step, iy0 * step, z0)
        GL.glVertex3f(ix1 * step, iy1 * step, z0)
        GL.glVertex3f(ix0 * step, iy1 * step, z0)
        GL.glEnd()
        # 网格线
        GL.glBegin(GL.GL_LINES)
        for i in range(ix0, ix1 + 1):
            x = i * step
            if i % 5 == 0:
                GL.glColor3f(0.34, 0.34, 0.37)
            else:
                GL.glColor3f(0.21, 0.21, 0.23)
            GL.glVertex3f(x, iy0 * step, z_line)
            GL.glVertex3f(x, iy1 * step, z_line)
        for j in range(iy0, iy1 + 1):
            y = j * step
            if j % 5 == 0:
                GL.glColor3f(0.34, 0.34, 0.37)
            else:
                GL.glColor3f(0.21, 0.21, 0.23)
            GL.glVertex3f(ix0 * step, y, z_line)
            GL.glVertex3f(ix1 * step, y, z_line)
        # 过原点的 X（红）/ Y（绿）轴线
        GL.glColor3f(0.62, 0.13, 0.13)
        GL.glVertex3f(ix0 * step, 0.0, z_line)
        GL.glVertex3f(ix1 * step, 0.0, z_line)
        GL.glColor3f(0.40, 0.60, 0.13)
        GL.glVertex3f(0.0, iy0 * step, z_line)
        GL.glVertex3f(0.0, iy1 * step, z_line)
        GL.glEnd()
        GL.glDepthMask(1)
        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_LIGHTING)
        if DEBUG_FRAME:
            dlog("grid err={:#x}".format(GL.glGetError()))

    def _draw_gizmo(self, w, h):
        """右下角世界坐标 gizmo：X 红 / Y 绿 / Z 蓝小球 + 短线，
        跟随视角旋转，背对视线的轴自动变暗。"""
        S = getattr(self, "S", 1.0)
        size = int(92 * S)
        pad = int(10 * S)
        if w < size + 2 * pad or h < size + 2 * pad:
            return
        axes = (((1.0, 0.0, 0.0), np.array([0.88, 0.22, 0.22])),
                ((0.0, 1.0, 0.0), np.array([0.55, 0.80, 0.25])),
                ((0.0, 0.0, 1.0), np.array([0.30, 0.55, 0.95])))
        # 与场景一致的旋转，算每根轴在视线方向的分量（z>0 朝向观察者）
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        dims = []
        for a, _c in axes:
            x, y, z = a
            y, z = cp * y - sp * z, sp * y + cp * z        # Rx(pitch)
            x, z = cy * x + sy * z, -sy * x + cy * z       # Ry(yaw)
            y, z = z, -y                                   # Rx(-90)：Z-up -> GL Y-up
            dims.append(1.0 if z > 0 else 0.62)
        # 把三个球真正画在哪儿记下来，点击命中就用这份坐标（保证"点哪个就是哪个"）
        Rg = self._view_rot3_gl()
        self._nav_balls = [(nm, float((Rg @ np.asarray(a, float))[0]),
                            float((Rg @ np.asarray(a, float))[1]))
                           for nm, (a, _c) in zip(("x", "y", "z"), axes)]

        GL.glViewport(w - size - pad, pad, size, size)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(-1.45, 1.45, -1.45, 1.45, -3.0, 3.0)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        # 背景圆盘（正对屏幕，不随视角转）+ 浅色描边环，和深色背景区分开
        GL.glColor4f(0.185, 0.185, 0.215, 0.92)
        GL.glBegin(GL.GL_TRIANGLE_FAN)
        GL.glVertex3f(0.0, 0.0, 0.0)
        for k in range(33):
            a = k * math.pi / 16.0
            GL.glVertex3f(1.28 * math.cos(a), 1.28 * math.sin(a), 0.0)
        GL.glEnd()
        GL.glColor4f(0.42, 0.42, 0.46, 0.9)
        GL.glBegin(GL.GL_LINE_LOOP)
        for k in range(33):
            a = k * math.pi / 16.0
            GL.glVertex3f(1.28 * math.cos(a), 1.28 * math.sin(a), 0.0)
        GL.glEnd()
        # 世界轴（随视角旋转）
        GL.glRotatef(self.pitch, 1.0, 0.0, 0.0)
        GL.glRotatef(self.yaw, 0.0, 1.0, 0.0)
        GL.glRotatef(-90.0, 1.0, 0.0, 0.0)
        GL.glLineWidth(max(1.0, 2.0 * S))
        GL.glBegin(GL.GL_LINES)
        for (a, c), d in zip(axes, dims):
            GL.glColor3f(*(c * d))
            GL.glVertex3f(0.0, 0.0, 0.0)
            GL.glVertex3f(*a)
        GL.glEnd()
        # 轴端小球
        GL.glEnable(GL.GL_POINT_SMOOTH)
        # 浅色外圈：先画一圈略大的浅灰点，再叠彩色球，深色背景下轮廓清楚
        GL.glPointSize(int(19 * S))
        GL.glBegin(GL.GL_POINTS)
        for (a, _c), _d in zip(axes, dims):
            GL.glColor3f(0.80, 0.80, 0.84)
            GL.glVertex3f(*a)
        GL.glEnd()
        GL.glPointSize(int(15 * S))
        GL.glBegin(GL.GL_POINTS)
        for (a, c), d in zip(axes, dims):
            GL.glColor3f(*(c * d))
            GL.glVertex3f(*a)
        GL.glColor3f(0.78, 0.78, 0.80)
        GL.glVertex3f(0.0, 0.0, 0.0)
        GL.glEnd()
        GL.glDisable(GL.GL_POINT_SMOOTH)
        GL.glPointSize(1.0)

        # 轴字母 X / Y / Z：用 2D 直线画字，摆在轴球外侧，始终正对屏幕
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glLineWidth(max(1.6, 2.2 * S))
        glyphs = {
            "x": (((-1.0, 1.0), (1.0, -1.0)), ((-1.0, -1.0), (1.0, 1.0))),
            "y": (((-1.0, 1.0), (0.0, 0.0)), ((1.0, 1.0), (0.0, 0.0)),
                  ((0.0, 0.0), (0.0, -1.0))),
            "z": (((-1.0, 1.0), (1.0, 1.0)), ((1.0, 1.0), (-1.0, -1.0)),
                  ((-1.0, -1.0), (1.0, -1.0))),
        }
        for (nm, ex, ey), (_a, _c), d in zip(self._nav_balls, axes, dims):
            # 字母直接压在球心上（和 Blender 一样：彩色球 + 白色字母）
            cx, cy2 = ex, ey
            hh = 0.115
            GL.glColor3f(0.05, 0.05, 0.05) if d > 0.6 else GL.glColor3f(0.25, 0.25, 0.25)
            GL.glBegin(GL.GL_LINES)
            for (p0, p1) in glyphs[nm]:
                GL.glVertex2f(cx + p0[0] * hh, cy2 + p0[1] * hh)
                GL.glVertex2f(cx + p1[0] * hh, cy2 + p1[1] * hh)
            GL.glEnd()
        GL.glLineWidth(1.0)
        GL.glPopMatrix()

        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_LIGHTING)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()
        GL.glViewport(0, 0, w, h)

    # ------------------------------------------------------------ 变换 gizmo
    # 手柄颜色沿用 Blender：X 红 / Y 绿 / Z 蓝，高亮黄
    GZ_COLORS = ((0.94, 0.26, 0.20), (0.53, 0.83, 0.16), (0.16, 0.44, 0.98))
    GZ_HL = (1.0, 0.85, 0.20)

    @staticmethod
    def _gz_axes():
        return (np.array([1.0, 0.0, 0.0]),
                np.array([0.0, 1.0, 0.0]),
                np.array([0.0, 0.0, 1.0]))

    def _gz_scale(self):
        """gizmo 的世界长度：屏幕上恒定约 88px。"""
        h = max(1, self.winfo_height())
        k = 2.0 * math.tan(math.radians(20.0)) * self.dist / h
        return k * 88.0 * getattr(self, "S", 1.0), k

    @staticmethod
    def _perp(d):
        """给轴向找一个正交基 (u, v)，用来画圆环/锥底。"""
        a = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(d, a)
        n = np.linalg.norm(u)
        if n < 1e-9:
            u = np.array([0.0, 1.0, 0.0])
            n = 1.0
        u = u / n
        v = np.cross(d, u)
        return u, v

    def _gz_plane_quad(self, P, L, axes, i, j):
        a, b = axes[i], axes[j]
        lo, hi = L * 0.26, L * 0.50
        return np.array([P + a * lo + b * lo,
                         P + a * hi + b * lo,
                         P + a * hi + b * hi,
                         P + a * lo + b * hi])

    def _gz_cone(self, base, d, r, h):
        u, v = self._perp(d)
        GL.glBegin(GL.GL_TRIANGLE_FAN)
        GL.glVertex3f(*(base + d * h))
        n = 14
        for k in range(n + 1):
            a = 2.0 * math.pi * k / n
            p = base + u * (r * math.cos(a)) + v * (r * math.sin(a))
            GL.glVertex3f(*p)
        GL.glEnd()

    @staticmethod
    def _gz_cube(c, r):
        s = ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
             (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1))
        p = [c + np.array(v, float) * r for v in s]
        faces = ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
                 (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0))
        GL.glBegin(GL.GL_QUADS)
        for f in faces:
            for k in f:
                GL.glVertex3f(*p[k])
        GL.glEnd()

    def _gz_draw(self, w, h):
        # 只有三个变换工具才画手柄；选择/相连面工具下不显示
        if self.tool not in ("move", "rotate", "scale") or self.pivot is None:
            return
        draw_tool = self.tool
        if draw_tool == "rotate" and getattr(self, "gz_no_rotate", False):
            return
        if not self.meshes:
            return
        P = np.asarray(self.pivot, float)
        L, _k = self._gz_scale()
        if L <= 1e-9:
            return
        axes = self._gz_axes()
        hu = self._gz_hover or (None, -1)
        S = getattr(self, "S", 1.0)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glLineWidth(max(1.6, 2.0 * S))
        try:
            if draw_tool == "rotate":
                for i, d in enumerate(axes):
                    if i in self.locked_axes:
                        continue
                    u, v = self._perp(d)
                    hot = (hu[0] == "ring" and hu[1] == i)
                    c = self.GZ_HL if hot else self.GZ_COLORS[i]
                    GL.glColor4f(c[0], c[1], c[2], 1.0)
                    GL.glBegin(GL.GL_LINE_LOOP)
                    for k in range(64):
                        a = 2.0 * math.pi * k / 64.0
                        GL.glVertex3f(*(P + (u * math.cos(a) + v * math.sin(a)) * L))
                    GL.glEnd()
                # 中心小圆点（提示枢轴位置）
                GL.glColor4f(0.95, 0.95, 0.95, 0.9)
                GL.glPointSize(max(4.0, 5.0 * S))
                GL.glBegin(GL.GL_POINTS)
                GL.glVertex3f(*P)
                GL.glEnd()
                GL.glPointSize(1.0)
            else:
                if draw_tool == "move":
                    for i, j in ((0, 1), (1, 2), (2, 0)):
                        if i in self.locked_axes or j in self.locked_axes:
                            continue
                        hot = (hu[0] == "plane" and hu[1] == i)
                        q = self._gz_plane_quad(P, L, axes, i, j)
                        c = self.GZ_HL if hot else self.GZ_COLORS[i]
                        GL.glColor4f(c[0], c[1], c[2], 0.55 if hot else 0.30)
                        GL.glBegin(GL.GL_QUADS)
                        for p in q:
                            GL.glVertex3f(*p)
                        GL.glEnd()
                for i, d in enumerate(axes):
                    if i in self.locked_axes:
                        continue
                    hot = (hu[0] == "axis" and hu[1] == i)
                    c = self.GZ_HL if hot else self.GZ_COLORS[i]
                    GL.glColor4f(c[0], c[1], c[2], 1.0)
                    a0, a1 = P + d * (L * 0.06), P + d * (L * (0.80 if draw_tool == "move" else 0.88))
                    GL.glBegin(GL.GL_LINES)
                    GL.glVertex3f(*a0)
                    GL.glVertex3f(*a1)
                    GL.glEnd()
                    GL.glDisable(GL.GL_LIGHTING)
                    if draw_tool == "move":
                        self._gz_cone(a1, d, L * 0.055, L * 0.16)
                    else:
                        self._gz_cube(a1 + d * (L * 0.06), L * 0.05)
                if draw_tool == "scale":
                    hot = (hu[0] == "center")
                    c = self.GZ_HL if hot else (0.90, 0.90, 0.92)
                    GL.glColor4f(c[0], c[1], c[2], 1.0)
                    self._gz_cube(P, L * 0.055)
        finally:
            GL.glLineWidth(1.0)
            GL.glDisable(GL.GL_BLEND)
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_LIGHTING)

    @staticmethod
    def _pt_in_quad(px, py, sx, sy):
        inside = False
        n = len(sx)
        for i in range(n):
            j = (i - 1) % n
            if ((sy[i] > py) != (sy[j] > py)):
                xin = (sx[j] - sx[i]) * (py - sy[i]) / (sy[j] - sy[i] + 1e-12) + sx[i]
                if px < xin:
                    inside = not inside
        return inside

    def _gz_hit(self, px, py):
        """屏幕点是否压在 gizmo 手柄上 -> ('axis'|'plane'|'ring'|'center', i[, j])"""
        if self.tool not in ("move", "rotate", "scale") or self.pivot is None or not self.meshes:
            return None
        if self.tool == "rotate" and getattr(self, "gz_no_rotate", False):
            return None
        P = np.asarray(self.pivot, float)
        L, _k = self._gz_scale()
        if L <= 1e-9:
            return None
        S = getattr(self, "S", 1.0)
        tol = max(6.0, 6.5 * S)
        axes = self._gz_axes()
        sx, sy, ok = self._project_points([P])
        if not ok[0]:
            return None
        cx, cy = float(sx[0]), float(sy[0])
        if abs(px - cx) > 260 * S and abs(py - cy) > 260 * S:
            return None

        if self.tool == "rotate":
            best = None
            for i, d in enumerate(axes):
                if i in self.locked_axes:
                    continue
                u, v = self._perp(d)
                pts = np.array([P + (u * math.cos(2 * math.pi * k / 48.0)
                                     + v * math.sin(2 * math.pi * k / 48.0)) * L
                                for k in range(48)])
                rx, ry, rok = self._project_points(pts)
                for k in range(48):
                    a, b = k, (k + 1) % 48
                    if not (rok[a] and rok[b]):
                        continue
                    dist = self._seg_dist(px, py, rx[a], ry[a], rx[b], ry[b])
                    if dist <= tol and (best is None or dist < best[0]):
                        best = (dist, ("ring", i))
            if best is None and math.hypot(px - cx, py - cy) <= 10 * S:
                return ("ring", 2)
            return best[1] if best else None

        best = None
        # 等比缩放的中心方块优先于三根轴，否则永远抢不到
        if self.tool == "scale" and math.hypot(px - cx, py - cy) <= 11 * S:
            return ("center",)
        for i, d in enumerate(axes):
            if i in self.locked_axes:
                continue
            tx, ty, tok = self._project_points([P + d * L * 0.06, P + d * L])
            if not tok.all():
                continue
            dist = self._seg_dist(px, py, tx[0], ty[0], tx[1], ty[1])
            if dist <= tol and (best is None or dist < best[0]):
                best = (dist, ("axis", i))
        if best is not None:
            return best[1]
        if self.tool == "move":
            for i, j in ((0, 1), (1, 2), (2, 0)):
                if i in self.locked_axes or j in self.locked_axes:
                    continue
                q = self._gz_plane_quad(P, L, axes, i, j)
                qx, qy, qok = self._project_points(q)
                if not qok.all():
                    continue
                if self._pt_in_quad(px, py, qx, qy):
                    return ("plane", i, j)
        return None

    def _gz_begin(self, e):
        hit = self._gz_hit(e.x, e.y)
        if hit is None:
            return False
        P = np.asarray(self.pivot, float).copy()
        self._gz_drag = {"hit": hit, "x0": e.x, "y0": e.y,
                         "kind": self.tool, "P": P}
        if self.tool == "rotate":
            sx, sy, ok = self._project_points([P])
            if ok[0]:
                self._gz_drag["c"] = (float(sx[0]), float(sy[0]))
                self._gz_drag["a0"] = math.atan2(-(e.y - sy[0]), e.x - sx[0])
        if callable(self.on_gizmo_start):
            try:
                self.on_gizmo_start()
            except Exception:  # noqa: BLE001
                pass
        return True

    def _gz_drag_update(self, e):
        d = self._gz_drag
        if not d:
            return
        cb = self.on_gizmo
        if not callable(cb):
            return
        kind, hit = d["kind"], d["hit"]
        P = d["P"]
        L, _k = self._gz_scale()
        axes = self._gz_axes()
        dx, dy = e.x - d["x0"], e.y - d["y0"]

        if kind == "move":
            if hit[0] == "plane":
                i, j = hit[1], hit[2]
                sx, sy, ok = self._project_points([P, P + axes[i] * L, P + axes[j] * L])
                if not ok.all():
                    return
                ux, uy = sx[1] - sx[0], sy[1] - sy[0]
                vx, vy = sx[2] - sx[0], sy[2] - sy[0]
                det = ux * vy - uy * vx
                if abs(det) < 1e-9:
                    return
                ta = (dx * vy - dy * vx) / det
                tb = (ux * dy - uy * dx) / det
                off = axes[i] * (ta * L) + axes[j] * (tb * L)
            else:
                i = hit[1]
                sx, sy, ok = self._project_points([P, P + axes[i] * L])
                if not ok.all():
                    return
                vx, vy = sx[1] - sx[0], sy[1] - sy[0]
                den = vx * vx + vy * vy
                if den < 1e-9:
                    return
                t = (dx * vx + dy * vy) / den
                off = axes[i] * (t * L)
            cb("move", None, off)

        elif kind == "rotate":
            c = d.get("c")
            if c is None:
                return
            a1 = math.atan2(-(e.y - c[1]), e.x - c[0])
            dang = a1 - d["a0"]
            while dang > math.pi:
                dang -= 2.0 * math.pi
            while dang < -math.pi:
                dang += 2.0 * math.pi
            i = hit[1] if len(hit) > 1 else 2
            axis = axes[i]
            ez = float((self._view_rot3() @ axis)[2])
            theta = dang if ez > 0 else -dang
            cb("rotate", axis, theta)

        else:  # scale
            if hit[0] == "center":
                sx, sy, ok = self._project_points([P])
                if not ok[0]:
                    return
                cx, cy = float(sx[0]), float(sy[0])
                d0 = math.hypot(d["x0"] - cx, d["y0"] - cy)
                d1 = math.hypot(e.x - cx, e.y - cy)
                # 用「拖了多少像素」算比例：拖出 80px = 放大到 2 倍，往回 40px = 缩到 0.5
                f = 1.0 + (d1 - d0) / (80.0 * getattr(self, "S", 1.0))
                if f < 0.01:
                    f = 0.01
                cb("scale", None, np.array([f, f, f]))
            else:
                i = hit[1]
                sx, sy, ok = self._project_points([P, P + axes[i] * L])
                if not ok.all():
                    return
                vx, vy = sx[1] - sx[0], sy[1] - sy[0]
                den = vx * vx + vy * vy
                if den < 1e-9:
                    return
                f = 1.0 + (dx * vx + dy * vy) / den
                f = max(0.01, f)
                s = np.ones(3)
                s[i] = f
                cb("scale", None, s)

    def _view_rot3(self):
        """模型视图矩阵的旋转部分（世界 -> 眼睛）。"""
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        RX = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], float)
        RY = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], float)
        RX90 = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
        return RX @ RY @ RX90

    # ------------------------------------------------------------ 视口工具条
    # 注意：这里必须用 OpenGL 画，不能用 Tk 控件叠在 GL 窗口上 ——
    # 子窗口会让父窗口带 WS_CLIPCHILDREN，SwapBuffers 会偶发整帧黑屏。
    TB_TOOLS = ("move", "rotate", "scale", "link")

    def mode_geom(self):
        """3D / UV 模式按钮（画在工具条右侧）。"""
        S = getattr(self, "S", 1.0)
        b = 30.0 * S
        gap = 4.0 * S
        pad = 9.0 * S
        x0 = pad + 3 * (b + gap) + gap * 2      # 让开左侧 3 个工具按钮
        return [(x0 + i * (b + gap), pad, b) for i in range(2)], b

    def mode_hit(self, px, py):
        if not getattr(self, "mode_buttons_enabled", True):
            return None
        rects, _b = self.mode_geom()
        for i, (x, y, sz) in enumerate(rects):
            if x <= px <= x + sz and y <= py <= y + sz:
                return i
        return None

    def tb_geom(self):
        """返回 [(x, y, size)]，坐标是客户区像素、左上角原点。"""
        S = getattr(self, "S", 1.0)
        b = 30.0 * S
        gap = 4.0 * S
        pad = 9.0 * S
        return [(pad, pad + i * (b + gap), b) for i in range(len(self.TB_TOOLS))], b

    def tb_hit(self, px, py):
        rects, _b = self.tb_geom()
        for i, (x, y, s) in enumerate(rects):
            if x <= px <= x + s and y <= py <= y + s:
                return i
        return None

    def _tb_draw(self, w, h):
        S = getattr(self, "S", 1.0)
        rects, b = self.tb_geom()
        if w < b * 2 or h < b * 5:
            return
        x0 = rects[0][0]
        y0 = rects[0][1]
        cw = b
        ch = len(self.TB_TOOLS) * b + (len(self.TB_TOOLS) - 1) * 4.0 * S
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(0, w, 0, h, -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        def quad(x, y, ww, hh, c, a=1.0):
            """左上角原点的矩形 -> GL 坐标（y 轴翻过来）。"""
            GL.glColor4f(c[0], c[1], c[2], a)
            GL.glBegin(GL.GL_QUADS)
            GL.glVertex2f(x, h - y)
            GL.glVertex2f(x + ww, h - y)
            GL.glVertex2f(x + ww, h - y - hh)
            GL.glVertex2f(x, h - y - hh)
            GL.glEnd()

        def line(x1, y1, x2, y2, c, lw=1.6):
            GL.glColor4f(c[0], c[1], c[2], 1.0)
            GL.glLineWidth(lw * S)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex2f(x1, h - y1)
            GL.glVertex2f(x2, h - y2)
            GL.glEnd()

        def tri(p0, p1, p2, c):
            GL.glColor4f(c[0], c[1], c[2], 1.0)
            GL.glBegin(GL.GL_TRIANGLES)
            for p in (p0, p1, p2):
                GL.glVertex2f(p[0], h - p[1])
            GL.glEnd()

        # 底板
        quad(x0 - 3 * S, y0 - 3 * S, cw + 6 * S, ch + 6 * S, (0.12, 0.12, 0.14), 0.88)
        GL.glColor4f(0.42, 0.42, 0.46, 0.85)
        GL.glLineWidth(1.0)
        GL.glBegin(GL.GL_LINE_LOOP)
        for px_, py_ in ((x0 - 3 * S, y0 - 3 * S), (x0 + cw + 3 * S, y0 - 3 * S),
                         (x0 + cw + 3 * S, y0 + ch + 3 * S), (x0 - 3 * S, y0 + ch + 3 * S)):
            GL.glVertex2f(px_, h - py_)
        GL.glEnd()

        for i, (rx, ry, rs) in enumerate(rects):
            name = self.TB_TOOLS[i]
            on = (self.tool == name)
            hot = (getattr(self, "tb_hover", None) == i)
            if on:
                bg = (0.04, 0.39, 0.78)
            elif hot:
                bg = (0.28, 0.28, 0.33)
            else:
                bg = (0.19, 0.19, 0.22)
            quad(rx, ry, rs, rs, bg)
            col = (1.0, 1.0, 1.0) if on else (0.86, 0.86, 0.88)
            cx, cy = rx + rs * 0.5, ry + rs * 0.5
            r = rs * 0.29
            if name == "move":
                line(cx, cy - r, cx, cy + r, col, 2.0)
                line(cx - r, cy, cx + r, cy, col, 2.0)
                aw, ah = r * 0.42, r * 0.50
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    tx, ty = cx + dx * r, cy + dy * r
                    if dx:
                        tri((tx, ty), (tx - dx * ah, ty - aw), (tx - dx * ah, ty + aw), col)
                    else:
                        tri((tx, ty), (tx - aw, ty - dy * ah), (tx + aw, ty - dy * ah), col)
            elif name == "rotate":
                pts = []
                for k in range(0, 300, 10):
                    a = math.radians(k + 30)
                    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
                GL.glColor4f(col[0], col[1], col[2], 1.0)
                GL.glLineWidth(2.0 * S)
                GL.glBegin(GL.GL_LINE_STRIP)
                for p in pts:
                    GL.glVertex2f(p[0], h - p[1])
                GL.glEnd()
                a = math.radians(30)
                tx, ty = cx + r * math.cos(a), cy + r * math.sin(a)
                nx, ny = -math.sin(a), math.cos(a)
                rx_, ry_ = math.cos(a), math.sin(a)
                tri((tx + nx * r * 0.55, ty + ny * r * 0.55),
                    (tx - nx * r * 0.10 + rx_ * r * 0.42, ty - ny * r * 0.10 + ry_ * r * 0.42),
                    (tx - nx * r * 0.10 - rx_ * r * 0.42, ty - ny * r * 0.10 - ry_ * r * 0.42),
                    col)
            elif name == "link":     # 选择相连面：两个套住的环
                GL.glColor4f(col[0], col[1], col[2], 1.0)
                GL.glLineWidth(1.8 * S)
                for ox in (-r * 0.34, r * 0.34):
                    GL.glBegin(GL.GL_LINE_LOOP)
                    for k in range(0, 360, 24):
                        a = math.radians(k)
                        GL.glVertex2f(cx + ox + r * 0.62 * math.cos(a) - r * 0.15,
                                      h - (cy + r * 0.62 * math.sin(a)))
                    GL.glEnd()
            else:   # scale
                ux, uy = 0.7071, -0.7071
                ax, ay = cx - r * 0.85, cy + r * 0.85
                bx, by = cx + r * 0.95, cy - r * 0.95
                line(ax, ay, bx - ux * r * 0.5, by - uy * r * 0.5, col, 2.0)
                tri((bx, by),
                    (bx - ux * r * 0.62 - uy * r * 0.34, by - uy * r * 0.62 + ux * r * 0.34),
                    (bx - ux * r * 0.62 + uy * r * 0.34, by - uy * r * 0.62 - ux * r * 0.34),
                    col)
                s2 = r * 0.30
                quad(ax - s2 * 0.5, ay - s2 * 0.5, s2, s2, col)

        GL.glLineWidth(1.0)
        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_LIGHTING)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()

    # ---------------------------------------------------------------- AABB
    def set_aabbs(self, boxes):
        """boxes: [(name, min(3), max(3))] —— 画成线框盒。"""
        boxes = list(boxes or [])
        sig = tuple((b[0], tuple(round(float(v), 4) for v in b[1]),
                     tuple(round(float(v), 4) for v in b[2])) for b in boxes)
        if sig == getattr(self, "_aabb_sig", None):
            return
        self._aabb_sig = sig
        self.aabbs = boxes
        if self.ready:
            opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
            self._upload_aabbs()
            self._kick()

    def highlight_aabb(self, name):
        if name == getattr(self, "_aabb_hl", None):
            return
        self._aabb_hl = name
        if self.ready:
            self._kick()

    def set_aabb_sel(self, name, mn=None, mx=None):
        """当前可单面拖动的 AABB（None 表示没有）。"""
        if name is None or mn is None or mx is None:
            self.sel_aabb = None
        else:
            self.sel_aabb = (name, np.asarray(mn, float), np.asarray(mx, float))
        if self.ready:
            self._kick()

    def _aabb_faces(self):
        """6 个面手柄：(axis, sign, 面中心点)。"""
        if self.sel_aabb is None:
            return []
        _n, mn, mx = self.sel_aabb
        c = (mn + mx) * 0.5
        out = []
        for axis in range(3):
            for sign in (-1, 1):
                pt = c.copy()
                pt[axis] = mx[axis] if sign > 0 else mn[axis]
                out.append((axis, sign, pt))
        return out

    def _aabb_face_hit(self, px, py):
        faces = self._aabb_faces()
        if not faces:
            return None
        pts = [f[2] for f in faces]
        sx, sy, ok = self._project_points(pts)
        S = getattr(self, "S", 1.0)
        tol = max(9.0, 12.0 * S)
        best = None
        for i, f in enumerate(faces):
            if not ok[i]:
                continue
            d = math.hypot(px - sx[i], py - sy[i])
            if d <= tol and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else best[1]

    def _upload_aabbs(self):
        if not self.ready:
            return
        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
        if self._aabb_vbo:
            try:
                GL.glDeleteBuffers(1, [self._aabb_vbo])
            except Exception:  # noqa: BLE001
                pass
        self._aabb_vbo = 0
        self._aabb_n = 0
        self._aabb_seg = {}
        if not self.aabbs:
            return

        COL = (0.25, 0.85, 1.00)
        verts = []
        # 8 个角，按 (x,y,z) 的 0/1 组合；12 条棱用角编号对表示
        EDGES = ((0, 1), (1, 3), (3, 2), (2, 0),      # 底面
                 (4, 5), (5, 7), (7, 6), (6, 4),      # 顶面
                 (0, 4), (1, 5), (2, 6), (3, 7))      # 竖棱
        for item in self.aabbs:
            name = item[0]
            mn = np.asarray(item[1], dtype=float)
            mx = np.asarray(item[2], dtype=float)
            c = []
            for i in range(8):
                c.append((mx[0] if i & 1 else mn[0],
                          mx[1] if i & 2 else mn[1],
                          mx[2] if i & 4 else mn[2]))
            first = len(verts)
            for a, b in EDGES:
                verts.append(c[a] + COL)
                verts.append(c[b] + COL)
            self._aabb_seg[name] = (first, len(verts) - first)

        arr = np.asarray(verts, dtype=np.float32)
        self._aabb_vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._aabb_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self._aabb_n = len(arr)
        dlog("AABB {} 个，线顶点 {}".format(len(self.aabbs), self._aabb_n))

    def _draw_aabbs(self):
        if self.solo is not None:      # 独显部件时不画包围盒，免得抢视线
            return
        if not self._aabb_vbo or not self._aabb_n:
            return
        stride = 6 * 4
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._aabb_vbo)
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_COLOR_ARRAY)
        GL.glVertexPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(0))
        GL.glColorPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(12))
        GL.glLineWidth(1.6)
        GL.glDrawArrays(GL.GL_LINES, 0, self._aabb_n)

        if self._aabb_hl in self._aabb_seg:
            first, cnt = self._aabb_seg[self._aabb_hl]
            GL.glDisableClientState(GL.GL_COLOR_ARRAY)
            GL.glColor3f(1.0, 0.85, 0.12)
            GL.glLineWidth(3.0)
            GL.glVertexPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(first * stride))
            GL.glDrawArrays(GL.GL_LINES, 0, cnt)

        GL.glLineWidth(1.0)
        GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

        # 选中的框画 6 个面手柄（屏幕空间固定大小，拖动手柄 = 单面移动）
        faces = self._aabb_faces()
        if faces:
            pts = [f[2] for f in faces]
            sx, sy, ok = self._project_points(pts)
            S = getattr(self, "S", 1.0)
            r = 8.0 * S                      # 半边长 ~8px，整块 16px
            w = max(1, self.winfo_width())
            h = max(1, self.winfo_height())
            GL.glMatrixMode(GL.GL_PROJECTION)
            GL.glPushMatrix()
            GL.glLoadIdentity()
            GL.glOrtho(0, w, 0, h, -1, 1)
            GL.glMatrixMode(GL.GL_MODELVIEW)
            GL.glPushMatrix()
            GL.glLoadIdentity()
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_LIGHTING)
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

            def _sq(x, y, half, col, a=1.0):
                GL.glColor4f(col[0], col[1], col[2], a)
                GL.glBegin(GL.GL_QUADS)
                GL.glVertex2f(x - half, h - (y - half))
                GL.glVertex2f(x + half, h - (y - half))
                GL.glVertex2f(x + half, h - (y + half))
                GL.glVertex2f(x - half, h - (y + half))
                GL.glEnd()

            for i, (axis, _sign, _pt) in enumerate(faces):
                if not ok[i]:
                    continue
                c = (1.0, 0.35, 0.35) if axis == 0 else (
                    (0.35, 0.90, 0.45) if axis == 1 else (0.45, 0.62, 1.0))
                if self._aabb_face_hover == i:
                    c = (1.0, 0.92, 0.15)
                x, y = float(sx[i]), float(sy[i])
                _sq(x, y, r + 2.0 * S, (0.05, 0.05, 0.05), 0.9)   # 深色描边
                _sq(x, y, r, c)                                    # 本体

            GL.glDisable(GL.GL_BLEND)
            GL.glMatrixMode(GL.GL_PROJECTION)
            GL.glPopMatrix()
            GL.glMatrixMode(GL.GL_MODELVIEW)
            GL.glPopMatrix()

        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_LIGHTING)

    # ---------------------------------------------------------------- 定位器
    def set_locators(self, locs):
        """locs: [(name, pos(3), basis(3x3), id)] —— 画成坐标三叉。"""
        self.locators = list(locs or [])
        self._loc_hl = None
        self._loc_hls = set()
        if self.ready:
            self._upload_locators()
            self.redraw(force=True)

    def highlight_locator(self, name):
        self._loc_hl = name
        self._loc_hls.discard(name)
        if self.ready:
            self.redraw(force=True)

    def highlight_locators(self, names, active=None):
        """多选定位器高亮：active 主选黄，其余淡蓝；names 空则全部清除。"""
        names = [n for n in (names or ()) if n]
        if active not in names:
            active = None
        hls = {n for n in names if n != active}
        if self._loc_hl == active and self._loc_hls == hls:
            return
        self._loc_hl = active
        self._loc_hls = hls
        if self.ready:
            self.redraw(force=True)
        if self.ready:
            self.redraw(force=True)

    def _upload_locators(self):
        if not self.ready:
            return
        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
        if self._loc_vbo:
            try:
                GL.glDeleteBuffers(1, [self._loc_vbo])
            except Exception:  # noqa: BLE001
                pass
            self._loc_vbo = 0
        self._loc_n = 0
        self._loc_tn = 0
        self._loc_seg = {}
        if not self.locators:
            return

        # 画成箭头（同 Blender 的 Single Arrow）：细杆 + 圆锥头，朝向定位器局部 +Z
        L = max(0.04, getattr(self, "_scene_radius", self.radius) * 0.15)
        self._arrow_len = L
        SHAFT = (0.72, 0.76, 0.86)
        HEAD = (0.80, 0.84, 0.93)
        lines, tris = [], []
        for item in self.locators:
            if item[0] in self.hidden:
                continue
            name, pos, basis = item[0], np.asarray(item[1], float), np.asarray(item[2], float)
            d = basis[:, 2]
            nl = np.linalg.norm(d)
            d = d / nl if nl > 1e-9 else np.array([0.0, 0.0, 1.0])

            tip = pos + d * L
            base = pos + d * (L * 0.72)
            r = L * 0.038
            tmp = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
            u = np.cross(d, tmp)
            u /= (np.linalg.norm(u) or 1.0)
            v = np.cross(d, u)

            line_first = len(lines)
            lines.append((pos[0], pos[1], pos[2]) + SHAFT)
            lines.append((base[0], base[1], base[2]) + SHAFT)

            tri_first = len(tris)
            seg = 6
            ring = []
            for i in range(seg):
                a = 2.0 * math.pi * i / seg
                ring.append(base + (u * math.cos(a) + v * math.sin(a)) * r)
            for i in range(seg):
                a, b = ring[i], ring[(i + 1) % seg]
                tris.append((a[0], a[1], a[2]) + HEAD)
                tris.append((b[0], b[1], b[2]) + HEAD)
                tris.append((tip[0], tip[1], tip[2]) + HEAD)
                # 底面：让箭头从背后看也是实心的
                tris.append((b[0], b[1], b[2]) + HEAD)
                tris.append((a[0], a[1], a[2]) + HEAD)
                tris.append((base[0], base[1], base[2]) + HEAD)

            self._loc_seg[name] = (line_first, 2, tri_first, len(tris) - tri_first)

        if not lines and not tris:
            return

        arr = np.asarray(lines + tris, dtype=np.float32)
        self._loc_vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._loc_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self._loc_n = len(lines)
        self._loc_tn = len(tris)
        dlog("定位器 {} 个，线顶点 {} 三角顶点 {}".format(
            len(self.locators), self._loc_n, self._loc_tn))

    def _draw_locators(self):
        if not self.show_locators or not self._loc_vbo or not self._loc_n:
            return
        stride = 6 * 4
        # 辅助标记一律画在最上层：被车体挡住就找不到定位器了
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._loc_vbo)
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_COLOR_ARRAY)
        GL.glVertexPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(0))
        GL.glColorPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(12))
        GL.glLineWidth(1.5)
        if self.solo is not None:
            # 独显网格 -> 箭头全不画；独显定位器 -> 只画那一根
            seg = self._loc_seg.get(self.solo)
            if seg is None:
                GL.glDisableClientState(GL.GL_COLOR_ARRAY)
                GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
                GL.glEnable(GL.GL_DEPTH_TEST)
                GL.glEnable(GL.GL_LIGHTING)
                return
            lf, ln, tf, tn = seg
            GL.glDrawArrays(GL.GL_LINES, lf, ln)
            GL.glDrawArrays(GL.GL_TRIANGLES, self._loc_n + tf, tn)
        else:
            GL.glDrawArrays(GL.GL_LINES, 0, self._loc_n)
            GL.glDrawArrays(GL.GL_TRIANGLES, self._loc_n, self._loc_tn)

        if self._loc_hl in self._loc_seg and self.solo in (None, self._loc_hl):
            lf, ln, tf, tn = self._loc_seg[self._loc_hl]
            GL.glDisableClientState(GL.GL_COLOR_ARRAY)
            GL.glColor3f(1.0, 0.85, 0.12)      # 选中的定位器点亮黄色
            GL.glLineWidth(4.0)
            GL.glVertexPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(lf * stride))
            GL.glDrawArrays(GL.GL_LINES, 0, ln)
            GL.glVertexPointer(3, GL.GL_FLOAT, stride,
                               ctypes.c_void_p((self._loc_n + tf) * stride))
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, tn)

        # 副选定位器淡蓝描边（多选时能看出选了哪些）
        for nm in getattr(self, "_loc_hls", ()):
            if nm not in self._loc_seg or self.solo not in (None, nm):
                continue
            lf, ln, tf, tn = self._loc_seg[nm]
            GL.glDisableClientState(GL.GL_COLOR_ARRAY)
            GL.glColor3f(0.45, 0.75, 1.0)      # 副选淡蓝
            GL.glLineWidth(3.0)
            GL.glVertexPointer(3, GL.GL_FLOAT, stride, ctypes.c_void_p(lf * stride))
            GL.glDrawArrays(GL.GL_LINES, 0, ln)
            GL.glVertexPointer(3, GL.GL_FLOAT, stride,
                               ctypes.c_void_p((self._loc_n + tf) * stride))
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, tn)

        GL.glLineWidth(1.0)
        GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_LIGHTING)

    # ---------------------------------------------------------------- UV 视图
    def set_view_mode(self, mode):
        mode = "uv" if str(mode).lower().startswith("uv") else "3d"
        if mode == getattr(self, "view_mode", "3d"):
            return
        self.view_mode = mode
        self._last_sig = None
        if self.ready:
            self.redraw(force=True)

    def _uv_bounds(self):
        """所有网格 UV 的总范围（默认 0..1）。"""
        lo = np.array([1e18, 1e18])
        hi = np.array([-1e18, -1e18])
        got = False
        for _name, uv in self.uv_visible_meshes():
            a = np.asarray(uv, float).reshape(-1, 2)
            if not len(a):
                continue
            lo = np.minimum(lo, a.min(axis=0))
            hi = np.maximum(hi, a.max(axis=0))
            got = True
        if not got or hi[0] - lo[0] < 1e-6 or hi[1] - lo[1] < 1e-6:
            return np.array([0.0, 0.0]), np.array([1.0, 1.0])
        return lo, hi

    def uv_visible_meshes(self):
        """UV 视图要画的网格：跟随眼睛隐藏 / 独显。"""
        out = []
        hidden = getattr(self, "hidden", set()) or set()
        solo = getattr(self, "solo", None)
        for it in self.meshes:
            name = it[0]
            if name in hidden:
                continue
            if solo is not None and name != solo:
                continue
            uv = it[5] if len(it) > 5 else None
            if uv is None:
                continue
            out.append((name, uv))
        return out

    def uv_scale(self):
        """当前 UV 视图的 像素/UV 比例和中心（给 app 换算拖拽增量）。"""
        w, h = self.winfo_width(), self.winfo_height()
        lo, hi = self._uv_bounds()
        span = np.maximum(hi - lo, 1e-6)
        side = max(32.0, min(w, h) - 40 * getattr(self, "S", 1.0))
        zoom = float(getattr(self, "_uv_zoom", 1.0) or 1.0)
        scale = side / float(max(span[0], span[1])) * zoom
        panx, pany = getattr(self, "_uv_pan", (0.0, 0.0))
        mx, my = w * 0.5 + panx, h * 0.5 + pany
        cx, cy = (lo + hi) * 0.5
        return scale, (mx, my), (cx, cy)

    def _uv_gz_geom(self):
        """gizmo 在屏幕上的几何：中心、U/V 轴端点、环半径。"""
        piv = getattr(self, "uv_pivot", None)
        if piv is None or not self.uv_sel_pts:
            return None
        scale, (mx, my), (cx, cy) = self.uv_scale()
        S = getattr(self, "S", 1.0)
        px = mx + (float(piv[0]) - cx) * scale
        py = my + (float(piv[1]) - cy) * scale
        L = 66.0 * S
        return {"c": (px, py), "u": (px + L, py), "v": (px, py + L),
                "r": 46.0 * S, "L": L, "k": scale}

    def uv_gz_hit(self, px, py):
        g = self._uv_gz_geom()
        if g is None:
            return None
        cx, cy = g["c"]
        S = getattr(self, "S", 1.0)
        if self.tool == "rotate":
            d = math.hypot(px - cx, py - cy)
            return "ring" if abs(d - g["r"]) <= 8.0 * S else None
        if math.hypot(px - cx, py - cy) <= 8.0 * S:
            return "center"
        for name, ex, ey in (("u", g["u"][0], g["u"][1]), ("v", g["v"][0], g["v"][1])):
            if self._seg_dist(px, py, cx, cy, ex, ey) <= 6.0 * S:
                return name
        return None

    def _draw_uv_gizmo(self, w, h):
        g = self._uv_gz_geom()
        if g is None:
            return
        S = getattr(self, "S", 1.0)
        cx, cy = g["c"]
        hover = getattr(self, "_uv_gz_hover", None)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(0, w, 0, h, -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        def quad2(x, y, half, col, a=1.0):
            GL.glColor4f(col[0], col[1], col[2], a)
            GL.glBegin(GL.GL_QUADS)
            GL.glVertex2f(x - half, h - (y - half))
            GL.glVertex2f(x + half, h - (y - half))
            GL.glVertex2f(x + half, h - (y + half))
            GL.glVertex2f(x - half, h - (y + half))
            GL.glEnd()

        def line2(x0, y0, x1, y1, col, lw):
            GL.glColor3f(*col)
            GL.glLineWidth(lw)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex2f(x0, h - y0)
            GL.glVertex2f(x1, h - y1)
            GL.glEnd()

        tool = self.tool
        if tool == "rotate":
            r = g["r"]
            col = (1.0, 0.85, 0.2) if hover == "ring" else (0.85, 0.88, 0.95)
            GL.glColor3f(*col)
            GL.glLineWidth(max(1.5, 2.0 * S))
            GL.glBegin(GL.GL_LINE_LOOP)
            for k in range(48):
                a = 2 * math.pi * k / 48
                GL.glVertex2f(cx + r * math.cos(a), h - (cy + r * math.sin(a)))
            GL.glEnd()
        else:
            for name, col in (("u", (0.92, 0.36, 0.32)), ("v", (0.45, 0.82, 0.36))):
                ex, ey = g[name]
                hot = (hover == name)
                line2(cx, cy, ex, ey, (1.0, 0.85, 0.2) if hot else col,
                      max(2.0, 2.6 * S) if hot else max(1.6, 1.8 * S))
                quad2(ex, ey, 5.0 * S if tool == "move" else 6.0 * S,
                      (1.0, 0.85, 0.2) if hot else col)
            quad2(cx, cy, 6.0 * S,
                  (1.0, 0.85, 0.2) if hover == "center" else (0.88, 0.88, 0.92), 0.95)

        GL.glLineWidth(1.0)
        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()

    def uv_pick(self, px, py, tol=8.0):
        """UV 视图命中：优先点，其次面。返回 (kind, name, face, corner)。"""
        scale, (mx, my), (cx, cy) = self.uv_scale()
        S = getattr(self, "S", 1.0)
        tol = max(6.0, tol * S)
        best = None
        for name, uv in self.uv_visible_meshes():
            a = np.asarray(uv, float).reshape(-1, 3, 2)
            if not len(a):
                continue
            xs = mx + (a[..., 0] - cx) * scale
            ys = my + (a[..., 1] - cy) * scale
            d = np.hypot(xs - px, ys - py)
            f, j = np.unravel_index(int(np.argmin(d)), d.shape)
            if d[f, j] <= tol and (best is None or d[f, j] < best[0]):
                best = (float(d[f, j]), "vert", name, int(f), int(j))
        if best is not None:
            return best[1:]
        # 面命中：点在三角形内
        for name, uv in self.uv_visible_meshes():
            a = np.asarray(uv, float).reshape(-1, 3, 2)
            if not len(a):
                continue
            for f in range(len(a)):
                p0 = (mx + (a[f, 0, 0] - cx) * scale, my + (a[f, 0, 1] - cy) * scale)
                p1 = (mx + (a[f, 1, 0] - cx) * scale, my + (a[f, 1, 1] - cy) * scale)
                p2 = (mx + (a[f, 2, 0] - cx) * scale, my + (a[f, 2, 1] - cy) * scale)
                d0 = (p1[0] - p0[0]) * (py - p0[1]) - (p1[1] - p0[1]) * (px - p0[0])
                d1 = (p2[0] - p1[0]) * (py - p1[1]) - (p2[1] - p1[1]) * (px - p1[0])
                d2 = (p0[0] - p2[0]) * (py - p2[1]) - (p0[1] - p2[1]) * (px - p2[0])
                if (d0 >= 0 and d1 >= 0 and d2 >= 0) or (d0 <= 0 and d1 <= 0 and d2 <= 0):
                    return ("face", name, int(f), -1)
        return None

    def _mode_draw(self, w, h):
        """画 3D / UV 模式按钮：选中蓝底，悬停浅灰（Blender 风格图标）。"""
        if not getattr(self, "mode_buttons_enabled", True):
            return
        S = getattr(self, "S", 1.0)
        rects, b = self.mode_geom()
        if w < b * 6 or h < b * 2:
            return
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(0, w, 0, h, -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        def quad(x, y, ww, hh, col, a=1.0):
            GL.glColor4f(col[0], col[1], col[2], a)
            GL.glBegin(GL.GL_QUADS)
            GL.glVertex2f(x, h - y)
            GL.glVertex2f(x + ww, h - y)
            GL.glVertex2f(x + ww, h - y - hh)
            GL.glVertex2f(x, h - y - hh)
            GL.glEnd()

        def ring(x, y, ww, hh, col, lw=1.4):
            GL.glColor3f(*col)
            GL.glLineWidth(lw)
            GL.glBegin(GL.GL_LINE_LOOP)
            GL.glVertex2f(x, h - y)
            GL.glVertex2f(x + ww, h - y)
            GL.glVertex2f(x + ww, h - y - hh)
            GL.glVertex2f(x, h - y - hh)
            GL.glEnd()

        def line(x0, y0, x1, y1, col, lw=1.4):
            GL.glColor3f(*col)
            GL.glLineWidth(lw)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex2f(x0, h - y0)
            GL.glVertex2f(x1, h - y1)
            GL.glEnd()

        modes = ("3d", "uv")
        for i, (x, y, sz) in enumerate(rects):
            active = (getattr(self, "view_mode", "3d") == modes[i])
            hot = (getattr(self, "_mode_hover", None) == i)
            bg = (0.04, 0.39, 0.78) if active else ((0.28, 0.28, 0.33) if hot else (0.19, 0.19, 0.22))
            quad(x, y, sz, sz, bg)
            col = (1.0, 1.0, 1.0) if active else (0.86, 0.86, 0.88)
            cx, cy = x + sz * 0.5, y + sz * 0.5
            r = sz * 0.26
            if modes[i] == "3d":
                # 立方体图标
                ring(cx - r, cy - r, 2 * r, 2 * r, col)
                line(cx - r, cy - r, cx - r * 0.4, cy - r * 1.5, col)
                line(cx + r, cy - r, cx + r * 0.4, cy - r * 1.5, col)
                line(cx - r * 0.4, cy - r * 1.5, cx + r * 0.4, cy - r * 1.5, col)
                line(cx - r, cy + r, cx - r * 0.4, cy + r * 0.5, col)
                line(cx + r, cy + r, cx + r * 0.4, cy + r * 0.5, col)
            else:
                # UV：方格 + 对角折线
                ring(cx - r, cy - r, 2 * r, 2 * r, col)
                line(cx - r, cy - r * 0.2, cx - r * 0.2, cy - r * 0.2, col)
                line(cx - r * 0.2, cy - r * 0.2, cx - r * 0.2, cy + r, col)
                line(cx + r, cy - r * 0.2, cx + r * 0.2, cy - r * 0.2, col)
                line(cx + r * 0.2, cy - r * 0.2, cx + r * 0.2, cy - r, col)

        GL.glLineWidth(1.0)
        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()

    def _draw_uv(self, w, h):
        """UV 视图：深灰底 + 0.1 网格 + UV 面填充/边/顶点（Blender UV 编辑器风格）。"""
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(0, w, 0, h, -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_TEXTURE_2D)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        # 关键：别让 AABB/网格留下的 VBO 绑着 —— client array 的顶点指针
        # 会被当成 VBO 偏移，画到别处（选中后 UV 线「消失」就是这个）
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)

        S = getattr(self, "S", 1.0)
        # 底色（比 3D 视口浅一点的灰，和 Blender UV 编辑器接近）
        GL.glColor4f(0.24, 0.24, 0.26, 1.0)
        GL.glBegin(GL.GL_QUADS)
        GL.glVertex2f(0, 0)
        GL.glVertex2f(w, 0)
        GL.glVertex2f(w, h)
        GL.glVertex2f(0, h)
        GL.glEnd()

        lo, hi = self._uv_bounds()
        span = np.maximum(hi - lo, 1e-6)
        zoom = float(getattr(self, "_uv_zoom", 1.0) or 1.0)
        panx, pany = getattr(self, "_uv_pan", (0.0, 0.0))
        side = max(32.0, min(w, h) - 40 * S)
        base = side / float(max(span[0], span[1]))
        scale = base * zoom
        cx, cy = (lo + hi) * 0.5
        mx, my = w * 0.5 + panx, h * 0.5 + pany

        def to_screen(u, v):
            return (mx + (float(u) - cx) * scale, my + (float(v) - cy) * scale)

        def to_uv(x, y):
            return ((x - mx) / scale + cx, (y - my) / scale + cy)

        # ---- 贴图当底（有贴图时，淡淡铺一层，便于对位）
        if getattr(self, "_tex_id", 0):
            x0, y0 = to_screen(0.0, 0.0)
            x1, y1 = to_screen(1.0, 1.0)
            GL.glEnable(GL.GL_TEXTURE_2D)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id)
            GL.glColor4f(1.0, 1.0, 1.0, 0.45)
            GL.glBegin(GL.GL_QUADS)
            GL.glTexCoord2f(0.0, 1.0); GL.glVertex2f(x0, y0)
            GL.glTexCoord2f(1.0, 1.0); GL.glVertex2f(x1, y0)
            GL.glTexCoord2f(1.0, 0.0); GL.glVertex2f(x1, y1)
            GL.glTexCoord2f(0.0, 0.0); GL.glVertex2f(x0, y1)
            GL.glEnd()
            GL.glDisable(GL.GL_TEXTURE_2D)

        # ---- 网格：0.1 细线（缩放太小就加粗到 0.5），整数格加强
        u0, v0 = to_uv(0, h)
        u1, v1 = to_uv(w, 0)
        umin, umax = min(u0, u1), max(u0, u1)      # 屏幕 y 是反的，v 会倒过来
        vmin, vmax = min(v0, v1), max(v0, v1)
        vis = max(umax - umin, vmax - vmin)
        step = 0.1
        while vis / step > 60:
            step *= 2.0
        while vis / step < 12:
            step *= 0.5
        GL.glLineWidth(max(1.0, S))
        for strong, col in ((False, (0.38, 0.38, 0.41, 0.95)),
                            (True, (0.50, 0.50, 0.54, 1.0))):
            GL.glColor4f(*col)
            GL.glBegin(GL.GL_LINES)
            k0 = int(math.floor(umin / step))
            k1 = int(math.ceil(umax / step))
            for k in range(k0, k1 + 1):
                uu = k * step
                is_int = abs(uu - round(uu)) < 1e-6
                if is_int != strong:
                    continue
                x, _ = to_screen(uu, 0)
                GL.glVertex2f(x, 0)
                GL.glVertex2f(x, h)
            k0 = int(math.floor(vmin / step))
            k1 = int(math.ceil(vmax / step))
            for k in range(k0, k1 + 1):
                vv = k * step
                is_int = abs(vv - round(vv)) < 1e-6
                if is_int != strong:
                    continue
                _, y = to_screen(0, vv)
                GL.glVertex2f(0, y)
                GL.glVertex2f(w, y)
            GL.glEnd()

        # ---- 原点轴：U 红 / V 绿
        ox_, oy_ = to_screen(0.0, 0.0)
        GL.glLineWidth(max(1.0, 1.2 * S))
        GL.glBegin(GL.GL_LINES)
        GL.glColor4f(0.85, 0.30, 0.30, 0.95)
        GL.glVertex2f(ox_ - 26 * S, oy_)
        GL.glVertex2f(ox_ + 26 * S, oy_)
        GL.glColor4f(0.45, 0.80, 0.35, 0.95)
        GL.glVertex2f(ox_, oy_ - 26 * S)
        GL.glVertex2f(ox_, oy_ + 26 * S)
        GL.glEnd()

        # ---- 0..1 贴图框（略亮，和 Blender 的 image border 一致）
        x0, y0 = to_screen(0.0, 0.0)
        x1, y1 = to_screen(1.0, 1.0)
        GL.glColor4f(0.62, 0.62, 0.66, 0.9)
        GL.glLineWidth(max(1.0, S))
        GL.glBegin(GL.GL_LINE_LOOP)
        for (px_, py_) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            GL.glVertex2f(px_, py_)
        GL.glEnd()

        # ---- 收集 UV 三角形
        others, sels, act = [], [], []
        sel_set = {self.hl_name} | set(getattr(self, "_hl_others", set()))
        sel_set.discard(None)
        for name, uv in self.uv_visible_meshes():
            a = np.asarray(uv, float).reshape(-1, 3, 2)
            if not len(a):
                continue
            xs = mx + (a[..., 0] - cx) * scale
            ys = my + (a[..., 1] - cy) * scale
            tri = np.stack([xs, ys], axis=-1).reshape(-1, 2)
            if name == self.hl_name:
                act.append(tri)
            elif name in sel_set:
                sels.append(tri)
            else:
                others.append(tri)

        def draw_tris(parts, fill, edge, vcol):
            if not parts:
                return
            arr = np.vstack(parts).astype(np.float32)
            ptr = arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            # 面填充（半透明）
            GL.glColor4f(fill[0], fill[1], fill[2], fill[3])
            GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
            GL.glVertexPointer(2, GL.GL_FLOAT, 0, ptr)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, len(arr))
            # 边（每三角形 3 条）
            seg = arr.reshape(-1, 3, 2)
            lines = np.empty((len(seg) * 6, 2), np.float32)
            lines[0::6] = seg[:, 0]; lines[1::6] = seg[:, 1]
            lines[2::6] = seg[:, 1]; lines[3::6] = seg[:, 2]
            lines[4::6] = seg[:, 2]; lines[5::6] = seg[:, 0]
            GL.glColor3f(*edge)
            GL.glLineWidth(max(1.0, S))
            GL.glVertexPointer(2, GL.GL_FLOAT, 0,
                               lines.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
            GL.glDrawArrays(GL.GL_LINES, 0, len(lines))
            # 顶点（小方点）
            GL.glPointSize(max(3.0, 3.2 * S))
            GL.glColor3f(*vcol)
            GL.glVertexPointer(2, GL.GL_FLOAT, 0, ptr)
            GL.glDrawArrays(GL.GL_POINTS, 0, len(arr))
            GL.glPointSize(1.0)
            GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

        # 岛内填充要够亮，否则纯线框分不清岛与背景
        draw_tris(others, (0.68, 0.70, 0.76, 0.62), (0.88, 0.90, 0.94), (0.10, 0.10, 0.12))
        # 副选：淡蓝（和视口描边一致）
        draw_tris(sels, (0.32, 0.60, 1.0, 0.60), (0.70, 0.86, 1.0), (0.04, 0.09, 0.16))
        # 活动：橙
        draw_tris(act, (1.0, 0.66, 0.22, 0.68), (1.0, 0.88, 0.50), (0.16, 0.10, 0.02))

        self._draw_uv_gizmo(w, h)

        # ---- 选中 UV 点：橙色方点
        pts = getattr(self, "uv_sel_pts", None) or []
        if pts:
            r = max(2.2, 2.4 * S)
            GL.glColor4f(1.0, 0.55, 0.10, 1.0)
            GL.glBegin(GL.GL_QUADS)
            for uu, vv in pts:
                xx, yy = to_screen(uu, vv)
                GL.glVertex2f(xx - r, yy - r)
                GL.glVertex2f(xx + r, yy - r)
                GL.glVertex2f(xx + r, yy + r)
                GL.glVertex2f(xx - r, yy + r)
            GL.glEnd()

        GL.glLineWidth(1.0)
        GL.glDisable(GL.GL_BLEND)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()

    # ---------------------------------------------------------------- 导出 UV 布局
    def uv_layout_rgba(self, out_w, out_h, opacity=1.0, line_px=None, color=(1.0, 1.0, 1.0),
                       fill=True, fill_color=(0.72, 0.74, 0.80),
                       line_alpha=1.0, fill_alpha=1.0, fit=False):
        """离屏渲染 UV 线框 + 岛内填充到 RGBA 缓冲区（背景全透明）。

        分两趟画成「覆盖度遮罩」（线一趟、填充一趟，都是不透明白），
        再在 numpy 里按各自的颜色 / 不透明度合成 —— 这样：
          · 相邻三角形的公共边不会重复混合出深浅缝
          · 线和底色可以分别设颜色和不透明度
          · 2x 超采样 + 补裂缝，岛内不会留 1 像素空点
        返回 (h, w, 4) uint8。
        """
        if not self.ready:
            try:
                self.redraw(force=True)      # 首次还没画过：先催一次初始化
            except Exception:  # noqa: BLE001
                pass
        if not self.ready:
            raise RuntimeError("OpenGL 未就绪")
        out_w = max(16, int(out_w))
        out_h = max(16, int(out_h))
        ss = 2 if max(out_w, out_h) * 2 <= 8192 else 1
        rw, rh = out_w * ss, out_h * ss
        meshes = self.uv_visible_meshes()
        if not meshes:
            raise RuntimeError("没有可导出的 UV（都隐藏了或模型没有 UV）")

        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        vp_old = GL.glGetIntegerv(GL.GL_VIEWPORT)
        fbo_old = GL.glGetIntegerv(GL.GL_FRAMEBUFFER_BINDING)
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, rw, rh, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                                  GL.GL_TEXTURE_2D, tex, 0)
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo_old)
            GL.glDeleteFramebuffers(1, [fbo])
            GL.glDeleteTextures([tex])
            raise RuntimeError("无法创建离屏缓冲（FBO 状态 0x%X）" % status)

        # 自适应铺满：留 4% 边距（两趟共用同一套矩阵）
        lo = np.array([1e18, 1e18])
        hi = np.array([-1e18, -1e18])
        for _n, uv in meshes:
            a = np.asarray(uv, float).reshape(-1, 2)
            if not len(a):
                continue
            lo = np.minimum(lo, a.min(axis=0))
            hi = np.maximum(hi, a.max(axis=0))
        span = np.maximum(hi - lo, 1e-6)
        pad = 0.04
        side = min(rw, rh) * (1.0 - 2 * pad)
        scale = side / float(max(span[0], span[1]))
        cx, cy = (lo + hi) * 0.5
        mx, my = rw * 0.5, rh * 0.5
        lw = float(line_px) if line_px else max(1.0, min(rw, rh) / 1024.0 * 1.6)

        def _screen(uv):
            a = np.asarray(uv, float).reshape(-1, 3, 2)
            if fit:
                xs = mx + (a[..., 0] - cx) * scale
                ys = my + (a[..., 1] - cy) * scale
            else:
                # 0~1 UV 空间 1:1 铺满整张图（和贴图对齐，Blender 的 UV 导出也是这个行为）
                xs = a[..., 0] * rw
                ys = a[..., 1] * rh
            return np.stack([xs, ys], axis=-1)

        def _run_pass(mode):
            """把覆盖度画成不透明白，返回 rw*rh 的 float 覆盖度。"""
            GL.glViewport(0, 0, rw, rh)
            GL.glClearColor(0.0, 0.0, 0.0, 0.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glDisable(GL.GL_LIGHTING)
            GL.glDisable(GL.GL_BLEND)
            GL.glColor4f(1.0, 1.0, 1.0, 1.0)
            if mode == "fill":
                GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
                GL.glPolygonOffset(1.0, 1.0)
            for _n, uv in meshes:
                tri2 = _screen(uv)
                if not len(tri2):
                    continue
                if mode == "fill":
                    flat = tri2.reshape(-1, 2).astype(np.float32)
                    GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
                    GL.glVertexPointer(2, GL.GL_FLOAT, 0,
                                       flat.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
                    GL.glDrawArrays(GL.GL_TRIANGLES, 0, len(flat))
                    GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
                else:
                    tri = tri2.reshape(-1, 3, 2)
                    m = tri.shape[0]
                    seg = np.empty((m * 6, 2), np.float32)
                    seg[0::2] = tri.reshape(-1, 2)
                    seg[1::2] = np.roll(tri, -1, axis=1).reshape(-1, 2)
                    GL.glLineWidth(lw)
                    GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
                    GL.glVertexPointer(2, GL.GL_FLOAT, 0,
                                       seg.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
                    GL.glDrawArrays(GL.GL_LINES, 0, len(seg))
                    GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
                    GL.glLineWidth(1.0)
            if mode == "fill":
                GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glFinish()
            buf = GL.glReadPixels(0, 0, rw, rh, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            a = np.frombuffer(buf, np.uint8).reshape(rh, rw, 4)[::-1]
            return a[:, :, 3].astype(np.float32) / 255.0

        try:
            GL.glMatrixMode(GL.GL_PROJECTION)
            GL.glPushMatrix()
            GL.glLoadIdentity()
            GL.glOrtho(0, rw, 0, rh, -1, 1)
            GL.glMatrixMode(GL.GL_MODELVIEW)
            GL.glPushMatrix()
            GL.glLoadIdentity()

            cov_fill = _run_pass("fill") if fill else None
            cov_line = _run_pass("lines")

            GL.glMatrixMode(GL.GL_PROJECTION)
            GL.glPopMatrix()
            GL.glMatrixMode(GL.GL_MODELVIEW)
            GL.glPopMatrix()
            arr = _uv_compose(cov_fill, cov_line, ss,
                              np.asarray(color, float), np.asarray(fill_color, float),
                              float(line_alpha), float(fill_alpha), float(opacity))
        finally:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo_old)
            GL.glDeleteFramebuffers(1, [fbo])
            GL.glDeleteTextures([tex])
            try:
                GL.glViewport(int(vp_old[0]), int(vp_old[1]), int(vp_old[2]), int(vp_old[3]))
            except Exception:  # noqa: BLE001
                pass
            self._last_sig = None
            self.redraw(force=True)
        return arr
    # ---------------------------------------------------------------- 纹理
    def _upload_texture(self):
        if not self.ready:
            return
        key = id(self.texture)
        if key == self._tex_key:
            return
        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))

        if self._tex_id:
            try:
                GL.glDeleteTextures([self._tex_id])
            except Exception:  # noqa: BLE001
                pass
            self._tex_id = 0

        if self.texture is None:
            self._tex_key = key
            return

        w, h, arr = self.texture
        rgb = np.ascontiguousarray(arr[:, :, ::-1])   # RGB -> BGR（GL_BGR 顺序）
        self._tex_id = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGB, w, h, 0,
                        GL.GL_BGR, GL.GL_UNSIGNED_BYTE, rgb)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        self._tex_key = key

    # ---------------------------------------------------------------- 渲染
    def redraw(self, force=False, size=None):
        if not self.ready:
            dlog("redraw: 跳过（GL 未就绪）")
            return
        if size is not None:
            w, h = int(size[0]), int(size[1])
        else:
            w = self.winfo_width()
            h = self.winfo_height()
        if w < 10 or h < 10:
            if DEBUG_FRAME:
                dlog("redraw: 跳过（尺寸 {}x{} 太小）".format(w, h))
            return

        sig = (round(self.yaw, 2), round(self.pitch, 2), round(self.dist, 3),
               round(self.pan[0], 1), round(self.pan[1], 1), w, h,
               id(self.texture), len(self.meshes), self.show_meshes,
               self.show_locators,
               self._loc_hl, len(self.locators), self.hl_name, len(self.hidden),
               tuple(sorted(getattr(self, "_hl_others", ()))),
               self.show_grid,
               self._aabb_hl, len(self.aabbs), self.solo,
               self.tool,
               None if self.pivot is None else tuple(np.round(np.asarray(self.pivot, float), 4)),
               self._gz_hover, getattr(self, "tb_hover", None),
               getattr(self, "view_mode", "3d"))
        if not force and sig == self._last_sig:
            if DEBUG_FRAME:
                dlog("redraw: 跳过（相机未变）")
            return
        self._last_sig = sig

        opengl32.wglMakeCurrent(ctypes.c_void_p(self.hdc), ctypes.c_void_p(self.glrc))
        self._upload_texture()

        GL.glViewport(0, 0, w, h)
        GL.glClearColor(0.10, 0.10, 0.12, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT
                   | GL.GL_STENCIL_BUFFER_BIT)

        if getattr(self, "view_mode", "3d") == "uv":
            self._draw_uv(w, h)
            self._mode_draw(w, h)
            gdi32.SwapBuffers(self.hdc)
            self.last_tri_count = 0
            return

        # 投影
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        aspect = w / float(h) if h else 1.0
        fovy = 40.0
        if getattr(self, "ortho", False):
            # 正交：可视高度取"透视在模型中心处的高度"，切换时画面不跳
            half_h = self.dist * math.tan(math.radians(fovy) * 0.5)
            half_w = half_h * aspect
            rng = self.dist * 12.0
            GL.glOrtho(-half_w, half_w, -half_h, half_h, -rng, rng)
        else:
            near = max(0.01, self.dist * 0.02)
            far = self.dist * 12.0
            top = near * np.tan(np.radians(fovy) * 0.5)
            right = top * aspect
            GL.glFrustum(-right, right, -top, top, near, far)

        # 视图
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        # 平移（右键拖动的画面偏移）
        pdx, pdy = self.pan_world(h)
        GL.glTranslatef(pdx, pdy, 0.0)
        GL.glTranslatef(0.0, 0.0, -self.dist)
        GL.glRotatef(self.pitch, 1.0, 0.0, 0.0)     # 俯仰
        GL.glRotatef(self.yaw, 0.0, 1.0, 0.0)       # 水平
        # .mod 是 Z-up，OpenGL 是 Y-up：绕 X 转 -90° 把 Z 轴抬到 Y 轴
        GL.glRotatef(-90.0, 1.0, 0.0, 0.0)
        GL.glTranslatef(-self.center[0], -self.center[1], -self.center[2])

        # ---- 选中描边（照搬 Blender workbench 的 ID 缓冲 + 邻域边界检测思路）
        #   1) 选中件完整投影写进模板 bit0（不测深度，小件遮挡不切描边）
        #   2) 8 个 ±o 像素偏移视口再画 8 遍写 bit1 —— 等效 Blender 对
        #      ID 缓冲做 4 邻域采样得到的"膨胀掩码"
        #   3) 全屏矩形只在 bit1 有、bit0 没有的像素上色 = 恒宽描边环，
        #      纯屏幕空间操作，任何角度/薄板/零厚度面都不会断
        act = None
        if (self._hl_src is not None and self.show_meshes
                and self.hl_name not in self.hidden
                and (self.solo is None or self.solo == self.hl_name)):
            act = [self._hl_src]
        oth = [gm for gm in getattr(self, "_hl_other", [])
               if self.show_meshes and gm.name not in self.hidden
               and (self.solo is None or self.solo == gm.name)]
        self._outline_act = act
        self._outline_oth = oth
        if self._stencil_ok:
            # 副选用 bit2/bit3，活动用 bit0/bit1，两套掩码互不覆盖
            self._outline_stencil(oth, w, h, 4, 8)
            self._outline_stencil(act, w, h, 1, 2)
        elif act:
            # 无模板缓冲的兜底：老的外扩外壳方案（只测深度）
            GL.glEnable(GL.GL_STENCIL_TEST)
            GL.glStencilMask(0xFF)
            GL.glStencilFunc(GL.GL_ALWAYS, 1, 0xFF)
            GL.glStencilOp(GL.GL_KEEP, GL.GL_KEEP, GL.GL_REPLACE)
            GL.glColorMask(0, 0, 0, 0)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glBindVertexArray(self._hl_src.vao)
            GL.glDrawElements(GL.GL_TRIANGLES, self._hl_src.n_index,
                              GL.GL_UNSIGNED_INT, None)
            GL.glBindVertexArray(0)
            GL.glColorMask(1, 1, 1, 1)
            GL.glDisable(GL.GL_STENCIL_TEST)
            GL.glEnable(GL.GL_DEPTH_TEST)

        self._draw_grid()                  # 基面网格（默认关，View 菜单开）

        ntri = 0
        for gm in self.gl_meshes:
            if not self.show_meshes:
                break                      # 网格被筛掉时整个模型不画
            if self.solo is not None and gm.name != self.solo:
                continue                   # Alt+眼睛 独显：其余部件不画
            if gm.name in self.hidden:
                continue                   # 眼睛图标关掉的部件
            use_tex = gm.has_uv and self._tex_id
            if use_tex:
                # 闪电战的贴图本身已经烘了光影，再叠光照只会发灰/过曝
                GL.glDisable(GL.GL_LIGHTING)
                GL.glEnable(GL.GL_TEXTURE_2D)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id)
                GL.glColor3f(1.0, 1.0, 1.0)
            else:
                # 白模才需要实时光照，否则就是一个纯色块
                GL.glDisable(GL.GL_TEXTURE_2D)
                GL.glEnable(GL.GL_LIGHTING)
                GL.glColor3f(*gm.color)

            GL.glBindVertexArray(gm.vao)
            GL.glDrawElements(GL.GL_TRIANGLES, gm.n_index, GL.GL_UNSIGNED_INT, None)
            ntri += gm.n_index // 3

        GL.glBindVertexArray(0)
        GL.glEnable(GL.GL_LIGHTING)

        # ---- 描边上色：全屏矩形，模板里「膨胀有、原投影无」的像素上色
        if self._stencil_ok:
            if self._outline_oth:
                self._outline_fill(w, h, (0.30, 0.62, 1.00, 0.90), 8, 0x0C)   # 副选：淡蓝
            if self._outline_act:
                self._outline_fill(w, h, (1.00, 0.55, 0.10, 1.00), 2, 0x03)   # 活动：橙
        elif self._outline_act:
            # 无模板缓冲的兜底：老的外扩外壳方案
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glDisable(GL.GL_LIGHTING)
            GL.glColor3f(1.0, 0.55, 0.10)
            GL.glBindVertexArray(self._hl_gl.vao)
            GL.glDrawElements(GL.GL_TRIANGLES, self._hl_gl.n_index,
                              GL.GL_UNSIGNED_INT, None)
            GL.glBindVertexArray(0)
            GL.glEnable(GL.GL_LIGHTING)

        self._ensure_sel_gl()
        if getattr(self, "_sel_gl", None) is not None:
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glDisable(GL.GL_LIGHTING)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glPolygonOffset(-2.0, -2.0)
            # 选中面用洋红，和描边（活动=橙、其他=淡蓝）区分开
            GL.glColor4f(0.85, 0.25, 0.95, 0.55)
            GL.glBindVertexArray(self._sel_gl.vao)
            GL.glDrawElements(GL.GL_TRIANGLES, self._sel_gl.n_index,
                              GL.GL_UNSIGNED_INT, None)
            GL.glBindVertexArray(0)
            GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glDisable(GL.GL_BLEND)
            GL.glEnable(GL.GL_LIGHTING)

        self._draw_aabbs()
        self._draw_locators()
        self._gz_draw(w, h)
        self._draw_gizmo(w, h)
        self._tb_draw(w, h)
        self._mode_draw(w, h)
        self.last_tri_count = ntri
        err = GL.glGetError()
        gdi32.SwapBuffers(self.hdc)
        if DEBUG_FRAME:
            dlog("redraw: {}x{}  mesh={}  三角形={}  glError={}".format(
                w, h, len(self.gl_meshes), ntri, err))

    # ---------------------------------------------------------------- 交互
    # ---------------------------------------------------------------- 右下角坐标球
    def _nav_geom(self):
        """坐标球在窗口里的位置 (左, 上, 边长)，窗口坐标 y 向下。"""
        S = getattr(self, "S", 1.0)
        size = int(92 * S)
        pad = int(10 * S)
        w, h = self.winfo_width(), self.winfo_height()
        if w < size + 2 * pad or h < size + 2 * pad:
            return None
        return (w - size - pad, h - pad - size, size)

    def _view_rot3_gl(self):
        """和 _draw_gizmo 里那串 glRotatef 完全一致的旋转（世界 -> 屏幕）。"""
        cy, sy = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        RX = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], float)
        RY = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], float)
        RXm90 = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)
        return RX @ RY @ RXm90

    def _nav_hit(self, px, py):
        """点坐标球：返回 'x'/'y'/'z'（对应正交视图）或 'home'（回默认透视）。"""
        g = self._nav_geom()
        if g is None:
            return None
        x0, y0, size = g
        if not (x0 <= px <= x0 + size and y0 <= py <= y0 + size):
            return None
        u = (px - x0) / float(size) * 2.9 - 1.45
        v = 1.45 - (py - y0) / float(size) * 2.9
        balls = getattr(self, "_nav_balls", None)
        if not balls:
            R = self._view_rot3_gl()
            balls = [(nm, float((R @ np.asarray(a, float))[0]),
                      float((R @ np.asarray(a, float))[1]))
                     for nm, a in (("x", (1.0, 0.0, 0.0)), ("y", (0.0, 1.0, 0.0)),
                                   ("z", (0.0, 0.0, 1.0)))]
        best = None
        for name, ex, ey in balls:
            # 轴正对/背对视线时球会挤在中心，这时不参与命中（否则点中心回不到默认视角）
            if math.hypot(ex, ey) < 0.35:
                continue
            dist = math.hypot(u - ex, v - ey)
            if dist <= 0.45 and (best is None or dist < best[0]):
                best = (dist, name)
        if best is not None:
            return best[1]
        if math.hypot(u, v) <= 0.30:
            return "home"
        return None

    def set_view_axis(self, axis):
        """切到对应轴的正交视图（和 Blender 小键盘 1/3/7 一个意思）。"""
        table = {"y": (0.0, 0.0, "前视图（沿 +Y）"),
                 "x": (-90.0, 0.0, "右视图（沿 -X）"),
                 "z": (0.0, 90.0, "顶视图（沿 -Z）")}
        yaw, pitch, label = table.get(axis, table["y"])
        self.yaw, self.pitch = yaw, pitch
        # 只摆正朝向，不擅自改投影方式（和 Blender 一致：透视就还是透视）
        self._last_sig = None
        self.redraw(force=True)
        if callable(getattr(self, "on_view_hint", None)):
            try:
                self.on_view_hint("{}：{}".format(
                    "正交" if getattr(self, "ortho", False) else "透视", label))
            except Exception:  # noqa: BLE001
                pass

    def _on_press(self, e):
        # 点视口就把键盘焦点拿回来（否则焦点留在搜索框里，快捷键全被当成打字）
        try:
            self.focus_set()
        except Exception:  # noqa: BLE001
            pass
        self._press_xy = (e.x, e.y)
        self._drag_moved = False
        self._tb_down = False
        # 右下角坐标球：点轴端球切正交视图，点中心回默认透视
        if getattr(e, "num", 1) == 1:
            nav = self._nav_hit(e.x, e.y)
            if nav is not None:
                self._tb_down = True          # 吃掉这次点击，别去拾取
                self._drag = None
                self.dragging = False
                if nav == "home":
                    self.ortho = False
                    self.reset_view()
                    if callable(getattr(self, "on_ortho_changed", None)):
                        try:
                            self.on_ortho_changed(False)
                        except Exception:  # noqa: BLE001
                            pass
                    if callable(getattr(self, "on_view_hint", None)):
                        try:
                            self.on_view_hint("透视视图：默认视角")
                        except Exception:  # noqa: BLE001
                            pass
                else:
                    self.set_view_axis(nav)
                return
        # 左上角工具条：点中就走工具切换，不转相机、不拾取
        if getattr(e, "num", 1) == 1:
            ti = self.tb_hit(e.x, e.y)
            if ti is not None:
                self._tb_down = True
                self._drag = None
                self.dragging = False
                if callable(self.on_tool_click):
                    try:
                        self.on_tool_click(self.TB_TOOLS[ti])
                    except Exception:  # noqa: BLE001
                        pass
                return
        # 左键：模式按钮（3D / UV）
        if getattr(e, "num", 1) == 1:
            mi = self.mode_hit(e.x, e.y)
            if mi is not None:
                mode = ("3d", "uv")[mi]
                self.set_view_mode(mode)
                cb = getattr(self, "on_view_mode_change", None)
                if callable(cb):
                    try:
                        cb(mode)
                    except Exception:  # noqa: BLE001
                        pass
                self._drag = None
                self.dragging = False
                return

        # UV 预览是只读的：左键既不拾取对象也不做 UV 编辑（中键仍可平移）
        if getattr(e, "num", 1) == 1 and getattr(self, "view_mode", "3d") == "uv":
            self._drag = None
            self.dragging = False
            return

        # 左键（UV 视图）：拾取 UV 点/面，拖动即移动
        if (getattr(e, "num", 1) == 1 and getattr(self, "view_mode", "3d") == "uv"
                and getattr(self, "uv_edit_enabled", False)):
            hit_gz = self.uv_gz_hit(e.x, e.y)
            if hit_gz is not None and self.uv_sel_pts:
                self._uv_gz = {"kind": hit_gz, "x": e.x, "y": e.y,
                               "g": self._uv_gz_geom()}
                cb = getattr(self, "on_uv_gz_start", None)
                if callable(cb):
                    try:
                        cb(hit_gz)
                    except Exception:  # noqa: BLE001
                        pass
                self._drag = None
                self.dragging = False
                return
            shift = bool(int(getattr(e, "state", 0)) & 0x0001)
            hit = self.uv_pick(e.x, e.y)
            self._uv_down = {"x": e.x, "y": e.y, "hit": hit}
            cb = getattr(self, "on_uv_pick", None)
            if callable(cb):
                try:
                    if hit is not None:
                        cb(hit[0], hit[1], hit[2], hit[3], e.x, e.y, shift)
                    else:
                        cb("none", "", -1, -1, e.x, e.y, shift)
                except Exception:  # noqa: BLE001
                    pass
            self._drag = None
            self.dragging = False
            return

        # 左键：选中的 AABB 面手柄优先（单面拖动）
        if getattr(e, "num", 1) == 1 and self.sel_aabb is not None:
            hi = self._aabb_face_hit(e.x, e.y)
            if hi is not None:
                axis, sign, pt = self._aabb_faces()[hi]
                self._aabb_face_drag = {"i": hi, "axis": axis, "sign": sign,
                                        "x0": e.x, "y0": e.y,
                                        "pt": np.asarray(pt, float)}
                if callable(self.on_aabb_face_start):
                    try:
                        self.on_aabb_face_start()
                    except Exception:  # noqa: BLE001
                        pass
                self._drag = None
                self.dragging = False
                return
        # 左键 + 变换工具：先判 gizmo 手柄，命中就别转相机了
        if getattr(e, "num", 1) == 1 and self.tool != "select" and self.pivot is not None:
            if self._gz_begin(e):
                self._drag = None
                self.dragging = False
                return
        self._drag = (e.x, e.y, "pan" if (e.state & 0x400) else "rot")
        self.dragging = True

    def _on_pan_press(self, e):
        """中键按下一律平移（不看 state 位）。"""
        self._drag = (e.x, e.y, "pan")
        self._press_xy = (e.x, e.y)
        self._drag_moved = False
        self.dragging = True

    def _on_drag(self, e):
        # UV 视图：中键拖 = 平移 UV（像素偏移），不影响 3D 相机
        if (getattr(self, "view_mode", "3d") == "uv" and self._drag
                and len(self._drag) == 3 and self._drag[2] == "pan"):
            panx, pany = getattr(self, "_uv_pan", (0.0, 0.0))
            # 纵向要反号：屏幕 y 向下，UV 的 v 向上（不然上下跟着鼠标反向走）
            self._uv_pan = (panx + (e.x - self._drag[0]),
                            pany - (e.y - self._drag[1]))
            self._drag = (e.x, e.y, "pan")
            self._dirty = True
            return
        if self._uv_gz is not None and getattr(self, "view_mode", "3d") == "uv":
            d = self._uv_gz
            dx, dy = e.x - d["x"], e.y - d["y"]
            cb = getattr(self, "on_uv_gz", None)
            if callable(cb):
                try:
                    cb(d["kind"], dx, dy, e.x, e.y, d["g"])
                except Exception:  # noqa: BLE001
                    pass
            return
        if (self._uv_down is not None and getattr(self, "view_mode", "3d") == "uv"
                and getattr(self, "uv_edit_enabled", False)):
            d = e.x - self._uv_down["x"], e.y - self._uv_down["y"]
            self._uv_down["x"], self._uv_down["y"] = e.x, e.y
            cb = getattr(self, "on_uv_drag", None)
            if callable(cb) and (abs(d[0]) or abs(d[1])):
                try:
                    cb(d[0], d[1], e.x, e.y)
                except Exception:  # noqa: BLE001
                    pass
            return
        if self._aabb_face_drag is not None:
            d = self._aabb_face_drag
            axis, sign = d["axis"], d["sign"]
            P = d["pt"]
            ex = np.zeros(3)
            ex[axis] = 1.0
            sx, sy, ok = self._project_points([P, P + ex])
            if ok.all():
                vx, vy = sx[1] - sx[0], sy[1] - sy[0]
                den = vx * vx + vy * vy
                if den > 1e-9:
                    dx, dy = e.x - d["x0"], e.y - d["y0"]
                    delta = (dx * vx + dy * vy) / den
                    val = float(P[axis]) + delta
                    if callable(self.on_aabb_face):
                        try:
                            self.on_aabb_face(axis, sign, val)
                        except Exception:  # noqa: BLE001
                            pass
            return
        if self._gz_drag is not None:
            self._gz_drag_update(e)
            return
        if not getattr(self, "_drag", None):
            return
        mode = self._drag[2]
        dx, dy = e.x - self._drag[0], e.y - self._drag[1]
        self._drag = (e.x, e.y, mode)
        if abs(e.x - self._press_xy[0]) + abs(e.y - self._press_xy[1]) > 3:
            self._drag_moved = True
        if mode == "rot":
            self.yaw += dx * 0.6
            self.pitch = max(-89.0, min(89.0, self.pitch + dy * 0.6))
        else:
            self.pan[0] += dx
            self.pan[1] += dy
        self.redraw()

    def _on_release(self, e):
        if self._uv_gz is not None and getattr(self, "view_mode", "3d") == "uv":
            self._uv_gz = None
            cb = getattr(self, "on_uv_gz_end", None)
            if callable(cb):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    pass
            return
        if (self._uv_down is not None and getattr(self, "view_mode", "3d") == "uv"
                and getattr(self, "uv_edit_enabled", False)):
            self._uv_down = None
            cb = getattr(self, "on_uv_release", None)
            if callable(cb):
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    pass
            return
        if self._aabb_face_drag is not None:
            self._aabb_face_drag = None
            if callable(self.on_aabb_face_end):
                try:
                    self.on_aabb_face_end()
                except Exception:  # noqa: BLE001
                    pass
            self.redraw(force=True)
            return
        if self._gz_drag is not None:
            self._gz_drag = None
            if callable(self.on_gizmo_end):
                try:
                    self.on_gizmo_end()
                except Exception:  # noqa: BLE001
                    pass
            self._last_sig = None
            self.redraw(force=True)
            return
        was_click = not self._drag_moved
        tb_down = getattr(self, "_tb_down", False)
        self._tb_down = False
        self._drag = None
        self.dragging = False
        self._last_sig = None
        if was_click and not tb_down:
            num = getattr(e, "num", 1)
            if num == 1 and getattr(self, "view_mode", "3d") != "uv":
                self.pick(e.x, e.y, getattr(e, "state", 0))
            elif num == 3 and callable(self.on_context_menu):
                try:
                    self.on_context_menu(int(e.x_root), int(e.y_root))
                except Exception:  # noqa: BLE001
                    pass
        self.redraw(force=True)

    def _set_cursor(self, name):
        """只记状态 + 直接 SetCursor；绝不再调 configure(cursor=...)（会黑屏）。"""
        if getattr(self, "_cursor", None) == name:
            return
        self._cursor = name
        try:
            # 鼠标不在本窗口上就别动全局光标，等它进来时 WM_SETCURSOR 会设置
            if not self.hwnd:
                return
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            if user32.WindowFromPoint(pt) == self.hwnd:
                self._apply_cursor()
        except Exception:  # noqa: BLE001
            pass

    def _on_motion(self, e):
        """悬停高亮 + 光标提示。"""
        if getattr(self, "view_mode", "3d") == "uv":
            # UV 预览只读：不做对象悬停拾取，也不改光标提示
            self._set_cursor("arrow")
            return
        if getattr(self, "view_mode", "3d") == "uv" and getattr(self, "uv_modal", False):
            cb = getattr(self, "on_uv_motion", None)
            if callable(cb):
                try:
                    cb(e.x, e.y)
                except Exception:  # noqa: BLE001
                    pass
            return
        if self._gz_drag is not None or self._aabb_face_drag is not None:
            return
        # 选中框的面手柄：悬停变黄 + 手型光标
        fh = self._aabb_face_hit(e.x, e.y) if self.sel_aabb is not None else None
        if fh != self._aabb_face_hover:
            self._aabb_face_hover = fh
            self.redraw(force=True)
        if fh is not None:
            self._set_cursor("hand2")
            return
        ti = self.tb_hit(e.x, e.y)
        if ti != getattr(self, "tb_hover", None):
            self.tb_hover = ti
            self.redraw(force=True)
        mi = self.mode_hit(e.x, e.y)
        if mi != getattr(self, "_mode_hover", None):
            self._mode_hover = mi
            self._dirty = True
        if ti is not None or self.tool == "select" or self.pivot is None:
            if self._gz_hover is not None:
                self._gz_hover = None
                self.redraw(force=True)
            self._set_cursor("hand2" if ti is not None else "")
            return
        hit = self._gz_hit(e.x, e.y)
        if hit != self._gz_hover:
            self._gz_hover = hit
            self.redraw(force=True)
        self._set_cursor("hand2" if hit else "")

    def pan_world(self, h):
        """把 pan（像素）换算成视空间的世界位移：1px 屏幕 = 1px 模型。"""
        k = 2.0 * self.dist * math.tan(math.radians(20.0)) / max(1, h)
        return self.pan[0] * k, -self.pan[1] * k

    # ---------------------------------------------------------------- 拾取
    def _view_matrix(self):
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        cz, sz = math.cos(math.radians(self.yaw)), math.sin(math.radians(self.yaw))
        cp, sp = math.cos(math.radians(self.pitch)), math.sin(math.radians(self.pitch))
        def T(x, y, z):
            m = np.eye(4)
            m[:3, 3] = (x, y, z)
            return m
        def RX(a):
            c, s_ = math.cos(a), math.sin(a)
            return np.array([[1, 0, 0, 0], [0, c, -s_, 0], [0, s_, c, 0], [0, 0, 0, 1]], float)
        def RY(a):
            c, s_ = math.cos(a), math.sin(a)
            return np.array([[c, 0, s_, 0], [0, 1, 0, 0], [-s_, 0, c, 0], [0, 0, 0, 1]], float)
        pdx, pdy = self.pan_world(h)
        M = (T(pdx, pdy, 0.0)
             @ T(0.0, 0.0, -self.dist)
             @ RX(math.radians(self.pitch))
             @ RY(math.radians(self.yaw))
             @ RX(math.radians(-90.0))
             @ T(-self.center[0], -self.center[1], -self.center[2]))
        aspect = w / float(h)
        if getattr(self, "ortho", False):
            half_h = self.dist * math.tan(math.radians(40.0) * 0.5)
            half_w = half_h * aspect
            rng = self.dist * 12.0
            P = np.array([
                [1.0 / half_w, 0, 0, 0],
                [0, 1.0 / half_h, 0, 0],
                [0, 0, -1.0 / rng, 0],
                [0, 0, 0, 1],
            ], float)
            return P @ M
        near = max(0.01, self.dist * 0.02)
        far = self.dist * 12.0
        top = near * math.tan(math.radians(40.0) * 0.5)
        right = top * aspect
        P = np.array([
            [near / right, 0, 0, 0],
            [0, near / top, 0, 0],
            [0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)],
            [0, 0, -1, 0],
        ], float)
        return P @ M

    def screen_ray(self, px, py):
        """屏幕点 -> 世界空间射线 (origin, dir)。"""
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return None
        try:
            inv = np.linalg.inv(self._view_matrix())
        except Exception:  # noqa: BLE001
            return None
        nx = 2.0 * px / w - 1.0
        ny = 1.0 - 2.0 * py / h
        pts = []
        for z in (-1.0, 1.0):
            v = inv @ np.array([nx, ny, z, 1.0])
            if abs(v[3]) < 1e-9:
                return None
            pts.append(v[:3] / v[3])
        o, f = pts
        d = f - o
        n = np.linalg.norm(d)
        if n < 1e-9:
            return None
        return o, d / n

    @staticmethod
    def _ray_mesh(o, d, v, f):
        v0 = v[f[:, 0]]
        e1 = v[f[:, 1]] - v0
        e2 = v[f[:, 2]] - v0
        pv = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, pv)
        ok = np.abs(det) > 1e-10
        if not ok.any():
            return None
        inv = np.zeros_like(det)
        inv[ok] = 1.0 / det[ok]
        tv = o - v0
        u = np.einsum("ij,ij->i", tv, pv) * inv
        qv = np.cross(tv, e1)
        w = qv.dot(d) * inv      # d 是一维的，用 dot 才能对上形状
        t = np.einsum("ij,ij->i", e2, qv) * inv
        hit = ok & (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6) & (t > 1e-6)
        if not hit.any():
            return None
        return float(t[hit].min())

    @staticmethod
    def _ray_mesh_tri(o, d, v, f):
        """返回 (最近命中距离, 三角面索引)，没打中返回 None。"""
        v0 = v[f[:, 0]]
        e1 = v[f[:, 1]] - v0
        e2 = v[f[:, 2]] - v0
        pv = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, pv)
        ok = np.abs(det) > 1e-10
        if not ok.any():
            return None
        inv = np.zeros_like(det)
        inv[ok] = 1.0 / det[ok]
        tv = o - v0
        u = np.einsum("ij,ij->i", tv, pv) * inv
        qv = np.cross(tv, e1)
        w = qv.dot(d) * inv
        t = np.einsum("ij,ij->i", e2, qv) * inv
        hit = ok & (u >= -1e-6) & (w >= -1e-6) & (u + w <= 1 + 1e-6) & (t > 1e-6)
        if not hit.any():
            return None
        idx = int(np.argmin(np.where(hit, t, 1e18)))
        return float(t[idx]), idx

    def pick_face(self, px, py):
        """屏幕点 -> (部件名, 三角面索引)；没打中返回 None。"""
        if getattr(self, "view_mode", "3d") == "uv" or not self.meshes:
            return None
        ray = self.screen_ray(px, py)
        if ray is None:
            return None
        o, d = ray
        best_t, best = 1e18, None
        for name, v, f, color, nrm, uv in self.meshes:
            if self.solo is not None and name != self.solo:
                continue
            if name in self.hidden:
                continue
            r = self._ray_mesh_tri(o, d, np.asarray(v, float), np.asarray(f, np.int64))
            if r is None:
                continue
            if r[0] < best_t:
                best_t, best = r[0], (name, r[1])
        return best

    def set_face_sel(self, name, verts=None, faces=None):
        """高亮选中的三角面（verts 世界坐标，faces 索引）。传 None 清空。"""
        if name is None or verts is None or faces is None or len(faces) == 0:
            self._face_sel = None
        else:
            self._face_sel = (name, np.asarray(verts, float), np.asarray(faces, np.int64))
        self._sel_stale = True
        if self.ready:
            self._kick()

    def _ensure_sel_gl(self):
        if not getattr(self, "_sel_stale", False):
            return
        self._sel_stale = False
        self._delete_glmesh(getattr(self, "_sel_gl", None))
        self._sel_gl = None
        fs = getattr(self, "_face_sel", None)
        if not fs or not self.ready:
            return
        name, v, f = fs
        try:
            self._sel_gl = self._build_glmesh("__facesel", v, f, (255, 140, 30), None)
        except Exception:  # noqa: BLE001
            self._sel_gl = None

    def _project_points(self, pts):
        """世界坐标点 -> (屏幕 x, 屏幕 y, 是否在相机前方)。"""
        w, h = self.winfo_width(), self.winfo_height()
        m = self._view_matrix()
        pts = np.asarray(pts, dtype=float).reshape(-1, 3)
        q = np.hstack([pts, np.ones((len(pts), 1))]) @ m.T
        cw = q[:, 3]
        ok = cw > 1e-6
        safe = np.where(ok, cw, 1.0)
        ndc = q[:, :3] / safe[:, None]
        sx = (ndc[:, 0] + 1.0) * 0.5 * w
        sy = (1.0 - ndc[:, 1]) * 0.5 * h
        return sx, sy, ok

    @staticmethod
    def _seg_dist(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 < 1e-9:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _pick_overlay(self, px, py, tol=7.0):
        """在定位器箭头 / AABB 棱上找离鼠标最近的一条（屏幕像素距离）。"""
        best = None            # (dist, kind, name)
        L = getattr(self, "_arrow_len", 0.1)

        # --- 定位器箭头：杆子 + 箭头尖端 ---
        for item in self.locators:
            name = item[0]
            if not self.show_locators or name in self.hidden:
                continue
            if self.solo is not None and self.solo != name:
                continue
            pos = np.asarray(item[1], float)
            d = np.asarray(item[2], float)[:, 2]
            n = np.linalg.norm(d)
            if n < 1e-9:
                continue
            tip = pos + d / n * L
            sx, sy, ok = self._project_points([pos, tip])
            if not ok.all():
                continue
            dist = self._seg_dist(px, py, sx[0], sy[0], sx[1], sy[1])
            if dist <= tol and (best is None or dist < best[0]):
                best = (dist, "locator", name)

        # --- AABB 线框：12 条棱 ---
        if self.solo is None:
            EDGES = ((0, 1), (1, 3), (3, 2), (2, 0),
                     (4, 5), (5, 7), (7, 6), (6, 4),
                     (0, 4), (1, 5), (2, 6), (3, 7))
            for item in self.aabbs:
                name, mn, mx = item[0], np.asarray(item[1], float), np.asarray(item[2], float)
                corners = np.array([[mx[0] if i & 1 else mn[0],
                                     mx[1] if i & 2 else mn[1],
                                     mx[2] if i & 4 else mn[2]] for i in range(8)])
                sx, sy, ok = self._project_points(corners)
                for a, b in EDGES:
                    if not (ok[a] and ok[b]):
                        continue
                    dist = self._seg_dist(px, py, sx[a], sy[a], sx[b], sy[b])
                    if dist <= tol and (best is None or dist < best[0]):
                        best = (dist, "aabb", name)
        return best

    def pick(self, px, py, state=0):
        """视口里点一下：优先命中叠在最上层的定位器 / AABB，否则做网格射线拾取。"""
        if getattr(self, "view_mode", "3d") == "uv":
            return None, None      # UV 预览只读：任何拾取入口都不生效
        
        if getattr(self, "tool", "") == "link":
            got = self.pick_face(px, py)
            if got is not None and callable(getattr(self, "on_face_pick", None)):
                try:
                    self.on_face_pick(got[0], got[1], state)
                except Exception:  # noqa: BLE001
                    pass
            return ("face", got[0]) if got else None

        hit = self._pick_overlay(px, py)
        if hit is not None:
            kind, name = hit[1], hit[2]
            if callable(self.on_pick):
                self.on_pick(kind, name, state)
            return kind, name

        if not self.meshes or not self.show_meshes:
            return None
        ray = self.screen_ray(px, py)
        if ray is None:
            return None
        o, d = ray
        best_t, best = 1e18, None
        for name, v, f, color, nrm, uv in self.meshes:
            if self.solo is not None and name != self.solo:
                continue
            if name in self.hidden:
                continue
            t = self._ray_mesh(o, d, np.asarray(v, float), np.asarray(f, np.int64))
            if t is not None and t < best_t:
                best_t, best = t, name
        if best is not None and callable(self.on_pick):
            self.on_pick("mesh", best, state)
        return ("mesh", best) if best is not None else None

    def _on_wheel(self, e):
        if getattr(self, "view_mode", "3d") == "uv":
            d = 1 if getattr(e, "delta", 0) > 0 else -1
            z = float(getattr(self, "_uv_zoom", 1.0)) * (1.15 if d > 0 else 1 / 1.15)
            self._uv_zoom = max(0.05, min(60.0, z))
            self._dirty = True
            return
        self.dist *= 0.88 if e.delta > 0 else 1.14
        self.dist = max(self.radius * 0.6, min(self.radius * 20, self.dist))
        self.redraw()

    def _on_resize(self, e):
        self._last_sig = None
        self._need_double = True
        # 接管了窗口过程时，随后的 WM_PAINT 会带着正确的客户区尺寸重画，
        # 这里再画一次等于每帧画两遍，拖动会明显发顿
        if self._proc is None:
            self.redraw(force=True)

    # ---------------------------------------------------------------- 兼容旧接口
    @property
    def quality(self):
        return (1, 1.0, 1)

    @quality.setter
    def quality(self, val):
        pass

    def reset_view(self):
        self.yaw, self.pitch = 35.0, -22.0
        self.pan = [0.0, 0.0]
        self.dist = self.radius / math.sin(math.radians(20.0)) * 0.92
        self._last_sig = None
        self.redraw(force=True)
