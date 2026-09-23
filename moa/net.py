"""Bounded public GETs: DNS-pinned connections, TLS, redirect checks, robots, pacing."""
from __future__ import annotations

import ipaddress
import os
import socket
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import certifi
import urllib3
from bs4 import UnicodeDammit

# Self-identifying bot token in the widely accepted "<browser> (compatible; <bot>; +<url>)" form.
# Several Korean school firewalls drop requests whose UA carries no browser-compatible token at all;
# this still states plainly that the client is MoaNoticeBot and where its operator can be reached.
BOT_TOKEN = 'MoaNoticeBot'
AGENT = os.environ.get('MOA_USER_AGENT',
                       'Mozilla/5.0 (compatible; MoaNoticeBot/0.1; +https://github.com/h19h29-design/moa)')


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('공개 http/https URL만 허용합니다.')
    if p.port not in (None, 80, 443):
        raise ValueError('비표준 포트는 수집하지 않습니다.')
    if any(ord(c) < 32 for c in url) or '\\' in url:
        raise ValueError('잘못된 URL')
    host = p.hostname.encode('idna').decode().lower()
    if ':' in host:
        host = '[' + host + ']'
    if p.port and p.port != (443 if p.scheme == 'https' else 80):
        host += ':' + str(p.port)
    q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
    # Drop legacy Java path parameters such as ";jsessionid=...". Some office firewalls treat
    # those URLs as an attack pattern, and a public GET does not need a server session id.
    path = (p.path or '/').split(';', 1)[0] or '/'
    return urlunsplit((p.scheme, host, path, urlencode(sorted(q)), ''))


