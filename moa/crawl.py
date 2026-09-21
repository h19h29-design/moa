"""NEIS registry and conservative, non-JavaScript school-board adapters."""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .core import kst_now, write_json
from .net import canonical_url

NOTICE = re.compile(r'가정\s*통신|학부모\s*통신|가정\s*안내|학교\s*통신')
POST = re.compile(r'/view/\d+|/read/\d+|selectNttInfo\.do|boardCnts/view\.do|[?&](?:nttSn|boardSeq|wr_id|articleSeq|num|idx)=[\w-]+', re.I)
EXT = re.compile(r'\.(?:pdf|hwpx?|docx|png|jpe?g|webp)(?:$|[?&#\s(])', re.I)
DOWNLOAD = re.compile(r'nttFileDownload|fileDown|download|atchFile|atchmnfl', re.I)
# Online viewers wrap the same file in HTML; the real download link is kept separately.
VIEWER = re.compile(r'/synap/skin/|/webviewer|documentViewer|/DocViewer|/viewer\.do|/skin/doc\.html', re.I)
BODY_SELECTORS = ('#nttCn', '.nttCn', '.bbs_view_cont', '.bbs_view_con', '.view_con', '.view_cont',
                  '.viewCont', '#boardContents', '.board_view_con', '.board_view_content',
                  '.bbsContent', '.view-content', '.board-view-body', '.bbs_view_body',
                  'td.tch-ctnt', '.tch-ctnt', '.usm-editor-view', '.usm-content-body-id',
                  '.bbs_view_cont_wrap', '.view_cont_wrap', '#content_body',
                  '.viewBox', '.cntBody', '.board-text', 'article')
TITLE_SELECTORS = ('.nttTitle', '.bbs_view_tit', '.bbs_view_title', '.view_title', '.board_view_title',
                   '.subject', 'th.tch-tit', '.tch-tit', '.bbs_view_tit_wrap', 'h1.tit', '.viewBox h1',
                   'h1.title', 'h2.title')
# Small shell pages such as <script>location.href='/main.do'</script> are the norm on school sites.
JS_TARGET = re.compile(r'''(?:location\s*\.\s*(?:href|replace)\s*(?:=\s*|\()|(?:document|window)\s*\.\s*location(?:\s*\.\s*href)?\s*=\s*|location\s*=\s*)["']([^"']{1,300})["']''', re.I)
META_TARGET = re.compile(r'''<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["'][^;"']*;\s*url=([^"'\s>]+)''', re.I)
SHELL_LIMIT = 6000


def redirect_targets(html: str) -> list[str]:
    """Absolute-https first, then absolute-http, then site-relative shell redirects."""
    cleaned = re.sub(r'<!--.*?-->', '', html, flags=re.S)
    found = [m.group(1) for m in JS_TARGET.finditer(cleaned)] + [m.group(1) for m in META_TARGET.finditer(cleaned)]
    targets: list[str] = []
    for target in (t.strip() for t in found):
        if not target or target.startswith(('javascript:', '#', 'mailto:', 'tel:')) or target in targets:
            continue
        targets.append(target)
    return sorted(targets, key=lambda t: 0 if t.startswith('https://') else 1 if t.startswith('http://') else 2)


def _shell_target(page, seen: set[str]) -> str | None:
    if len(page.data) > SHELL_LIMIT:
        return None
    if BeautifulSoup(page.text, 'html.parser').select('a[href]'):
        return None
    for candidate in redirect_targets(page.text):
        try:
            resolved = canonical_url(urljoin(page.url, candidate))
        except ValueError:
            continue
        if resolved not in seen:
            return resolved
    return None


def open_pages(fetcher, url: str, hosts: set[str], hops: int = 2,
               allow_site_move: bool = False) -> tuple[list, set[str]]:
    """Fetch a page and follow the meta/JS redirect shells the site itself publishes.

    Many Korean school sites answer with a tiny <script>location.href=...</script> page
    (homepage, board or even a single post). Following those shells is ordinary public
    navigation; login walls, captchas and robots restrictions are never bypassed here.
    """
    seen = {url}
    page = fetcher.get(url, allowed_hosts=hosts, limit=2 * 1024 * 1024, allow_site_move=allow_site_move)
    pages = [page]
    for _ in range(hops):
        host = urlsplit(page.url).hostname
        if host:
            hosts.add(host)
        target = _shell_target(page, seen)
        if not target:
            break
        target_host = urlsplit(target).hostname
        if target_host not in hosts:
            if not allow_site_move:
                break
            if target_host:
                hosts.add(target_host)
        seen.add(target)
        page = fetcher.get(target, allowed_hosts=hosts, limit=2 * 1024 * 1024)
        pages.append(page)
    return pages, hosts


