# BlenderHub

Blender 插件管理桌面客户端（PyQt6）。

## 功能

- **版本管理**：扫描本机 Blender 安装、管理多版本。
- **插件管理**：本机插件扫描、一键安装 / 卸载 / 更新，支持从 ZIP、从目录、在线安装。
- **在线目录**：浏览官方插件目录，搜索、按分类筛选、缩略图缓存。
- **项目分析**：项目占用分析、最近项目快速打开。
- **体验**：暗色主题、高 DPI 适配、响应式布局，窗口缩小时自动重排不裁切。

## 运行

```bash
pip install -r requirements.txt
python main.py
```

> 需要 Windows + 已安装 Python 3.11+ 与至少一份 Blender（用于扫描版本与插件）。

## 打包

```powershell
.\build.ps1
```

产物在 `dist/`，单目录免安装。

## 目录结构

```
blenderhub/        # 应用源码（UI、扫描、商店、主题）
tools/             # 维护脚本（提取 / 校验 / 诊断）
assets/            # 字体、图标
main.py            # 入口
BlenderHub.spec    # PyInstaller 打包规格
```

## 许可证

[MIT](LICENSE) —— 详见 LICENSE 文件。
