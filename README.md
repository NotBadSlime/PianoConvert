# PianoConvert

离线音频转 MIDI + MusicXML。钢琴用 Kong 模型，其他乐器用 basic-pitch。

开发时请将 Kong 模型 checkpoint 从安装目录复制到 `models/`：

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

1. `gh auth login`（本机若未登录 GitHub CLI）
2. 在 GitHub 创建公开仓库 `PianoConvert`，然后 `git remote add origin https://github.com/<user>/PianoConvert.git`
3. `git tag v0.2.0 && git push origin main --tags`

推送 `v*` 标签后，GitHub Actions 会运行测试、打包 PyInstaller 并编译 Inno Setup 安装包，将 `PianoConvertSetup-<version>.exe` 挂到对应 Release。

也可在 Actions 页手动触发 **Release Installer** workflow，填写版本号（如 `0.2.0`）。

### 本地打包

不依赖 GitHub Actions 时，在本机执行：

```powershell
.\scripts\build_installer.ps1
```

脚本会调用 `build_exe.ps1`（PyInstaller）并用 Inno Setup 生成 `installer\output\PianoConvertSetup-<version>.exe`。打包前需确保 `models\` 下已有 Kong checkpoint（见上文复制命令）。

### 模型权重与 CI

`models/*.pth` 已加入 `.gitignore`（约 164 MB），不会随 git 推送。`app.spec` 打包时需要该文件，因此：

- **本地**：`scripts\build_installer.ps1` 使用本机 `models\*.pth`，按上文从 `E:\PianoConvert\_internal\piano_transcription_inference_data\` 复制即可。
- **GitHub Actions**：runner 上没有 checkpoint 时，PyInstaller 步骤会失败。在提供权重之前，CI 安装包 job 无法成功。可选做法包括：将 `.pth` 作为 Release 资产上传后在 workflow 中下载到 `models/`，或使用 `actions/cache` 缓存已上传的权重——当前 workflow **尚未**配置自动下载；需自行补充下载步骤或把文件放入 `models/` 后再触发构建。
