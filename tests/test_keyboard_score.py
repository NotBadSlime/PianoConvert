from pathlib import Path

import pretty_midi

from app.keyboard_score import (
    DOT_HIGH,
    DOT_LOW,
    STYLE_SIMPLE,
    KeyboardScoreError,
    MeterMark,
    NoteEvent,
    arrange_notes,
    best_transpose,
    keyboard_text,
    midi_to_keyboard_text,
    parse_keyboard_text,
    pitch_to_key,
    pitch_to_num,
    read_keyboard_cells,
)

# 120 bpm: one quarter note = 0.5 s


def q(quarters: float) -> float:
    return quarters * 0.5


def note(pitch: int, start_q: float, end_q: float, **kw) -> NoteEvent:
    return NoteEvent(pitch, q(start_q), q(end_q), **kw)


def _section(text: str, name: str) -> str:
    body = text.split(f"【{name}】")[1]
    return body.split("【")[0].strip()


def test_pitch_tokens_follow_jianpu_octave_dots():
    assert pitch_to_num(60) == "1"
    assert pitch_to_key(60) == "A"
    assert pitch_to_num(72) == "1" + DOT_HIGH
    assert pitch_to_key(72) == "Q"
    assert pitch_to_num(48) == "1" + DOT_LOW
    assert pitch_to_key(48) == "Z"
    assert pitch_to_num(83) == "7" + DOT_HIGH
    assert pitch_to_key(83) == "U"
    arranged = arrange_notes([NoteEvent(84, 0.0, 0.4)])
    assert arranged[0][1] == [72]


def test_black_key_f_sharp_snaps_to_g():
    arranged = arrange_notes([NoteEvent(66, 0.0, 0.4)])
    assert arranged[0][1] == [67]


def test_d_major_transposes_to_c():
    pitches = [62, 64, 66, 67, 69, 71, 73, 74]
    notes = [NoteEvent(p, i * 0.5, i * 0.5 + 0.4) for i, p in enumerate(pitches)]
    mapped = [group[1][0] for group in arrange_notes(notes)]
    assert mapped == [60, 62, 64, 65, 67, 69, 71, 72]


def test_c_major_piece_is_never_transposed():
    # pentatonic C tune with one F#: all-white a fourth away, but it is already in C
    pitches = [60, 62, 64, 67, 69] * 8 + [66]
    assert best_transpose(pitches) == 0


def test_notes_within_50ms_are_one_chord():
    notes = [NoteEvent(60, 1.000, 1.4), NoteEvent(64, 1.012, 1.4), NoteEvent(67, 1.020, 1.4)]
    assert arrange_notes(notes) == [(1.0, [60, 64, 67])]
    text = keyboard_text(notes, "t", style=STYLE_SIMPLE)
    assert "(135)" in text
    assert "(ADG)" in text


def test_notes_80ms_apart_are_not_a_chord():
    notes = [NoteEvent(60, 1.000, 1.2), NoteEvent(64, 1.080, 1.3)]
    assert len(arrange_notes(notes)) == 2


def test_one_cell_per_beat_for_quarter_notes():
    notes = [note(p, i, i + 0.9) for i, p in enumerate([60, 62, 64, 65])]
    text = keyboard_text(notes, "q")
    assert "每拍 1 格（一格 = 四分音符）" in text
    assert _section(text, "简谱") == "1 | 1 2 3 4 |"
    assert _section(text, "键盘谱") == "1 | A S D F |"


def test_hold_and_rest_are_told_apart():
    # a quarter note, a quarter rest, then a half note
    text = keyboard_text([note(60, 0, 1), note(62, 2, 4)], "rest")
    assert _section(text, "键盘谱") == "1 | A 0 S - |"


def test_articulation_gap_is_not_a_rest():
    # notation software often plays notes 90% long; the gap is not a rest
    notes = [note(60, 0, 0.9), note(62, 1, 2.8), note(64, 3, 3.9)]
    assert _section(keyboard_text(notes, "gap"), "键盘谱") == "1 | A S - D |"


