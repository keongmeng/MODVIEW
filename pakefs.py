# -*- coding: utf-8 -*-
"""只读访问 Blitzkrieg 的 .pak（实为 ZIP）与散装目录。

给特效预览提供三件事：
  1) 目录枚举：Effects/Effects、Effects/Particles、Weapons
  2) 按虚拟路径读字节（大小写不敏感、反斜杠兼容）
  3) 贴图按名字解析出真实路径（name -> name.dds / name_h.dds / name.tga）

索引会缓存到本文件同目录的 fx_index.json，pak 未变时二次启动直接读缓存。
"""
import glob
import json
import os
import sys
import zipfile

CACHE_NAME = "fx_index.json"
# 只索引这些前缀，避免把 6 万条目全塞进内存/缓存
KEEP_PREFIXES = ("effects/", "weapons/", "textures/", "units/technics/")


def norm(vpath):
    """统一成小写、正斜杠、无首尾斜杠。"""
    return vpath.replace("\\", "/").strip("/").lower()


def _here():
    # 打包后 __file__ 在临时目录里（退出即删），缓存要放到 exe 旁边
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _file_sig(path):
    try:
        st = os.stat(path)
        return [os.path.abspath(path), int(st.st_mtime), int(st.st_size)]
    except OSError:
        return None


