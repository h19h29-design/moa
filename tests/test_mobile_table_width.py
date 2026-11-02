"""Wide merged tables must scroll instead of squeezing Korean into one-letter columns."""
from moa.render import render_mobile


def test_wide_merged_table_has_readable_minimum_width_and_keeps_spans():
    table={'rows':2,'cols':8,'cells':[
        {'row':0,'col':0,'rowspan':1,'colspan':8,'text':'학년별 공개수업 안내'},
        *({'row':1,'col':i,'rowspan':1,'colspan':1,'text':f'{i+1}학년'} for i in range(8))]}
    fragment=render_mobile(table,'grade_cards')
    assert 'class="moa-scroll"' in fragment
    assert 'min-width:640px' in fragment
    assert 'colspan="8"' in fragment
    assert all(f'{i+1}학년' in fragment for i in range(8))
