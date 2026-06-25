from piano_transcription_inference import PianoTranscription, sample_rate, load_audio
import os

directory = './Input'

file_lists = os.listdir(directory)

music_num = len(file_lists)

print("转换的歌曲数量:" + str(music_num))

for i in file_lists:
    file_path = './Input/' + i
    output_path = './Output/' + i[:-4] + ".mid"
    (audio, _) = load_audio(file_path, sr=sample_rate, mono=True)
    
    
    # Transcriptor
    transcriptor = PianoTranscription(device='cuda')    # 'cuda' | 'cpu'
    
    # Transcribe and write out to MIDI file
    transcribed_dict = transcriptor.transcribe(audio, output_path)
    
 
print("已转换完成全部音乐!")