def public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        addresses = sorted({r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('내부망/로컬/특수 IP로의 수집 요청은 차단됩니다.')
    return addresses


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    data: bytes

    @property
    def text(self) -> str:
        return UnicodeDammit(self.data, is_html=True).unicode_markup or self.data.decode('utf-8', 'replace')


class Fetcher:
    def __init__(self, delay: float = 2.0, max_requests: int = 5000, meter=None):
        self.delay = max(1.0, delay)
        self.max_requests = max_requests
        self.meter = meter  # optional shared Budget; spend(1) raises when the daily cap is hit
        self.requests = 0
        self.last: dict[str, float] = {}
        self.rules: dict[str, RobotFileParser] = {}
        self.delays: dict[str, float] = {}
        self.cooldowns: dict[str, float] = {}

    def _one(self, url: str, limit: int) -> Response:
        if self.requests >= self.max_requests:
            raise RuntimeError('일일 HTTP 요청 상한에 도달했습니다.')
        p = urlsplit(url)
        host, port = p.hostname, p.port or (443 if p.scheme == 'https' else 80)
        # Many schools share one hosting server (same IP, different sub-domains). Pacing per
        # server keeps us polite to that server's own rate limit instead of per host name.
        pace = host
        try:
            pace = public_addresses(host, port)[0]  # Pin verified IP; no second DNS lookup / proxy env.
        except ValueError:
            raise
        if max(self.cooldowns.get(host, 0), self.cooldowns.get(pace, 0)) > time.monotonic():
            raise RuntimeError('요청 제한 응답으로 해당 호스트를 일시 중지했습니다.')
        ip = pace
        wait = max(self.delay, self.delays.get(host, 0)) - (time.monotonic() - self.last.get(pace, 0))
        if wait > 120:
            raise RuntimeError('사이트 요청 간격이 길어 이번 수집에서 제외합니다.')
        if wait > 0:
            time.sleep(wait)
        self.last[pace] = time.monotonic()
        self.requests += 1
        if self.meter:
            self.meter.spend(1)
        kw = dict(port=port, timeout=urllib3.Timeout(connect=8, read=20), maxsize=1)
        # School servers routinely drop the first connection (reset/timeout). Two extra attempts
        # with a short backoff keep a whole school from being written off as failed.
        for attempt in range(3):
            if attempt:
                self.requests += 1
                if self.meter:
                    self.meter.spend(1)
                time.sleep(2.0 * attempt)
            pool = (urllib3.HTTPSConnectionPool(ip, server_hostname=host, assert_hostname=host,
                        cert_reqs='CERT_REQUIRED', ca_certs=certifi.where(), **kw)
                    if p.scheme == 'https' else urllib3.HTTPConnectionPool(ip, **kw))
            response = None
            try:
                response = pool.urlopen('GET', urlunsplit(('', '', p.path or '/', p.query, '')),
                    headers={'Host': p.netloc, 'User-Agent': AGENT, 'Accept-Encoding': 'identity'},
                    redirect=False, retries=False, preload_content=False)
                chunks, size = [], 0
                started = time.monotonic()
                for chunk in response.stream(65536, decode_content=True):
                    size += len(chunk)
                    if size > limit or time.monotonic() - started > 60:
                        raise ValueError('응답 크기/다운로드 시간 제한 초과')
                    chunks.append(chunk)
                if response.status == 429:
                    self.cooldowns[host] = time.monotonic() + 3600
                    self.cooldowns[pace] = self.cooldowns[host]
                return Response(url, response.status, {k.lower():v for k,v in response.headers.items()}, b''.join(chunks))
            except urllib3.exceptions.HTTPError:
                if attempt == 2:
                    raise
            finally:
                if response:
                    response.close()
                pool.close()
        raise RuntimeError('HTTP 연결 실패')

    def _raw(self, url: str, limit: int, allowed_hosts: set[str]) -> Response:
        for _ in range(6):
            url = canonical_url(url)
            if urlsplit(url).hostname not in allowed_hosts:
                raise ValueError('승인되지 않은 외부 호스트로의 이동을 차단했습니다.')
            r = self._one(url, limit)
            if r.status in (301,302,303,307,308) and 'location' in r.headers:
                url = urljoin(url, r.headers['location'])
                continue
            return r
        raise ValueError('리다이렉트 횟수 초과')

    def _allowed(self, url: str, hosts: set[str]) -> bool:
        p = urlsplit(url)
        origin = p.scheme + '://' + p.netloc
        if origin not in self.rules:
            try:
                r = self._raw(origin + '/robots.txt', 512 * 1024, hosts)
            except urllib3.exceptions.HTTPError:
                # Legacy http origins are often half-retired: the plain-http robots request is
                # reset while the https one answers. Ask the https origin instead, and keep
                # failing closed if that also fails.
                if p.scheme != 'http':
                    raise
                r = self._raw('https://' + p.netloc + '/robots.txt', 512 * 1024, hosts)
            rp = RobotFileParser()
            if r.status in (404,410):
                rp.parse(['User-agent: *', 'Disallow:'])
            elif r.status == 200 and '<html' not in r.text[:500].lower():
                rp.parse(r.text.splitlines())
            else:
                rp.parse(['User-agent: *', 'Disallow: /'])  # fail closed on 403/429/5xx/HTML
            self.rules[origin] = rp
            self.delays[p.hostname] = max(self.delay, rp.crawl_delay(BOT_TOKEN) or rp.crawl_delay('*') or 0)
        return self.rules[origin].can_fetch(BOT_TOKEN, url)

    def get(self, url: str, *, allowed_hosts: set[str] | None = None,
            limit: int = 5*1024*1024, robots: bool = True, allow_site_move: bool = False) -> Response:
        url = canonical_url(url)
        hosts = allowed_hosts or {urlsplit(url).hostname}
        moved: set[str] = set()
        for _ in range(6):
            if urlsplit(url).hostname not in hosts:
                # Only the school-entry lookup may follow its own hosting move (e.g. a school
                # homepage that now lives on the office-of-education platform domain).
                if not (allow_site_move and len(moved) < 2):
                    raise ValueError('허용되지 않은 호스트')
                moved.add(urlsplit(url).hostname)
                hosts = hosts | moved
            if robots and not self._allowed(url, hosts):
                raise PermissionError('robots.txt 수집 제한')
            r = self._one(url, limit)
            if r.status in (301,302,303,307,308) and 'location' in r.headers:
                url = canonical_url(urljoin(url,r.headers['location']))
                continue
            if r.status != 200:
                raise RuntimeError(f'HTTP {r.status}')
            return r
        raise ValueError('리다이렉트 횟수 초과')
