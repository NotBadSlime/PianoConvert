"""Summarize the full-library rhythm audit, keeping partial references explicit."""
from __future__ import annotations

from collections import Counter
import json
from multiprocessing import Pool
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from scripts.rhythm_benchmark import f1, midi_data, onset_only, write_json


def detail(task):
    item, base = task
    base = Path(base)
    before_dir = base/('before-vector' if item['kind']=='vector' else 'before-omr')
    initial = before_dir/'results'/(item['sha256']+'.json')
    if not initial.exists():
        return dict(item,status='pending')
    before = json.loads(initial.read_text(encoding='utf-8'))
    final_dir = before_dir
    for alternate in ('after-long','after-omr'):
        if (base/alternate/'results'/initial.name).exists():
            final_dir = base/alternate
    row = json.loads((final_dir/'results'/initial.name).read_text(encoding='utf-8'))
    row['before_status'] = before['status']
    row['before_notes'] = before.get('reader_notes')
    row['before_pages_tested'] = before.get('pages_tested',item['pages'])
    row['before_first_bpm'] = (before.get('reader_tempos') or [[None,None]])[0][1]
    row['result_folder'] = str((final_dir/'artifacts'/item['sha256'][:20]).resolve())
    row['pages_tested'] = row.get('pages_tested',item['pages'])
    if row.get('status') != 'success':
        return row
    folder = final_dir/'artifacts'/item['sha256'][:20]
    if item['kind']=='omr':
        from app.omr_tempo import has_tempo_mark
        xml = folder/'score.musicxml'
        row['tempo_available'] = has_tempo_mark(xml) if xml.exists() else False
    else:
        row['tempo_available'] = row.get('tempo_printed',False)
    if item.get('midi'):
        # Report both whole-MIDI and covered-span scores. A four-page preview
        # must not be presented as a complete performance of a full-song MIDI.
        ref,_ = midi_data(item['midi'])
        ref = sorted(set(ref))
        raw = json.loads((folder/'reader-notes.json').read_text(encoding='utf-8'))
        if raw and ref:
            off = row.get('reader_vs_reference',{}).get('global_offset_q',0)
            lo,hi = min(n[0] for n in raw)+off-.03,max(n[0] for n in raw)+off+.03
            covered = [n for n in ref if lo <= n[0] <= hi]
            row['reference_note_coverage'] = len(covered)/len(ref)
            row['covered_span_onset_f1_003'] = f1(onset_only(covered),onset_only(raw),off,.03)
    return row


def main():
    base = Path(sys.argv[1]).resolve()
    manifest = json.loads((base/'manifest.json').read_text(encoding='utf-8'))
    with Pool(4) as pool:
        rows = pool.map(detail,[(r,str(base)) for r in manifest])
    summary = {'files':len(rows),'groups':dict(Counter(r['group'] for r in rows)),
               'status':dict(Counter(r['status'] for r in rows)),
               'with_same_named_midi':sum(bool(r.get('midi')) for r in rows),
               'long_pdfs_now_complete':sum(r.get('before_pages_tested',0)<r.get('pages_tested',0) for r in rows),
               'tempo_recovered':sum(bool(r.get('tempo_recovered')) for r in rows),
               'recovered_failed_conversions':sum(r.get('before_status')=='failed' and r['status']=='success' for r in rows),
               'unclosed_repeat_warnings':sum(bool(r.get('unclosed_repeat_warning')) for r in rows)}
    summary['by_group'] = {}
    for group in summary['groups']:
        chosen = [r for r in rows if r['group']==group]
        quantitative = [r for r in chosen if r['status']=='success' and 'reader_vs_reference' in r]
        values = lambda field: [r[field] for r in quantitative if field in r]
        summary['by_group'][group] = {'files':len(chosen),'successful':sum(r['status']=='success' for r in chosen),
            'quantitative':len(quantitative),
            'whole_midi_note_f1_003':sum(r['reader_vs_reference']['f1_0.03'] for r in quantitative)/len(quantitative) if quantitative else None,
            'whole_midi_onset_f1_003':sum(r['onsets_vs_reference']['f1_0.03'] for r in quantitative)/len(quantitative) if quantitative else None,
            'whole_midi_keyboard_onset_f1_003':sum(r['keyboard_onsets_vs_reference']['f1_0.03'] for r in quantitative)/len(quantitative) if quantitative else None,
            'covered_span_onset_f1_003':sum(values('covered_span_onset_f1_003'))/len(values('covered_span_onset_f1_003')) if values('covered_span_onset_f1_003') else None,
            'unknown_tempo':sum(r['status']=='success' and not r.get('tempo_available',False) for r in chosen)}
    write_json(base/'report-data.json',rows)
    write_json(base/'summary.json',summary)
    payload = json.dumps({'summary':summary,'rows':rows},ensure_ascii=False).replace('<','\\u003c')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PianoConvert 节奏测试</title>