def resolve_site(fetcher, url: str, hosts: set[str], hops: int = 3) -> tuple[list, set[str]]:
    """Open the school's entry page, following hosting moves, redirect shells and framesets."""
    pages, hosts = open_pages(fetcher, url, hosts, hops=hops, allow_site_move=True)
    page = pages[-1]
    if not BeautifulSoup(page.text, 'html.parser').select('a[href]'):
        soup = BeautifulSoup(page.text, 'html.parser')
        for frame in [urljoin(page.url, f.get('src', '')) for f in soup.select('frame[src],iframe[src]') if f.get('src')][:2]:
            if urlsplit(frame).hostname in hosts:
                try:
                    frame_pages, hosts = open_pages(fetcher, frame, hosts, hops=1)
                except (ValueError, RuntimeError, PermissionError):
                    continue
                pages.extend(frame_pages)
    return pages, hosts


LOGIN_GATE = re.compile(r'fm_xb_login|loginpost\.php|name=["\']?(?:mem_id|userid)["\']?|input[^>]+type=["\']?password', re.I)


def login_required(html: str) -> bool:
    return bool(LOGIN_GATE.search(html))


def sitemap_candidates(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for a in soup.select('a'):
        url = _href(a, base)
        if url and '사이트맵' in (a.get_text() + a.get('title', '')) and url not in out:
            out.append(url)
    return out[:2]


def sync_schools(fetcher, root: Path, api_key: str) -> list[dict]:
    if not api_key or api_key.strip().lower() in ('sample', 'sample key', 'your-key', '여기에_입력'):
        raise ValueError('NEIS_API_KEY가 필요합니다. 샘플 5건으로 전국 수집을 시작하지 않습니다.')
    all_rows, seen, expected = [], set(), None
    records_read = 0
    for page in range(1, 101):
        query = urlencode({'KEY': api_key.strip(), 'Type': 'json', 'pIndex': page, 'pSize': 1000})
        payload = None
        last = ''
        for attempt in range(3):
            try:
                response = fetcher.get('https://open.neis.go.kr/hub/schoolInfo?' + query, robots=False,
                                       limit=20*1024*1024, allowed_hosts={'open.neis.go.kr'})
                payload = json.loads(response.text)
            except Exception as exc:
                # Never log a request exception that may contain the API key in its URL.
                last = type(exc).__name__
                payload = None
            if isinstance(payload, dict) and 'schoolInfo' in payload:
                break
            result = (payload or {}).get('RESULT', {})
            # INFO-200 (no data) is also what NEIS answers during short throttling windows.
            last = str(result.get('CODE', 'unknown')) + ' ' + str(result.get('MESSAGE', ''))[:120]
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
        if not isinstance(payload, dict) or 'schoolInfo' not in payload:
            # The API answers INFO-200 once the last page is passed; that is end-of-data, not a fault.
            if page > 1 and expected and records_read >= int(expected):
                break
            raise RuntimeError('NEIS 학교목록 응답 오류: ' + last)
        parts = payload['schoolInfo']
        head = next((p['head'] for p in parts if 'head' in p), [])
        total = next((h['list_total_count'] for h in head if 'list_total_count' in h), None)
        if total is None or int(total) <= 5:
            raise RuntimeError('전국 학교목록 전체 건수를 검증하지 못했습니다.')
        expected = int(total)
        rows = next((p['row'] for p in parts if 'row' in p), [])
        if not rows:
            raise RuntimeError('학교목록이 중간에 비었습니다. 기존 목록을 유지합니다.')
        records_read += len(rows)
        before = len(seen)
        for r in rows:
            key = (r['ATPT_OFCDC_SC_CODE'], r['SD_SCHUL_CODE'])
            if key in seen:
                continue
            seen.add(key)
            homepage = (r.get('HMPG_ADRES') or '').strip()
            if not homepage:
                continue
            if '://' not in homepage:
                homepage = 'https://' + homepage
            try:
                homepage = canonical_url(homepage)
            except ValueError:
                continue
            all_rows.append({'office_code': key[0], 'office_name': r['ATPT_OFCDC_SC_NM'],
                'school_code': key[1], 'school_name': r['SCHUL_NM'],
                'school_kind': r.get('SCHUL_KND_SC_NM','기타'), 'home_url': homepage})
        if len(seen) == before:
            raise RuntimeError('NEIS 페이지 반복 감지: 샘플 키 또는 페이징 오류')
        if records_read >= expected:
            break
        if len(rows) < 1000:  # last page answered a short page
            break
    if expected is None or records_read < expected:
        raise RuntimeError('학교목록 동기화 미완료(반복/샘플 응답 의심): 기존 목록을 유지합니다.')
    if not all_rows:
        raise RuntimeError('사용 가능한 학교 홈페이지가 없습니다.')
    offices = sorted({r['office_code'] for r in all_rows})
    if len(offices) < 17:
        raise RuntimeError(f'교육청 코드가 {len(offices)}개만 반환되었습니다. 부분/샘플 응답으로 보고 기존 목록을 유지합니다.')
    write_json(Path(root)/'registry/schools.json', {'schema_version': 1, 'source': 'NEIS schoolInfo',
        'synced_at': kst_now().isoformat(), 'total_records': records_read, 'unique_schools': len(seen),
        'offices': {code: sum(1 for r in all_rows if r['office_code'] == code) for code in offices},
        'schools': all_rows})
    return all_rows


def sampled_schools(schools: list[dict], day: str, office: str) -> list[dict]:
    seed = int(hashlib.sha256((day + ':' + office).encode()).hexdigest(), 16)
    rng = random.Random(seed)
    groups = defaultdict(list)
    for s in schools:
        groups[s['school_kind']].append(s)
    for group in groups.values():
        rng.shuffle(group)
    # School levels are shuffled as well, so the level that happens to be tried first is not
    # always the same one (the round-robin below keeps every level in the sample).
    keys = sorted(groups)
    rng.shuffle(keys)
    result = []
    # Random within school level, interleaved to avoid one-level-only samples.
    while any(groups.values()):
        for key in keys:
            if groups[key]:
                result.append(groups[key].pop())
    return result


def _href(a, base: str) -> str | None:
    href = a.get('href', '')
    if not href or href.startswith(('#', 'javascript:')):
        action = a.get('onclick','') + ' ' + href
        match = re.search(r'''(?:location(?:\.href)?\s*=|window\.open\()\s*['"]([^'"]+)''', action)
        href = match.group(1) if match else ''
    if not href:
        return None
    try:
        return canonical_url(urljoin(base, href))
    except ValueError:
        return None


def board_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html,'html.parser')
    out = []
    for a in soup.select('a'):
        url = _href(a, base)
        label = a.get_text(' ',strip=True) + ' ' + a.get('title','')
        if url and NOTICE.search(label) and not POST.search(url) and url != canonical_url(base):
            if url not in out:
                out.append(url)
    return out[:6]