def test_eighth_notes_get_two_cells_per_beat():
    notes = [note(60, 0, 0.4), note(64, 0.5, 0.9), note(67, 2.0, 2.8)]
    text = keyboard_text(notes, "eighth")
    assert "每拍 2 格（一格 = 八分音符）" in text
    assert _section(text, "简谱") == "1 | 13 00 5- 00 |"


def test_loose_timing_keeps_sloppy_notes_on_beats():
    notes = [
        NoteEvent(60, 0.04, 0.4),
        NoteEvent(64, 0.09, 0.4),
        NoteEvent(62, 0.53, 0.9),
        NoteEvent(64, 1.08, 1.4),
        NoteEvent(65, 1.47, 1.9),
    ]
    text = keyboard_text(notes, "slop", strict=False, style=STYLE_SIMPLE)
    assert _section(text, "简谱") == "1 | (13) 2 3 4 |"


def test_tune_and_accompaniment_on_two_rows():
    right = [note(p, i, i + 0.9, part=0) for i, p in enumerate([72, 74, 76, 77])]
    left = [note(48, 0, 1.8, part=1), note(55, 2, 3.8, part=1)]
    text = keyboard_text(right + left, "rows")
    assert _section(text, "键盘谱") == "1 旋律 | Q W E R |\n  伴奏 | Z - B - |"
    high = [f"{d}{DOT_HIGH}" for d in "1234"]
    assert _section(text, "简谱") == f"1 旋律 | {' '.join(high)} |\n  伴奏 | 1{DOT_LOW} - 5{DOT_LOW} - |"
    assert "上面「旋律」" in text
    simple = keyboard_text(right + left, "rows", style=STYLE_SIMPLE)
    assert _section(simple, "键盘谱") == "1 | (ZQ) W (BE) R |"
    assert "上面「旋律」" not in simple


def test_rows_line_up_beat_by_beat():
    right = [note(72, 0, 3.9, part=0)]
    left = [note(p, 0, 0.9, part=1) for p in (48, 52, 55)] + [note(48, 1, 1.9, part=1)]
    lines = _section(keyboard_text(right + left, "align"), "键盘谱").splitlines()
    assert lines == ["1 旋律 | Q     - - - |", "  伴奏 | (ZCB) Z 0 0 |"]


def test_the_tune_holds_over_an_inner_voice():
    # a long C5 in the right hand while the same hand plays a lower E4 under it
    right = [note(72, 0, 2, part=0), note(64, 1, 2, part=0), note(74, 2, 4, part=0)]
    left = [note(48, 0, 4, part=1)]
    rows = _section(keyboard_text(right + left, "inner"), "键盘谱").splitlines()
    assert rows == ["1 旋律 | Q - W - |", "  伴奏 | Z D - - |"]


def test_grace_note_is_a_lowercase_key_before_its_note():
    notes = [note(60, 0, 1), note(62, 0.875, 1.0, grace=True), note(64, 1, 2)]
    text = keyboard_text(notes, "grace")
    assert "每拍 1 格" in text
    assert _section(text, "键盘谱") == "1 | A sD 0 0 |"
    assert _section(text, "简谱") == "1 | 1 ²3 0 0 |"
    assert parse_keyboard_text(text) == [(0.0, [60]), (0.875, [62]), (1.0, [64])]


def test_marked_roll_is_written_in_braces():
    chord = [note(p, 0, 4, arpeggio=True) for p in (48, 52, 55, 60)]
    simple = keyboard_text(chord, "roll", style=STYLE_SIMPLE)
    assert _section(simple, "键盘谱") == "1 | {ZCBA} - - - |"
    rows = _section(keyboard_text(chord, "roll"), "键盘谱").splitlines()
    assert rows == ["1 旋律 | A     - - - |", "  伴奏 | {ZCB} - - - |"]


