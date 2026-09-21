# PianoConvert 简易转换工具

日期：2026-09-20  
状态：待实现  
版本：0.2.0

## 背景

当前 `E:\PianoConvert` 是已安装的桌面程序（`PianoConvert.exe` + `_internal`），不是源码仓库。现有界面是钢琴转录工作台：设备、预设、阈值、队列、钢琴卷帘。用户只要一条直线流程，并支持钢琴以外的音频。

本 spec 描述 **新源码仓库里的 0.2.0**。安装目录保持不动，开发完成后再用 Inno Setup 安装包覆盖安装。

## 目标

Windows 离线小工具：

1. 用户选择一首音频（mp3 / wav / flac / ogg / m4a）
2. 选择钢琴或其他乐器
3. 看到转换进度
4. 完成后写入历史列表，可打开 MIDI、打开 MusicXML、打开所在文件夹

输出两种格式：`.mid`（播放/导入）和 `.musicxml`（五线谱交换格式）。不生成 `.mscz`，不捆绑 MuseScore。

## 非目标

- 钢琴卷帘、播放预览、阈值/量化/踏板微调
- 批量队列（一次只转一首）
- 人声分离、PDF、MusicXML 手改音符
- 把 MuseScore 打进安装包
- 修改现有安装目录里的 PyInstaller 文件作为开发方式

## 源码位置

新仓库：`E:\PianoConvertApp`  
语言：Python 3.10 + PySide6  
界面语言：简体中文

现有安装包里的钢琴权重可复用：

`E:\PianoConvert\_internal\piano_transcription_inference_data\note_F1=0.9677_pedal_F1=0.9186.pth`

## 架构

四个边界清楚的部分：

| 单元 | 职责 | 依赖 |
|---|---|---|
| `app.engines.kong_piano` | 钢琴音频 → 音符事件 → MIDI | `piano_transcription_inference`、本地 `.pth` |
| `app.engines.basic_pitch` | 其他乐器音频 → 音符事件 → MIDI | `basic-pitch`（ONNX） |
| `app.convert` | 选引擎、写 MIDI、MIDI→MusicXML、进度回调、取消 | 两个引擎、`music21` |
| `app.history` | 读写历史 JSON，不碰模型 | 文件系统 |
| `app.ui.main_window` | 选文件、选类型、进度、历史按钮 | `convert`、`history` |

引擎接口统一为：输入音频路径 + 取消事件 + 进度回调，输出 MIDI 路径（或抛错）。`convert` 再把 MIDI 转成 MusicXML。界面不直接调用引擎。

设备策略：能用 CUDA 就用 CUDA，否则 CPU；状态栏提示「使用 CPU，会比较慢」。不提供设备下拉框。

## 界面

单窗口，上转换、下历史。

**转换区**

- 选择或拖入一个音频文件，显示文件名
- 两个选项：钢琴（默认）/ 其他乐器
- 「开始转换」；进行中改为「取消」
- 进度条 + 短状态：读取音频 → 转录 → 写入 MIDI → 写入 MusicXML
- 一次只转一首；进行中禁用选文件和类型切换

**历史区**

每条显示：歌名、钢琴/其他、完成时间、成功/部分成功/失败。

成功：三个按钮——打开 MIDI、打开 MusicXML、打开所在文件夹。  
部分成功（仅 MIDI）：MusicXML 按钮禁用，旁注「谱面导出失败」。  
失败：一句原因，没有打开文件按钮（文件夹若已创建且为空则不提供）。

历史按时间倒序。重启后仍在。

## 转换流程

