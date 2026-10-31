from scripts.rhythm_benchmark import best_offset, f1, match_notes, timing_metrics, midi_data


def test_matching_never_reuses_a_reference_note():
    ref = [(0, 1, 60)]
    est = [(0, 1, 60), (.01, 1, 60)]
    assert len(match_notes(ref, est)) == 1
    assert f1(ref, est) == 2/3


def test_one_global_offset_does_not_hide_drift():
    ref = [(q, q+1, 60) for q in range(10)]
    est = [(q+2+(q>=5)*.2, q+3+(q>=5)*.2, 60) for q in range(10)]
    off = best_offset(ref, est)
    assert off == -2
    assert f1(ref, est, off, .03) == .5


def test_pitch_and_duration_are_checked_independently():
    ref = [(0, 1, 60), (1, 2, 62)]
    est = [(0, .5, 60), (1, 2, 62)]
    metrics = timing_metrics(ref, est)
    assert metrics['f1_0.03'] == 1
    assert metrics['matched_duration_count'] == 2
    assert metrics['release_within_0125q_or_20pct'] == .5


def test_unordered_input_and_different_pitches_do_not_cross_match():
    ref = [(1, 2, 60), (0, 1, 62)]
    est = [(0, 1, 60), (1, 2, 62)]
    assert f1(ref, est) == 0


def test_matching_maximizes_count_even_when_closest_first_would_lose_a_pair():
    ref = [(0, 1, 60), (.04, 1, 60)]
    est = [(.025, 1, 60), (.065, 1, 60)]
    assert len(match_notes(ref, est, tolerance=.03)) == 2


def test_later_track_tempo_at_same_tick_is_not_sorted_by_bpm(tmp_path):
    import mido

    mid = mido.MidiFile()
    for bpm in (120,140):
        track = mido.MidiTrack()
        track.append(mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(bpm)))
        mid.tracks.append(track)
    path = tmp_path/'tempo.mid'
    mid.save(path)
    _, tempos = midi_data(path)
    assert len(tempos) == 1
    assert abs(tempos[0][1]-140)<.001