def test_roll_played_out_in_midi_goes_to_its_beat():
    # notation software plays a rolled chord as notes 1/12 beat apart, ending on the beat
    times = [1 - 3 / 12, 1 - 2 / 12, 1 - 1 / 12, 1.0]
    chord = [note(p, t, 3) for p, t in zip((48, 52, 55, 60), times)]
    text = keyboard_text([note(64, 0, 1)] + chord, "played", style=STYLE_SIMPLE)
    assert "每拍 1 格" in text
    assert _section(text, "键盘谱") == "1 | D {ZCBA} - 0 |"


def test_spread_chord_of_a_performance_is_not_a_roll():
    times = [0.0, 0.11, 0.25, 0.30]  # irregular
    notes = [note(p, t, 2) for p, t in zip((48, 52, 55, 60), times)]
    assert "{" not in _section(keyboard_text(notes, "spread", strict=False, style=STYLE_SIMPLE), "键盘谱")


def test_later_tempo_is_marked_and_keeps_the_grid():
    notes = [NoteEvent(p, i * 0.5, i * 0.5 + 0.4) for i, p in enumerate([60, 62, 64, 65])]
    notes += [NoteEvent(p, 2.0 + i, 2.8 + i) for i, p in enumerate([60, 62, 64, 65])]
    text = keyboard_text(notes, "tempo", [(0.0, 120.0), (2.0, 60.0)], [MeterMark(0.0, 4, 4)])
    assert _section(text, "简谱") == "1 | 1 2 3 4 | 〔♩=60〕 1 2 3 4 |"
    assert "♩=120" in text.splitlines()[1]


def test_later_meter_starts_a_new_bar_length():
    notes = [note(p, i, i + 0.9) for i, p in enumerate([60, 62, 64, 65, 67, 69, 71])]
    text = keyboard_text(notes, "meter", [(0.0, 120.0)], [MeterMark(0.0, 4, 4), MeterMark(4.0, 3, 4)])
    assert _section(text, "简谱") == "1 | 1 2 3 4 | 〔3/4〕 5 6 7 |"


def test_short_first_bar_is_marked_with_its_beats():
    # a MIDI that marks a one-beat pickup by repeating 4/4 after the first beat
    notes = [note(67, 0, 0.9)] + [note(60, 1 + i, 1.9 + i) for i in range(4)]
    text = keyboard_text(notes, "pickup", [(0.0, 120.0)], [MeterMark(0.0, 4, 4), MeterMark(1.0, 4, 4)])
    assert _section(text, "简谱") == "1 | 〔1/4〕 5 | 〔4/4〕 1 1 1 1 |"


def test_number_and_key_sections_match_cell_for_cell():
    notes = [note(p, i * 0.5, i * 0.5 + 0.4) for i, p in enumerate([48, 60, 72, 64, 79, 83])]
    text = keyboard_text(notes, "demo", style=STYLE_SIMPLE)
    num_block = _section(text, "简谱").split("|", 1)[1]
    key_block = _section(text, "键盘谱").split("|", 1)[1]
    table = {"1" + DOT_LOW: "Z", "1": "A", "1" + DOT_HIGH: "Q", "3": "D", "5" + DOT_HIGH: "T", "7" + DOT_HIGH: "U"}
    for num, key in sorted(table.items(), key=lambda item: -len(item[0])):
        num_block = num_block.replace(num, key)
    assert num_block == key_block


def test_lines_start_with_bar_numbers():
    notes = [note(60, i * 4, i * 4 + 0.9) for i in range(9)]
    lines = _section(keyboard_text(notes, "bars"), "键盘谱").splitlines()
    assert [line.split("|")[0].strip() for line in lines] == ["1", "5", "9"]


def test_whole_piece_offset_from_the_beat_is_aligned():
    notes = [NoteEvent(60 + (i % 3) * 2, 0.0675 + i * 0.25, 0.0675 + i * 0.25 + 0.2) for i in range(16)]
    assert "每拍 2 格" in keyboard_text(notes, "late")


