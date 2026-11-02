"""Control bytes must not consume following Korean fact values."""
from moa.extract import _hwp_text, _hwp_content
from test_moa import _hwp_rec


def test_lone_control_keeps_following_korean_text_in_real_record_flow():
    records=_hwp_rec(67,1,'참관\x18신청 과목\r'.encode('utf-16-le'))
    tables,blocks=_hwp_content(records)
    assert not tables
    assert blocks==[{'type':'paragraph','text':'참관신청 과목'}]


def test_paired_inline_control_and_extended_tab_do_not_leak_metadata():
    # Valid 8-unit controls include a closing marker. Printable metadata inside
    # them is not notice content; ordinary text after a lone marker is content.
    assert _hwp_text(('앞\x03FIELD!\x03뒤\r').encode('utf-16-le'))=='앞뒤'
    assert _hwp_text(('앞\x09TABXYZ\x09뒤\r').encode('utf-16-le'))=='앞 뒤'


def test_hwp_space_controls_keep_word_boundaries():
    assert _hwp_text('전\x1e학생과 전\x1f교직원'.encode('utf-16-le'))=='전 학생과 전 교직원'


def test_extended_control_length_is_utf16_units_not_unicode_codepoints():
    # The six metadata WCHARs may include a surrogate pair: this is still an
    # eight-unit control, not an excuse to leak metadata into notice text.
    assert _hwp_text(('앞\x0bAB😀CD\x0b뒤\r').encode('utf-16-le'))=='앞뒤'


def test_malformed_control_does_not_swallow_following_printable_text():
    assert _hwp_text(('\x0bOCR어쩌구'+ '\x00'*6 + '\x0b\x00본문').encode('utf-16-le'))=='OCR어쩌구본문'
