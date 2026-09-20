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