def test_keyboard_text_reads_back_from_both_rows():
    notes = [note(60, 0, 0.4), note(64, 0.5, 0.8), note(67, 0.5, 0.8), note(72, 4.25, 4.6)]
    text = keyboard_text(notes, "round")
    assert "旋律" in text and "伴奏" in text
    assert parse_keyboard_text(text) == [(0.0, [60]), (0.5, [64, 67]), (4.25, [72])]
    cells = [c for c in read_keyboard_cells(text) if c.keys]
    assert {(c.q, c.row) for c in cells} == {(0.0, "旋律"), (0.5, "旋律"), (0.5, "伴奏"), (4.25, "旋律")}


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
    assert "【简谱】" in text
    assert "【键盘谱】" in text
    assert "1=C  4/4  ♩=120" in text
    assert "读法" in text and "键位" in text and "0 是不响" in text


def test_midi_tracks_give_tune_and_accompaniment(tmp_path):
    midi = tmp_path / "hands.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    for name, pitches in (("Track 2", [48, 55]), ("Track 1", [72, 76])):
        inst = pretty_midi.Instrument(program=0, name=name)
        for i, p in enumerate(pitches):
            inst.notes.append(pretty_midi.Note(80, p, i * 1.0, i * 1.0 + 0.9))
        pm.instruments.append(inst)
    pm.write(str(midi))
    rows = _section(midi_to_keyboard_text(midi, title="hands"), "键盘谱").splitlines()
    # the higher track has the tune even though it comes second
    assert rows == ["1 旋律 | Q - E - |", "  伴奏 | Z - B - |"]


