"""Conservative auto-classification rules for school-notice corpus files.

These heuristics NEVER grant approval: ``candidate`` is not permission to
train, only a signal that a file looks like convertible guide text. All
functions are pure, deterministic, and standard-library only.

Use-label precedence (highest first):
    evaluation > excluded > held > attachment > candidate

Known limitations: cues use Korean filenames/headings, blank-field markers
and common form-field labels. They cannot reliably detect scanned
forms, ambiguous mixed documents, or guides that embed a full
form inline without a heading line; such cases fall back to conservative
labels. Reason codes are fixed machine strings and never embed source
facts.
"""

# Literal substring cues. Deliberately small and conservative.
FORM_CUES = ("신청서", "동의서", "서식", "양식")
REFERENCE_CUES = ("참고자료", "자료집")
GUIDE_CUES = ("안내", "일정", "운영", "공지")

MIN_USEFUL_CHARS = 10  # stripped-text length below this is 'thin'
BLANK_FIELD = "___"
MIN_FORM_FIELDS = 2

USABLE_STATUS = "extracted"


def _has_any(haystack, cues):
    return any(c in haystack for c in cues)


def _has_form_heading(text):
    """A line whose content is essentially a form title (e.g. '희망 신청서').

    A mention like '신청서를 제출하세요' inside a sentence does NOT count:
    the line must be short, so it looks like a heading, not a sentence.
    """
    for line in text.splitlines():
        s = line.strip()
        if s and len(s) <= 15 and _has_any(s, FORM_CUES):
            return True
    return False


def _blank_field_count(text):
    return text.count(BLANK_FIELD)


def _detect_content_kind(filename, text):
    """Return content_kind or None if the text is too thin to tell."""
    fname_form = _has_any(filename, FORM_CUES)
    fname_ref = _has_any(filename, REFERENCE_CUES)
    fname_guide = _has_any(filename, GUIDE_CUES)

    stripped = text.strip()
    has_guide = _has_any(stripped, GUIDE_CUES)
    has_heading = _has_form_heading(text)
    blanks = _blank_field_count(text)
    fields = sum(word in text for word in ('지원자', '보호자', '서명', '성명', '신청자'))
    has_fields = blanks >= MIN_FORM_FIELDS or fields >= 3

    # Mixed: guide material coexists with a real form (heading + blanks),
    # or the filename itself announces both. Boundary is never guessed;
    # caller must hold for human confirmation.
    if fname_form and fname_guide:
        return "mixed"
    if has_guide and has_heading and has_fields:
        return "mixed"

    if fname_form:
        return "form"
    if fname_ref:
        return "reference"

    # Content-only form: form heading + multiple blank fields + NO guide
    # introduction. Required because a filename may not name the form.
    if has_heading and has_fields and not has_guide:
        return "form"

    if len(stripped) < MIN_USEFUL_CHARS:
        return "unknown"
    return "guide"


def classify_file(descriptor):
    """Classify one file descriptor dict.

    Returns {'role', 'use', 'content_kind', 'reasons'}. Pure; does not
    mutate the input. Never returns 'approved'.
    """
    d = dict(descriptor)
    filename = d.get("filename") or ""
    text = d.get("text") or ""
    status = d.get("status") or ""

    content_kind = _detect_content_kind(filename, text)

    if content_kind in ("form", "reference"):
        role = "attachment"
    elif content_kind == "unknown":
        role = "uncertain"
    else:  # guide or mixed stay in the conversion lane, possibly held
        role = "convert"

    reasons = []
    if content_kind == "form":
        reasons.append("original_form")
    elif content_kind == "reference":
        reasons.append("original_reference")
    elif content_kind == "mixed":
        reasons.append("boundary_unconfirmed")
    elif content_kind == "unknown":
        reasons.append("insufficient_text")

    # Hold gates apply only to conversion-lane items: original forms and
    # references remain attachments even when unsupported or unparseable.
    hold_reasons = []
    if role != "attachment":
        if not d.get("parser_current", True):
            hold_reasons.append("parser_stale")
        if d.get("incomplete", False):
            hold_reasons.append("incomplete")
        if d.get("privacy_flag", False):
            hold_reasons.append("privacy_review")
        if status != USABLE_STATUS:
            hold_reasons.append("status_unusable")
        if d.get('kind') not in ('html','pdf','hwp','hwpx','docx'):
            hold_reasons.append('needs_vision' if d.get('kind')=='image' else 'needs_parser')
        if content_kind == "mixed":
            hold_reasons.append("boundary_unconfirmed")
        if content_kind == "unknown":
            hold_reasons.append("insufficient_text")

    # Use-label precedence: evaluation > excluded > held/attachment.
    # Attachments bypass hold gates (an unparsed original is still an
    # attachment) but never bypass evaluation or confirmed exclusion.
    if d.get("evaluation", False):
        use = "evaluation"
        reasons.append("evaluation")
    elif d.get("rights_denied", False):
        use = "excluded"
        reasons.append("rights_denied")
    elif d.get("unrelated", False):
        use = "excluded"
        reasons.append("unrelated")
    elif status=='missing':
        use='held'
        reasons.append('original_missing')
    elif role == "attachment":
        use = "attachment"
    elif hold_reasons:
        use = "held"
        reasons.extend(hold_reasons)
    else:
        use = "candidate"
        reasons.append("rights_unreviewed")

    # Deduplicate while preserving order; codes are fixed lowercase strings.
    seen = set()
    ordered = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            ordered.append(r)

    return {
        "role": role,
        "use": use,
        "content_kind": content_kind,
        "reasons": ordered,
    }


def _as_result(item):
    """Accept a raw descriptor or an already-classified result dict."""
    if "use" in item and "role" in item:
        return item
    return classify_file(item)


def summarise(items):
    """Summarise a list of descriptors or classified results.

    Corpus-level precedence: evaluation > excluded > held > attachment >
    candidate. A held or excluded conversion-lane item blocks 'candidate'.
    Never returns 'approved'. Does not mutate inputs.
    """
    results = [_as_result(i) for i in items]

    counts = {}
    for r in results:
        u = r.get("use", "held")
        counts[u] = counts.get(u, 0) + 1

    if not results:
        use = "held"
    elif "evaluation" in counts:
        use = "evaluation"
    elif any(r.get('use')=='excluded' and r.get('role')!='attachment' for r in results):
        use = "excluded"
    elif any(r.get('role')=='uncertain' or r.get('use')=='held' for r in results):
        use = "held"
    elif "candidate" in counts:
        use = "candidate"
    elif counts.get("attachment"):
        use = "attachment"
    else:
        use = "held"

    return {"use": use, "counts": counts}
