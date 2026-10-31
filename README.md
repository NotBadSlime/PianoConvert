<div align="center">

# PianoConvert

**把歌曲、MIDI 和五线谱变成《原神》风物之诗琴可以直接弹的键盘谱**

Windows 桌面程序 · 中文界面 · 本地运行

**1.0.0 Beta**：[下载预发布版](https://github.com/NotBadSlime/PianoConvert/releases/tag/v1.0.0-beta.1) · [更新说明](docs/releases/1.0.0-beta.1.md)

[下载](https://github.com/NotBadSlime/PianoConvert/releases/latest) · [键盘谱记法](docs/键盘谱记法.md) · [常见问题](#常见问题) · [从源码运行](#从源码运行)

</div>

<p align="center">
  <img src="docs/images/screenshot-light.png" alt="浅色主题" width="49%">
  <img src="docs/images/screenshot-dark.png" alt="深色主题" width="49%">
</p>

## 最近更新（2026-10-31，当前源码）

- **图像识谱改用 Copista 28m**：扫描 PDF、截图和照片中的印刷五线谱默认使用 Copista，保留模型原生 MusicXML，再生成 MIDI 和键盘谱。可解析的矢量 PDF 和双手简谱继续使用各自的读取路线。
- **加强节奏与谱面读取**：补充轮廓字形 PDF 支持，改进弱起、不完整拍、附点、三连音和复杂连音组、跨谱表及加线音符处理；长 PDF 按全部页面读取，避免静默截断。
- **修复临时升降与转调作用范围**：区分谱表、音高和八度，正确处理还原、重升重降、延音跨小节及调号变化；键盘谱按对应调号段落适配诗琴音域，局部临时记号不会触发整首移调。
- **统一演奏顺序与变拍变速**：MIDI 和键盘谱共用 MusicXML 反复展开顺序，反复跳回与一二房子恢复实际进入该段的速度、拍号和调号；保留已识别的小节内变速位置与非整数速度。
- **补齐模型管理、诊断和打包**：固定 Copista 源码与权重版本，加载前校验 SHA-256，支持模型下载续传和识别取消；保留逐页原始 XML、版本和结构审计，缺页或乐器分组不一致时明确报错。修复 torchvision 原生算子打包，新增节奏回归工具和测试。

本地回归共运行 516 项用例：首次 504 项通过，其余 12 项在更新模型提示断言、调整代理环境或单独复验后全部通过。源码与打包后的 EXE 均完成《无名的人》4 页转换，读到 **♩=67**，MIDI 约 **4 分 31 秒**，两者 MIDI 字节一致；EXE 还通过空用户模型缓存、禁网条件下的转换。以上未包含逐音人工校订或听奏验收，也不代表任意来源谱面都能达到同样效果。

## 功能

- **四种来源都能转**
  - 歌曲录音（mp3 / wav / flac / ogg / m4a）：钢琴录音用 TransKun 钢琴转录模型，其他乐器用通用模型，转成键盘谱和 MIDI
  - MIDI、MusicXML（mid / midi / musicxml / xml / mxl）：直接排成键盘谱
  - 打谱软件导出的五线谱 PDF：直接读取谱面上的音符、休止符、升降号、连音线、反复记号、琶音和倚音，速度快、基本不会认错
  - 扫描件、照片、截图（pdf / png / jpg）：用图像识别读五线谱或双手简谱
- **统一、易读的键盘谱**：小节线和小节号，每拍固定格数，和弦、琶音、倚音、延长、休止、拍号和速度变化都有固定写法；简谱和键盘谱一格对一格
- **两种样式**：「详细」把旋律和伴奏分成上下两行，按不过来时可以先只弹旋律；「简洁」写在一行
- **自动适配诗琴**：别的调整首移到 C 调，黑键改到相邻的白键，超出 C3–B5 的音按八度挪进来；做过哪些处理都写在谱头
- **顺手的界面**：拖入文件即可转换，内置键盘谱预览和一键复制，转换记录可搜索，支持浅色 / 深色主题，配备简洁钢琴图标与温迪主题背景
- **可选 GPU 加速**：有 NVIDIA 显卡时，在程序里下载 GPU 组件后，歌曲转录快很多

## 下载安装

1. 到 [Releases](https://github.com/NotBadSlime/PianoConvert/releases/latest) 下载最新的 `PianoConvertSetup-<版本>.exe`。
2. 运行安装包，按提示完成安装。
3. 从开始菜单或桌面打开 **PianoConvert**。

系统要求：64 位 Windows 10 / 11。转换都在本地完成，不用联网；看图识别用的模型已经包含在安装包里。只有可选的 GPU 组件需要联网下载。

## 快速上手

1. 把文件拖进窗口，或点击选择文件（`Ctrl+O`）。
2. 如果是歌曲录音，选择乐器类型（钢琴 / 其他乐器）和计算设备（CPU / GPU）。钢琴录音默认用 TransKun 模型，想换回原来的 ByteDance 模型，在 **高级选项 → 钢琴识别模型** 里选。
3. 选择键盘谱样式（详细 / 简洁）和音域处理方式，点 **开始转换**（`Ctrl+Enter`）。转换中可以随时取消。需要的话，展开 **高级选项** 调整钢琴识别模型、琶音写法和和弦判定。
4. 转完后在右侧预览键盘谱，可以直接复制，或者打开文件、打开所在文件夹。

每次转换的结果放在 `文档\PianoConvert\Output\<歌名>_<日期时间>\`，每次都有键盘谱 `<歌名>_键盘谱.txt` 和 MIDI（歌曲录音、简谱、打谱软件导出的 PDF 读出来的就是 MIDI，MIDI 文件本身就是；MusicXML 文件和看图识别的五线谱会顺手转出 MIDI）。**MusicXML 要用时再转**：转换完成后结果条里、右侧「记录」每一条里都有 **转 MusicXML** 按钮，点一下用这次的 MIDI 生成（短的曲子几秒，长的要十几秒，期间软件照常能用），生成后自动打开，以后再点就直接打开；节奏和键盘谱的格子一致。来源本身是 MusicXML 文件或看图识别的五线谱时，MusicXML 就是原文件，直接打开。以前的转换都在右侧「记录」里，可以搜索、预览和重新打开。

## 键盘谱长什么样

```text
# 小星星（示例）
# 1=C  4/4  ♩=120  每拍 1 格（一格 = 四分音符）  原神风物之诗琴 C3–B5 三排白键

【键盘谱】
1 旋律 | Q     Q T T | Y Y T - | R R wE E | W W Q - |
  伴奏 | {ZCB} - 0 C | V - Z - | X 0 Z  - | B - Z - |
```

| 记号 | 意思 |
|---|---|
| `Z X C V B N M` / `A S D F G H J` / `Q W E R T Y U` | 低音 / 中音 / 高音的 1–7（诗琴的三排键） |
| `\|` | 小节线；每行开头的数字是小节号 |
| 空格 | 分开每一拍；一拍里的格子紧挨着写，每拍格数整首不变（`〈3〉` 这样标出的拍除外） |
| `〈3〉ABC` | 只有这一拍平均分成 3 格（三连音；`〈5〉` 五连音、`〈6〉` 六连音……） |
| `(ADG)` | 一起按 |
| `{ZCB}` | 琶音：从低到高依次快速按 |
| `wE` | 倚音：先快速带一下 W，再按 E |
| `-` | 前面的音还在响 |
| `0` | 不响（休止） |
| `〔3/4〕` `〔♩=60〕` | 从这一小节起改拍号、改速度 |

键盘谱文件包含一格对一格的【简谱】、新版【键盘谱】，以及附在末尾的旧版格式【指尖键盘谱】。完整说明见 [键盘谱记法](docs/键盘谱记法.md)。

## 识别效果

| 来源 | 效果 |
|---|---|
| MIDI、MusicXML | 音符原样读取，按诗琴音域和键位排版 |
| 打谱软件导出的 PDF | 直接读取谱面，反复记号和一二房子会展开，基本不会认错 |
| 歌曲录音 | 钢琴独奏录音效果最好；人声、伴奏很重的歌会有杂音和漏音 |
| 扫描件、照片、截图 | 默认由 Copista 28m 识别印刷五线谱，保留原生 MusicXML 并生成 MIDI；反复按识别出的标记处理，结果请对照原谱 |
| 吉他谱、手写谱 | 不支持 |

## 常见问题

**键盘谱和原曲不是一个调？**
C 大调和 A 小调的曲子不会移调。别的调会整首移到 C 调白键，因为诗琴只有白键；谱头的「处理」一行会写移了几个半音。

**`-` 要一直按住吗？**
不用。风物之诗琴按下就响，按住也不会更长。`-` 和 `0` 告诉你乐句怎么走：`-` 是音还连着，`0` 是该停的地方。换成能按住延长的乐器时，`-` 就是要按住的格子。

**和弦太多按不过来？**
用「详细」样式，先只弹上面「旋律」那一行，熟了再加伴奏。

**音域处理怎么选？**
默认使用新版逐音八度折叠：每个音独立移进 C3–B5。也可以选择旧版智能乐句移调，尽量保持旋律和弦的整体八度。两种方式都会把黑键改成相邻白键，选择会被记住；生成谱头会写入本次处理的移调、改键和折叠统计。

**钢琴录音用哪个识别模型？**
默认的 TransKun 在 MAESTRO 钢琴录音上实测比原来的 ByteDance 模型准约 1.5 个百分点（起音 F1 约 0.98 对 0.97），CPU 上也更快；音符会按延音踏板延长，键盘谱里 `-` 和 `0` 的写法和原来一致。想对比或换回去，在 **高级选项 → 钢琴识别模型** 里选 ByteDance，选择会被记住。这个选项只对「钢琴」录音有用。

**琶音 `{ }` 不好弹，或者想照原样一个个弹？**
在 **高级选项 → 琶音写法** 里选「一起按」（写成 `( )`）或「分开写」（MIDI 里弹出来的琶音按先后分格）。

**演奏录的 MIDI、歌曲录音里，同一个和弦被拆成好几格？**
在 **高级选项 → 和弦判定** 里选「宽松」（相差 80 毫秒内算一起按）；反过来想拆得更细就选「严格」（30 毫秒）。

改了键盘谱样式、音域处理或高级选项后，右侧正在预览的键盘谱会立刻按新设置重新排版，不用重新转换；软件也会记住你的选择。

**谱头写着 ♩=90「只是默认值」？**
这份 PDF 没有读到开头速度，矢量路线用了每分钟 90 拍的默认值。图像路线没有识别到速度时使用 120；默认值不代表识别成功，请对照谱面速度。图片识谱会额外尝试读取首页数字速度，中途变速仍需核对。

**想要 MusicXML（比如导进 MuseScore 接着改）？**
转换完成后点结果条里的 **转 MusicXML**，或者在「记录」里找到那一条，点同名按钮。软件用这次的 MIDI 生成，节奏按键盘谱的格子取整；歌曲录音转出来的 MIDI 本来就不规整，生成的 MusicXML 只能当草稿。生成好的文件留在这次的结果文件夹里。

**转歌曲很慢？**
窗口底部写着「使用 CPU」时，有 NVIDIA 显卡可以切换到 GPU，并按提示下载 GPU 组件（约 2.3 GB，可中途取消、下次接着下）。

**PDF 没认出来？**
先确认 PDF 里是印刷的五线谱或双手简谱。扫描件尽量清晰、端正，截图宽度最好在 1200 像素以上。

**看图识别还要下载模型吗？**
新构建的安装包携带 Copista 28m 的检测器和上下文模型（合计约 124 MB），可离线识别。从源码运行且模型缺失时，首次转换会从 Hugging Face 下载到 `%LOCALAPPDATA%\PianoConvert\copista-28m\`；支持取消和断点续传，加载前校验固定版本的 SHA-256。转换可用 CPU 运行；安装匹配 CUDA 版本的 PyTorch 与 torchvision 时自动使用 GPU。

打谱软件导出的可解析 PDF 仍优先直接读取，双手简谱仍使用专用识别路线。图像五线谱的原始逐页 XML、模型版本和校验记录保存在输出目录 `_omr_raw`；不对 Copista 套用 HOMR 的连线改写规则，也不会把缺页结果当作整曲交付。尚未验证任意来源、手写谱或所有扫描质量。

## 从源码运行

需要 Python 3.10（64 位）和 PowerShell。

```powershell
git clone https://github.com/NotBadSlime/PianoConvert.git
cd PianoConvert
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 读取 PDF / 图片乐谱需要的组件
.venv\Scripts\python -m pip install --ignore-requires-python --no-deps homr==0.7.0
.venv\Scripts\python -m pip install musicxml==1.4 "opencv-python-headless>=4.10,<4.13" "pypdfium2>=4,<6" "rapidocr>=3.7,<4"
# 默认看图识别模型：打包前准备；源码首次使用也可自动下载到用户缓存
.venv\Scripts\python -m app.copista_models --package
# 保留的 HOMR 兼容后端（不作为默认图像引擎；现有打包配置仍携带）
.venv\Scripts\python -m app.homr_models

# 钢琴转录模型 TransKun（权重随包自带；不装它为训练准备的依赖）
.venv\Scripts\python -m pip install --no-deps transkun==2.0.1

# ByteDance 钢琴转录模型（约 165 MB）
.\scripts\download_model.ps1 -Output "models\note_F1=0.9677_pedal_F1=0.9186.pth"

# 启动
.venv\Scripts\python -m app
```

运行测试：

```powershell
.venv\Scripts\python -m pytest -q
```

图像识谱使用 [KobiMusic/copisteria](https://github.com/kobimusic/copisteria) 的 Copista 28m。固定的未修改源代码、版本记录及 Apache-2.0 `LICENSE`、`NOTICE` 位于 `third_party/copisteria/`；本项目适配层在 Python 3.10 上验证推理兼容性，上游声明的安装环境为 Python 3.12 及以上。

打包：

```powershell
.\scripts\build_exe.ps1                       # PyInstaller，输出到 dist\（会先下载看图识别模型，一起打进去）
.\scripts\build_installer.ps1 -Version 1.0.0  # Inno Setup 6 安装包，输出到 installer\output\
```

推送 `v*` 标签后，GitHub Actions 会自动测试、打包，并把安装包发布到 Releases。

## 项目结构

```text
app/
  ui/                界面（PySide6）
  engines/           歌曲转录引擎（TransKun、ByteDance 钢琴模型、Basic Pitch）
  transkun_infer.py  TransKun 钢琴转录（应用和 GPU 组件共用）
  convert.py         转换流程
  keyboard_score.py  键盘谱排版
  pdf_vector.py      读取打谱软件导出的 PDF
  pdf_omr.py         扫描件 / 图片识别
  jianpu.py          简谱识别
piano_transcription_inference/   钢琴转录推理代码
scripts/             下载模型、打包、安装 GPU 组件
installer/           Inno Setup 安装脚本
docs/                说明文档
tests/               自动化测试
```

## 致谢与第三方组件

PianoConvert 使用了这些开源项目（版权和许可证归各自作者所有）：

| 项目 | 用途 | 许可证 |
|---|---|---|
| [TransKun](https://github.com/Yujia-Yan/Transkun) | 钢琴录音转录（默认） | MIT |
| [piano_transcription_inference](https://github.com/qiuqiangkong/piano_transcription_inference) | 钢琴录音转录（ByteDance 模型，可选） | 见原项目 |
| [Basic Pitch](https://github.com/spotify/basic-pitch) | 其他乐器录音转录 | Apache-2.0 |
| [KobiMusic/copisteria — Copista 28m](https://github.com/kobimusic/copisteria) | 印刷五线谱图像识别（默认） | Apache-2.0 |
| [homr](https://github.com/liebharc/homr) | 保留的图像识谱兼容后端 | AGPL-3.0 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | 简谱识别中的文字识别 | Apache-2.0 |
| [music21](https://github.com/cuthbertLab/music21) | MusicXML 读写 | BSD-3-Clause |
| [pretty_midi](https://github.com/craffel/pretty-midi)、[mido](https://github.com/mido/mido) | MIDI 读写 | MIT |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | 读取 PDF | Apache-2.0 / BSD-3-Clause |
| [PySide6](https://doc.qt.io/qtforpython-6/) | 界面 | LGPL-3.0 |
| [PyTorch](https://pytorch.org/)、[ONNX Runtime](https://onnxruntime.ai/)、[librosa](https://librosa.org/)、[OpenCV](https://opencv.org/) | 推理和音频、图像处理 | BSD-3-Clause / MIT / ISC / Apache-2.0 |

安装包内含 homr（AGPL-3.0），其识别模型的训练数据有非商业使用条款，商用前请自行确认相关许可。

《原神》及风物之诗琴是 miHoYo 的商标和游戏内容，本项目与其没有关联。
