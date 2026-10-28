#!/usr/bin/env sh
# scripts/backup.sh 가 만든 덤프로 DB를 되돌린다. 지금 DB 내용은 전부 덤프 시점으로 바뀐다.
#   sh scripts/restore.sh data/backups/ggeol-20261029-0830.sql.gz --yes
# --yes 없이 돌리면 뭘 할지만 보여 주고 끝난다. 복원 전에 지금 상태를 한 번 더 백업해 둔다.
set -eu
cd "$(dirname "$0")/.."
dump="${1:-}"
if [ -z "$dump" ] || [ ! -f "$dump" ]; then
  echo "사용법: sh scripts/restore.sh <data/backups/ggeol-*.sql.gz> --yes" >&2
  ls -1t data/backups/ggeol-*.sql.gz 2>/dev/null | head -5 >&2 || true
  exit 2
fi
echo "복원할 덤프: $dump ($(du -h "$dump" | cut -f1), $(date -r "$dump" +%Y-%m-%d\ %H:%M))"
if [ "${2:-}" != "--yes" ]; then
  echo "지금 DB(ggeol)를 이 덤프로 덮어씁니다. 진행하려면 --yes 를 붙이세요."
  exit 0
fi
sh scripts/backup.sh >/dev/null && echo "복원 전 현재 상태를 data/backups/ 에 백업했어요."
gunzip -c "$dump" | docker compose exec -T db psql -U ggeol -q -v ON_ERROR_STOP=1 ggeol
uv run alembic upgrade head
echo "복원 완료. 앱을 띄워 /health 와 매매 기록을 확인하세요."
