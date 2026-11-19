# -*- coding: utf-8 -*-
"""读 .mod → 打印部件 id 表 → 自动核对同目录 xml 引用。

用法：
  1. 把 .mod 文件拖到「查ID.bat」上
  2. 或命令行：python mod_id.py 路径\1.mod [更多.mod ...]
  3. 或直接双击 mod_id.py（会列出当前目录的 .mod 让你选）
"""

import os
import re
import sys
import glob

# ---------------------------------------------------------------- 找 mod_scene
BLENDER_ADDONS_CANDIDATES = []

# ① 打包成 exe 时，mod_scene 被塞在程序内部
if hasattr(sys, "_MEIPASS"):
    BLENDER_ADDONS_CANDIDATES.append(os.path.join(sys._MEIPASS, "mod"))

# ② 脚本旁边自带的 mod/ 目录（分发时可以连 mod_scene 一起拷）
BLENDER_ADDONS_CANDIDATES.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mod"))

# ③ 本机 Blender 插件目录
for _ver in ("5.2", "5.1", "5.0", "4.5", "4.2", "4.1", "4.0"):
    BLENDER_ADDONS_CANDIDATES.append(
        os.path.expandvars(r"%APPDATA%\Blender Foundation\Blender\{}\scripts\addons\mod".format(_ver)))

for _p in BLENDER_ADDONS_CANDIDATES:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)
        break

try:
    from mod_scene import ModScene
except ImportError:
    print("[错误] 找不到 mod_scene 模块。")
    print("请确认已经安装 Blender 的「Blitzkrieg MOD format」插件，")
    print("并且路径在以下位置之一：")
    for _p in BLENDER_ADDONS_CANDIDATES:
        print("   ", _p)
    input("\n按回车退出...")
    sys.exit(1)

AXES = "XYZ"
TYPES = {0: "停用", 1: "旋转", 2: "位移"}

# xml 字段 → 期望指向的对象名候选（按优先级）
FIELD_HINTS = {
    "ShootPoint": {
        "locator": True,
        "by_weapon": {
            "dt7": ["LMachineGun_Turret_COAX", "LMachineGun_Turret_Coax"],
            "7_62": ["LMachineGun_Turret_COAX", "LMachineGun_Turret_Coax"],
            "12mm": ["LMachineGun_Turret_COAX1", "LMachineGun_Turret_COAX_02", "LMachineGun_Turret"],
            "HMG": ["LMachineGun_Turret_COAX1", "LMachineGun_Turret_COAX_02", "LMachineGun_Turret"],
            "default": ["LMainGun"],
        },
    },
    "EntrancePoint": {"locator": True, "by_name": ["LPeople", "LPeople01", "LPeople1"]},
    "FatalitySmokePoint": {"locator": True,
                           "by_name": ["LFatalitySmoke01", "LFatalitySmoke", "LFatalitySmoke02"]},
    "TowPoint": {"locator": True, "by_name": ["LTowingPoint"]},
    "ShootDustPoint": {"locator": True, "by_name": ["LSmoke01"]},
    "ModelPart": {"locator": False, "by_name": ["Basis", "Turret", "MainBarrel"]},
}

# 字段的中英文对照（显示用）
FIELD_CN = {
    "TowPoint": "拖拽（牵引）点",
    "EntrancePoint": "人员登车点",
    "FatalitySmokePoint": "被击毁冒烟特效点",
    "ShootDustPoint": "开火扬起尘土特效点",
    "ShootPoint": "该部件的射击效果点",
    "ModelPart": "该射击点的连接部件",   # 兜底：认不出指向哪个部件时用它
    "ExhaustPoints": "车辆（废气）排气点",
    "DamagePoints": "车辆受损冒烟点",
}

# ModelPart 的中文说明按「实际指向的部件」区分（同一份 xml 里会出现多次）
MODEL_PART_CN = {
    "Basis": "车体连接部件",
    "Turret": "炮塔连接部件",
    "MainBarrel": "炮管连接部件",
    "GunCarriage": "炮架连接部件",
    "ArmouredMantlet": "炮盾连接部件",
    "Mantlet": "炮盾连接部件",
}

# 定位器部件名的中文说明（定稿 v2）
LOCATOR_CN = {
    "LPeople": "人员登车点",
    "LTowingPoint": "牵引挂钩点",
    "LExplosion": "爆炸冒烟点",
    "LFatality_Smoke": "毁灭烟雾点",
    "LExhaust": "车辆排气点",
    "LSmoke": "普通冒烟点",
}

