"""Bounded, independently authored R2 source/actual-DOM preparation.

Generated originals, observations, answers and shards belong in private storage,
not Git. This module never trains, calls an AI, or touches production data.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from . import mobile, r2_data as r2
from .core import Store, atomic_write
from .worker_contract import canonical
from .worker_handoff import questions, rubric_sha256

# Author seeds are private data, never code constants. Keeping them off Git
# also prevents a MacBook-side trainer from reconstructing the blind answers.


def save(root, name, value):
    path = root/name
    atomic_write(path, canonical(value)+b'\n'); path.chmod(0o600)


def code_hashes():
    return {'generator': r2.sha(Path(__file__).read_bytes()),
            'formatter': r2.sha(Path(r2.__file__).read_bytes())}


def _native_bytes(paragraphs, rows, pattern):
    p = lambda t: '<p><run><t>'+escape(t)+'</t></run></p>'
    def cell(r, c, text, colspan=1):
        return ('<tc><subList>'+p(text)+'</subList>'
                f'<cellAddr rowAddr="{r}" colAddr="{c}"/>'
                f'<cellSpan rowSpan="1" colSpan="{colspan}"/></tc>')
    table = '<tbl>'
    if pattern == 3:
        table += '<tr>'+cell(0, 0, '대상별 확인 위치', len(rows[0]))+'</tr>'
    for r, row in enumerate(rows, start=1 if pattern == 3 else 0):
        table += '<tr>'+''.join(cell(r, c, text) for c, text in enumerate(row))+'</tr>'
    table += '</tbl>'
    # ZIP timestamps are fixed, so rerunning is byte reproducible.
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        info = zipfile.ZipInfo('Contents/section0.xml', (2026, 10, 19, 0, 0, 0))
        z.writestr(info, '<sec>'+''.join(p(t) for t in paragraphs)+table+'</sec>')
    return out.getvalue()


def native_facts(raw):
    """Independent XML read; does not call MOA parser or renderer helpers."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root = ET.fromstring(z.read('Contents/section0.xml'))
    text = lambda node: ''.join(e.text or '' for e in node.iter('t'))
    paragraphs = [text(e) for e in root if e.tag == 'p']
    table = root.find('tbl')
    physical = [[text(c) for c in row if c.tag == 'tc'] for row in table if row.tag == 'tr']
    geometry = [[int(c.find('cellAddr').get('rowAddr')), int(c.find('cellAddr').get('colAddr')),
                 int(c.find('cellSpan').get('rowSpan')), int(c.find('cellSpan').get('colSpan'))]
                for c in table.iter('tc')]
    caption = physical[0][0] if len(physical[0]) == 1 else ''
    if caption: physical = physical[1:]
    headers, rows = physical[0], physical[1:]
    if len(headers) == 6 and headers[:3] == headers[3:]:
        rows = [row[o:o+3] for row in rows for o in (0, 3) if any(row[o:o+3])]
        headers = headers[:3]
    return {'paragraphs': paragraphs, 'table': {'headers': headers, 'rows': rows,
             'geometry': geometry, 'caption': caption}}


def authored_sources(topics):
    if not topics or len(topics) > r2.MAX_GROUPS:
        raise ValueError('author source count must be 1..100')
    sources = []
    for i, (title, key, value, a, b, x, y, condition, note) in enumerate(topics):
        paras = [title+' 안내', f'신청 마감: 2027년 {i%7+3}월 {i%20+5}일',
                 f'운영 시각: {i%5+10}:30', f'비용: {1200+i*400}',
                 f'선택 수: {i%3+2}개', condition, '비용 단위: 원/1회', '※ '+note]
        pattern = i % 4
        if pattern == 0: rows = [[key, value], [a, x], [b, y]]
        elif pattern == 2: rows = [['번호', key, value]*2, ['1', a, x, '2', b, y]]
        else: rows = [['번호', key, value], ['1', a, x], ['2', b, y]]
        raw = _native_bytes(paras, rows, pattern)
        native = native_facts(raw)
        family = r2.sha(canonical({'headers': native['table']['headers'],
            'geometry': native['table']['geometry'], 'unit': paras[6],
            'condition_role': condition, 'note_role': note}))
        sources.append({'id': 'authored-'+str(i), 'sha256': r2.sha(raw),
                        'text': '\n'.join(paras)+'\n'+str(rows), 'family_ids': [family],
                        'rights_status': 'allowed', 'rights_evidence': 'independently_authored_synthetic_by_moa',
                        'lineage_sha256': [], 'native': native, 'raw': raw, 'pattern': pattern})
    return sources