1. 校验文件存在且后缀为 `.mp3` / `.wav` / `.flac` / `.ogg` / `.m4a`。不通过则弹提示，不写历史。
2. 在 `文档\PianoConvert\Output\<原名>_<YYYYMMDD-HHMMSS>\` 建新目录，不覆盖旧次转换。
3. 按用户选择调用钢琴或 basic-pitch，在该目录写入 `<原名>.mid`。
4. 用 `music21` 将 MIDI 转为 `<原名>.musicxml`（未压缩 MusicXML，便于用记事本/MuseScore 打开）。
5. 写入历史：输入路径、类型、时间、两个输出路径、状态、错误信息。
6. 界面刷新历史，选中刚完成的那条。

取消：设置取消标志；工作线程在当前可中断点退出；删除本次输出目录；不写历史；界面恢复可再转。

## 路径与数据

| 数据 | 位置 |
|---|---|
| 输出文件 | `%USERPROFILE%\Documents\PianoConvert\Output\<stem>_<timestamp>\` |
| 历史 | `%LOCALAPPDATA%\PianoConvert\history.json` |
| 钢琴模型（开发） | 仓库 `models\note_F1=0.9677_pedal_F1=0.9186.pth`（从现有安装目录复制） |
| 钢琴模型（安装后） | 安装目录 `_internal\piano_transcription_inference_data\` |

`history.json` 为对象数组。每条至少包含：`id`、`title`、`source_path`、`kind`（`piano` \| `other`）、`created_at`、`status`（`success` \| `partial` \| `failed`）、`midi_path`、`musicxml_path`、`folder`、`error`。

打开文件/文件夹：用 Windows 默认程序（`os.startfile`）。文件缺失时提示「文件不在了」，不崩溃。

## 错误处理

| 情况 | 行为 |
|---|---|
| 未选文件就点开始 | 「开始转换」保持禁用，不进历史 |
| 读音频失败 | 失败历史 + 「无法读取这个音频文件」 |
| 显存不足 / CUDA 运行时错误 | 失败历史 + 「显存不足或 GPU 出错，可关闭其他占用显卡的程序后重试」；不自动静默切 CPU 重跑同一任务（避免两次长时间等待）。无 CUDA 的机器启动时即用 CPU |
| 钢琴/basic-pitch 抛错 | 失败历史 + 「转录失败」和截断后的异常信息 |
| MusicXML 导出失败 | 状态 `partial`，保留 MIDI |
| 磁盘满 / 无写权限 | 失败历史 + 「无法写入输出目录」 |

工作在 QThread 或等价后台线程；异常必须回到主线程更新 UI，界面不得卡死。

## 测试

最少覆盖：

1. 钢琴引擎与其他乐器引擎的单元测试可用短音频夹具（或 mock 引擎）得到 MIDI 字节/文件。
2. MIDI → MusicXML：对一份已知 MIDI 能写出含 `<score-partwise>` 的文件。
3. 历史：写入、读取、重启后条数与路径一致。
4. 取消：取消标志为真时 `convert` 不写出完整产物、不追加历史。
5. UI 冒烟（`QT_QPA_PLATFORM=offscreen`）：窗口能创建，未选文件时开始按钮不可用。

不把完整长音频端到端转录纳入默认 CI（太慢、要权重）。Release 工作流跑上述单测即可。

## 仓库结构

```text
PianoConvertApp/
  app/
    __init__.py
    __main__.py
    paths.py
    history.py
    convert.py
    engines/
      base.py
      kong_piano.py
      basic_pitch.py
    ui/
      main_window.py
      styles.py
  tests/
  models/
  scripts/
    build_exe.ps1
    build_installer.ps1
  installer/
    PianoConvert.iss
  .github/workflows/release.yml
  requirements.txt
  README.md
```

入口：`python -m app`。打包入口同一模块。

## 安装包与 GitHub Release

- PyInstaller onedir，再 Inno Setup 生成 `PianoConvertSetup-0.2.0.exe`
- 捆绑：Python 运行时、PySide6、PyTorch、钢琴 `.pth`、basic-pitch ONNX、music21
- 不捆绑 MuseScore
- 默认安装路径：`C:\Program Files\PianoConvert`
- 开始菜单 + 桌面快捷方式，卸载用 Inno 卸载器
- 工作流：推送 tag `v0.2.0` 时构建安装包并挂到 GitHub Release
- 本机 `gh` 尚未登录；发布前需要用户执行 `gh auth login` 并指定仓库。仓库公开，名称 `PianoConvert`

首次发布由本机或 Actions 完成均可；spec 不绑定必须在开发机登录才能写代码。

## 外观

现代桌面工具，不是旧版工作台的删减版：

- 浅色背景、清晰层级、主按钮一个
- 控件少，间距宽
- 不使用卷帘、工具栏图标堆、多列分割器

具体像素级视觉实现阶段再定，不阻塞本 spec。
