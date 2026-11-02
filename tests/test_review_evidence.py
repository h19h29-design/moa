import copy
import hashlib
import json
import urllib.error
import urllib.request

import pytest
from bs4 import BeautifulSoup

from moa import review_evidence as e
from moa.worker_contract import canonical
from test_mobile import uploaded, web
from test_quick_review import login


def record(ident, rev):
    return {'mobile_id': ident, 'render_revision': rev['id'], 'html_sha256': rev['html_sha'],
            'files_sha256': hashlib.sha256(canonical(rev['data']['files'])).hexdigest(),
            'source_revision': 'a' * 64, 'versions': {'parser': 'parser-v1', 'renderer': 'renderer-v1', 'checker': 'gate-v3'},
            'human_approved': False, 'training_allowed': False,
            'sections': [{'source_id': 's1', 'origin_location': '원본 파일 0 · 구역 3 (페이지 미확인)',
                          'source_excerpt': '<script>unsafe()</script> 원문 금액 3000원',
                          'dom_excerpt': '모바일 금액 5000원', 'direct_label': 'changed',
                          'models': [{'name': 'R1', 'prediction': 'preserved', 'status': 'historical_input_not_reinferred',
                                      'source_revision': 'a' * 64, 'render_revision': rev['id']}],
                          'final_label': 'insufficient', 'reasons': ['file_role_unverified', 'direct_mismatch'],
                          'evidence': {'file_complete': None, 'scope_complete': True, 'table_context_complete': None, 'role_confirmed': False}}]}


def save(store, doc):
    (store.root/'reports/review-evidence-v1.json').write_text(json.dumps({'version': e.VERSION, 'documents': [doc]}))


def test_revision_bound_sidecar_is_read_only_and_escaped(web):
    store, base = web
    ident, rev = uploaded(store); doc = record(ident, rev); save(store, doc)
    before = store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0]
    browser = login(base)
    page = browser.open(base+'/notice?id='+ident).read().decode()
    assert '원문과 모바일 · 확인할 부분' in page and '원본 파일 0 · 구역 3' in page
    rendered = BeautifulSoup(page, 'html.parser')
    assert '모바일 금액 5000원' in rendered.get_text() and '규칙: 내용 차이' in page
    assert '<script>unsafe()</script>' in rendered.get_text() and '<script>unsafe()' not in page
    assert '이전 입력 결과 · 재추론 아님' in page
    assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == before
    with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(base+'/notice?id='+ident)
    assert error.value.code == 401


def test_known_content_difference_is_visible_without_opening_comparison_and_escaped(web):
    store, base = web
    ident, rev = uploaded(store); doc = record(ident, rev); save(store, doc)
    page = BeautifulSoup(login(base).open(base+'/notice?id='+ident).read(), 'html.parser')
    section = page.select_one('.evidence-section')
    comparison = section.select_one('.evidence-comparison')
    assert comparison is not None and comparison.find_parent('details') is None
    assert comparison.select_one('[data-side=source] mark').get_text()
    assert '5000' in comparison.select_one('[data-side=dom]').get_text()
    assert not comparison.select('script')
    assert '원본 파일 0 · 구역 3' in section.get_text()


def test_current_sidecar_suggests_editable_hold_reason_but_never_saves_on_get(web):
    store, base = web; ident, rev = uploaded(store); doc = record(ident, rev)
    doc['sections'][0]['reasons'] = ['missing_file_pages']; save(store, doc)
    before = store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0]
    page = BeautifulSoup(login(base).open(base+'/notice?id='+ident).read(), 'html.parser')
    form = page.find('form', action='/mobile/review')
    assert form.select_one('[name=reason] option[selected]')['value'] == 'incomplete'
    assert form.select_one('[name=reason]').has_attr('disabled') is False
    assert '자동 제안' in form.get_text()
    assert form.select_one('[name=note]').get_text() == ''
    assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == before
    doc['html_sha256'] = 'stale'; save(store, doc)
    page = BeautifulSoup(login(base).open(base+'/notice?id='+ident).read(), 'html.parser')
    assert page.select_one('form[action="/mobile/review"] [name=reason] option[selected]')['value'] == 'unconfirmed'