def prepare(root, protected, topics, *, frozen=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    records = authored_sources(topics)
    descriptor_fields = {'id','sha256','text','family_ids','rights_status','rights_evidence',
                         'lineage_sha256','evaluation_only'}
    protected = [{k:v for k,v in s.items() if k in descriptor_fields} for s in protected]
    grouped = r2.group_sources(records, protected)
    assignments = r2.assign_splits(grouped['groups'], frozen)
    save(root, 'PROTECTION.json', protected)
    save(root, 'SPLITS.json', assignments)
    documents = []
    with Store(root/'data') as store:
        for group in grouped['groups']:
            for s in group['members']:
                (root/'originals').mkdir(exist_ok=True, mode=0o700)
                path = root/'originals'/(s['sha256']+'.hwpx')
                atomic_write(path, s['raw']); path.chmod(0o600)
                ident = mobile.create_upload(store, [('합성 안내.hwpx', s['raw'])])
                rev = mobile.generate(store, ident)
                html = store.object_path(rev['html_sha']).read_bytes()
                path = root/(s['sha256']+'.html'); atomic_write(path, html); path.chmod(0o600)
                blocks = rev['data']['blocks']
                paragraphs = [b for b in blocks if b['type'] == 'paragraph']
                if [b['text'] for b in paragraphs] != s['native']['paragraphs']:
                    raise ValueError('independent native paragraph mismatch')
                tables = [b for b in blocks if b['type'] == 'table']
                if len(tables) != 1: raise ValueError('native table missing')
                documents.append({'source': {k:v for k,v in s.items() if k != 'raw'},
                    'group_id': group['id'], 'split': assignments[s['sha256']],
                    'html_file': path.name, 'html_sha256': r2.sha(html),
                    'mobile_id': ident, 'revision_id': rev['id'],
                    'paragraph_ids': [b['id'] for b in paragraphs], 'table_id': tables[0]['id']})
        if store.db.execute('select count(*) from mobile_reviews').fetchone()[0]:
            raise ValueError('unexpected human decisions')
    save(root, 'DOM_SPEC.json', {'documents': documents, 'input_revision': r2.INPUT_REVISION,
                               'variants': variants()})
    counts = Counter(d['split'] for d in documents)
    summary = {'groups': len(grouped['groups']), 'sources': len(documents), 'split_groups': dict(counts),
        'excluded_sources': len(grouped['rejected']), 'rejections': grouped['rejected'],
        'target_group_shortfall': 100-len(grouped['groups']), 'new_real_training_originals': 0,
        'native_format': 'synthetic_HWPX', 'physical_table_patterns': 4,
        'training_started': False, 'human_approvals': 0, 'browser_complete': False,
        'private_author_seeds_sha256': r2.sha(canonical(topics))}
    save(root, 'PREPARATION.json', summary)
    files = ['DOM_SPEC.json','SPLITS.json','PROTECTION.json','PREPARATION.json']
    files += ['originals/'+d['source']['sha256']+'.hwpx' for d in documents]
    files += [d['html_file'] for d in documents]
    save(root, 'SOURCE_FREEZE.json', {'files': {name:r2.sha((root/name).read_bytes()) for name in files},
                                   'code_sha256': code_hashes(), 'rubric_sha256': rubric_sha256(),
                                   'partition_fixed_before_variants':True})
    return summary


def scope_facts(native, observation, scope, index=0):
    def extract(value):
        p = value['paragraphs']
        # Empty DOM text stays empty; never repair it from the native source.
        result = {'conditions': [p[5]] if p[5] else [], 'unit': p[6],
                  'notes': [p[7]] if p[7] else []}
        if scope == 'table': result.update(copy.deepcopy(value['table']))
        elif scope == 'scalar': result['facts'] = [p[index]] if p[index] else []
        elif scope == 'condition': result['facts'] = [p[5]] if p[5] else []
        elif scope == 'note': result['facts'] = [p[7]] if p[7] else []
        else: raise ValueError('fact scope')
        return result
    return extract(native), extract(observation)


def _truth(facts):
    # The authored tables are unordered keyed lookups, not timelines/priorities.
    result = {k:v for k,v in facts.items() if k != 'geometry'}
    if 'rows' in result: result['rows'] = sorted(result['rows'])
    return result


def _variants():
    out = [('normal_scalar_'+str(i), 'scalar', i, 'normal', 'conditions') for i in range(1,5)]
    out += [('normal_condition','condition',0,'normal','conditions'),
            ('normal_note','note',0,'normal','conditions'),
            ('normal_table','table',0,'normal','table_relations'),
            ('normal_reorder','table',0,'reorder','table_relations')]
    out += [('changed_scalar_'+str(i),'scalar',i,'replace','conditions') for i in range(1,5)]
    out += [('condition_removed','condition',5,'remove','omission'),
            ('note_removed','note',7,'remove','omission'),
            ('relation_swapped','table',0,'swap','table_relations'),
            ('row_removed','table',0,'drop_row','table_relations')]
    out += [('missing_header','table',0,'missing_header','table_relations'),
            ('missing_unit','scalar',3,'missing_unit','conditions'),
            ('missing_note','note',0,'missing_note','conditions'),
            ('missing_page','condition',0,'missing_page','conditions')]
    return out


def variants():
    return [dict(name=n,scope=s,index=i,action=a,criterion=c) for n,s,i,a,c in _variants()]


def finalize(root, observations):
    root = Path(root)
    if (root/'R2_READY.json').exists(): raise ValueError('ready bundle already exists')
    frozen = json.loads((root/'SOURCE_FREEZE.json').read_bytes())
    if frozen.get('code_sha256') != code_hashes() or frozen.get('rubric_sha256') != rubric_sha256():
        raise ValueError('frozen preparation code or rubric changed')
    for name, expected in frozen['files'].items():
        if r2.sha((root/name).read_bytes()) != expected: raise ValueError('frozen source/split changed')
    spec = json.loads((root/'DOM_SPEC.json').read_bytes())
    if not spec['documents']: raise ValueError('no eligible source; cannot mark bundle ready')
    documents = observations.get('documents', [])
    if len(documents) != len(spec['documents']): raise ValueError('browser evidence missing')
    if (observations.get('engine') != 'Playwright_Chrome_actual_DOM' or observations.get('offline') is not True
            or observations.get('spec_sha256') != r2.sha((root/'DOM_SPEC.json').read_bytes())):
        raise ValueError('browser spec/engine evidence mismatch')
    if len({d['source_sha256'] for d in documents}) != len(documents): raise ValueError('duplicate browser document')
    observed = {d['source_sha256']: d for d in documents}
    assignments = json.loads((root/'SPLITS.json').read_bytes())
    rows, browser_count = [], 0
    for doc in spec['documents']:
        s, native = doc['source'], doc['source']['native']
        view = observed.get(s['sha256'])
        if not view or view['html_sha256'] != doc['html_sha256'] or view.get('console_errors'):
            raise ValueError('browser provenance or console error')
        if view['baseline'].get('page_overflow'): raise ValueError('browser page overflow')
        if view['baseline']['paragraphs'] != native['paragraphs']:
            raise ValueError('actual DOM paragraph mismatch')
        left, right = scope_facts(native, view['baseline'], 'table')
        if _truth(left) != _truth(right): raise ValueError('independent native/DOM table mismatch')
        variants_by_name = {v['name']:v for v in view['variants']}
        if len(variants_by_name) != 20: raise ValueError('browser variants incomplete')
        for v in variants():
            evidence = variants_by_name[v['name']]
            before, after = scope_facts(native, evidence['observation'], v['scope'], v['index'])
            intended = copy.deepcopy(before)
            action = v['action']
            complete = not action.startswith('missing_')
            expected = 'preserved' if v['name'].startswith('normal') else 'changed' if complete else 'insufficient'
            if action == 'replace':
                old = native['paragraphs'][v['index']]
                new = re.sub(r'\d+', lambda m: str(int(m[0])+1), old, count=1)
                intended['facts'] = [new]
            elif action == 'remove':
                intended['facts'] = []
                intended['conditions' if v['scope']=='condition' else 'notes'] = []
            elif action == 'swap':
                intended['rows'][0][-1], intended['rows'][1][-1] = intended['rows'][1][-1], intended['rows'][0][-1]
            elif action == 'drop_row': intended['rows'] = intended['rows'][:-1]
            if complete and _truth(intended) != _truth(after):
                raise ValueError('actual DOM does not match recorded operation')
            full_before = copy.deepcopy(before)
            if action == 'missing_header': before['headers'] = []; before['geometry'] = []
            elif action == 'missing_unit': before['unit'] = ''
            elif action == 'missing_note': before['notes'] = []; before['facts'] = []
            elif action == 'missing_page': before['conditions'] = []; before['facts'] = []
            state = r2.fact_state(before, after)
            # Honest source-context availability, not a parser-success/whole-file claim.
            state['source_complete'] = complete
            q = {v['criterion']: questions()[v['criterion']]}
            case_id = r2.sha(canonical({'state':state,'questions':q}))
            proof = {'before': _truth(full_before), 'after': _truth(intended), 'complete': complete}
            r2.validate_operation(proof['before'],proof['after'],expected,complete)
            # S0 compares the same selected facts/context, just not JSON fact keys.
            legacy = r2.text_state(state)
            rows.append({'case_id':case_id,'state':state,'legacy_state':legacy,'questions':q,
                'expected':{v['criterion']:expected},
                'meta':{'schema_version':'moa.training.row.v2','original_group_id':doc['group_id'],
                    'family_group_id':s['family_ids'][0], 'source_sha256':s['sha256'],
                    'split':doc['split'],'rights_status':'allowed','rights_evidence':s['rights_evidence'],
                    'label_origin':'deterministic_oracle','synthetic_gold':True,'human_gold':False,
                    'forbidden_lineage':[],'evaluation_only':False,
                    'operation':proof,'operation_name':v['name'],'browser_observation_sha256':r2.sha(canonical(evidence))}})
            browser_count += 1
    audit = r2.validate_rows(rows, assignments)
    shards = []
    for split in r2.SPLITS:
        raw = b''; chunk = 0
        def flush(content, number):
            name = f'{split}-{number:03d}.jsonl'
            atomic_write(root/name, content); (root/name).chmod(0o600)
            shards.append({'file':name,'split':split,'bytes':len(content),'rows':content.count(b'\n'),
                           'sha256':r2.sha(content),'transport_to_book':split!='blind'})
        for row in (r for r in rows if r['meta']['split']==split):
            line = canonical(row)+b'\n'
            if len(line)>r2.MAX_SHARD_BYTES: raise ValueError('single row exceeds artifact budget')
            if raw and len(raw)+len(line)>r2.MAX_SHARD_BYTES: flush(raw,chunk); raw=b'';chunk+=1
            raw += line
        if raw: flush(raw,chunk)
    ready = {'bundle_id':root.name,'input_revision':r2.INPUT_REVISION,
        'contract_version':'moa.worker.v1','rubric_sha256':rubric_sha256(),
        'questions':questions(),'choice_order':list(r2.LABELS),'gold_index':{k:i for i,k in enumerate(r2.LABELS)},
        'state_field_order':list(r2.STATE_FIELDS),'max_tokens':1024,'head_max_tokens':256,
        'formatter_sha256':r2.sha(Path(r2.__file__).read_bytes()),
        'generator_sha256':r2.sha(Path(__file__).read_bytes()),
        'protection_sha256':r2.sha((root/'PROTECTION.json').read_bytes()),
        'source_freeze_sha256':r2.sha((root/'SOURCE_FREEZE.json').read_bytes()),
        'splits_sha256':r2.sha((root/'SPLITS.json').read_bytes()),
        'browser_observations_sha256':r2.sha(canonical(observations)),
        'rows':audit['rows'],'source_template_groups':audit['groups'],'synthetic_gold':len(rows),'human_gold':0,
        'split_counts':{s:dict(Counter(next(iter(r['expected'].values())) for r in rows if r['meta']['split']==s)) for s in r2.SPLITS},
        'shards':shards,'blind_policy':'inputs and answers stay on mini until candidate, input representation and settings hashes are frozen',
        'training_loader_split':'train_only','runtime_split_mapping':{'train':'train','dev':'calibration','blind':'validation_after_freeze_only'},
        'native_sources':len(spec['documents']),'physical_table_patterns':4,'new_real_training_originals':0,
        'target_groups':100,'target_group_shortfall':100-audit['groups'],'browser_variants':browser_count,
        'training_started':False,'human_approvals':0,'operational_promotion_allowed':False,
        'auto_pass_enabled':False,'auto_publish_enabled':False,'paid_models_allowed':False,
        'book_training_limits':{'S1_questions':32,'S1_updates':40,'S1_seconds':600,
            'R2A_epochs':2,'R2A_updates':400,'R2A_seconds':2700,'total_updates':440,'total_seconds':3300,
            'evaluation_seconds':2700,'evaluation_chunk_seconds':600,'executor':'MacBook_Codex_only',
            'checkpoint_every_updates':50,'checkpoint_every_seconds':300,'save_before_evaluation':True,
            'start_from':'R1_saved_checkpoint','reset_optimizer_scheduler':True,'frozen_encoder':True}}
    save(root, 'R2_READY.json', ready)
    return ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare','finalize'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--protected',type=Path)
    parser.add_argument('--topics',type=Path,help='Private author seeds; never sent with blind data')
    parser.add_argument('--frozen-splits',type=Path,help='Prior immutable assignment map when extending a bundle')
    parser.add_argument('--observations',type=Path)
    args = parser.parse_args()
    if args.stage=='prepare':
        if not args.protected or not args.topics: parser.error('--protected and --topics are required')
        result=prepare(args.root,json.loads(args.protected.read_bytes()),json.loads(args.topics.read_bytes()),
                       frozen=json.loads(args.frozen_splits.read_bytes()) if args.frozen_splits else None)
    else:
        if not args.observations: parser.error('--observations is required')
        result=finalize(args.root,json.loads(args.observations.read_bytes()))
    print(json.dumps({k:v for k,v in result.items() if k in ('groups','rows','source_template_groups','split_groups','split_counts','target_group_shortfall','browser_variants')}))


if __name__=='__main__': main()
