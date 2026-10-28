# 껄무새 탈출

매매 전에 "왜 사는지·왜 파는지"를 기록하고, 비슷한 과거 내 매매의 승률로 추천/비추천하는 1인용 투자 상담 서비스. 미국 주식만.

## 실행
```bash
docker compose up -d                 # PostgreSQL 16 (localhost:5432, ggeol/ggeol)
uv run alembic upgrade head          # 마이그레이션
uv run fastapi dev app/main.py       # http://localhost:8000 (비밀번호: .env 의 APP_PASSWORD)
docker compose --profile app up -d --build   # 앱까지 컨테이너로 (Dockerfile, /health 헬스체크)
```
- 사용자용 안내는 `README.md`, 환경 변수 목록은 `.env.example`. 기본 비밀값(APP_PASSWORD·SESSION_SECRET)이면 시작 로그와 로그인 화면에 경고
- `.env`에 `ANTHROPIC_API_KEY`가 있으면 대화형 상담, 없으면 폼 + 키워드 태그 추천으로 동작
- IONQ 데모 DB(`ggeol_demo`) 만들기: `scripts/demo_ionq.py` 상단 docstring 참고. `seed`는 DB 이름에 `demo`가 없으면 거부

## 검사 (변경 후 항상 실행)
```bash
uv run pytest -q && uv run ruff check app tests && uv run ruff format --check app tests && uv run pyright
```

## 구조
- `app/models.py` — Decision(결정: 이유·감정·context·outcome·판정), ConsultMessage(상담 대화), Fill(체결), LotMatch(FIFO 매칭), PriceBar/PriceCoverage(시세 캐시)
- `app/taxonomy.py` — 이유·감정 고정 어휘. **key는 유사도 매칭 기준이므로 바꾸지 말 것** (label만 수정 가능)
- `app/services/importer/` — `csv_parser`(CSV·xlsx→ParsedFill, 위쪽 제목 줄 자동 건너뜀), `presets`(증권사별 매핑·헤더 추측), `fifo`, `pipeline`(DB 저장, 같은 날·종목·방향 체결을 Decision 하나로 묶음. 상담 결정과 연결: 같은 날 수동 체결 → 상담일 전후 3일/+1일 순)
- `app/services/manual.py` — 상담 뒤 체결 직접 기록(`dedup_key`가 `manual:`로 시작)·삭제. CSV로 같은 날 체결이 들어오면 수동 체결은 CSV로 대체됨
- `app/services/market/` — `provider`(yfinance), `prices`(DB 캐시), `snapshot`(결정 시점 지표)
- `app/services/outcomes.py` — 결과 지표·계획 준수 여부 (순수 함수)
- `app/services/report.py` — 껄껄껄 리포트: 이유·감정·상황·계획·종목별 성적, 반복 실수 Top 3, 너무 일찍 판 비율 (순수 함수)
- `app/services/recompute.py` — 티커 단위로 FIFO → context → outcome 재계산, `track_skipped`(보류한 상담 결정의 '샀다면' 결과를 `outcome`에 `status: skipped`로 기록)
- `app/services/engine/` — 판정 엔진 (전부 순수 함수): `case`(Decision→Case, 매수·매도), `similarity`(유사 전적 Top-K), `verdict`(베타 스무딩 승률·판정·패턴), `checks`(매수 계획·매도 위험 신호), `backtest`(엔진 사후 검증)
  - 매도 Case: 판 뒤 20거래일 동안 안 오르면 win, `ret` = -(판 뒤 20일 수익률), `closed_on` = 그 20거래일째(`post_exit_on`). 그래서 매수와 같은 `decide`를 쓴다
- `app/services/position.py` — 보유 lot·평단가·평가 손익 (매도 상담 시작 시 context에 저장)
- `app/services/watch.py` — 보유 종목 계획 점검 (순수 함수): 살 때 남긴 손절가·목표가·기간과 현재가 대조 → `/positions` 화면. 시세는 캐시만 쓰고 '시세 갱신' 버튼으로 받음
- `app/services/llm/` — `consult`(Claude 상담: record_decision 도구로 구조화, 매수·매도 도구 따로), `prompts/consult_system.md`·`consult_sell_system.md`(상담 지침), `keywords`(API 키 없을 때 태그 추천)
- `app/services/consult.py` — 상담 시작(스냅샷, 매도면 보유 현황·살 때 계획) → 대화/폼 기록 → `judge`(같은 쪽 과거 결정과 비교, 판정 결과를 verdict_detail에 고정)
- `app/routers/` + `app/templates/` — FastAPI + Jinja2 + HTMX (JS 프론트엔드 없음). `/trades/{id}` 결정 상세(체결·결과·태그 수정), `trades/_fills.html`은 판정 화면과 상세 화면이 함께 씀. `/trades/export.csv` 결정 전체 내보내기. HTTPException·500은 `error.html`로 렌더
- `app/services/llm/prompts/` — 상담 지침. 화면이 첫 질문("왜 사려고/팔려고 하세요?")을 고정 멘트로 먼저 보여주므로(`consult/_messages.html`) 프롬프트는 그 답부터 받는 전제. 사용자가 한 번에 다 말하면 첫 턴에 기록, 권유·전망 금지, 어긋남은 숫자로 한 번만 짚기
- `scripts/recompute.py` — 화면의 '시세·결과 재계산'과 같은 일을 하는 cron용 CLI (보류 건 추적 포함). `scripts/backup.sh` — pg_dump → `data/backups/` 30개 보관