def test_normal_reordering_does_not_paint_a_confirmed_error_and_note_reason_is_not_training():
    doc = record('x', {'id':'r','html_sha':'a'*64, 'data':{'files':[]}})
    doc['status'] = 'current'; doc['sections'][0]['direct_label'] = 'preserved'
    assert '<mark' not in e.render(doc)
    assert e.suggest_hold({'status':'missing'}) == ('unconfirmed', '')
    assert e.suggest_hold({'status':'stale', 'sections':doc['sections']}) == ('unconfirmed', '')


def test_native_nli_neutral_is_visible_as_semantic_unresolved_not_missing_source(web):
    store, base=web;ident,rev=uploaded(store);doc=record(ident,rev)
    model=doc['sections'][0]['models'][0]
    model.update(name='Korean NLI',prediction=None,nli={'semantic_relation':'not_entailed',
                 'direction_counts':{'source_to_dom':{'entailment':1,'neutral':1,'contradiction':0},
                                     'dom_to_source':{'entailment':2,'neutral':0,'contradiction':0}}})
    save(store,doc)
    page=BeautifulSoup(login(base).open(base+'/notice?id='+ident).read(),'html.parser')
    assert 'neutral' in page.select_one('.evidence-review').get_text()
    assert 'neutral은 원문 부족 판정이 아닙니다' in page.get_text()
    assert '의미 일치 미확정' in page.get_text()
    assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0]==0


def test_malformed_native_nli_summary_invalidates_sidecar_not_the_review_page(web):
    store,base=web;ident,rev=uploaded(store);doc=record(ident,rev)
    doc['sections'][0]['models'][0]['nli']={'semantic_relation':'not_entailed','direction_counts':'not-counts'}
    save(store,doc)
    assert e.load(store,ident,rev,rev['data']['files'])['status']=='invalid'
    assert login(base).open(base+'/notice?id='+ident).status==200


@pytest.mark.parametrize('field', ['render_revision', 'html_sha256', 'files_sha256'])
def test_changed_revision_html_or_file_roles_invalidates_explanations(web, field):
    store, _ = web
    ident, rev = uploaded(store); doc = record(ident, rev); doc[field] = 'stale'; save(store, doc)
    result = e.load(store, ident, rev, rev['data']['files'])
    assert result['status'] == 'stale' and not result.get('sections')


def test_missing_malformed_and_symlink_sidecars_never_leak_or_break_review(web, tmp_path):
    store, _ = web; ident, rev = uploaded(store)
    assert e.load(store, ident, rev, rev['data']['files'])['status'] == 'missing'
    path = store.root/'reports/review-evidence-v1.json'; path.write_text('{')
    assert e.load(store, ident, rev, rev['data']['files'])['status'] == 'invalid'
    path.unlink(); outside = tmp_path/'not-an-evidence.json'; outside.write_text('{}'); path.symlink_to(outside)
    assert e.load(store, ident, rev, rev['data']['files'])['status'] == 'invalid'


def human_record():
    return {'source_sha256': 'a' * 64, 'source_group': 'group-a', 'lineage': [], 'evaluation_only': False,
            'source_sections': [{'id': 's1', 'evidence': '原文'}], 'dom_sections': [{'source_refs': ['s1'], 'visible_text': '結果'}],
            'human': {'origin': 'authenticated_human', 'action': 'approved', 'content_compared': True,
                      'label': 'preserved', 'errors': [], 'corrected_blocks': [], 'review_revision': 'human-v1'},
            'role': {'value': 'notice', 'confirmed': True}, 'rights': {'rule_examples': True, 'model_training': False},
            'versions': {'source': 'src1', 'render': 'html1', 'review': 'review1', 'model': 'r1', 'renderer': 'v1'}}