<style>body{font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif;background:#f3f7f6;color:#183b36;margin:0;padding:32px}main{max-width:1500px;margin:auto}h1{font-size:28px}p{max-width:1050px}.cards{display:flex;gap:16px;flex-wrap:wrap}.card{background:white;padding:18px 24px;border-radius:12px;min-width:140px}.card strong{display:block;font-size:28px}.note{background:#fff6df;padding:16px;border-radius:12px}.filters{display:flex;gap:12px;margin:24px 0;flex-wrap:wrap}input,select{font:inherit;padding:8px;border:1px solid #b8d0cb;border-radius:6px;background:white}.table{overflow:auto;background:white;border-radius:12px}table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{padding:10px 14px;text-align:left;border-bottom:1px solid #e2ece9}th{position:sticky;top:0;background:#dcece7;cursor:pointer}td.name{white-space:normal;min-width:340px}.bad{color:#a24b27}.small{font-size:13px;color:#58716c}details{white-space:normal;min-width:300px;max-width:650px}a{color:#146f60}button{font:inherit;padding:8px 16px;border:0;border-radius:6px;background:#1c7867;color:white;cursor:pointer}</style>
<main><h1>PianoConvert 节奏测试 · 2026-10-31</h1><div class="cards" id="cards"></div>
<p>范围：指定目录的全部 PDF，以及项目已有 random1、random2、msrand1～3 测试 PDF。原文件只读，识别结果和逐份数据保存在同目录。直接读取谱面的文件与图像识谱文件分别统计。</p>
<p class="note">F1 是自动匹配指标，不是逐音人工验收。严格容差为 0.03 个四分音符。只有同名 MIDI 的谱能做参考对照；同名版本是否完全一致仍需人工确认。MuseScore 的部分 PDF 是前四页预览，整曲 F1 会计入 PDF 未覆盖的后续音符，因此同时列出参考覆盖率和对应片段 F1。无参考的文件不标准确率。疑似拍数异常也不等于已确认的错音。</p>
<p>已采用：小字号、浅色谱线和二分／附点速度单位识别；未闭合反复段按片段播放一次并提示；矢量谱完整读取超过 40 页的内容。扩大节奏网格和改变和弦合并的试验未合入，原因是参考对照中出现局部回退。图像识谱复验复用了本轮重新识别的 MusicXML；另外执行完整 PDF 转换验证所采用的修复。</p>
<div class="filters"><input id="search" placeholder="搜索曲名或路径"><select id="group"><option value="">全部分组</option></select><select id="kind"><option value="">全部读取方式</option><option value="vector">直接读取</option><option value="omr">图像识谱</option></select><select id="state"><option value="">全部状态</option><option value="failed">失败</option><option value="pending">待完成</option><option value="unknown">速度未识别</option><option value="noref">无同名参考 MIDI</option></select><button id="download">下载逐份 JSON</button><span id="count"></span></div>
<div class="table"><table><thead><tr><th data-key="name">曲名／路径</th><th data-key="group">分组</th><th data-key="status">转换</th><th data-key="pages">页数</th><th data-key="bpm">四分速度</th><th data-key="coverage">参考覆盖率</th><th data-key="strict">识谱起音 F1</th><th data-key="kb">键盘起音 F1</th><th data-key="span">片段起音 F1</th><th data-key="bars">疑似拍数异常</th></tr></thead><tbody id="body"></tbody></table></div><p class="small">提示：点击表头排序。展开曲名查看源路径和测试输出位置。没有进行整库人工听音验证；本次测试不包含 EXE 打包或发布。</p></main>
<script id="data" type="application/json">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent),s=data.summary; const $=id=>document.getElementById(id);let sort='name',direction=1;
for(const [label,value] of [['PDF 总数',s.files],['转换成功',s.status.success||0],['有同名 MIDI',s.with_same_named_midi],['新增速度识别',s.tempo_recovered],['长谱完整读取',s.long_pdfs_now_complete]]){let x=document.createElement('div');x.className='card';let n=document.createElement('strong');n.textContent=value;x.append(n,label);$('cards').append(x)}
for(const g of Object.keys(s.groups)){let o=document.createElement('option');o.value=g;o.textContent=g+' ('+s.groups[g]+')';$('group').append(o)}
const value=(r,key)=>({name:r.pdf.split(/[\\\\/]/).pop(),group:r.group,status:r.status,pages:r.pages,bpm:r.reader_tempos?.[0]?.[1]??-1,coverage:r.reference_note_coverage??-1,strict:r.onsets_vs_reference?.['f1_0.03']??-1,kb:r.keyboard_onsets_vs_reference?.['f1_0.03']??-1,span:r.covered_span_onset_f1_003??-1,bars:r.suspect_bars_count??-1})[key];
const pct=x=>x==null?'—':(100*x).toFixed(2)+'%';function render(){let q=$('search').value.toLowerCase();let rs=data.rows.filter(r=>(!q||r.pdf.toLowerCase().includes(q))&&(!$('group').value||r.group===$('group').value)&&(!$('kind').value||r.kind===$('kind').value)&&(!$('state').value||($('state').value==='unknown'?!r.tempo_available:$('state').value==='noref'?!r.midi:r.status===$('state').value)));rs.sort((a,b)=>{let x=value(a,sort),y=value(b,sort);return direction*(typeof x==='string'?x.localeCompare(y):x-y)});$('count').textContent=rs.length+' 份';$('body').replaceChildren();for(const r of rs){let tr=document.createElement('tr');let td=document.createElement('td');td.className='name';let d=document.createElement('details'),title=document.createElement('summary');title.textContent=value(r,'name');d.append(title);let p=document.createElement('p');p.className='small';p.textContent=r.pdf+'；输出：'+(r.result_folder||'待完成')+(r.error?'；错误：'+r.error:'');d.append(p);td.append(d);tr.append(td);let cells=[r.group,r.status==='success'?'成功':r.status==='pending'?'待完成':'失败',(r.pages_tested??0)+' / '+r.pages,r.reader_tempos?.[0]?.[1]?(Math.round(r.reader_tempos[0][1]*10)/10)+(r.tempo_available?'':'（默认）'):'—',pct(r.reference_note_coverage),pct(r.onsets_vs_reference?.['f1_0.03']),pct(r.keyboard_onsets_vs_reference?.['f1_0.03']),pct(r.covered_span_onset_f1_003),r.suspect_bars_count??'—'];for(const c of cells){let x=document.createElement('td');x.textContent=c;tr.append(x)}$('body').append(tr)}}
for(const id of ['search','group','kind','state'])$(id).addEventListener('input',render);document.querySelectorAll('th[data-key]').forEach(th=>th.onclick=()=>{direction=sort===th.dataset.key?-direction:1;sort=th.dataset.key;render()});$('download').onclick=()=>{let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));a.download='PianoConvert-rhythm-results.json';a.click();URL.revokeObjectURL(a.href)};render();
</script></html>'''.replace('PAYLOAD',payload)
    (base/'report.html').write_text(page,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
