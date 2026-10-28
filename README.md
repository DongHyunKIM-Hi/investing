# 껄무새 탈출

"그때 살걸, 그때 팔걸"을 반복하는 개인 투자자를 위한 1인용 매매 상담 도구입니다. 사고팔기 **전에** 이유와 계획을 말로 남기고, 그 이유·감정·시장 상황이 비슷했던 **내 과거 매매의 승률**로 추천·비추천을 받습니다. 미국 주식만 다룹니다.

- 매수 전 상담: 왜 사는지, 목표가·손절가·기간 → 비슷한 과거 매수 Top-K 승률 → 추천 / 신중 / 비추천 / 데이터 부족
- 매도 전 상담: 왜 파는지, 살 때 계획과 비교 → 비슷하게 팔았을 때 판 뒤 20일 흐름
- 안 산 것·안 판 것도 기록해 "참은 게 잘한 건지" 추적
- 껄껄껄 리포트: 이유별·감정별·상황별 승률, 반복 실수 Top 3, 너무 일찍 판 비율
- 성적표: 상담 때 받은 판정이 맞았는지, 내가 그 판정을 따랐는지 (적중률·따른 비율) + 엔진 사후 검증
- 보유 현황: 살 때 남긴 손절가·목표가와 지금 가격 대조
- 정리할 결정: 판정 전에 멈춘 상담, 샀다는데 체결이 없는 건, 실행·보류를 안 고른 건을 한 화면에서 처리

개인 기록 기반 참고 도구이며 투자 자문이 아닙니다.

## 시작하기

```bash
cp .env.example .env            # APP_PASSWORD, SESSION_SECRET 반드시 채우기
docker compose up -d            # PostgreSQL 16
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py  # http://localhost:8000
```

`.env`에 `ANTHROPIC_API_KEY`가 있으면 Claude와 대화로 상담하고, 없으면 폼 입력 + 키워드 태그 추천으로 동작합니다.

### 첫 사용 흐름
1. **가져오기** — 증권사 해외주식 체결내역(CSV/xlsx)을 올리고 컬럼을 맞춥니다. 같은 날·같은 종목·같은 방향 체결은 결정 1건으로 묶입니다.
2. **이유 태깅** — 과거 매수·매도에 "그때 왜" 를 붙입니다. 기억 안 나면 '모름'으로 두어도 됩니다.
3. **매매 상담** — 다음 매매부터 사기 전·팔기 전에 상담을 거칩니다. 판정 뒤 '샀어요/안 살래요'를 고르고, 샀으면 체결가·수량을 바로 적으면 CSV 없이도 결과가 추적됩니다. 나중에 CSV를 올리면 자동으로 대체·연결됩니다.

## 매일 돌리기 (선택)
미국 장 마감 뒤 시세를 받아 결과·보류 건 추적을 갱신합니다. 화면의 '시세·결과 재계산' 버튼과 같습니다.

```bash
uv run python scripts/recompute.py              # 전체 (끝나면 오늘 점검할 보유 종목을 출력)
uv run python scripts/review.py                 # 지난 7일 회고: 상담·판정 거스른 결정·청산
sh scripts/backup.sh                            # DB 덤프 → data/backups/ (30개 보관)
```
```cron
0 8 * * 2-6  cd /path/to/investing && uv run python scripts/recompute.py >> data/recompute.log 2>&1
30 8 * * *   cd /path/to/investing && sh scripts/backup.sh >> data/backup.log 2>&1
45 8 * * 1   cd /path/to/investing && uv run python scripts/review.py
```

## 컨테이너로 배포
```bash
docker compose --profile app up -d --build      # db + app (http://localhost:8000, /health)
```
외부에서 접속할 거면 앞에 HTTPS 리버스 프록시(Caddy·nginx)를 두고 `.env`의 비밀값을 바꾸세요. 로그인은 비밀번호 하나짜리 세션 인증이라 1인용을 전제로 합니다.

## 데이터
- 결정 전체는 매매 기록 화면에서 CSV로 내려받을 수 있습니다 (`/trades/export.csv`).
- 체결은 CSV 원본 그대로 저장하고, 주식 분할은 계산할 때만 보정합니다.
- 시세는 yfinance에서 받아 DB에 캐시합니다. 결정 시점 지표는 전 거래일까지만 씁니다.

## 개발
```bash
uv run pytest -q && uv run ruff check app tests && uv run ruff format --check app tests && uv run pyright
```
구조와 규칙은 `CLAUDE.md`, 데모 데이터는 `scripts/demo_ionq.py` 상단 설명을 보세요.