def post_links(html: str, base: str, selector: str = '') -> list[dict]:
    soup = BeautifulSoup(html,'html.parser')
    out, seen = [], set()
    p = urlsplit(base)
    query = dict(parse_qsl(p.query))
    # Jinhak-style boards hide the post link inside a javascript: handler. The CMS convention is
    # selectNttList.do -> selectNttInfo.do with the same mi/bbsId parameters.
    list_to_info = None
    if 'selectNttList.do' in p.path:
        list_to_info = (p.scheme, p.netloc, p.path.replace('selectNttList.do', 'selectNttInfo.do'), query)
    # boardCnts CMS (Gangwon/Incheon/...): posts are opened by javascript:goView('boardID','boardSeq',...).
    list_to_view = None
    if '/boardCnts/list.do' in p.path:
        list_to_view = (p.scheme, p.netloc, p.path.replace('list.do', 'view.do'), query)
    for a in soup.select(selector or 'a'):
        title = a.get_text(' ',strip=True) or a.get('title','')
        url = _href(a,base)
        action = (a.get('onclick','') or '') + ' ' + (a.get('href','') or '')
        if (not url or not POST.search(url)) and list_to_info:
            ident = a.get('data-id') or a.get('data-nttsn') or a.get('data-num')
            match = re.search(r'''(?:nttInfo|goView|fnView|viewNtt|fnDetail|goDetail|viewArticle|[A-Za-z_]*[Vv]iew[A-Za-z_]*)\s*\(\s*[\'\"]?(\d{2,})''', action)
            ident = ident or (match.group(1) if match else None)
            if ident and str(ident).isdigit():
                scheme, netloc, path, base_query = list_to_info
                url = canonical_url(urlunsplit((scheme, netloc, path, urlencode({**base_query, 'nttSn': str(ident)}), '')))
        if (not url or not POST.search(url)) and list_to_view:
            call = re.search(r'''goView\s*\(([^)]{0,200})\)''', action)
            numbers = re.findall(r'\d{3,}', call.group(1)) if call else []
            if len(numbers) >= 2:
                scheme, netloc, path, base_query = list_to_view
                seq = {**base_query, 'boardID': numbers[0], 'boardSeq': numbers[-1], 'lev': '0'}
                url = canonical_url(urlunsplit((scheme, netloc, path, urlencode(seq), '')))
        if url and (POST.search(url) or selector) and title and url not in seen:
            if DOWNLOAD.search(url):
                continue
            seen.add(url)
            context = a.find_parent('tr') or a.find_parent('li') or a
            dates = re.findall(r'\b(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b',context.get_text(' ',strip=True))
            published = '-'.join((dates[-1][0],dates[-1][1].zfill(2),dates[-1][2].zfill(2))) if dates else None
            out.append({'url': url, 'title': title, 'published_date': published})
    return out[:50]


