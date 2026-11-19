# MODVIEW — 闪电战（Blitzkrieg）MOD 模型查看 / 编辑器

一个用 Python + tkinter + PyOpenGL 写的 `.mod` 模型工具：**查看**、**改**、**再导出**，
面向老游戏《闪电战》（Blitzkrieg）及其 MOD 的模型移植工作流。

> 纯 Python，无引擎依赖；打包成单文件 exe 后直接双击运行。

---

## 功能

**查看**

- 3D 视口：鼠标左键旋转、滚轮缩放、右键平移
- 左侧部件树：`id / 名字 / 类型 / 旋转限制`
- 同目录 `.dds` 贴图自动解码预览（GDI+，不需要装 DirectX SDK）
- 线框 / 实体 / 贴图切换，AABB 包围盒显示

**编辑**

- 移动 / 旋转 / 缩放，带手柄与吸附
- 改旋转限制（`limits`）、改部件名、分离 / 合并部件
- 部件 ID 重排（游戏按 ID 与 Geometry 顺序配对，乱序会导致部件错位）
- 模型整体缩放、镜像

**动画**

- 导入 `.mod` 里的动画段并在时间线上预览
- 在 Blender 里做好的动画可以按段写回（配合 Blender 的 `mod` 插件）

**其他**

- XML（平台 / 武器 / 车辆）编辑与**语法校验**（错误给出行列号）
- 导出 OBJ
- 导出 .mod 前自动回读校验（对象数 / 面数 / 包围盒 / 动画段数）

---

## 运行

### 依赖

| 依赖 | 说明 |
|---|---|
| Python 3.8+ | 需要 tkinter（官方安装包默认自带） |
| numpy | `pip install numpy` |
| PyOpenGL | `pip install PyOpenGL` |
| **mod_scene** | `.mod` 解析/写入模块，**来自 Blender 的 `mod` 插件**，见下文 |

```bash
pip install -r requirements.txt
python modview.py            # 或者直接把 .mod 拖到窗口里
```

### 关于 mod_scene

`modview.py` 会在这些位置找一个叫 `mod` 的包（Blender 插件目录）：

```
<python 可执行文件同目录>/mod
%APPDATA%\Blender Foundation\Blender\{5.2,5.1,5.0,4.5,4.2,4.1,4.0}\scripts\addons\mod
```

所以最省事的做法是：**在 Blender 里装好 `mod` 插件**（导入/导出 Blitzkrieg `.mod` 的那个），
本程序会自动复用它的解析器。也可以把那个 `mod` 目录整个复制到本程序旁边。

> 本仓库**不包含** `mod_scene`，它属于 Blender 插件，版权归其作者。

### 打包成 exe

```bash
pip install pyinstaller
python -m PyInstaller MODVIEW.spec
# 产物：dist/MODVIEW.exe
```

---

## 目录结构

```
modview.py     主程序（界面、编辑逻辑、导出流程）
glviewport.py  OpenGL 视口（渲染、拾取、手柄、贴图）
mod_id.py      部件 ID 工具（重排 / 冲突检测）
i18n.py        中英文案
MODVIEW.spec   PyInstaller 打包配置
```

---

## 已知限制

- 只在 Windows 上测过（用到了 GDI+ 解码 DDS、`ctypes` 调 `user32`）
- 动画只能预览和整体写回，不支持在编辑器里逐帧编辑
- `.mod` 里的 AABB 只能看，不能编辑

---

## 许可证

MIT，见 [LICENSE](LICENSE)。

`mod_scene`（Blender 插件）不在本许可证范围内。
