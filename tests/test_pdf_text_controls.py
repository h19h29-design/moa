"""Synthetic PDF with a broken space Unicode map, like collected school files."""
from moa.extract import pdf_document


def control_space_pdf():
    content=b'BT /F1 12 Tf 40 700 Td (Submission deadline is 2026.10.02 at 17:00.) Tj ET'
    cmap=(b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap '
          b'/CIDSystemInfo << /Registry (Test) /Ordering (Unicode) /Supplement 0 >> def '
          b'/CMapName /Test def /CMapType 2 def '
          b'1 begincodespacerange <00> <FF> endcodespacerange '
          b'1 beginbfchar <20> <0001> endbfchar '
          b'endcmap CMapName currentdict /CMap defineresource pop end end')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>',
        b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'\nendstream',
        b'<< /Length '+str(len(cmap)).encode()+b' >>\nstream\n'+cmap+b'\nendstream']
    raw=b'%PDF-1.4\n';offsets=[]
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw));raw+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(raw)
    raw+=b'xref\n0 7\n0000000000 65535 f \n'+b''.join(
        f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    return raw+b'trailer << /Size 7 /Root 1 0 R >>\nstartxref\n'+str(start).encode()+b'\n%%EOF'


def test_pdf_unmapped_control_cannot_be_claimed_complete_visible_notice():
    result=pdf_document(control_space_pdf())
    # A parser must either independently recover readable text, or keep this
    # file pending. Hidden control bytes must not prove complete conversion.
    assert result['status'] != 'extracted' or '\x01' not in result['text']
    assert '2026.10.02' in result['text']