def test_midi_with_illegal_key_signature_still_converts(tmp_path):
    import mido

    midi = tmp_path / "badkey.mid"
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.MetaMessage("key_signature", key="C", time=0))
    track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    track.append(mido.Message("note_off", note=60, velocity=80, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    mid.save(midi)
    raw = bytearray(midi.read_bytes())
    marker = bytes([0xFF, 0x59, 0x02])
    index = raw.find(marker)
    assert index >= 0
    raw[index + 3] = 14
    midi.write_bytes(raw)
    text = midi_to_keyboard_text(midi, title="badkey")
    assert _section(text, "简谱").startswith("1 | 1")


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

def test_musicxml_grace_notes_and_arpeggio(tmp_path):
    from music21 import chord, expressions, meter, note as m21note, stream

    from app.keyboard_score import score_file_to_keyboard_text

    score = stream.Score()
    right, left = stream.Part(), stream.Part()
    bar = stream.Measure()
    bar.append(meter.TimeSignature("4/4"))
    bar.append(m21note.Note("C5", quarterLength=1))
    bar.append(m21note.Note("D5").getGrace())
    bar.append(m21note.Note("E5", quarterLength=1))
    bar.append(m21note.Note("G5", quarterLength=2))
    right.append(bar)
    low = stream.Measure()
    low.append(meter.TimeSignature("4/4"))
    rolled = chord.Chord(["C3", "E3", "G3"], quarterLength=2)
    rolled.expressions.append(expressions.ArpeggioMark())
    low.append(rolled)
    low.append(m21note.Rest(quarterLength=2))
    left.append(low)
    score.insert(0, right)
    score.insert(0, left)
    path = tmp_path / "marks.musicxml"
    score.write("musicxml", fp=str(path))
    rows = _section(score_file_to_keyboard_text(path, "marks"), "键盘谱").splitlines()
    assert rows == ["1 旋律 | Q     wE T - |", "  伴奏 | {ZCB} -  0 0 |"]

def test_stray_meter_restatement_does_not_cut_a_bar():
    # an exported MIDI restates 4/4 a few ticks after beat 3 of the last bar
    notes = [note(p, i, i + 0.9) for i, p in enumerate([60, 62, 64, 65])] + [note(62, 6.06, 7)]
    meters = [MeterMark(0.0, 4, 4), MeterMark(6.069, 4, 4)]
    text = keyboard_text(notes, "stray", [(0.0, 120.0)], meters)
    assert _section(text, "键盘谱") == "1 | A S D F | 0 0 S 0 |"


def test_meter_change_a_few_ticks_off_the_beat_goes_on_the_beat():
    notes = [note(60, i, i + 0.9) for i in range(7)]
    text = keyboard_text(notes, "snap", [(0.0, 120.0)], [MeterMark(0.0, 4, 4), MeterMark(4.03, 3, 4)])
    assert _section(text, "键盘谱") == "1 | A A A A | 〔3/4〕 A A A |"


def test_rolled_octave_folded_onto_one_key_is_a_plain_key():
    chord = [note(74, 0, 2, arpeggio=True), note(86, 0, 2, arpeggio=True)]  # D5 + D6 -> W
    assert _section(keyboard_text(chord, "octave", style=STYLE_SIMPLE), "键盘谱") == "1 | W - 0 0 |"

def test_roll_ending_with_the_other_hand_is_still_a_roll():
    # E4 A4 C5 rolled into D5 on beat 2, while the left hand plays F3 on that beat
    times = [1 - 3 / 12, 1 - 2 / 12, 1 - 1 / 12, 1.0]
    roll = [note(p, t, 3, part=0) for p, t in zip((64, 69, 72, 74), times)]
    text = keyboard_text([note(60, 0, 0.9, part=0), note(53, 1.0, 3, part=1)] + roll, "hands", style=STYLE_SIMPLE)
    assert _section(text, "键盘谱") == "1 | A {VDHQW} - 0 |"


def test_both_hands_rolling_together_is_one_roll():
    steps = [0.0, 1 / 12, 2 / 12]
    notes = [note(p, t, 2) for t, pair in zip(steps, ((48, 60), (52, 64), (55, 67))) for p in pair]
    assert _section(keyboard_text(notes, "both", style=STYLE_SIMPLE), "键盘谱") == "1 | {ZCBADG} - 0 0 |"


def test_simple_style_rolls_only_rolled_keys():
    # an octave rolled onto one key (C3 + C4 -> Z ... A) plus an unrolled tune note
    rolled = [note(36, 0, 2, arpeggio=True), note(48, 0, 2, arpeggio=True)]  # both fold to Z
    text = keyboard_text(rolled + [note(76, 0, 2)], "octave", style=STYLE_SIMPLE)
    assert _section(text, "键盘谱") == "1 | (ZE) - 0 0 |"


def test_tune_part_silent_in_a_bar_lets_the_next_part_carry_the_tune():
    # bar 1: only the piano right hand plays; bar 2: the voice comes in
    voice = [note(76, 4, 7.9, part=0)]
    right = [note(72, i, i + 0.9, part=1) for i in range(8)]
    left = [note(48, 0, 7.9, part=2)]
    rows = _section(keyboard_text(voice + right + left, "intro"), "键盘谱").splitlines()
    assert rows[0] == "1 旋律 | Q Q Q Q | E - - - |"
    assert rows[1] == "  伴奏 | Z - - - | Q Q Q Q |"


def test_short_ornament_does_not_take_the_tune_slot():
    # sixteenths, then a 32nd D5 squeezed into the cell of the C5 it decorates
    notes = [note(60 + i % 3, i * 0.25, i * 0.25 + 0.2) for i in range(40)]
    notes += [note(74, 9.875, 10.0), note(72, 10, 11)]
    text = keyboard_text(notes, "orn")
    assert "每拍 4 格" in text
    at = {c.row: c.keys for c in read_keyboard_cells(text) if abs(c.q - 10.0) < 1e-6}
    assert at == {"旋律": [72], "伴奏": [74]}


def test_accompaniment_copy_of_a_tune_key_is_held_in_the_tune_row():
    right = [note(72, 0, 1, part=0), note(74, 1, 2, part=0)]
    left = [note(72, 0, 4, part=1), note(48, 2, 4, part=1)]  # the same C5 held in the left hand
    rows = _section(keyboard_text(right + left, "dup"), "键盘谱").splitlines()
    # the left hand's C5 is not written again; it keeps the tune's Q sounding instead
    assert rows == ["1 旋律 | Q W - - |", "  伴奏 | 0 0 Z - |"]


def test_pickup_in_its_own_meter_keeps_the_header_meter():
    notes = [note(67, 0, 0.25)] + [note(60, 0.25 + i, 1.15 + i) for i in range(4)]
    meters = [MeterMark(0.0, 1, 16), MeterMark(0.25, 4, 4)]
    text = keyboard_text(notes, "pickup16", [(0.0, 120.0)], meters)
    assert "  4/4  " in text.splitlines()[1]
    assert "〔1/16〕" in text and "〔4/4〕" in text


def test_sixteenths_in_an_eighth_note_piece_get_their_own_cells():
    # mostly eighths, but 4 % of the onsets are sixteenths that would share a cell
    notes = [note(60 + (i % 5), i * 0.5, i * 0.5 + 0.4) for i in range(96)]
    notes += [note(72, i * 12 + 0.25, i * 12 + 0.45) for i in range(4)]
    assert "每拍 4 格" in keyboard_text(notes, "sixteenths")


def test_later_track_tempo_at_the_same_tick_wins(tmp_path):
    import mido

    midi = tmp_path / "tempo.mid"
    mid = mido.MidiFile(ticks_per_beat=480)
    meta, notes_track = mido.MidiTrack(), mido.MidiTrack()
    mid.tracks += [meta, notes_track]
    meta.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(120), time=0))
    notes_track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(80), time=0))
    for i in range(4):
        notes_track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        notes_track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.save(midi)
    assert "♩=80" in midi_to_keyboard_text(midi, title="tempo").splitlines()[1]


