# 껄무새 탈출

매매 전에 "왜 사는지"를 기록하고, 비슷한 과거 내 매매의 승률로 추천/비추천하는 1인용 투자 상담 서비스. 미국 주식만.

## 실행
```bash
docker compose up -d                 # PostgreSQL 16 (localhost:5432, ggeol/ggeol)
uv run alembic upgrade head          # 마이그레이션
uv run fastapi dev app/main.py       # http://localhost:8000 (비밀번호: .env 의 APP_PASSWORD)
```
- `.env`에 `ANTHROPIC_API_KEY`가 있으면 대화형 상담, 없으면 폼 + 키워드 태그 추천으로 동작
- IONQ 데모 DB(`ggeol_demo`) 만들기: `scripts/demo_ionq.py` 상단 docstring 참고. `seed`는 DB 이름에 `demo`가 없으면 거부

## 검사 (변경 후 항상 실행)
```bash
uv run pytest -q && uv run ruff check app tests && uv run ruff format --check app tests && uv run pyright
```

## 구조
- `app/models.py` — Decision(결정: 이유·감정·context·outcome·판정), ConsultMessage(상담 대화), Fill(체결), LotMatch(FIFO 매칭), PriceBar/PriceCoverage(시세 캐시)
- `app/taxonomy.py` — 이유·감정 고정 어휘. **key는 유사도 매칭 기준이므로 바꾸지 말 것** (label만 수정 가능)
- `app/services/importer/` — `csv_parser`(CSV→ParsedFill), `presets`(증권사별 매핑·헤더 추측), `fifo`, `pipeline`(DB 저장, 같은 날·종목·방향 체결을 Decision 하나로 묶음)
- `app/services/market/` — `provider`(yfinance), `prices`(DB 캐시), `snapshot`(결정 시점 지표)
- `app/services/outcomes.py` — 결과 지표·계획 준수 여부 (순수 함수)
- `app/services/report.py` — 껄껄껄 리포트: 이유·감정·상황·계획·종목별 성적, 반복 실수 Top 3, 너무 일찍 판 비율 (순수 함수)
- `app/services/recompute.py` — 티커 단위로 FIFO → context → outcome 재계산, `track_skipped`(보류한 상담 결정의 '샀다면' 결과를 `outcome`에 `status: skipped`로 기록)
- `app/services/engine/` — 판정 엔진 (전부 순수 함수): `case`(Decision→Case), `similarity`(유사 전적 Top-K), `verdict`(베타 스무딩 승률·판정·패턴), `checks`(계획 위험 신호), `backtest`(엔진 사후 검증)
- `app/services/llm/` — `consult`(Claude 상담: record_decision 도구로 구조화), `prompts/consult_system.md`(상담 지침), `keywords`(API 키 없을 때 태그 추천)
- `app/services/consult.py` — 상담 시작(스냅샷) → 대화/폼 기록 → `judge`(판정 결과를 verdict_detail에 고정)
- `app/routers/` + `app/templates/` — FastAPI + Jinja2 + HTMX (JS 프론트엔드 없음)

## 규칙
- 계산 로직은 DB 없는 순수 함수로 두고 `tests/`에서 단위 테스트
- 실현 수익률은 실제 체결가·수수료 기준, 기간 비교(SPY 대비·낙폭·매도 후 20일)는 `adj_close` 기준
- 결정 시점 지표는 **전 거래일까지만** 사용 (당일 종가 미반영, 미래 정보 누출 방지)
- 모델 변경 시 `uv run alembic revision --autogenerate -m "..."` 후 생성된 파일 검토
- 등락 색상은 국내 관례: 상승 빨강(`.up`), 하락 파랑(`.down`)
- 금액은 `Decimal`(DB `Numeric`), 분석은 pandas float
- 판정 화면에는 표본 수와 "투자 자문 아님"을 항상 표시
- 유사 전적·패턴은 **판정 시점 이전에 청산된** 매매만 사용 (`Case.resolved_before`)
- 판정 기준 수익률은 평균이 아닌 중앙값 (테마주 대박 한 건이 평균을 왜곡)
- 상담 대화는 API content 블록 그대로 append-only 저장·재전송 (thinking 블록 수정 금지)

## 계획
전체 로드맵: `/Users/viva/.claude/plans/scalable-finding-pudding.md`
- [x] 1단계: 기반 + CSV 가져오기 + FIFO + 결과 지표 + 사후 이유 태깅
- [x] 2단계: 매수 전 상담 (Claude 대화 → 태그 추출 → 상황 스냅샷)
- [x] 3단계: 유사 전적 매칭 + 승률·판정 (+ 엔진 백테스트 `/backtest`)
- [ ] 4단계: 매도 상담 + 보류 건 추적 + 껄껄껄 리포트
  - [x] 껄껄껄 리포트 (`/report`)
  - [x] 보류 건 추적 (보류 선택 시·재계산 시 5/20/60거래일 뒤 수익률)
  - [ ] 매도 상담

## 알려진 한계
- 보유 중 주식 분할이 있으면 FIFO 수량이 어긋남 (CSV 수량은 분할 전 기준)
- 실적발표일 지표 미구현 (yfinance 과거 실적일 신뢰도 낮음)
- 증권사 프리셋은 `generic`만 있음 — 실제 CSV를 받으면 `presets.py`에 추가
- 대화형 상담은 가짜 LLM으로만 흐름 검증됨 (실제 API 키로 프롬프트 품질 미검증)
- 유사도 가중치·척도(`similarity.py` 상수)와 판정 임계값(`verdict.py`)은 직관값 — 데이터가 쌓이면 백테스트로 조정
- 보류 건은 '그날 종가에 샀다면'으로 계산 (실제로 살 수 있었던 가격과 다를 수 있음)
- 계획 준수의 손절 판정은 매수일 종가 기준 최대낙폭으로 근사 (3% 여유)
- 백테스트 화면의 '너무 일찍 판 비율'은 청산 후 20거래일 데이터를 쓰므로 그 시점엔 몰랐던 정보가 섞일 수 있음
