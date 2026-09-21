# PianoConvert

Windows 离线小工具：把音频转成 MIDI 和 MusicXML，或把 MIDI / MusicXML 转成原神风物之诗琴键盘谱（数字谱 + 电脑键位，自动对齐 C 大调）。

## 能做什么

1. **音频 → 乐谱**  
   选 mp3 / wav / flac / ogg / m4a，选钢琴或其他乐器。完成后得到：
   - `<歌名>.mid`
   - `<歌名>.musicxml`
   - `<歌名>_键盘谱.txt`

2. **MIDI / MusicXML → 键盘谱**  
   点「选择 MIDI / MusicXML」，或把 `.mid` / `.musicxml` 拖进窗口。不经过音频转录，只出键盘谱。多音轨会对齐到同一条时间线；相差 50ms 以内的音写成括号和弦。

输出目录：`文档\PianoConvert\Output\<歌名>_时间戳\`。历史记录可打开 MIDI、MusicXML、键盘谱或所在文件夹。

## 键盘谱怎么看

txt 里两段结构相同，只是记号不同：

| 音域 | 数字谱 | 电脑键 |
|---|---|---|
| 低 C3–B3 | `-1` … `-7` | `Z X C V B N M` |
| 中 C4–B4 | `1` … `7` | `A S D F G H J` |
| 高 C5–B5 | `+1` … `+7` | `Q W E R T Y U` |

- 空格：停顿  
- `/`：小节  
- `( )`：同时按，例如 `(135)` 对应 `(ADG)`

曲子会自动转到 C 大调白键，黑键就近收到白键，超出 C3–B5 的音按八度折进三排（C6 记成 `+1` / `Q`）。

## 安装

到 [Releases](https://github.com/NotBadSlime/PianoConvert/releases) 下载 `PianoConvertSetup-0.3.0.exe`，按向导安装。需要 64 位 Windows。首次钢琴转换若用 CPU 会比较慢，状态栏会提示。

## 开发

将 Kong 模型 checkpoint 复制到 `models/`：

```powershell
Copy-Item "E:\PianoConvert\_internal\piano_transcription_inference_data\note_F1=0.9677_pedal_F1=0.9186.pth" "models\"
```

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app
.\.venv\Scripts\python.exe -m pytest -v
```

## 发布

1. `gh auth login`（若尚未登录）
2. 打标签并推送：`git tag v0.3.0 && git push origin v0.3.0`

GitHub Actions 会跑测试并尝试打安装包。当前 workflow **不会**自动下载约 164MB 的 `.pth` 权重，Actions 上的安装包步骤可能失败。本机打包：

```powershell
.\scripts\build_installer.ps1 -Version 0.3.0
```

生成 `installer\output\PianoConvertSetup-0.3.0.exe`，再挂到对应 GitHub Release。