def test_low_track_named_voice_is_not_the_tune(tmp_path):
    midi = tmp_path / "named.mid"
    pm = pretty_midi.PrettyMIDI(initial_tempo=120)
    for name, pitch in (("Voice", 45), ("Piano", 76)):
        inst = pretty_midi.Instrument(program=0, name=name)
        for i in range(4):
            inst.notes.append(pretty_midi.Note(80, pitch, i * 0.5, i * 0.5 + 0.45))
        pm.instruments.append(inst)
    pm.write(str(midi))
    rows = _section(midi_to_keyboard_text(midi, title="named"), "键盘谱").splitlines()
    assert rows[0] == "1 旋律 | E E E E |"

def test_arpeggio_setting_changes_how_rolls_are_written():
    times = [1 - 3 / 12, 1 - 2 / 12, 1 - 1 / 12, 1.0]
    chord = [note(p, t, 3) for p, t in zip((48, 52, 55, 60), times)]
    notes = [note(64, 0, 1)] + chord
    auto = keyboard_text(notes, "a", style=STYLE_SIMPLE)
    together = keyboard_text(notes, "a", style=STYLE_SIMPLE, arpeggio="chord")
    spread = keyboard_text(notes, "a", style=STYLE_SIMPLE, arpeggio="spread")
    assert _section(auto, "键盘谱") == "1 | D {ZCBA} - 0 |"
    assert _section(together, "键盘谱") == "1 | D (ZCBA) - 0 |"
    assert "{" not in _section(spread, "键盘谱") and "每拍 1 格" not in spread   # each note in its own cell


def test_chord_setting_changes_what_counts_as_together():
    # sixteenths, then a performed chord whose two notes are struck 70 ms apart, across a cell line
    notes = [note(60, i * 0.25, i * 0.25 + 0.2) for i in range(16)]
    notes += [note(64, 4.10, 5.0), note(67, 4.24, 5.0)]
    standard = keyboard_text(notes, "c", strict=False, style=STYLE_SIMPLE)
    loose = keyboard_text(notes, "c", strict=False, style=STYLE_SIMPLE, chord="loose")
    assert "(DG)" in loose
    assert "(DG)" not in standard and "DG" in standard

def test_a_triplet_beat_gets_cells_of_its_own():
    # 16ths, then one beat of eighth-note triplets, then 16ths again
    notes = [note(60 + i % 3, i * 0.25, i * 0.25 + 0.2) for i in range(8)]
    notes += [note(64, 2 + i / 3, 2 + i / 3 + 0.3) for i in range(3)]
    notes += [note(65, 3 + i * 0.25, 3 + i * 0.25 + 0.2) for i in range(4)]
    text = keyboard_text(notes, "triplet")
    assert "每拍 4 格" in text
    assert "〈3〉" in text and "〈3〉这类记号" in text
    onsets = sorted({round(q, 4) for q, _keys in parse_keyboard_text(text)})
    assert [2.0, 2.3333, 2.6667] == [q for q in onsets if 2 <= q < 3]
    assert [3.0, 3.25, 3.5, 3.75] == [q for q in onsets if q >= 3]


