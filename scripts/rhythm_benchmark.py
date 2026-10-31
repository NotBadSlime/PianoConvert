"""Reproducible local PDF rhythm audit; originals are read-only, results are resumable.

inventory OUT --pdf-dir DIR --random-root DIR --midi-dir DIR
run MANIFEST OUT --root CODE_ROOT [--kind vector|omr] [--workers N]
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from multiprocessing import Pool
from pathlib import Path
import sys
import time


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def classify(item):
    from app.pdf_vector import is_vector_score
    import pypdfium2 as pdfium

    path = Path(item['pdf'])
    row = dict(item)
    try:
        row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        doc = pdfium.PdfDocument(path)
        row['pages'] = len(doc)
        doc.close()
        row['kind'] = 'vector' if is_vector_score(path) else 'omr'
    except Exception as exc:
        row['error'] = str(exc)[:300]
    return row


def inventory(args):
    root = str(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, root)
    items = []
    midi_dir = Path(args.midi_dir)
    for pdf in sorted(Path(args.pdf_dir).glob('*.pdf')):
        mid = midi_dir / (pdf.stem + '.mid')
        items.append(dict(pdf=str(pdf.resolve()), group='user_library',
                          midi=str(mid.resolve()) if mid.exists() else None, xml=None))
    for batch in ('random1', 'random2', 'msrand1', 'msrand2', 'msrand3'):
        for folder in sorted((Path(args.random_root) / batch).glob('*')):
            if not folder.is_dir():
                continue
            mids = list(folder.glob('*.mid'))
            xmls = [p for p in folder.glob('*.musicxml') if '由MIDI生成' not in p.name]
            for pdf in sorted(folder.glob('*.pdf')):
                name = pdf.stem.removeprefix('selftest-')
                mid = next((m for m in mids if m.stem == name), None)
                xml = next((x for x in xmls if x.stem == name), None)
                items.append(dict(pdf=str(pdf.resolve()), group=batch,
                                  midi=str(mid.resolve()) if mid else None,
                                  xml=str(xml.resolve()) if xml else None))
    rows = []
    with Pool(args.workers, initializer=init_worker, initargs=(root,), maxtasksperchild=30) as pool:
        for row in pool.imap_unordered(classify, items):
            rows.append(row)
            if len(rows) % 200 == 0:
                print(f'inventory {len(rows)}/{len(items)}', flush=True)
    rows.sort(key=lambda r: (r['group'], r['pdf']))
    write_json(args.out, rows)
    print(json.dumps(dict(files=len(rows), unique=len({r.get('sha256') for r in rows}),
                          groups=dict(Counter(r['group'] for r in rows)),
                          kinds=dict(Counter(r.get('kind', 'error') for r in rows)),
                          with_midi=sum(bool(r['midi']) for r in rows)), ensure_ascii=True), flush=True)


def init_worker(root):
    sys.path.insert(0, str(root))


def midi_data(path):
    import mido
    from app.keyboard_score import _enable_lenient_key_signatures

    _enable_lenient_key_signatures()
    mid = mido.MidiFile(str(path))
    notes, tempos = [], []
    for track in mid.tracks:
        tick = 0
        active = defaultdict(list)
        for msg in track:
            tick += msg.time
            q = tick / mid.ticks_per_beat
            if msg.type == 'set_tempo':
                tempos.append((q, 60e6 / msg.tempo))
            if msg.type not in ('note_on', 'note_off') or msg.channel == 9:
                continue
            key = msg.channel, msg.note
            if msg.type == 'note_on' and msg.velocity:
                active[key].append(q)
            elif active[key]:
                notes.append((active[key].pop(0), q, msg.note))
    # At a shared tick, later-track metadata wins, as in the app's MIDI reader.
    return sorted(notes), sorted(dict(tempos).items())


def match_notes(ref, est, offset=0.0, tolerance=.03):
    """Maximum ordered one-to-one onset matching, independently for each pitch."""
    rp, ep = defaultdict(list), defaultdict(list)
    for i, n in enumerate(ref):
        rp[n[2]].append((n[0], i))
    for i, n in enumerate(est):
        ep[n[2]].append((n[0]+offset, i))
    pairs = []
    for pitch, r in rp.items():
        e = ep.get(pitch, [])
        r.sort()
        e.sort()
        i = j = 0
        while i < len(r) and j < len(e):
            delta = r[i][0] - e[j][0]
            if abs(delta) <= tolerance + 1e-8:
                pairs.append((r[i][1], e[j][1]))
                i += 1
                j += 1
            elif delta < 0:
                i += 1
            else:
                j += 1
    return pairs


def f1(ref, est, offset=0.0, tolerance=.03):
    hits = len(match_notes(ref, est, offset, tolerance))
    return 2 * hits / (len(ref) + len(est)) if ref or est else 1.0


def best_offset(ref, est):
    if not ref or not est:
        return 0.0
    # One global shift only; local realignment would hide accumulated rhythm errors.
    counts = Counter(round(r[0]-e[0], 5) for r in ref[:16] for e in est[:16] if r[2] == e[2])
    candidates = {0.0} | {q for q, _ in counts.most_common(10)}
    return max(candidates, key=lambda q: (f1(ref, est, q, .03), f1(ref, est, q, .26), -abs(q)))


def timing_metrics(ref, est):
    offset = best_offset(ref, est)
    out = {'reference_notes':len(ref), 'estimate_notes':len(est), 'global_offset_q':offset}
    for tol in (.03, .06, .26):
        out[f'f1_{tol}'] = round(f1(ref, est, offset, tol), 6)
    pairs = match_notes(ref, est, offset, .03)
    if pairs:
        import statistics
        errors = [abs(ref[i][1] - (est[j][1]+offset)) for i,j in pairs]
        out['matched_duration_count'] = len(pairs)
        out['release_median_error_q'] = statistics.median(errors)
        out['release_within_0125q_or_20pct'] = sum(
            error <= max(.125, .2*(ref[i][1]-ref[i][0])) + 1e-8
            for (i,j), error in zip(pairs, errors)
        ) / len(pairs)
    return out


def onset_only(notes):
    return [(q, q, 0) for q in sorted({round(n[0], 6) for n in notes})]


def evaluate(task):
    item, out = task
    completed = Path(out)/'results'/(item['sha256']+'.json')
    if completed.exists():
        return json.loads(completed.read_text(encoding='utf-8'))
    from threading import Event
    from app import keyboard_score as ks
    from app import pdf_vector as pv

    start = time.monotonic()
    folder = Path(out) / 'artifacts' / item['sha256'][:20]
    folder.mkdir(parents=True, exist_ok=True)
    row = dict(item)
    try:
        if item['kind'] == 'vector':
            limit = None if item.get('read_all_pages') else 40
            score = pv.read_vector_pdf(Path(item['pdf']), limit, Event())
            midi = pv.write_midi(score, folder/'score.mid')
            raw = sorted((float(n.start_q), float(n.end_q), n.pitch) for n in score.notes if not n.grace)
            text = ks.score_file_to_keyboard_text(midi, Path(item['pdf']).stem)
            (folder/'keyboard.txt').write_text(text, encoding='utf-8')
            tempos = [(0, score.bpm)] + list(score.tempos)
            row['tempo_printed'] = score.tempo_printed
            row['bars'] = score.bars
            unique = list({id(b):b for _,b in score.timeline}.values())
            anomalies = []
            for i, bar in enumerate(unique):
                if i in (0, len(unique)-1) or bar.final or bar.repeat_end:
                    continue
                expected = bar.meter[0]*4/bar.meter[1]
                bad = [(staff, float(total)) for staff,total in getattr(bar,'totals',[]) if abs(total-expected)>.001]
                if bad:
                    anomalies.append(dict(page=bar.page+1, system=bar.system+1, x=bar.x0,
                                          expected_q=expected, staff_totals=bad))
            row['suspect_bars_count'] = len(anomalies)
            row['suspect_bars_examples'] = anomalies[:12]
            row['pages_tested'] = item['pages'] if limit is None else min(item['pages'],40)
        else:
            from app.convert import run_pdf
            result = run_pdf(Path(item['pdf']), Event(), output_root=folder)
            if result.status != 'success':
                raise RuntimeError(result.error or result.status)
            text = result.keyboard_path.read_text(encoding='utf-8')
            if result.musicxml_path.exists():
                events, timeline, _ = ks.notes_from_musicxml(result.musicxml_path)
                raw = sorted((ks.seconds_to_quarters(n.start,timeline),
                              ks.seconds_to_quarters(n.end,timeline),n.pitch) for n in events if not n.grace)
                tempos = [(ks.seconds_to_quarters(t,timeline),bpm) for t,bpm in timeline]
            else:
                raw, tempos = midi_data(result.midi_path)
            row['conversion_folder'] = str(result.folder.resolve())
        raw = sorted(set(raw))
        kb = [(q,q,p) for q, pitches in ks.parse_keyboard_text(text) for p in pitches]
        row['reader_notes'] = len(raw)
        row['keyboard_attacks'] = len(kb)
        row['reader_tempos'] = tempos
        row['reader_end_q'] = max((n[1] for n in raw),default=0)
        row['keyboard_end_q'] = max((c.q+c.slot for c in ks.read_keyboard_cells(text)),default=0)
        write_json(folder/'reader-notes.json', raw)
        if item.get('midi'):
            ref, ref_tempos = midi_data(item['midi'])
            ref = sorted(set(ref))
            row['reader_vs_reference'] = timing_metrics(ref,raw)
            row['onsets_vs_reference'] = timing_metrics(onset_only(ref),onset_only(raw))
            row['keyboard_onsets_vs_reference'] = timing_metrics(onset_only(ref),onset_only(kb))
            # The reader-to-keyboard comparison isolates extra errors in grid layout.
            row['keyboard_onsets_vs_reader'] = timing_metrics(onset_only(raw),onset_only(kb))
            row['reference_tempos'] = ref_tempos
            row['reference_end_q'] = max((n[1] for n in ref),default=0)
        row['status'] = 'success'
    except Exception as exc:
        import traceback
        row.update(status='failed', error=str(exc)[:400], traceback=traceback.format_exc()[-1800:])
    row['seconds'] = round(time.monotonic()-start,2)
    write_json(Path(out)/'results'/(item['sha256']+'.json'),row)
    return row


def rerender(task):
    item, source, out = task
    from app import keyboard_score as ks

    started = time.monotonic()
    source = Path(source)
    row = json.loads((source/'results'/(item['sha256']+'.json')).read_text(encoding='utf-8'))
    folder = source/'artifacts'/item['sha256'][:20]
    target = Path(out)/'artifacts'/item['sha256'][:20]
    target.mkdir(parents=True,exist_ok=True)
    try:
        raw = json.loads((folder/'reader-notes.json').read_text(encoding='utf-8'))
        text = ks.score_file_to_keyboard_text(folder/'score.mid',Path(item['pdf']).stem)
        (target/'keyboard.txt').write_text(text,encoding='utf-8')
        kb = [(q,q,p) for q, ps in ks.parse_keyboard_text(text) for p in ps]
        row['keyboard_attacks'] = len(kb)
        row['keyboard_end_q'] = max((c.q+c.slot for c in ks.read_keyboard_cells(text)),default=0)
        row['keyboard_onsets_vs_reader'] = timing_metrics(onset_only(raw),onset_only(kb))
        if item.get('midi'):
            ref,_ = midi_data(item['midi'])
            row['keyboard_onsets_vs_reference'] = timing_metrics(onset_only(ref),onset_only(kb))
        row['reader_artifact_reused'] = str(folder.resolve())
        row['status'] = 'success'
    except Exception as exc:
        row.update(status='failed',error=str(exc)[:400])
    row['seconds'] = round(time.monotonic()-started,2)
    write_json(Path(out)/'results'/(item['sha256']+'.json'),row)
    return row


def run(args):
    rows = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    selected = [r for r in rows if r.get('kind') == args.kind]
    if args.all_pages:
        selected = [dict(r,read_all_pages=True) for r in selected]
    if args.limit:
        selected = selected[:args.limit]
    out = Path(args.out)
    if args.cached_from:
        selected = [r for r in selected if (Path(args.cached_from)/'results'/(r['sha256']+'.json')).exists()]
    pending = [r for r in selected if not (out/'results'/(r['sha256']+'.json')).exists()]
    write_json(out/'run-info.json', dict(root=str(Path(args.root).resolve()), total=len(selected),
                                        kind=args.kind, workers=args.workers, created=time.time()))
    done = len(selected)-len(pending)
    with Pool(args.workers, initializer=init_worker, initargs=(args.root,), maxtasksperchild=12) as pool:
        tasks = [(r,args.cached_from,str(out)) for r in pending] if args.cached_from else [(r,str(out)) for r in pending]
        for row in pool.imap_unordered(rerender if args.cached_from else evaluate, tasks):
            done += 1
            if done % 25 == 0 or row['status'] != 'success' or args.kind == 'omr':
                print(json.dumps(dict(done=done,total=len(selected),name=Path(row['pdf']).name,
                                      status=row['status'],seconds=row['seconds'],error=row.get('error')),
                                 ensure_ascii=True),flush=True)
    print(f'completed {done}/{len(selected)}',flush=True)


def tempo_audit(args):
    import pypdfium2 as pdfium
    from app.omr_tempo import opening_tempo
    from app.jianpu import _reader

    rows = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    out = Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    results = []
    for item in rows:
        if item.get('kind') != 'omr':
            continue
        pdf = Path(item['pdf'])
        doc = pdfium.PdfDocument(pdf)
        page = doc[0]
        image = page.render(scale=2400/page.get_width()).to_pil().convert('RGB')
        doc.close()
        path = out/(item['sha256']+'.png')
        image.save(path)
        header = image.crop((0,0,image.width,min(1000,image.height)))
        header.thumbnail((1200,1000))
        header.save(out/(item['sha256']+'-header.png'))
        evidence = []
        def reader(*a,**k):
            result = _reader().ocr(*a,**k)
            evidence.append(dict(text=list(result.txts or []),confidence=[float(x) for x in result.scores or []]))
            return result
        bpm = opening_tempo(path,reader)
        ref = midi_data(item['midi'])[1] if item.get('midi') else []
        result = dict(item,bpm=bpm,ocr=evidence,reference_first_bpm=ref[0][1] if ref else None)
        results.append(result)
        write_json(out/'results.json',results)
        print(json.dumps(dict(name=pdf.name,bpm=bpm,reference=result['reference_first_bpm']),ensure_ascii=True),flush=True)


def repair_omr(args):
    import shutil
    import xml.etree.ElementTree as ET
    from app import keyboard_score as ks
    from app.omr_repeats import normalize_homr_repeats, has_unclosed_repeat_warning
    from app.omr_tempo import supplement_opening_tempo, has_tempo_mark
    from app.score_io import musicxml_to_midi

    source, out = Path(args.source), Path(args.out)
    for result_path in sorted((source/'results').glob('*.json')):
        target = out/'results'/result_path.name
        if target.exists() and not args.force:
            continue
        row = json.loads(result_path.read_text(encoding='utf-8'))
        folder = out/'artifacts'/row['sha256'][:20]
        folder.mkdir(parents=True,exist_ok=True)
        try:
            xml = next((source/'artifacts'/row['sha256'][:20]).rglob('*.musicxml'))
            dest = folder/'score.musicxml'
            shutil.copy2(xml,dest)
            tree = ET.parse(dest)
            row['repeat_repairs'] = normalize_homr_repeats(tree.getroot())
            tree.write(dest,encoding='utf-8',xml_declaration=True)
            image = Path(args.tempo_inputs)/(row['sha256']+'.png')
            row['tempo_recovered'] = supplement_opening_tempo(dest,image)
            row['unclosed_repeat_warning'] = has_unclosed_repeat_warning(dest)
            musicxml_to_midi(dest,folder/'score.mid')
            text = ks.score_file_to_keyboard_text(dest,Path(row['pdf']).stem)
            if row['unclosed_repeat_warning']:
                from app.convert import _with_note
                text = _with_note(text,'# 注意：有反复起点但没有找到对应终点，可能是片段或识别遗漏；未闭合的段落只演奏一次。\n')
            if not has_tempo_mark(dest):
                from app.convert import _with_note
                text = _with_note(text,'# 注意：未识别到速度标记，♩=120 是默认值，请对照原谱。\n')
            (folder/'keyboard.txt').write_text(text,encoding='utf-8')
            events, timeline,_ = ks.notes_from_musicxml(dest)
            raw = sorted(set((ks.seconds_to_quarters(n.start,timeline),ks.seconds_to_quarters(n.end,timeline),n.pitch)
                             for n in events if not n.grace))
            kb = [(q,q,p) for q,ps in ks.parse_keyboard_text(text) for p in ps]
            row.update(status='success',reader_notes=len(raw),keyboard_attacks=len(kb),
                       reader_tempos=[(ks.seconds_to_quarters(t,timeline),bpm) for t,bpm in timeline],
                       reader_end_q=max((n[1] for n in raw),default=0),
                       keyboard_end_q=max((c.q+c.slot for c in ks.read_keyboard_cells(text)),default=0))
            row.pop('error',None)
            row.pop('traceback',None)
            if row.get('midi'):
                ref,_ = midi_data(row['midi'])
                ref = sorted(set(ref))
                row['reader_vs_reference'] = timing_metrics(ref,raw)
                row['onsets_vs_reference'] = timing_metrics(onset_only(ref),onset_only(raw))
                row['keyboard_onsets_vs_reference'] = timing_metrics(onset_only(ref),onset_only(kb))
            row['keyboard_onsets_vs_reader'] = timing_metrics(onset_only(raw),onset_only(kb))
            write_json(folder/'reader-notes.json',raw)
            row['evaluation_mode'] = 'replay cached image-recognition XML with updated tempo and repeat handling'
        except Exception as exc:
            row.update(status='failed',error=str(exc)[:400])
        write_json(target,row)
        print(json.dumps(dict(name=Path(row['pdf']).name,status=row['status'],tempo_recovered=row.get('tempo_recovered'),
                              repeat_repairs=row.get('repeat_repairs'),error=row.get('error')),ensure_ascii=True),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('out')
    inv.add_argument('--pdf-dir', required=True)
    inv.add_argument('--random-root', required=True)
    inv.add_argument('--midi-dir', required=True)
    inv.add_argument('--workers', type=int, default=6)
    bench = sub.add_parser('run')
    bench.add_argument('manifest')
    bench.add_argument('out')
    bench.add_argument('--root',required=True)
    bench.add_argument('--kind',choices=['vector','omr'],required=True)
    bench.add_argument('--workers',type=int,default=4)
    bench.add_argument('--limit',type=int)
    bench.add_argument('--cached-from', help='rerun keyboard layout using unchanged baseline reader MIDI')
    bench.add_argument('--all-pages', action='store_true')
    tmp = sub.add_parser('tempo')
    tmp.add_argument('manifest')
    tmp.add_argument('out')
    repair = sub.add_parser('repair-omr')
    repair.add_argument('source')
    repair.add_argument('out')
    repair.add_argument('--tempo-inputs',required=True)
    repair.add_argument('--force',action='store_true')
    args = parser.parse_args()
    if args.command == 'inventory':
        inventory(args)
    elif args.command == 'run':
        run(args)
    elif args.command == 'tempo':
        sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
        tempo_audit(args)
    elif args.command == 'repair-omr':
        sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
        repair_omr(args)


if __name__ == '__main__':
    main()