# 网格部件名的中文说明（定稿 v2）
MESH_CN = {
    "FrontWheels": "车轮（轮式）",
    "Hull": "车身（轮式）",
    "Chassis": "车壳（轮式）",
    "Chasis": "车壳（轮式）",
    "Tracks": "履带",
    "Basis": "车体",
    "Turret": "炮塔",
    "GunCarriage": "炮架",
    "MainBarrel": "炮管",
    "ArmouredMantlet": "炮盾",
    "Mantlet": "炮盾",
}


def locator_cn(name):
    """按前缀匹配定位器中文说明（LSmoke01 -> 普通冒烟点）。"""
    if name in LOCATOR_CN:
        return LOCATOR_CN[name]
    for k, v in LOCATOR_CN.items():
        if name.startswith(k):
            return v
    return ""

W = 78   # 输出总宽度：Win7 CMD 默认 80 列，中文占 2 格，留余量不折行


def _wcw(s):
    """按东亚字符宽度计算显示宽度（中文算 2）。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s, n):
    return s + " " * max(0, n - _wcw(s))


def out_line(s, indent=4):
    """输出一行；超宽时自动折行并缩进续行（不依赖终端滚动条）。"""
    while _wcw(s) > W:
        acc = 0
        cut = 0
        for i, ch in enumerate(s):
            acc += 2 if _is_wide(ch) else 1
            if acc > W:
                break
            cut = i + 1
        if cut <= 0:
            break
        print(s[:cut])
        s = " " * indent + s[cut:].lstrip()
    print(s)


def _is_wide(ch):
    import unicodedata
    return unicodedata.east_asian_width(ch) in "WF"


def field_label(f):
    cn = FIELD_CN.get(f)
    return "{} ({})".format(f, cn) if cn else f


def part_label(field, target):
    """ModelPart 按指向的部件名给具体中文；其余字段照旧。

    Basis -> 车体连接部件；Turret -> 炮塔连接部件；MainBarrel -> 炮管连接部件。
    """
    if field == "ModelPart" and target:
        cn = MODEL_PART_CN.get(target)
        if cn is None:
            for k, v in MODEL_PART_CN.items():
                if target.startswith(k):
                    cn = v
                    break
        if cn:
            return "{} ({})".format(field, cn)
    return field_label(field)


def enable_hscroll(min_width=200):
    """把控制台缓冲区调宽（窗口不变），让 CMD 出现水平滚动条。

    原理：CMD 的滚动条由「缓冲区宽度 > 窗口宽度」触发。
    SetConsoleScreenBufferSize 不能小于窗口，所以要先临时缩小窗口。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT),
                        ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]

        class CSBI(ctypes.Structure):
            _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD),
                        ("wAttributes", wintypes.WORD), ("srWindow", SMALL_RECT),
                        ("dwMaximumWindowSize", COORD)]

        h = k32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        info = CSBI()
        if not k32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
            return

        win_w = info.srWindow.Right - info.srWindow.Left + 1
        win_h = info.srWindow.Bottom - info.srWindow.Top + 1
        if win_w <= 0 or win_h <= 0:
            return

        want_w = max(min_width, win_w + 60)
        if info.dwSize.X >= want_w:
            return                           # 已经够宽了

        # 1) 临时把窗口缩到 1x1（否则缓冲区不能放大）
        tiny = SMALL_RECT(0, 0, 0, 0)
        k32.SetConsoleWindowInfo(h, True, ctypes.byref(tiny))
        # 2) 扩缓冲区宽度
        k32.SetConsoleScreenBufferSize(h, COORD(want_w, info.dwSize.Y))
        # 3) 恢复窗口尺寸（宽度不变 -> 出现水平滚动条）
        restore = SMALL_RECT(0, 0, win_w - 1, win_h - 1)
        k32.SetConsoleWindowInfo(h, True, ctypes.byref(restore))
    except Exception:
        pass


def pick_id(name_id, cands):
    for c in cands:
        if c in name_id:
            return name_id[c], c
    return None, None


def read_xml(path):
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    return (raw.decode("utf-8-sig") if bom else raw.decode("utf-8")), bom


