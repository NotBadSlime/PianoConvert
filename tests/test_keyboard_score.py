from pathlib import Path

import pretty_midi

from app.keyboard_score import (
    KeyboardScoreError,
    NoteEvent,
    arrange_notes,
    midi_to_keyboard_text,
    pitch_to_key,
    pitch_to_num,
    render_score,
)


def test_pitch_tokens_c4_c5_c3_c6():
    assert pitch_to_num(60) == "1"
    assert pitch_to_key(60) == "A"
    assert pitch_to_num(72) == "+1"
    assert pitch_to_key(72) == "Q"
    assert pitch_to_num(48) == "-1"
    assert pitch_to_key(48) == "Z"
    arranged = arrange_notes([NoteEvent(84, 0.0, 0.4)])
    assert arranged[0][1] == [72]
    assert pitch_to_num(72) == "+1"
    assert pitch_to_key(72) == "Q"


def test_black_key_f_sharp_snaps_to_g():
    arranged = arrange_notes([NoteEvent(66, 0.0, 0.4)])
    assert arranged[0][1] == [67]
    assert pitch_to_num(67) == "5"
    assert pitch_to_key(67) == "G"


def test_d_major_transposes_to_c():
    pitches = [62, 64, 66, 67, 69, 71, 73, 74]
    notes = [NoteEvent(p, i * 0.5, i * 0.5 + 0.4) for i, p in enumerate(pitches)]
    arranged = arrange_notes(notes)
    mapped = [group[1][0] for group in arranged]
    assert mapped == [60, 62, 64, 65, 67, 69, 71, 72]


def test_cluster_three_tracks_within_50ms():
    notes = [
        NoteEvent(60, 1.000, 1.4),
        NoteEvent(64, 1.012, 1.4),
        NoteEvent(67, 1.020, 1.4),
    ]
    arranged = arrange_notes(notes)
    assert len(arranged) == 1
    assert arranged[0][1] == [60, 64, 67]
    text = render_score(arranged, beat_sec=0.5, beats_per_bar=4, title="t")
    assert "(135)" in text
    assert "(ADG)" in text


def test_notes_80ms_apart_are_not_a_chord():
    notes = [NoteEvent(60, 1.000, 1.2), NoteEvent(64, 1.080, 1.3)]
    arranged = arrange_notes(notes)
    assert len(arranged) == 2


def test_sloppy_onsets_snap_onto_the_beat_for_keyboard_score():
    notes = [
        NoteEvent(60, 0.04, 0.4),
        NoteEvent(64, 0.09, 0.4),
        NoteEvent(62, 0.53, 0.9),
        NoteEvent(64, 1.08, 1.4),
        NoteEvent(65, 1.47, 1.9),
    ]
    text = render_score(arrange_notes(notes), beat_sec=0.5, beats_per_bar=4, title="slop")
    num = text.split("【数字谱】")[1].split("【键盘谱】")[0].strip()
    assert num.startswith("(13)  2  3  4")


def test_eighth_notes_stay_separate():
    notes = [NoteEvent(60, 0.0, 0.2), NoteEvent(64, 0.25, 0.45)]
    text = render_score(arrange_notes(notes), beat_sec=0.5, beats_per_bar=4, title="eighth")
    num = text.split("【数字谱】")[1].split("【键盘谱】")[0].strip()
    assert num.startswith("1 3")


def test_later_tempo_keeps_quarter_spacing_and_marks_bpm():
    from app.keyboard_score import MeterMark, render_score

    notes = [
        NoteEvent(60, 0.0, 0.4),
        NoteEvent(62, 0.5, 0.9),
        NoteEvent(64, 1.0, 1.4),
        NoteEvent(65, 1.5, 1.9),
        NoteEvent(60, 2.0, 2.8),
        NoteEvent(62, 3.0, 3.8),
        NoteEvent(64, 4.0, 4.8),
        NoteEvent(65, 5.0, 5.8),
    ]
    text = render_score(
        arrange_notes(notes),
        0.5,
        4,
        "tempo",
        [(0.0, 120.0), (2.0, 60.0)],
        [MeterMark(0.0, 4, 4)],
    )
    num = text.split("【数字谱】")[1].split("【键盘谱】")[0].strip()
    assert num == "1  2  3  4 / 〔60拍〕1  2  3  4 /"


def test_later_meter_starts_a_new_bar_length():
    from app.keyboard_score import MeterMark, render_score

    notes = [NoteEvent(p, i * 0.5, i * 0.5 + 0.4) for i, p in enumerate([60, 62, 64, 65, 67, 69, 71])]
    text = render_score(
        arrange_notes(notes),
        0.5,
        4,
        "meter",
        [(0.0, 120.0)],
        [MeterMark(0.0, 4, 4), MeterMark(4.0, 3, 4)],
    )
    num = text.split("【数字谱】")[1].split("【键盘谱】")[0].strip()
    assert "〔3/4〕" in num
    assert num.startswith("1  2  3  4 / 〔3/4〕")


def test_bar_layout_matches_between_number_and_key():
    notes = [
        NoteEvent(60, 0.0, 0.4),
        NoteEvent(62, 0.5, 0.9),
        NoteEvent(64, 1.0, 1.4),
        NoteEvent(65, 1.5, 1.9),
    ]
    arranged = arrange_notes(notes)
    text = render_score(arranged, beat_sec=0.5, beats_per_bar=4, title="demo")
    num_block = text.split("【数字谱】")[1].split("【键盘谱】")[0].strip()
    key_block = text.split("【键盘谱】")[1].strip()
    assert "/" in num_block and "/" in key_block
    assert num_block.replace("1", "A").replace("2", "S").replace("3", "D").replace("4", "F") == key_block


def _write_midi(path: Path, notes: list[tuple[int, float, float]], drums: bool = False) -> None:
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    inst = pretty_midi.Instrument(program=0, is_drum=drums)
    for pitch, start, end in notes:
        inst.notes.append(pretty_midi.Note(velocity=80, pitch=pitch, start=start, end=end))
    pm.instruments.append(inst)
    pm.write(str(path))


def test_midi_to_keyboard_text_headers(tmp_path):
    midi = tmp_path / "song.mid"
    _write_midi(midi, [(60, 0.0, 0.4), (62, 0.5, 0.9)])
    text = midi_to_keyboard_text(midi, title="song")
    assert "【数字谱】" in text
    assert "【键盘谱】" in text
    assert "1=C" in text


def test_empty_midi_raises(tmp_path):
    midi = tmp_path / "empty.mid"
    _write_midi(midi, [])
    try:
        midi_to_keyboard_text(midi, title="empty")
        assert False, "expected error"
    except KeyboardScoreError as exc:
        assert "没有可转换的音符" in str(exc)


def test_skips_drum_track(tmp_path):
    midi = tmp_path / "drums.mid"
    _write_midi(midi, [(36, 0.0, 0.2)], drums=True)
    try:
        midi_to_keyboard_text(midi, title="drums")
        assert False, "expected error"
    except KeyboardScoreError as exc:
        assert "没有可转换的音符" in str(exc)
