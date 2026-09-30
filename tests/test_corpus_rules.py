"""Tests for moa.corpus_rules auto-classification of school-notice files.

All descriptors are synthetic. Auto classification is never human approval:
no result may be 'approved' and 'candidate' is not permission to train.
"""

import pytest

from moa.corpus_rules import classify_file, summarise

ROLES = {"convert", "attachment", "uncertain"}
USES = {"candidate", "attachment", "held", "excluded", "evaluation"}
KINDS = {"guide", "form", "reference", "mixed", "unknown"}


def desc(**overrides):
    base = {
        "filename": "notice.hwp",
        "kind": "hwp",
        "status": "extracted",
        "text": "2024학년도 방과후학교 운영 안내입니다. 수강 일정과 준비물을 확인하세요.",
        "parser_current": True,
        "evaluation": False,
        "incomplete": False,
        "privacy_flag": False,
        "rights_denied": False,
        "unrelated": False,
    }
    base.update(overrides)
    return base


def test_vocabularies_and_no_approval():
    r = classify_file(desc())
    assert r["role"] in ROLES
    assert r["use"] in USES
    assert r["content_kind"] in KINDS
    assert isinstance(r["reasons"], list)
    assert r["use"] != "approved"
    assert r.get("role") != "approved"


# --- happy path -------------------------------------------------------------

@pytest.mark.parametrize("kind", ["html", "pdf", "hwp", "hwpx", "docx"])
def test_extracted_current_guide_is_candidate(kind):
    r = classify_file(desc(kind=kind, filename=f"guide.{kind}"))
    assert r["role"] == "convert"
    assert r["use"] == "candidate"
    assert r["content_kind"] == "guide"
    assert "rights_unreviewed" in r["reasons"]
    assert r["use"] != "approved"


# --- clear filename forms / reference ---------------------------------------

@pytest.mark.parametrize("name", ["수강 신청서.hwp", "개인정보 동의서.pdf", "제출 서식.docx", "참가 양식.hwpx"])
def test_form_filename_is_attachment(name):
    r = classify_file(desc(filename=name, text=""))
    assert r["role"] == "attachment"
    assert r["use"] == "attachment"
    assert r["content_kind"] == "form"


@pytest.mark.parametrize("name", ["참고자료.pdf", "안전교육 자료집.pdf"])
def test_reference_filename_is_attachment(name):
    r = classify_file(desc(filename=name))
    assert r["role"] == "attachment"
    assert r["use"] == "attachment"
    assert r["content_kind"] == "reference"


def test_unsupported_original_form_still_attachment():
    r = classify_file(desc(filename="동의서.hwp", kind="unsupported", status="needs_parser", text=""))
    assert r["role"] == "attachment"
    assert r["use"] == "attachment"
    assert r["content_kind"] == "form"


# --- mixed boundaries ---------------------------------------------------------

MIXED_TEXT = (
    "프로그램 운영 안내 및 일정을 확인하세요.\n"
    "희망 신청서\n"
    "지원자 성명: ___  보호자 성명: ___  연락처: ___  서명: ___"
)


def test_filename_mixed_guide_and_form_is_held():
    r = classify_file(desc(filename="안내 및 신청서.hwp", text=MIXED_TEXT))
    assert r["role"] == "convert"
    assert r["use"] == "held"
    assert r["content_kind"] == "mixed"
    assert "boundary_unconfirmed" in r["reasons"]


def test_text_mixed_guide_and_form_is_held():
    r = classify_file(desc(text=MIXED_TEXT))
    assert r["role"] == "convert"
    assert r["use"] == "held"
    assert r["content_kind"] == "mixed"
    assert "boundary_unconfirmed" in r["reasons"]


def test_submission_wording_alone_is_not_mixed():
    r = classify_file(desc(text="행사 안내입니다. 참여를 원하면 신청서를 제출하세요. "
                               "일정은 3월 15일이며 장소는 강당입니다."))
    assert r["content_kind"] == "guide"
    assert r["use"] == "candidate"


# --- thin / unusable content ----------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "알림"])
def test_thin_text_is_uncertain_held(text):
    r = classify_file(desc(text=text))
    assert r["role"] == "uncertain"
    assert r["use"] == "held"
    assert r["content_kind"] == "unknown"


@pytest.mark.parametrize("status", ["needs_parser", "needs_vision", "parse_error", "missing"])
def test_unusable_status_held_not_candidate(status):
    r = classify_file(desc(status=status))
    assert r["use"] == "held"
    assert r["use"] != "candidate"


def test_image_guide_held():
    r = classify_file(desc(kind="image", filename="poster.png", status="needs_vision", text=""))
    assert r["use"] == "held"
    assert r["use"] != "candidate"


def test_missing_file_held():
    r = classify_file(desc(status="missing", text=""))
    assert r["use"] == "held"


