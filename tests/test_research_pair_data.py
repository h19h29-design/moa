import copy
import hashlib

import pytest

from moa import research_pair_data as d


def test_author_replays_all_gold_and_schemas_are_not_random_family_names():
    docs = d.author()
    assert len(docs) == 216
    assert len({r['schema']['structural_sha256'] for r in docs}) == 18
    assert all(d.validate_oracle(r) == r['oracle']['expected'] for r in docs)
    assert {r['split'] for r in docs} == {'train', 'dev'}
    assert len({r['family'] for r in docs if r['split'] == 'train'}) == 12
    assert not ({r['family'] for r in docs if r['split'] == 'train'} &
                {r['family'] for r in docs if r['split'] == 'dev'})


def test_changed_source_fact_cannot_be_relabelled_as_a_normal_reflow():
    doc = copy.deepcopy(d.author()[0])
    doc['oracle']['rendered_facts']['rows'][0][-1] = '다른 교실'
    with pytest.raises(ValueError, match='replay'):
        d.validate_oracle(doc)


def test_build_requires_browser_provenance_and_preserves_source_gap():
    docs = d.author()
    seen = {}
    for doc in docs:
        seen[doc['case_id']] = {'html_sha256': hashlib.sha256(doc['html'].encode()).hexdigest(),
            'observer': 'chromium-innerText-v1', 'network_blocked': True, 'page_errors': 0,
            'blocks': [{'id': 'd-'+b['id'], 'source_refs': [b['id']], 'visible_text': b['text']}
                       for b in d.sections(doc['oracle']['rendered_facts'])]}
    rows, whole, summary = d.build(docs, seen)
    assert summary['families'] == 18
    assert summary['counts']['train'] == {'preserved': 36, 'changed': 72, 'insufficient': 36}
    assert all(not r['state']['source_complete'] for r in rows
               if next(iter(r['expected'].values())) == 'insufficient')
    assert all(len(r['state']['source_blocks']) < len(w['state']['source_blocks'])
               for r, w in zip(rows, whole))
    assert len({tuple(r['questions']) for r in rows}) == 1  # no question/label shortcut
    seen[docs[0]['case_id']]['html_sha256'] = '0'*64
    with pytest.raises(ValueError, match='DOM'):
        d.build(docs, seen)