def analyze(mod_path):
    print("=" * W)
    print("文件: {}".format(os.path.basename(mod_path)))
    print("目录: {}".format(os.path.dirname(mod_path)))
    print("大小: {} 字节".format(os.path.getsize(mod_path)))
    print("=" * W)

    s = ModScene()
    s.from_file(mod_path)

    id_name = {o.id: o.name for o in s.objects}
    name_id = {o.name: o.id for o in s.objects}
    loc_ids = {o.id for o in s.objects if o.data is None}
    parent = {}
    for o in s.objects:
        for c in o.children_ids:
            parent[c] = o.name

    n_mesh = len(s.objects) - len(loc_ids)
    print("对象 {} 个（网格 {} + 定位器 {}）".format(len(s.objects), n_mesh, len(loc_ids)))
    print("-" * W)
    print("{} {} {} {} {}".format(
        _pad("id", 4), _pad("名字", 26), _pad("类型", 6), _pad("父级", 10), "限制"))
    print("-" * W)
    for o in sorted(s.objects, key=lambda x: x.id):
        kind = "定位器" if o.data is None else "网格"
        lim = ""
        if int(o.limits["type"]):
            lim = "{} {} {}~{}".format(
                TYPES.get(int(o.limits["type"]), "?"), AXES[int(o.limits["axis"])],
                round(o.limits["min"], 2), round(o.limits["max"], 2))
        print("{} {} {} {} {}".format(
            _pad(str(o.id), 4), _pad(o.name, 26), _pad(kind, 6),
            _pad(parent.get(o.id, "-"), 10), lim))
    print("-" * W)

    # 常用名字速查（中文按定稿 v2 翻译表）
    quick = []
    for label, cands in (
        ("履带", ["Tracks"]),
        ("车体", ["Basis"]),
        ("车身（轮式）", ["Hull"]),
        ("车壳（轮式）", ["Chassis", "Chasis"]),
        ("炮塔", ["Turret", "Turret01", "Turret_01", "Turret_02"]),
        ("炮架", ["GunCarriage", "GunCarriage_01", "GunCarriage01"]),
        ("炮管", ["MainBarrel", "MainBarrel01", "MainBarrel02"]),
        ("炮盾", ["ArmouredMantlet", "Mantlet"]),
        ("车轮（轮式）", ["FrontWheels01", "FrontWheels", "Wheel01_ROTATE"]),
        ("炮塔主炮", ["LMainGun"]),
        ("炮塔同轴机枪", ["LMachineGun_Turret_COAX", "LMachineGun_Turret_Coax"]),
        ("炮塔高射机枪", ["LMachineGun_Turret_Cupola"]),
        ("炮塔后置机枪", ["LMachineGun_Turret_Back"]),
        ("车体航向机枪", ["LMachineGun_Basis"]),
        ("人员登车点", ["LPeople", "LPeople01", "LPeople1"]),
        ("牵引挂钩点", ["LTowingPoint"]),
        ("车辆排气点", ["LExhaust01", "LExhaust02", "LExhaust"]),
        ("普通冒烟点", ["LSmoke01", "LSmoke02", "LSmoke03"]),
        ("毁灭烟雾点", ["LFatality_Smoke", "LFatalitySmoke01", "LFatalitySmoke"]),
        ("爆炸冒烟点", ["LExplosion01", "LExplosion"]),
    ):
        hits = [(c, name_id[c]) for c in cands if c in name_id]
        if hits:
            quick.append("  {} {}".format(
                _pad(label, 18), " | ".join("{}={}".format(n, i) for n, i in hits)))
    if quick:
        print("常用部件：")
        for line in quick:
            print(line)

    # ---------------------------------------------------------------- xml 核对
    folder = os.path.dirname(mod_path)
    xmls = sorted(glob.glob(os.path.join(folder, "*.xml")))
    if not xmls:
        print("\n同目录没有 xml，跳过引用核对。")
        return

    for xp in xmls:
        print("\n" + "=" * W)
        print("核对: {}".format(os.path.basename(xp)))
        print("=" * W)
        try:
            xml, _bom = read_xml(xp)
        except Exception as exc:  # noqa: BLE001
            print("  读取失败:", exc)
            continue

        out_line("{} {} {} {}".format(
            _pad("字段", 40), _pad("值", 4), _pad("指向", 22), "判定"))
        print("-" * W)
        bad = 0
        total = 0

        def report(field, value, hint, weapon=""):
            nonlocal bad, total
            total += 1

            if value < 0:
                out_line("{} {} {} {}".format(
                    _pad(field_label(field), 40), _pad(str(value), 4),
                    _pad("<未使用>", 22), ""))
                return

            target = id_name.get(value)
            label = part_label(field, target)      # ModelPart 按指向的部件给具体中文
            if target is None:
                out_line("{} {} {} {}".format(
                    _pad(label, 40), _pad(str(value), 4), _pad("?", 22),
                    "! 越界"))
                print("      +-> 本模型 id 范围 0~{}（共 {} 个）".format(
                    len(s.objects) - 1, len(s.objects)))
                bad += 1
                return

            is_loc = value in loc_ids
            want_loc = hint.get("locator", True)
            issues = []
            if is_loc != want_loc:
                issues.append("定位器" if want_loc else "网格")

            # 建议值
            suggest = None
            if "by_weapon" in hint:
                for key, cands in hint["by_weapon"].items():
                    if key != "default" and key in weapon:
                        suggest = pick_id(name_id, cands)
                        break
                if suggest is None:
                    suggest = pick_id(name_id, hint["by_weapon"]["default"])
            elif "by_name" in hint:
                suggest = pick_id(name_id, hint["by_name"])

            if issues:
                bad += 1
                out_line("{} {} {} {}".format(
                    _pad(label, 40), _pad(str(value), 4), _pad(target, 22),
                    "! " + " / ".join(issues)))
                if suggest and suggest[0] is not None and suggest[0] != value:
                    print("      +-> 建议 {} ({})".format(suggest[0], suggest[1]))
            else:
                out_line("{} {} {} {}".format(
                    _pad(label, 40), _pad(str(value), 4), _pad(target, 22), "OK"))

        # 单值字段
        for m in re.finditer(r'(ShootPoint|ModelPart|EntrancePoint|TowPoint|FatalitySmokePoint|ShootDustPoint)="(-?\d+)"',
                             xml):
            f, v = m.group(1), int(m.group(2))
            # 找该 entry 的 <Weapon>
            weapon = ""
            wm = re.search(r"<Weapon>([^<]+)</Weapon>", xml[m.end():m.end() + 400])
            if wm:
                weapon = wm.group(1)
            report(f, v, FIELD_HINTS.get(f, {"locator": f != "ModelPart"}), weapon)

        # 列表字段
        for blk, want_loc, cands in (
            ("ExhaustPoints", True, ["LExhaust01", "LExhaust02"]),
            ("DamagePoints", True, ["LSmoke01", "LSmoke02", "LSmoke03", "LSmoke04",
                                    "LSmoke05", "LSmoke06"]),
        ):
            mm = re.search(r"<{}>(.*?)</{}>".format(blk, blk), xml, re.S)
            if not mm:
                continue
            for idx, v in enumerate([int(x) for x in re.findall(r'data="(\d+)"', mm.group(1))]):
                # 列表字段按出现顺序分配候选，第 N 项用第 N 个候选名
                sub = cands[idx:idx + 1] or cands[-1:]
                report(blk, v, {"locator": want_loc, "by_name": sub})

        print("-" * W)
        print("共 {} 项引用，其中 {} 项有问题".format(total, bad))


