#!/bin/sh
# Run on the NAS: sudo sh scripts/install-nas.sh
# Never relocate Docker itself or touch another project's permissions.
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
case "$APP_DIR" in /volume2/*) ;; *) echo '코드를 /volume2 아래에 먼저 배치하세요. 볼륨1로 대체하지 않습니다.' >&2; exit 1;; esac
[ -d /volume2 ] || { echo '/volume2가 없습니다.' >&2; exit 1; }
awk '$2 == "/volume2" {ok=1} END {exit !ok}' /proc/mounts || {
    echo '/volume2의 실제 마운트를 확인하지 못했습니다. 기존 NAS 설정을 확인하세요.' >&2; exit 1;
}
cd "$APP_DIR"
if [ ! -f .env ]; then cp .env.example .env; fi
chmod 600 .env
# Read only these known settings. Do not source arbitrary shell code from .env.
DATA=$(sed -n 's/^NAS_DATA_DIR=//p' .env | tail -n 1 | tr -d '\r')
[ -n "$DATA" ] || DATA=/volume2/moa/data
case "$DATA" in /volume2/*) ;; *) echo 'NAS_DATA_DIR는 /volume2 아래여야 합니다.' >&2; exit 1;; esac
case "$DATA" in *'..'*|*' '*|*'"'*|*"'"*) echo '공백/상대경로/따옴표 없는 절대경로를 사용하세요.' >&2; exit 1;; esac
ANCESTOR="$DATA"
while [ ! -d "$ANCESTOR" ]; do ANCESTOR=$(dirname "$ANCESTOR"); done
REAL_ANCESTOR=$(CDPATH= cd -- "$ANCESTOR" && pwd -P)
case "$REAL_ANCESTOR" in /volume2|/volume2/*) ;; *) echo '데이터 상위 경로가 볼륨2 밖을 가리킵니다.' >&2; exit 1;; esac
mkdir -p "$DATA"
REAL_DATA=$(CDPATH= cd -- "$DATA" && pwd -P)
case "$REAL_DATA" in /volume2/*) ;; *) echo '데이터 경로가 볼륨2 밖으로 연결됩니다.' >&2; exit 1;; esac
UID_VALUE=${SUDO_UID:-$(id -u)}
GID_VALUE=${SUDO_GID:-$(id -g)}
if [ "$UID_VALUE" = 0 ]; then
    UID_VALUE=$(sed -n 's/^MOA_UID=//p' .env | tail -n 1)
    GID_VALUE=$(sed -n 's/^MOA_GID=//p' .env | tail -n 1)
fi
case "$UID_VALUE" in ''|*[!0-9]*|0) echo '비root 숫자 UID를 지정하세요.' >&2; exit 1;; esac
case "$GID_VALUE" in ''|*[!0-9]*) echo '숫자 GID를 지정하세요.' >&2; exit 1;; esac
sed -i "s/^MOA_UID=.*/MOA_UID=$UID_VALUE/;s/^MOA_GID=.*/MOA_GID=$GID_VALUE/" .env
# Change ownership only for an empty new MOA data directory, never recursively.
if [ -z "$(ls -A "$DATA")" ]; then
    chown "$UID_VALUE:$GID_VALUE" "$DATA"
    chmod 750 "$DATA"
fi
if ! grep -q '^NEIS_API_KEY=.' .env; then
    printf '\n.env의 NEIS_API_KEY에 키를 입력하고 같은 명령을 다시 실행하세요.\n'
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    PATH="/usr/local/bin:/var/packages/ContainerManager/target/usr/bin:$PATH"
    export PATH
fi
if docker compose version >/dev/null 2>&1; then
    compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
    compose() { docker-compose "$@"; }
else
    echo 'Synology Container Manager / Docker Compose를 확인하세요.' >&2; exit 1
fi
compose config --quiet
compose build
compose run --rm collector doctor
compose up -d
echo "설치 명령 완료. 자료 위치: $REAL_DATA"
echo '상태: docker compose logs --tail=60 collector'
echo '집계: docker compose exec collector python -m moa status'
