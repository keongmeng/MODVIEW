# -*- coding: utf-8 -*-
"""特效定位自测：播放一个定位器上的特效，然后换几个视角各截一张图。

用法（在 modview-v169 目录下）：
    python fx_shot_test.py --model "D:\\unit\\1.mod" --locator 17
    python fx_shot_test.py --model "..." --locator 24 --effect shootgun5 --yaws 0,60

输出：默认在脚本目录生成 fx_shot_1.png / fx_shot_2.png ...
      每张图上画了红色十字 = 该定位器在屏幕上的投影位置，方便肉眼核对。
"""
import argparse
import os
import struct
import sys
import time
import zlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import modview as MV  # noqa: E402


def ppm_to_png(ppm, path, marks=()):
    idx = 2
    toks = []
    while len(toks) < 3:
        while ppm[idx:idx + 1].isspace():
            idx += 1
        st = idx
        while not ppm[idx:idx + 1].isspace():
            idx += 1
        toks.append(int(ppm[st:idx]))
    idx += 1
    w, h = toks[0], toks[1]
    arr = np.frombuffer(ppm[idx:idx + w * h * 3], np.uint8).reshape(h, w, 3).copy()
    for (mx, my, color) in marks:
        mx, my = int(round(mx)), int(round(my))
        for d in range(-16, 17):
            for (px, py) in ((mx + d, my), (mx, my + d)):
                if 0 <= px < w and 0 <= py < h:
                    arr[py, px] = color
    rgba = np.dstack([arr, np.full((h, w, 1), 255, np.uint8)])
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as fh:
        fh.write(bytes([137, 80, 78, 71, 13, 10, 26, 10])
                 + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                 + ch(b"IDAT", zlib.compress(raw, 6)) + ch(b"IEND", b""))
    return w, h


def pump(app, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.update()
        time.sleep(0.01)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help=".mod 路径")
    ap.add_argument("--game", default="", help="游戏根目录或 data 目录（含 *.pak）")
    ap.add_argument("--locator", type=int, default=17, help="要预览的定位器 id（0 基）")
    ap.add_argument("--effect", default="", help="强制指定特效名（默认取该定位器在 XML 里的配置）")
    ap.add_argument("--yaws", default="0,60", help="两个视角的 yaw，逗号分隔")
    ap.add_argument("--wait", type=float, default=1.2, help="播放后等待多少秒再截图")
    ap.add_argument("--loop", action="store_true", help="循环发射（便于观察）")
    ap.add_argument("--scale", type=int, default=1, help="输出缩放倍数")
    args = ap.parse_args()

    app = MV.ModViewer()
    if args.game:
        app.prefs["fx_data_roots"] = [args.game]
    app.geometry("1000x680+40+40")
    app.after(300, lambda: app.open_mod_path(args.model, ask=False))
    pump(app, 2.6)

    if app.scene is None:
        print("模型没打开成功")
        return 2

    if not app._xml_open:
        app.toggle_xml_panel()
    app.update()
    app.right_nb.select(app.tab_vfx)
    app.update()
    app._vfx_reset()
    app._vfx_rebuild_slots()
    print("槽位 %d 个" % len(app._vfx_slots))

    # 选中目标定位器
    app.sel_id = args.locator
    app.sel_ids = {args.locator}
    app.on_select()
    pump(app, 0.3)

    slot = None
    for s in app._vfx_slots:
        if s["part_id"] == args.locator and s["effect"]:
            slot = s
            break
    eff = args.effect or (slot["effect"] if slot else "")
    if not eff:
        print("定位器 %d 没有关联特效，用 --effect 指定一个" % args.locator)
        return 3
    print("播放 %s（定位器 %d）" % (eff, args.locator))

    if args.loop:
        app._vfx_loop.set(True)
    app._vfx_play_effect(eff, args.locator, key_hint="test")
    pump(app, args.wait)
    if getattr(app, "_vfx_rt", None) is None:
        print("特效库没加载：用 --game 指定游戏目录（含 *.pak）")
        return 4

    out_dir = os.path.dirname(os.path.abspath(__file__))
    for i, yaw in enumerate([float(x) for x in args.yaws.split(",") if x.strip()], 1):
        app.view.yaw = yaw
        app.view._last_sig = None
        app.view.redraw(force=True)
        app.update()
        app.view.redraw(force=True)
        app.update()

        anchor, _basis = app._vfx_anchor(args.locator)
        sx, sy, _z, ok = app.view._project_points_depth(np.asarray([anchor]))
        batches = app._vfx_rt.build_quads(app.view._project_points_depth,
                                          app.view.winfo_height()) if app._vfx_rt else []
        n = sum(len(b[1]) for b in batches) // 6
        cx = np.mean([b[1][:, 0].mean() for b in batches]) if batches else float("nan")
        cy = np.mean([b[1][:, 1].mean() for b in batches]) if batches else float("nan")

        ppm = app.view.snapshot_ppm()
        path = os.path.join(out_dir, "fx_shot_%d.png" % i)
        size = None
        if ppm:
            size = ppm_to_png(ppm, path, marks=[(sx[0], sy[0], (255, 0, 0))])
        for bi, bb in enumerate(batches):
            vv = bb[1].reshape(-1, 2)
            print("   batch%d %-34s quads=%-3d center=(%7.1f,%7.1f)" % (
                bi, bb[0][-34:], len(vv) // 6, vv[:, 0].mean(), vv[:, 1].mean()), flush=True)
        print("yaw=%-6.1f anchor=(%7.1f,%7.1f) quads=%d center=(%7.1f,%7.1f) -> %s %s" % (
            yaw, sx[0], sy[0], n, cx, cy, os.path.basename(path), size or ""))

    pump(app, 0.3)
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