## 규칙
- 계산 로직은 DB 없는 순수 함수로 두고 `tests/`에서 단위 테스트
- 실현 수익률은 실제 체결가·수수료 기준, 기간 비교(SPY 대비·낙폭·매도 후 20일)는 `adj_close` 기준
- 결정 시점 지표는 **전 거래일까지만** 사용 (당일 종가 미반영, 미래 정보 누출 방지)
- 모델 변경 시 `uv run alembic revision --autogenerate -m "..."` 후 생성된 파일 검토
- 등락 색상은 국내 관례: 상승 빨강(`.up`), 하락 파랑(`.down`)
- 금액은 `Decimal`(DB `Numeric`), 분석은 pandas float
- 체결(Fill)은 CSV 원본 그대로 저장, 분할 보정은 계산할 때만 (`split_factor`)
- 판정 화면에는 표본 수와 "투자 자문 아님"을 항상 표시
- 유사 전적·패턴은 **판정 시점 이전에 청산된** 매매만 사용 (`Case.resolved_before`)
- 판정 기준 수익률은 평균이 아닌 중앙값 (테마주 대박 한 건이 평균을 왜곡)
- 상담 대화는 API content 블록 그대로 append-only 저장·재전송 (thinking 블록 수정 금지)

## 계획
전체 로드맵: `/Users/viva/.claude/plans/scalable-finding-pudding.md`
- [x] 1단계: 기반 + CSV 가져오기 + FIFO + 결과 지표 + 사후 이유 태깅
- [x] 2단계: 매수 전 상담 (Claude 대화 → 태그 추출 → 상황 스냅샷)
- [x] 3단계: 유사 전적 매칭 + 승률·판정 (+ 엔진 백테스트 `/backtest`)
- [x] 4단계: 매도 상담 + 보류 건 추적 + 껄껄껄 리포트 (`/report`)
- [x] 4단계 보완: 체결 직접 기록(CSV 없이 결과 추적), 결정 상세·태그 수정, 기록 필터, 상담 삭제, 재계산 CLI
- [x] 보유 현황·계획 점검 (`/positions`): 손절 이탈·목표 도달·기간 초과·계획 없음 알림
- [x] 런칭 준비: 프롬프트 개정, Dockerfile·compose app 프로필·/health, 에러 화면, 로그인 실패 지연, 기본 비밀값 경고, 첫 사용 안내, README, 결정 CSV 내보내기, 백업 스크립트

## 알려진 한계
- 주식 분할은 계산 시 체결을 지금 기준으로 보정함(`importer/splits.py`, 분할 이력은 `price_coverage.splits`에 캐시). 단 상담에서 남긴 목표가·손절가는 보정하지 않으므로 상담 후 분할되면 어긋남
- 실적발표일 지표 미구현 (yfinance 과거 실적일 신뢰도 낮음)
- 증권사 프리셋은 `generic`만 있음 — 토스증권 내보내기 형식은 공개 자료로 확인 못 함. 실제 파일을 받으면 `presets.py`에 추가 (그 전까지는 헤더 추측 + 컬럼 맞추기 화면으로 가져옴)
- 대화형 상담은 가짜 LLM으로만 흐름 검증됨 (실제 API 키로 프롬프트 품질 미검증 — 2026-10-28 기준 `.env`의 키 값이 비어 있음)
- 유사도 가중치·척도(`similarity.py` 상수)와 판정 임계값(`verdict.py`)은 직관값 — 데이터가 쌓이면 백테스트로 조정
- 매도 판정 기준(판 뒤 20일 안 오르면 잘 판 것)과 매도 점검 임계값(5일 -15% 급락, 익절 +5% 미만)은 직관값. 데모 데이터 백테스트에서는 매도 추천/비추천이 거꾸로 나옴(표본 5·3건, 매도 이유 미태깅) — 실제 데이터로 `/backtest` 확인 필요
- 보류 건은 '그날 종가에 샀다면'으로 계산 (실제로 살 수 있었던 가격과 다를 수 있음)
- 직접 기록한 체결은 날짜가 CSV와 같아야 대체됨. 날짜를 틀리게 적으면 CSV 가져오기 때 같은 거래가 두 번 잡힐 수 있음 (상세 화면에서 수동 체결을 지우면 됨)
- 계획 준수의 손절 판정은 매수일 종가 기준 최대낙폭으로 근사 (3% 여유)
- 보유 현황의 계획은 지금 열린 lot 중 가장 최근 상담 하나만 보여줌. 같은 종목을 여러 번 상담해 샀으면 이전 계획은 무시됨. 평단가는 전체 lot 기준이라 그 상담의 매수가와 다를 수 있음
- 백테스트 화면의 '너무 일찍 판 비율'은 청산 후 20거래일 데이터를 쓰므로 그 시점엔 몰랐던 정보가 섞일 수 있음