class PakFS(object):
    """多个 pak + 散装目录的统一只读视图。"""

    def __init__(self):
        self.roots = []
        self._zips = {}       # abspath -> ZipFile
        self._files = {}      # lower vpath -> abspath( pak ) 或 绝对文件路径（散装）
        self._loose = set()   # 散装文件的绝对路径集合
        self._cache_path = os.path.join(_here(), CACHE_NAME)

    # ---------------------------------------------------------------- 扫描
    def set_roots(self, roots, use_cache=True):
        roots = [os.path.abspath(r) for r in (roots or []) if r and os.path.isdir(r)]
        self.roots = roots
        self._close()
        self._files = {}
        self._loose = set()

        if use_cache and self._load_cache():
            return len(self._files)

        paks = []
        for root in roots:
            for depth_pattern in ("*.pak", "*/*.pak", "*/*/*.pak", "*/*/*/*.pak"):
                paks.extend(glob.glob(os.path.join(root, depth_pattern)))
        paks = sorted({os.path.abspath(p) for p in paks})

        for p in paks:
            try:
                z = zipfile.ZipFile(p)
            except Exception:
                continue
            self._zips[p] = z
            for info in z.infolist():
                name = info.filename
                if name.endswith("/"):
                    continue
                key = norm(name)
                if not key.startswith(KEEP_PREFIXES):
                    continue
                self._files[key] = p

        # 散装目录（未打包的 Effects/Weapons 也认）
        for root in roots:
            for sub in ("Effects", "Weapons", "effects", "weapons"):
                base = os.path.join(root, sub)
                if not os.path.isdir(base):
                    continue
                for dp, _dn, fn in os.walk(base):
                    for f in fn:
                        full = os.path.join(dp, f)
                        rel = norm(os.path.relpath(full, root))
                        self._files[rel] = full
                        self._loose.add(full)

        self._save_cache(sorted(paks))
        return len(self._files)

    def _close(self):
        for z in self._zips.values():
            try:
                z.close()
            except Exception:
                pass
        self._zips = {}

    # ---------------------------------------------------------------- 缓存
    def _load_cache(self):
        try:
            with open(self._cache_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return False
        if not isinstance(data, dict) or data.get("v") != 1:
            return False
        paks = data.get("paks") or []
        stored = data.get("files") or {}
        if not isinstance(stored, dict) or not stored:
            return False
        # pak 列表必须与当前磁盘一致（同路径、同 mtime、同大小）
        current = []
        for item in paks:
            sig = _file_sig(item[0]) if isinstance(item, list) and item else None
            if sig != item:
                return False
            current.append(item)
        for p in current:
            try:
                self._zips[p[0]] = zipfile.ZipFile(p[0])
            except Exception:
                self._close()
                return False
        pak_set = set(self._zips)
        for k, v in stored.items():
            if not isinstance(v, str):
                continue
            self._files[k] = v
            if v not in pak_set:
                self._loose.add(v)
        return bool(self._files)

    def _save_cache(self, paks):
        try:
            payload = {
                "v": 1,
                "paks": [sig for sig in (_file_sig(p) for p in paks) if sig],
                "files": self._files,
            }
            tmp = self._cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self._cache_path)
        except Exception:
            pass

    # ---------------------------------------------------------------- 查询
    def count(self):
        return len(self._files)

    def exists(self, vpath):
        return norm(vpath) in self._files

    def read(self, vpath):
        key = norm(vpath)
        owner = self._files.get(key)
        if not owner:
            return None
        if owner in self._loose:
            try:
                with open(owner, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
        z = self._zips.get(owner)
        if z is None:
            try:
                z = zipfile.ZipFile(owner)
                self._zips[owner] = z
            except Exception:
                return None
        try:
            return z.read(key)
        except KeyError:
            # 大小写/斜杠有时不完全一致，退回一次不区分大小写的线性查找
            for info in z.infolist():
                if norm(info.filename) == key:
                    return z.read(info.filename)
        except Exception:
            return None
        return None

    def list_prefix(self, prefix, suffix=".xml"):
        """列出某前缀下（任意深度）以 suffix 结尾的相对路径。"""
        p = norm(prefix) + "/"
        out = []
        for key in self._files:
            if key.startswith(p) and key.endswith(suffix):
                out.append(key)
        return sorted(out)

    def basenames(self, prefix, suffix=".xml"):
        return sorted({os.path.basename(k) for k in self.list_prefix(prefix, suffix)})

    # ---------------------------------------------------------------- 业务便捷
    def effects(self):
        return self.basenames("effects/effects", ".xml")

    def particles(self):
        return self.basenames("effects/particles", ".xml")

    def weapons(self):
        return self.basenames("weapons", ".xml")

    def effect_path(self, name):
        return self._pick(["effects/effects/%s.xml" % norm(name)])

    def particle_path(self, name):
        n = norm(name)
        if n.endswith(".xml"):
            n = n[:-4]
        cands = [n + ".xml"]
        if not n.startswith("effects/"):
            cands.insert(0, "effects/particles/" + n + ".xml")
        return self._pick(cands)

    def weapon_path(self, name):
        return self._pick(["weapons/%s.xml" % norm(name)])

    def texture_path(self, texture_name):
        """TextureName（无扩展名、可带目录）→ 真实路径。"""
        n = norm(texture_name)
        cands = []
        if os.path.splitext(n)[1] in (".dds", ".tga", ".png", ".bmp"):
            cands.append(n)
        else:
            for ext in (".dds", "_h.dds", ".tga", ".png"):
                cands.append(n + ext)
                if not n.startswith("effects/"):
                    cands.append("effects/particles/" + n + ext)
        hit = self._pick(cands)
        if hit:
            return hit
        # 兜底：按 basename 在全部贴图里找
        base = os.path.basename(n)
        for key in self._files:
            if not key.endswith((".dds", ".tga", ".png")):
                continue
            stem = os.path.basename(key)
            if stem == base or stem in (base + ".dds", base + "_h.dds"):
                return key
        return None

    def _pick(self, cands):
        for c in cands:
            key = norm(c)
            if key in self._files:
                return key
        return None


def autodetect_roots(mod_path=None, extra=None):
    """猜游戏数据目录：从 .mod 往上找含 data/*.pak 的目录，再并上额外路径。"""
    roots = []
    for p in (extra or []):
        if p and os.path.isdir(p):
            roots.append(os.path.abspath(p))
    if mod_path:
        d = os.path.dirname(os.path.abspath(mod_path))
        for _ in range(6):
            for cand in (d, os.path.join(d, "data")):
                if os.path.isdir(cand) and glob.glob(os.path.join(cand, "*.pak")):
                    roots.append(os.path.abspath(cand))
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    out = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out