def main():
    enable_hscroll()          # 让 CMD 出现水平滚动条
    if len(sys.argv) > 1:
        targets = [a for a in sys.argv[1:] if a.lower().endswith(".mod") and os.path.exists(a)]
        if not targets:
            print("参数里没有有效的 .mod 文件。")
        for t in targets:
            try:
                analyze(t)
            except Exception as exc:  # noqa: BLE001
                print("[错误] {} 解析失败: {}".format(t, exc))
            print()
        input("按回车退出...")
        return

    # 无参数：列出当前目录及附近的 .mod
    here = os.getcwd()
    mods = sorted(glob.glob(os.path.join(here, "*.mod")))
    print("当前目录 {} 下找到 {} 个 .mod".format(here, len(mods)))
    for i, m in enumerate(mods, 1):
        print("  {:>2}. {}".format(i, os.path.basename(m)))
    if not mods:
        print("\n用法：把 .mod 文件拖到这个脚本（或 .bat）上，")
        print("      或在命令行里：python mod_id.py 文件1.mod 文件2.mod")
        input("\n按回车退出...")
        return

    sel = input("\n输入编号查看（多个用逗号分隔，回车退出）: ").strip()
    if not sel:
        return
    for part in sel.replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(mods):
            try:
                analyze(mods[int(part) - 1])
            except Exception as exc:  # noqa: BLE001
                print("[错误]", exc)
    input("\n按回车退出...")


if __name__ == "__main__":
    main()
