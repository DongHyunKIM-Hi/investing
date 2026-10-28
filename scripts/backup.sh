#!/usr/bin/env sh
# DB를 data/backups/에 gzip으로 덤프하고 30개만 남긴다. cron 예: 30 8 * * * cd /path/to/investing && sh scripts/backup.sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p data/backups
out="data/backups/ggeol-$(date +%Y%m%d-%H%M).sql.gz"
docker compose exec -T db pg_dump -U ggeol --clean --if-exists ggeol | gzip > "$out"
ls -1t data/backups/ggeol-*.sql.gz | tail -n +31 | xargs -r rm --
echo "백업 완료: $out ($(du -h "$out" | cut -f1))"
echo "복원: gunzip -c $out | docker compose exec -T db psql -U ggeol ggeol"