def test_a_quintuplet_beat_and_the_beats_after_it_keep_their_times():
    notes = [note(60, i * 0.5, i * 0.5 + 0.4) for i in range(2)]
    notes += [note(62 + i % 2, 1 + i / 5, 1 + i / 5 + 0.15) for i in range(5)]
    notes += [note(67, 2, 4)]
    text = keyboard_text(notes, "quint")
    assert "〈5〉" in text
    onsets = sorted({round(q, 4) for q, _keys in parse_keyboard_text(text)})
    assert onsets == [0.0, 0.5, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]


def test_a_lone_ornament_does_not_split_its_beat():
    # a single 32nd squeezed before a beat is written in its neighbour's cell, not as a split beat
    notes = [note(60 + i % 3, i * 0.25, i * 0.25 + 0.2) for i in range(16)]
    notes += [note(74, 3.875, 4.0), note(72, 4, 5)]
    text = keyboard_text(notes, "orn")
    assert "〈" not in text


def test_the_musicxml_grid_includes_split_beats():
    from app.convert import _grid_divisors

    head = "# 1=C  4/4  ♩=90  每拍 4 格（一格 = 十六分音符）\n【键盘谱】\n 1 | ABCD 〈3〉ABC 〈5〉ABCDE ABCD |\n"
    assert _grid_divisors(head) == (3, 4, 5)


def test_exact_thirty_seconds_are_not_approximated_as_sextuplets():
    # A mostly-16th score has one beat at 0, 3/8 and 7/8 quarters.
    # Six cells look close, but move both offbeat notes by 1/24 quarter.
    notes=[note(60,i*.25,i*.25+.2) for i in range(160) if not 20<i*.25<21]
    notes += [note(62,20.375,20.6),note(67,20.875,21)]
    text=keyboard_text(notes,'exact',style=STYLE_SIMPLE)
    assert '每拍 4 格' in text
    assert '〈8〉' in text
    assert [q for q,_ in parse_keyboard_text(text) if 20<=q<21] == [20,20.375,20.875]


def test_notated_grid_uses_the_closest_fallback_when_no_small_denominator_is_exact():
    from app.keyboard_score import Beat,_split_tuplet_beats
    import math

    beat=Beat(0,1,4)
    times=[0,.2,.37,.59,.86]
    _split_tuplet_beats([[beat]],times,4,exact_timing=True)
    snapped=[math.floor(q*beat.cells+.5)/beat.cells for q in times]
    assert len(set(snapped)) == len(times)
    assert max(abs(q-s) for q,s in zip(times,snapped)) < .03


def test_unrepresentable_close_attacks_are_not_stretched_to_distant_cells():
    from app.keyboard_score import Beat,_split_tuplet_beats
    import math

    beat=Beat(0,1,4)
    times=[.123,.128]
    _split_tuplet_beats([[beat]],times,4,exact_timing=True)
    snapped=[math.floor(q*beat.cells+.5)/beat.cells for q in times]
    assert max(abs(q-s) for q,s in zip(times,snapped)) <= 1/32


def test_two_written_voices_keep_eighth_and_twelfth_subdivisions_in_one_beat():
    from app.keyboard_score import Beat,_split_tuplet_beats
    import math

    beat=Beat(0,1,8)
    times=sorted({i/8 for i in range(8)}|{i/12 for i in range(12)})
    _split_tuplet_beats([[beat]],times,8,exact_timing=True)
    snapped=[math.floor(q*beat.cells+.5)/beat.cells for q in times]
    assert len(set(snapped)) == len(times)
    assert max(abs(q-s) for q,s in zip(times,snapped)) < 1e-8
