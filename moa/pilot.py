"""Bounded shadow pilot: originals -> HTML -> actual browser -> leased model jobs.

All evidence is private runtime data, separate from human approval and raw model
answers. No public output or training from unreviewed real originals is enabled.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from . import mobile, worker_api as api, worker_contract as wc
from .core import Store, atomic_write, kst_now
from .extract import PARSER_VERSION
from .verification import compare
from .worker_handoff import rubric_sha256


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def table_rows(table):
    return [[c['text'] for c in sorted(table['cells'], key=lambda c: (c['row'], c['col']))
             if c['row'] == r] for r in range(table['rows'])]


def source_inventory(store, data):
    """Freshly read parser output, not text copied back from rendered candidates.

    Repeated semantic columns may be normalised, but row associations are retained.
    Coverage here describes the supported parser, not a human original-file audit.
    """
    from .learn import extract_asset
    sources, excluded, parser = [], [], []
    complete = True
    forms = {(x['file_id'], x['source_block']) for x in data['excluded'] if x['reason'] == 'form'}
    for f in data['files']:
        raw = store.object_path(f['sha256']).read_bytes()
        if sha(raw) != f['sha256']:
            raise ValueError('original object hash mismatch')
        if f['role'] == 'attachment':
            continue
        result = extract_asset(store, f)
        parser.append({'file_id': f['id'], 'original_sha256': f['sha256'],
                       'parser_version': result.get('parser_version'), 'status': result['status']})
        complete &= result['status'] == 'extracted' and not f.get('incomplete', False)
        content, decorative = mobile.ordered_blocks(result)
        excluded.extend({'reason': 'decorative', 'file_id': f['id'], 'text_sha256': sha(x['text'].encode())}
                        for x in decorative)
        for index, b in enumerate(content):
            source = {'id': f's{len(sources):04d}', 'origin_block': f"{f['id']}:{index}",
                      'file_id': f['id'], 'text': b.get('text', '')}
            if (f['id'], index) in forms:
                source.update(role='form', attachment_id=f['id'])
                if not f.get('boundary_confirmed'):
                    complete = False
            elif b['type'] == 'table':
                table, rule = mobile.repeated_columns(copy.deepcopy(b['table']))
                source.update(table_rows=table_rows(table), caption=table.get('caption', ''), note='')
                source['text'] = '\n'.join(' / '.join(row) for row in source['table_rows'])
                if source['caption']:
                    source['text'] = source['caption'] + '\n' + source['text']
                if any(c.get('rowspan', 1) > 1 or c.get('colspan', 1) > 1 for c in table['cells']):
                    source['table_geometry'] = [[c['row'], c['col'], c.get('rowspan', 1), c.get('colspan', 1)]
                                               for c in sorted(table['cells'], key=lambda c: (c['row'], c['col']))]
                if table.get('geometry_uncertain') or table.get('nested'):
                    complete = False
            sources.append(source)
    complete &= not any(w['code'] in ('boundary', 'incomplete', 'geometry') for w in data['warnings'])
    return {'sources': sources, 'parser_coverage_complete': bool(complete),
            'human_original_audit': False, 'parsers': parser, 'excluded': excluded}


def prepare(store, identifiers):
    api.ensure_worker_tables(store)
    existing = store.db.execute('SELECT count(*) FROM pilot_documents').fetchone()[0]
    new = [i for i in identifiers if not store.db.execute('SELECT 1 FROM pilot_documents WHERE mobile_id=?', (i,)).fetchone()]
    if len(new) > 3 or existing + len(new) > 20:
        raise ValueError('pilot supports an initial batch of 3, absolute maximum 20')
    if new and existing and existing + len(new) > 3:
        summary = status(store)
        if summary['end_to_end_documents'] < 3 or summary['hard_findings']:
            raise ValueError('first 3 have not completed the full chain without hard findings')
    documents = []
    for ident in identifiers:
        row = mobile.notice_row(store, ident)
        if row['split'] == 'eval' or row['approved_revision']:
            raise ValueError('evaluation or human-approved documents excluded from pilot mutations')
        if row['origin_notice_id']:
            from .corpus import _evaluation, _file_evaluation
            if _evaluation(store, row['origin_notice_id']) or any(_file_evaluation(store, f['sha256']) for f in json.loads(row['files'])):
                raise ValueError('evaluation original or derivative excluded')
        previous = store.db.execute('SELECT * FROM pilot_documents WHERE mobile_id=?', (ident,)).fetchone()
        if previous:
            if row['latest_revision'] != previous['revision_id']:
                raise ValueError('pilot revision changed; explicit re-review required')
            manifest = json.loads(previous['manifest'])
        else:
            revision = mobile.generate(store, ident)
            inventory = source_inventory(store, revision['data'])
            manifest = {'mobile_id': ident, 'revision_id': revision['id'],
                        'html_sha256': revision['html_sha'], 'parser_version': PARSER_VERSION,
                        'source_revision': sha(wc.canonical(inventory)), **inventory,
                        'files': revision['data']['files'], 'auto_pass_enabled': False,
                        'auto_publish_enabled': False, 'final_verdict': 'needs_review'}
            with store.db:
                store.db.execute('INSERT INTO pilot_documents VALUES(?,?,?,?)',
                                 (ident, revision['id'], json.dumps(manifest, ensure_ascii=False), api._now()))
        documents.append({k: manifest[k] for k in ('mobile_id', 'revision_id', 'html_sha256', 'files')})
    output = store.root / 'coordination/PILOT_BROWSER_INPUT.json'
    atomic_write(output, wc.canonical({'documents': documents})); output.chmod(0o600)
    return {'documents': len(documents), 'browser_input': str(output)}


def _observations(manifest, browser):
    origin_ids = {s['origin_block']: s['id'] for s in manifest['sources']}
    observed = []
    for b in browser['blocks']:
        # Keep unknown DOM IDs: the gate will reject them, never silently drop.
        source_id = origin_ids.get(b['source_block'], 'unknown-' + sha(b['source_block'].encode())[:12])
        observed.append({**b, 'id': 'r' + str(len(observed)), 'source_refs': [source_id]})
    return observed


def ingest_browser(store, observation):
    api.ensure_worker_tables(store)
    ident = observation['mobile_id']
    entry = store.db.execute('SELECT * FROM pilot_documents WHERE mobile_id=?', (ident,)).fetchone()
    if not entry:
        raise ValueError('not a registered pilot document')
    manifest = json.loads(entry['manifest'])
    row = mobile.notice_row(store, ident)
    if observation['revision_id'] != manifest['revision_id'] or row['latest_revision'] != manifest['revision_id']:
        raise ValueError('browser evidence targets a stale revision')
    revision = mobile.revision(store, ident, manifest['revision_id'])
    if sha(store.object_path(revision['html_sha']).read_bytes()) != manifest['html_sha256']:
        raise ValueError('candidate HTML object corrupted')
    views = observation.get('viewports', [])
    if sorted(v['width'] for v in views) != [360, 393, 430]:
        raise ValueError('all three actual viewport observations required')
    expected_files = {f['id']: f['sha256'] for f in manifest['files']}
    downloads = {d['file_id']: d for d in observation.get('downloads', [])}
    originals_ok = set(downloads) == set(expected_files) and all(
        downloads[i]['status'] == 200 and downloads[i]['sha256'] == digest for i, digest in expected_files.items())
    attachments = [{'id': i, 'sha256': h, 'available': originals_ok} for i, h in expected_files.items()]
    reports = []
    for v in views:
        report = compare(manifest['sources'], _observations(manifest, v),
                         source_complete=manifest['parser_coverage_complete'], attachments=attachments)
        for bad, code in ((v.get('http_status') != 200, 'preview_http_error'),
                          (v.get('html_sha256') != manifest['html_sha256'], 'actual_html_revision_mismatch'),
                          (v.get('page_overflow'), 'page_overflow'), (not originals_ok, 'original_download_failed'),
                          (v.get('unbound_content'), 'unbound_render_content'),
                          (bool(observation.get('console_errors')), 'browser_javascript_error'),
                          (not observation.get('html_download_verified'), 'html_download_failed'),
                          (not observation.get('bundle_download_verified'), 'bundle_download_failed')):
            if bad:
                report['findings'].append({'code': code, 'severity': 'critical', 'source_id': ''})
        report['status'] = 'needs_review' if report['findings'] else 'passed'
        reports.append({'width': v['width'], **report})
    manifest.update(browser=observation, rule_report={'status': 'passed' if all(r['status'] == 'passed' for r in reports)
                    else 'needs_review', 'viewports': reports}, browser_received_at=api._now())
    with store.db:
        store.db.execute('UPDATE pilot_documents SET manifest=? WHERE mobile_id=?',
                         (json.dumps(manifest, ensure_ascii=False), ident))
        store.db.execute('INSERT INTO verification_evidence VALUES(?,?,?,?,?,?,?,?,?,?)'
                         ' ON CONFLICT(job_id) DO UPDATE SET rule_report=excluded.rule_report,'
                         'browser_report=excluded.browser_report,updated_at=excluded.updated_at',
                         ('parent-' + ident, ident, manifest['source_revision'], manifest['revision_id'],
                          manifest['html_sha256'], json.dumps(manifest['rule_report']),
                          json.dumps(observation, ensure_ascii=False), None, 'needs_review', api._now()))
    return {'mobile_id': ident, 'rules': manifest['rule_report']['status'],
            'hard_findings': sum(len(r['findings']) for r in reports), 'human_approved': False}


def publish(store, worker_id='macbook-laya-01'):
    if not api.ready(store, worker_id):
        return {'published': 0, 'reason': 'actual worker conformance readiness missing'}
    jobs, skipped = [], []
    for entry in store.db.execute('SELECT * FROM pilot_documents').fetchall():
        manifest = json.loads(entry['manifest'])
        if not manifest.get('browser'):
            continue
        if mobile.notice_row(store, entry['mobile_id'])['latest_revision'] != manifest['revision_id']:
            continue
        observed = {b['source_refs'][0]: b for b in _observations(manifest, manifest['browser']['viewports'][0])}
        document_jobs = []
        for source in manifest['sources']:
            if source.get('role') == 'form':
                continue
            rendered = observed.get(source['id'])
            if not rendered:
                skipped.append(source['id']); continue
            # Whole logical blocks; never truncate a table/header/condition to fit.
            # Bigger units stay explicit rule-only exceptions until a context-safe splitter exists.
            if len(source['text']) + len(rendered['visible_text']) > 1800:
                skipped.append(entry['mobile_id'] + ':' + source['id']); continue
            src = [{'id': source['id'], 'text': source['text']}]
            rendered_text = rendered['visible_text']
            if 'table_rows' in source:
                rendered_text = '\n'.join(' / '.join(row) for row in rendered.get('table_rows', []))
                if rendered.get('caption'):
                    rendered_text = rendered['caption'] + '\n' + rendered_text
                if rendered.get('note'):
                    rendered_text += '\n' + rendered['note']
            render = [{'id': 'r1', 'source_refs': [source['id']], 'visible_text': rendered_text}]
            source_manifest = {'source_revision': manifest['source_revision'], 'scope': 'section', 'complete': True,
                               'block_sha256': {source['id']: sha(source['text'].encode())}}
            artifact = api.put_artifact(store, wc.canonical(source_manifest))
            qs = ['table_relations'] if 'table_rows' in source else ['conditions', 'omission', 'addition']
            payload = {'scope': 'section', 'file_role': 'notice', 'source_blocks': src, 'rendered_blocks': render,
                       'questions': qs, 'required_context_ids': [source['id']],
                       'coverage_manifest': {'expected_blocks': 1, 'provided_blocks': 1},
                       'model_policy': {'checkpoint': 'current', 'max_tokens': 1024, 'vision': False},
                       'deterministic_findings': manifest['rule_report']['viewports'][0]['findings'],
                       'source_manifest_artifact': artifact, 'notice_id': entry['mobile_id'],
                       'parent_html_sha256': manifest['html_sha256'], 'parent_source_complete': manifest['parser_coverage_complete'],
                       'requires_readiness': True, 'require_rubric_hash': True, 'rubric_sha256': rubric_sha256(),
                       'validation_mode': 'shadow', 'auto_pass_enabled': False, 'auto_publish_enabled': False}
            digest = wc.input_sha256('verify', payload, manifest['source_revision'], manifest['revision_id'], api.RUBRIC_REVISION)
            job_id = 'job-pilot-' + digest[:32]
            env = {'contract_version': wc.CONTRACT_VERSION, 'message_type': 'job', 'job_id': job_id,
                   'job_kind': 'verify', 'idempotency_key': digest, 'input_sha256': digest,
                   'lease_id': 'unclaimed', 'fencing_token': 1, 'source_revision': manifest['source_revision'],
                   'render_revision': manifest['revision_id'], 'rubric_revision': api.RUBRIC_REVISION, 'payload': payload}
            answer = api.enqueue_job(store, env, target_worker_id=worker_id)
            if not answer.get('ok'):
                raise ValueError('pilot enqueue rejected: ' + ';'.join(answer.get('errors', [])))
            with store.db:
                store.db.execute('INSERT OR IGNORE INTO verification_evidence VALUES(?,?,?,?,?,?,?,?,?,?)',
                                 (answer['job_id'], entry['mobile_id'], manifest['source_revision'], manifest['revision_id'],
                                  manifest['html_sha256'], json.dumps(manifest['rule_report']),
                                  json.dumps({'parent_id': 'parent-' + entry['mobile_id']}), None, 'needs_review', api._now()))
            jobs.append(answer['job_id']); document_jobs.append(answer['job_id'])
        manifest['model_job_ids'] = document_jobs
        manifest['model_context_exceptions'] = [s for s in skipped if s.startswith(entry['mobile_id'] + ':')]
        with store.db:
            store.db.execute('UPDATE pilot_documents SET manifest=? WHERE mobile_id=?',
                             (json.dumps(manifest, ensure_ascii=False), entry['mobile_id']))
    return {'published': len(jobs), 'job_ids': jobs, 'context_exceptions': len(skipped)}


def status(store):
    api.ensure_worker_tables(store)
    result = {'documents': 0, 'html_documents': 0, 'browser_documents': 0, 'end_to_end_documents': 0,
              'hard_findings': 0, 'human_approvals_created': 0, 'auto_publication_enabled': False, 'items': []}
    for entry in store.db.execute('SELECT * FROM pilot_documents'):
        m = json.loads(entry['manifest']); ids = m.get('model_job_ids', [])
        evidence = [store.db.execute('SELECT model_report FROM verification_evidence WHERE job_id=?', (i,)).fetchone() for i in ids]
        complete = bool(ids) and not m.get('model_context_exceptions') and all(e and e[0] and
            json.loads(e[0]).get('status') == 'completed' and not json.loads(e[0]).get('context_missing') and
            not json.loads(e[0]).get('truncated') for e in evidence)
        result['documents'] += 1; result['html_documents'] += 1
        result['browser_documents'] += int('browser' in m); result['end_to_end_documents'] += int(complete)
        hard = sum(len(v['findings']) for v in m.get('rule_report', {}).get('viewports', []))
        result['hard_findings'] += hard
        result['items'].append({'mobile_id': entry['mobile_id'], 'revision_id': entry['revision_id'],
                                'rules': m.get('rule_report', {}).get('status', 'not_run'), 'hard_findings': hard,
                                'model_jobs': len(ids), 'model_results': sum(bool(e and e[0]) for e in evidence),
                                'end_to_end': complete, 'final_verdict': 'needs_review'})
    return result


def review_suggestions(store, day=None):
    """Stable daily maximum: two unresolved exceptions + one whole-document sample.

    This is a recommendation, not an approval queue that silently labels holds.
    Completed choices are not replaced with more work on the same day.
    """
    api.ensure_worker_tables(store)
    day = day or kst_now().date().isoformat()
    store.db.execute('CREATE TABLE IF NOT EXISTS pilot_daily_reviews('
                     'day TEXT NOT NULL,mobile_id TEXT NOT NULL,revision_id TEXT NOT NULL,'
                     'reason TEXT NOT NULL,PRIMARY KEY(day,mobile_id))')
    existing = store.db.execute('SELECT * FROM pilot_daily_reviews WHERE day=?', (day,)).fetchall()
    if not existing:
        candidates = []
        for row in store.db.execute('SELECT p.*,n.state,n.split,n.title,n.latest_revision FROM pilot_documents p'
                                     ' JOIN mobile_notices n ON n.id=p.mobile_id'):
            if row['split'] == 'eval' or row['state'] in ('approved', 'rejected') or row['revision_id'] != row['latest_revision']:
                continue
            m = json.loads(row['manifest'])
            exception = m.get('rule_report', {}).get('status') != 'passed' or bool(m.get('model_context_exceptions'))
            for jid in m.get('model_job_ids', []):
                evidence = store.db.execute('SELECT model_report FROM verification_evidence WHERE job_id=?', (jid,)).fetchone()
                if evidence and evidence[0] and json.loads(evidence[0]).get('status') != 'completed':
                    exception = True
            candidates.append({'mobile_id': row['mobile_id'], 'revision_id': row['revision_id'], 'exception': exception})
        rank = lambda item: sha((day + ':' + item['mobile_id']).encode())
        exceptions = sorted((c for c in candidates if c['exception']), key=rank)[:2]
        selected = [dict(c, reason='important_exception') for c in exceptions]
        pool = [c for c in candidates if c['mobile_id'] not in {e['mobile_id'] for e in exceptions}]
        if pool: selected.append(dict(min(pool, key=rank), reason='whole_document_sample'))
        with store.db:
            for item in selected:
                store.db.execute('INSERT OR IGNORE INTO pilot_daily_reviews VALUES(?,?,?,?)',
                                 (day, item['mobile_id'], item['revision_id'], item['reason']))
        existing = store.db.execute('SELECT * FROM pilot_daily_reviews WHERE day=?', (day,)).fetchall()
    return [dict(row) for row in existing]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, required=True)
    p.add_argument('action', choices=['prepare', 'browser', 'publish', 'status'])
    p.add_argument('--ids', nargs='+', default=[])
    p.add_argument('--evidence', type=Path)
    args = p.parse_args()
    if not (args.data_dir / 'db/moa.sqlite3').is_file():
        p.error('existing MOA DB required')
    with Store(args.data_dir) as store:
        if args.action == 'prepare': out = prepare(store, args.ids)
        elif args.action == 'browser': out = ingest_browser(store, json.loads(args.evidence.read_bytes()))
        elif args.action == 'publish': out = publish(store)
        else: out = status(store)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
