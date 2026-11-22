# -*- coding: utf-8 -*-
"""DDS → RGBA 解码（专供特效贴图，保留 alpha）。

支持：DXT1（含 1bit 透明）、DXT3、DXT5、未压缩 32/24 位。
输出 numpy (h, w, 4) uint8，行序与文件一致（首行在上），不翻转。
"""
import struct

import numpy as np


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def _rgb565(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return np.array([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], np.float32)


def _color_block(block, has_alpha1bit, out, ox, oy, w, h):
    c0 = _u16(block, 0)
    c1 = _u16(block, 2)
    idx = _u32(block, 4)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    if c0 > c1 or not has_alpha1bit:
        p2 = (2.0 * p0 + p1) / 3.0
        p3 = (p0 + 2.0 * p1) / 3.0
        a3 = 255
    else:
        p2 = (p0 + p1) / 2.0
        p3 = np.zeros(3, np.float32)
        a3 = 0
    pal = np.stack([p0, p1, p2, p3])
    for yy in range(4):
        for xx in range(4):
            if ox + xx >= w or oy + yy >= h:
                continue
            k = (idx >> (2 * (yy * 4 + xx))) & 3
            out[oy + yy, ox + xx, :3] = pal[k]
            out[oy + yy, ox + xx, 3] = 255 if k != 3 else a3


def _alpha_dxt3(block, out, ox, oy, w, h):
    bits = struct.unpack("<Q", block[0:8])[0]
    for yy in range(4):
        for xx in range(4):
            if ox + xx >= w or oy + yy >= h:
                continue
            a = (bits >> (4 * (yy * 4 + xx))) & 0xF
            out[oy + yy, ox + xx, 3] = a * 17


def _alpha_dxt5(block, out, ox, oy, w, h):
    a0, a1 = block[0], block[1]
    bits = block[2] | (block[3] << 8) | (block[4] << 16) | (block[5] << 24) | \
        (block[6] << 32) | (block[7] << 40)
    if a0 > a1:
        alphas = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        alphas = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    for yy in range(4):
        for xx in range(4):
            if ox + xx >= w or oy + yy >= h:
                continue
            k = (bits >> (3 * (yy * 4 + xx))) & 7
            out[oy + yy, ox + xx, 3] = alphas[k]


def decode(data):
    """返回 (w, h, ndarray(h,w,4) uint8) 或 None。"""
    if not data or len(data) < 128 or data[:4] != b"DDS ":
        return None
    h = struct.unpack("<I", data[12:16])[0]
    w = struct.unpack("<I", data[16:20])[0]
    fourcc = data[84:88]
    bit_count = struct.unpack("<I", data[88:92])[0]
    if w <= 0 or h <= 0 or w > 8192 or h > 8192:
        return None
    body = data[128:]
    out = np.zeros((h, w, 4), np.uint8)

    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        block = 8 if fourcc == b"DXT1" else 16
        need = ((w + 3) // 4) * ((h + 3) // 4) * block
        if len(body) < need:
            return None
        for by in range((h + 3) // 4):
            for bx in range((w + 3) // 4):
                o = (by * ((w + 3) // 4) + bx) * block
                blk = body[o:o + block]
                ox, oy = bx * 4, by * 4
                if fourcc == b"DXT1":
                    _color_block(blk, True, out, ox, oy, w, h)
                else:
                    if fourcc == b"DXT3":
                        _alpha_dxt3(blk, out, ox, oy, w, h)
                        _color_block(blk[8:16], False, out, ox, oy, w, h)
                    else:
                        _alpha_dxt5(blk, out, ox, oy, w, h)
                        _color_block(blk[8:16], False, out, ox, oy, w, h)
        return w, h, out

    # 未压缩
    if bit_count == 32:
        need = w * h * 4
        if len(body) < need:
            return None
        arr = np.frombuffer(body[:need], np.uint8).reshape(h, w, 4)
        out[:, :, 0] = arr[:, :, 2]   # B
        out[:, :, 1] = arr[:, :, 1]   # G
        out[:, :, 2] = arr[:, :, 0]   # R
        out[:, :, 3] = arr[:, :, 3]   # A
        return w, h, out
    if bit_count == 24:
        need = w * h * 3
        if len(body) < need:
            return None
        arr = np.frombuffer(body[:need], np.uint8).reshape(h, w, 3)
        out[:, :, 0] = arr[:, :, 2]
        out[:, :, 1] = arr[:, :, 1]
        out[:, :, 2] = arr[:, :, 0]
        out[:, :, 3] = 255
        return w, h, out
    return None


def placeholder(size=32):
    """程序生成的白色径向渐变占位贴图（贴图缺失时用）。"""
    y, x = np.mgrid[0:size, 0:size]
    cx = cy = (size - 1) / 2.0
    r = np.hypot(x - cx, y - cy) / (size / 2.0)
    a = np.clip(1.0 - r, 0.0, 1.0) ** 1.5
    out = np.zeros((size, size, 4), np.uint8)
    out[:, :, :3] = 255
    out[:, :, 3] = (a * 255).astype(np.uint8)
    return size, size, out


def soften_cells(img, dx, dy, margin=0.12):
    """给图集每格加边缘羽化，消除「画到格子边界就被切断」的硬边。

    img: (h, w, 4) uint8；返回新数组（不修改入参）。
    """
    if img is None or dx <= 0 or dy <= 0 or (dx == 1 and dy == 1):
        return img
    h, w = img.shape[0], img.shape[1]
    out = img.copy()
    cw = w / float(dx)
    chh = h / float(dy)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    fx_ = (xx % cw) / max(1.0, cw)
    fy_ = (yy % chh) / max(1.0, chh)
    d = np.minimum(np.minimum(fx_, 1.0 - fx_), np.minimum(fy_, 1.0 - fy_))
    m = np.clip(d / max(1e-6, margin), 0.0, 1.0)
    mask = (m * m * (3.0 - 2.0 * m))          # smoothstep
    a = out[:, :, 3].astype(np.float32) * mask
    out[:, :, 3] = np.clip(a, 0, 255).astype(np.uint8)
    return out
