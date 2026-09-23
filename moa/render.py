"""Mobile HTML rendering of reviewed table structures.

Only fixed components are used; cell text is emitted verbatim (dates, amounts,
conditions are never rewritten). Anything unclear falls back to the raw table.
"""
from __future__ import annotations

import html

from .extract import LAYOUTS


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ''))


def _grid(table: dict) -> list:
    rows = table.get('rows', 0)
    cols = table.get('cols', 0)
    grid = [[None] * cols for _ in range(rows)]
    for c in table.get('cells', []):
        if 0 <= c['row'] < rows and 0 <= c['col'] < cols:
            grid[c['row']][c['col']] = c
    return grid


def _raw_table(table: dict) -> str:
    """The complete table, merged cells preserved. Always available as fallback."""
    out = ['<table class="moa-raw"><tbody>']
    for row in _grid(table):
        out.append('<tr>')
        for cell in row:
            if cell is None:
                continue
            tag = 'th' if cell.get('header') else 'td'
            span = ''
            if cell.get('rowspan', 1) > 1:
                span += ' rowspan="%d"' % cell['rowspan']
            if cell.get('colspan', 1) > 1:
                span += ' colspan="%d"' % cell['colspan']
            out.append('<%s%s>%s</%s>' % (tag, span, _esc(cell['text']), tag))
        out.append('</tr>')
    out.append('</tbody></table>')
    return ''.join(out)


def _key_value(table: dict) -> str:
    """2-column field/value table -> stacked cards. Wider tables keep raw form."""
    if table.get('cols', 0) != 2:
        return ''
    cards = []
    for row in _grid(table):
        cells = [c for c in row if c]
        if len(cells) == 2:
            cards.append('<div class="moa-kv"><div class="moa-k">%s</div>'
                         '<div class="moa-v">%s</div></div>'
                         % (_esc(cells[0]['text']), _esc(cells[1]['text'])))
        elif cells:
            cards.append('<div class="moa-kv"><div class="moa-v">%s</div></div>'
                         % _esc(' '.join(c['text'] for c in cells)))
    return '<div class="moa-cards">' + ''.join(cards) + '</div>' if cards else ''


def _grade_cards(table: dict) -> str:
    """Rows grouped by their first cell (grade/class) into per-group cards."""
    grid = _grid(table)
    if len(grid) < 2:
        return ''
    header = [c['text'] for c in grid[0] if c]
    groups = []
    for row in grid[1:]:
        cells = [c for c in row if c]
        if not cells:
            continue
        title = cells[0]['text']
        pairs = ''.join('<div class="moa-kv"><div class="moa-k">%s</div>'
                        '<div class="moa-v">%s</div></div>'
                        % (_esc(header[i] if i < len(header) else ''), _esc(c['text']))
                        for i, c in enumerate(cells[1:], 1))
        groups.append('<div class="moa-card"><h4>%s</h4>%s</div>' % (_esc(title), pairs))
    return '<div class="moa-cards">' + ''.join(groups) + '</div>' if groups else ''


def _timeline(table: dict) -> str:
    items = []
    for row in _grid(table):
        cells = [c for c in row if c]
        if not cells:
            continue
        when = cells[0]['text']
        rest = ' '.join(c['text'] for c in cells[1:])
        items.append('<li><span class="moa-when">%s</span><span>%s</span></li>'
                     % (_esc(when), _esc(rest)))
    return '<ol class="moa-timeline">' + ''.join(items) + '</ol>' if items else ''


CSS = ('body{font-family:system-ui;margin:0;padding:12px;background:#f6f7f9;color:#222}'
       '.moa-cards{display:grid;gap:10px}.moa-card{background:#fff;border-radius:10px;'
       'padding:10px 12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}'
       '.moa-card h4{margin:0 0 6px;font-size:15px}'
       '.moa-kv{display:flex;gap:8px;background:#fff;border-radius:10px;padding:10px 12px;'
       'margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}'
       '.moa-k{flex:0 0 34%;font-weight:600;color:#456}'
       '.moa-v{flex:1;white-space:pre-wrap;overflow-wrap:anywhere}'
       '.moa-timeline{margin:0;background:#fff;border-radius:10px;'
       'padding:12px 12px 12px 32px}.moa-timeline li{margin:6px 0}'
       '.moa-when{font-weight:600;color:#0a5;margin-right:8px}'
       '.moa-scroll{overflow-x:auto;background:#fff;border-radius:10px;padding:4px}'
       'table.moa-raw{border-collapse:collapse;width:100%;font-size:13px}'
       'table.moa-raw th,table.moa-raw td{border:1px solid #ccd;padding:6px 8px;'
       'text-align:left;vertical-align:top;white-space:pre-wrap}'
       'table.moa-raw th{background:#eef2f7}'
       '.moa-note{color:#789;font-size:12px;margin:8px 2px}'
       'details{margin-top:10px}summary{color:#468;cursor:pointer}')


def render_mobile(table: dict, layout: str) -> str:
    """Render one extracted/corrected table to a mobile HTML fragment.

    The component is chosen only from the fixed LAYOUTS set; every render ends with
    the raw table inside <details> so no cell is ever hidden by the conversion.
    """
    if not table.get('cells'):
        return '<p class="moa-note">추출된 표가 없습니다. 원문을 확인하세요.</p>'
    body = ''
    if layout == 'key_value_cards':
        body = _key_value(table)
    elif layout == 'grade_cards':
        body = _grade_cards(table)
    elif layout == 'timeline':
        body = _timeline(table)
    elif layout in ('scroll_table', 'comparison', 'read_only_form'):
        body = '<div class="moa-scroll">' + _raw_table(table) + '</div>'
    if not body:
        return ('<p class="moa-note">이 구조는 아직 전용 컴포넌트가 없어 원문 표를 표시합니다.</p>'
                '<div class="moa-scroll">' + _raw_table(table) + '</div>')
    return (body + '<details><summary>원문 표 그대로 보기</summary>'
            '<div class="moa-scroll">' + _raw_table(table) + '</div></details>')


def render_page(title: str, fragment: str) -> str:
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><h3>%s</h3>%s</body></html>'
            % (_esc(title), CSS, _esc(title), fragment))
