"""Trial evidence cannot masquerade as independent real-world accuracy."""
from moa import shadow_quality as quality


def test_per_error_recall_and_normal_false_positives_have_separate_denominators():
    rows = [
        {'expected': 'changed', 'rule_label': 'changed', 'model_label': 'preserved', 'error': 'room_exchange'},
        {'expected': 'changed', 'rule_label': 'changed', 'model_label': 'changed', 'error': 'date'},
        {'expected': 'preserved', 'rule_label': 'preserved', 'model_label': 'changed', 'error': 'identity'},
        {'expected': 'preserved', 'rule_label': 'preserved', 'model_label': 'preserved', 'error': 'identity'}]
    report = quality.benchmark_metrics(rows)
    assert report['rules']['changed_recall'] == 1
    assert report['model']['changed_recall'] == .5
    assert report['combined']['changed_recall'] == 1
    assert report['rules']['normal_false_positive_rate'] == 0
    assert report['model']['normal_false_positive_rate'] == .5
    assert report['combined']['normal_false_positive_rate'] == .5
    assert report['by_error']['room_exchange']['combined_detected'] == 1


def test_unknown_or_unrun_results_do_not_count_as_correct_or_normal_gold():
    report = quality.benchmark_metrics([
        {'expected': None, 'rule_label': 'preserved', 'model_label': 'preserved', 'error': 'real_unreviewed'},
        {'expected': 'changed', 'rule_label': 'changed', 'model_label': None, 'error': 'date'}])
    assert report['excluded_without_independent_gold'] == 1
    assert report['model']['evaluated'] == 0
    assert report['model']['unrun'] == 1
    assert report['combined']['changed_recall'] == 1
    assert report['combined']['normal_false_positive_rate'] is None


def test_table_section_payload_uses_actual_dom_and_never_exposes_labels():
    source = {'id': 's1', 'origin_block': 'f:0', 'text': '학과 / 교실\n법학과 / 2-1',
              'table_rows': [['학과','교실'],['법학과','2-1']]}
    manifest = {'sources': [source], 'parser_coverage_complete': True, 'files': []}
    view = {'blocks': [{'source_block': 'f:0', 'visible_text': '학과 교실 법학과 2-2',
                       'table_rows': [['학과','교실'],['법학과','2-2']]}]}
    parts = quality.inference_sections(manifest, view)
    assert len(parts) == 1
    assert parts[0]['payload']['source_blocks'][0]['text'] == '학과 / 교실\n법학과 / 2-1'
    assert parts[0]['payload']['rendered_blocks'][0]['visible_text'] == '학과 / 교실\n법학과 / 2-2'
    assert parts[0]['payload']['questions'] == ['table_relations']
    assert 'expected' not in parts[0]['payload']
    assert not parts[0]['payload']['deterministic_findings']