def detail(html: str, base: str, fallback_title: str, body_selector: str = '') -> dict:
    soup = BeautifulSoup(html,'html.parser')
    if soup.select_one('input[type="password"]') or any(s in soup.get_text() for s in ('로그인이 필요합니다','접근 권한이 없습니다','자동입력 방지문자')):
        raise ValueError('로그인/권한/자동입력 방지 페이지')
    heading = soup.select_one(', '.join(TITLE_SELECTORS))
    title = heading.get_text(' ',strip=True) if heading else fallback_title
    # boardCnts-style markup keeps a visible "제목" label inside the heading element.
    title = re.sub(r'^(?:제\s*목|title)\s*[:：]?\s*', '', title, flags=re.I).strip() or fallback_title
    attachments, used = [], set()
    for a in soup.select('a'):
        url = _href(a,base)
        label = a.get_text(' ',strip=True)
        if not url or url in used:
            continue
        if VIEWER.search(url):
            continue
        if EXT.search(label) or EXT.search(url) or DOWNLOAD.search(url):
            if '문서보기' in label and not DOWNLOAD.search(url):
                continue
            used.add(url)
            filename = label if EXT.search(label) else urlsplit(url).path.rsplit('/',1)[-1]
            attachments.append({'url':url, 'filename':filename[:240] or 'attachment'})
    node = None
    for selector in ((body_selector,) if body_selector else BODY_SELECTORS):
        node = soup.select_one(selector)
        if node:
            break
    if node:
        for unwanted in node.select('script,style,form,iframe,object,embed,nav,button'):
            unwanted.decompose()
        for img in node.select('img[src]'):
            try:
                url=canonical_url(urljoin(base,img['src']))
            except ValueError:
                continue
            if url not in used:
                used.add(url)
                attachments.append({'url':url,'filename':urlsplit(url).path.rsplit('/',1)[-1] or 'image'})
        body = str(node)
    else:
        body = ''
    if len(BeautifulSoup(body,'html.parser').get_text(' ',strip=True)) < 10 and not attachments:
        raise ValueError('본문/첨부를 식별하지 못했습니다. 전용 선택자가 필요합니다.')
    if len(attachments) > 12:
        raise ValueError('첨부 12개 초과: 대량/비통신문 페이지 검수 필요')
    return {'title':title, 'body_html':body, 'attachments':attachments}


def discover(fetcher, school: dict, override: dict, cached: str | None = None) -> tuple[list[dict],set[str]]:
    home = canonical_url(school['home_url'])
    host = urlsplit(home).hostname
    hosts = {host, host[4:] if host.startswith('www.') else 'www.'+host}
    hosts.update(override.get('allowed_hosts',[]))
    boards = override.get('board_urls',[]) or ([cached] if cached else [])
    if not boards:
        pages, hosts = resolve_site(fetcher, home, hosts)
        for page in pages:
            boards += board_links(page.text,page.url)
        if not boards:
            for url in sitemap_candidates(pages[0].text, pages[0].url):
                if urlsplit(url).hostname not in hosts:
                    continue
                r=fetcher.get(url,allowed_hosts=hosts)
                boards += board_links(r.text,r.url)
                if boards:
                    break
    if not boards:
        raise ValueError('가정통신문 게시판 링크를 찾지 못했습니다.')
    posts=[]
    last_error=None
    for url in dict.fromkeys(boards):
        try:
            pages,_=open_pages(fetcher,url,hosts)
        except (ValueError,RuntimeError,PermissionError) as exc:
            last_error=exc
            continue
        page=pages[-1]
        found=post_links(page.text,page.url,override.get('post_selector',''))
        if not found and login_required(page.text):
            last_error=ValueError('로그인/인증이 필요한 게시판: 우회하지 않습니다.')
            continue
        for post in found:
            post['board_url']=page.url
        posts.extend(found)
        if posts:
            break
    if not posts:
        if last_error:
            raise last_error
        raise ValueError('게시글 링크 미지원/빈 게시판: 전용 어댑터가 필요합니다.')
    return posts,hosts