def test_missing_original_form_is_held_not_a_usable_attachment():
    r=classify_file(desc(filename='신청서.hwp',status='missing',text=''))
    assert r['role']=='attachment' and r['use']=='held'
    assert 'original_missing' in r['reasons']
    assert summarise([desc(),r])['use']=='held'


# --- held reason gates ----------------------------------------------------------

def test_stale_parser_held_with_reason():
    r = classify_file(desc(parser_current=False))
    assert r["use"] == "held"
    assert "parser_stale" in r["reasons"]


def test_incomplete_held_with_reason():
    r = classify_file(desc(incomplete=True))
    assert r["use"] == "held"
    assert "incomplete" in r["reasons"]


def test_privacy_flag_held_with_reason():
    r = classify_file(desc(privacy_flag=True))
    assert r["use"] == "held"
    assert "privacy_review" in r["reasons"]


# --- exclusion ------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["rights_denied", "unrelated"])
def test_confirmed_exclusion(flag):
    r = classify_file(desc(**{flag: True}))
    assert r["use"] == "excluded"
    assert r["use"] != "candidate"


# --- evaluation override ----------------------------------------------------------

def test_evaluation_overrides_good_guide():
    r = classify_file(desc(evaluation=True))
    assert r["use"] == "evaluation"
    assert r["use"] != "candidate"


def test_evaluation_overrides_attachment():
    r = classify_file(desc(evaluation=True, filename="신청서.hwp"))
    assert r["use"] == "evaluation"


def test_evaluation_overrides_privacy():
    r = classify_file(desc(evaluation=True, privacy_flag=True))
    assert r["use"] == "evaluation"
    assert r["use"] != "candidate"


# --- hygiene -----------------------------------------------------------------------

def test_reasons_carry_no_source_facts():
    fname = "2024 3학년 김철수 신청서.hwp"
    r = classify_file(desc(filename=fname, text="연락처 010-1234-5678 김철수"))
    blob = " ".join(r["reasons"])
    assert "김철수" not in blob
    assert "010-1234-5678" not in blob
    assert fname not in blob
    for code in r["reasons"]:
        assert code == code.lower()


def test_deterministic():
    d = desc(text=MIXED_TEXT)
    assert classify_file(d) == classify_file(dict(d))


def test_defaults_when_booleans_absent():
    d = {"filename": "a.hwp", "kind": "hwp", "status": "extracted", "text": "충분한 안내 내용이 담긴 문서입니다."}
    r = classify_file(d)
    assert r["use"] == "candidate"  # parser_current defaults true


# --- summarise ----------------------------------------------------------------------

def test_summary_candidate_plus_attachments():
    files = [desc(), desc(filename="신청서.hwp"), desc(filename="참고자료.pdf")]
    s = summarise(files)
    assert s["use"] == "candidate"
    assert s["counts"] == {"candidate": 1, "attachment": 2}
    assert s["use"] != "approved"


def test_summary_all_attachments():
    files = [desc(filename="신청서.hwp"), desc(filename="동의서.pdf")]
    s = summarise(files)
    assert s["use"] == "attachment"
    assert s["counts"] == {"attachment": 2}


def test_summary_empty_is_held():
    s = summarise([])
    assert s["use"] == "held"


def test_summary_held_guide_blocks_candidate():
    files = [desc(), desc(privacy_flag=True)]
    s = summarise(files)
    assert s["use"] == "held"
    assert s["use"] != "candidate"


def test_summary_uncertain_guide_means_held():
    files = [desc(), desc(text="")]
    s = summarise(files)
    assert s["use"] == "held"


def test_summary_excluded_guide_blocks_candidate():
    files = [desc(), desc(rights_denied=True)]
    s = summarise(files)
    assert s["use"] != "candidate"
    assert s["use"] != "approved"


def test_summary_evaluation_dominates():
    files = [desc(), desc(evaluation=True), desc(filename="신청서.hwp")]
    s = summarise(files)
    assert s["use"] == "evaluation"
    assert "evaluation" in s["counts"]


def test_summary_never_approved():
    for files in ([desc()], [desc(), desc(filename="신청서.hwp")], []):
        assert summarise(files)["use"] != "approved"


def test_image_with_misleading_extracted_status_is_not_candidate():
    r=classify_file(desc(kind='image',status='extracted'))
    assert r['use']=='held'


def test_content_only_form_with_empty_fields_is_original_only():
    r=classify_file(desc(text='희망 신청서\n지원자 성명 ( )\n보호자 ( )\n서명 ( )'))
    assert r['content_kind']=='form' and r['use']=='attachment'


def test_mixed_form_with_empty_fields_is_held():
    r=classify_file(desc(text='행사 안내 및 신청 일정입니다.\n희망 신청서\n지원자 성명 ( )\n보호자 ( )\n서명 ( )'))
    assert r['content_kind']=='mixed' and r['use']=='held'


def test_excluded_original_attachment_does_not_disqualify_safe_guide():
    s=summarise([{'role':'convert','use':'candidate'},{'role':'attachment','use':'excluded'}])
    assert s['use']=='candidate'