def test_future_human_data_has_separate_rule_and_model_eligibility_and_eval_firewall():
    r = human_record()
    result = e.learning_eligibility(r, protected_hashes=set(), protected_groups=set())
    assert result['rule_example'] and not result['model_training']
    r['rights']['model_training'] = True
    assert e.learning_eligibility(r, protected_hashes=set(), protected_groups=set())['model_training']
    for change in ('eval', 'hash', 'group', 'lineage', 'held', 'unconfirmed', 'automated'):
        x = copy.deepcopy(r); hashes, groups = set(), set()
        if change == 'eval': x['evaluation_only'] = True
        elif change == 'hash': hashes.add(x['source_sha256'])
        elif change == 'group': groups.add(x['source_group'])
        elif change == 'lineage': x['lineage'] = ['protected-parent']; hashes.add('protected-parent')
        elif change == 'held': x['human']['action'] = 'held'
        elif change == 'unconfirmed': x['human']['content_compared'] = False
        else: x['human']['origin'] = 'automated_rule'
        out = e.learning_eligibility(x, protected_hashes=hashes, protected_groups=groups)
        assert not out['rule_example'] and not out['model_training'] and not out['negative_example']


def test_malformed_lineage_is_not_interpreted_as_characters_and_allowed_to_learn():
    r = human_record(); r['lineage'] = 'protected-parent'; r['rights']['model_training'] = True
    result = e.learning_eligibility(r, protected_hashes={'protected-parent'}, protected_groups=set())
    assert not result['model_training'] and not result['rule_example']


def test_completed_model_without_actual_input_model_and_checker_bindings_is_historical(web):
    store, _ = web; ident, rev = uploaded(store); doc = record(ident, rev)
    doc['sections'][0]['models'][0]['status'] = 'completed'; save(store, doc)
    model = e.load(store, ident, rev, rev['data']['files'])['sections'][0]['models'][0]
    assert model['status'] == 'historical_input_not_reinferred'


@pytest.mark.parametrize('changed_field', [None, 'ordered_input_sha256', 'model_sha256', 'input_revision', 'checker_version'])
def test_only_exact_model_input_checkpoint_and_checker_match_is_current(web, changed_field):
    store, _ = web; ident, rev = uploaded(store); doc = record(ident, rev)
    doc['versions']['input_revision'] = 'evidence-v2'
    doc['models_sha256'] = {'R1': 'b' * 64}
    section = doc['sections'][0]; section['model_input_sha256'] = 'c' * 64
    model = section['models'][0]
    model.update(status='completed', ordered_input_sha256='c' * 64, model_sha256='b' * 64,
                 input_revision='evidence-v2', checker_version=doc['versions']['checker'])
    if changed_field: model[changed_field] = 'stale'
    save(store, doc)
    result = e.load(store, ident, rev, rev['data']['files'])['sections'][0]['models'][0]
    assert result['status'] == ('historical_input_not_reinferred' if changed_field else 'completed')


@pytest.mark.parametrize('field,value', [('lineage', ['parent', 'parent']), ('source_sections', 'not-sections'),
                                       ('versions', []), ('rights', []), ('dom_sections', [{'source_refs': ['other'], 'visible_text': 'x'}])])
def test_malformed_human_contract_has_no_learning_authority(field, value):
    r = human_record(); r[field] = value
    result = e.learning_eligibility(r, protected_hashes=set(), protected_groups=set())
    assert not result['rule_example'] and not result['model_training']


@pytest.mark.parametrize('field', ['evidence', 'status'])
def test_malformed_explanation_cannot_break_an_otherwise_valid_review_page(web, field):
    store, _ = web; ident, rev = uploaded(store); doc = record(ident, rev)
    if field == 'evidence': doc['sections'][0]['evidence'] = []
    else: doc['sections'][0]['models'][0]['status'] = []
    save(store, doc)
    assert e.load(store, ident, rev, rev['data']['files'])['status'] == 'invalid'
