# 다중 플랫폼 수집 안정화 구현 계획

## 구현 원칙

- 승인된 설계 문서만 구현 범위로 사용한다.
- 각 단계는 실패하는 테스트를 먼저 추가하고 최소 코드로 통과시킨다.
- Collector 간 인증 정보가 공유되지 않도록 요청 단위 헤더를 사용한다.
- 기존 콘텐츠 스키마와 `source + external_id` 중복 방지 계약을 유지한다.
- 각 작업이 끝날 때 관련 테스트를 실행하고, 마지막에 전체 품질 검사를 수행한다.

## 작업 1: 구조화된 수집 결과와 실행 이력 추가

대상 파일:

- `src/glean_ai/models.py`
- `src/glean_ai/storage.py`
- `migrations/versions/0002_collection_run_details.py`
- `tests/test_storage_report.py`
- 신규 `tests/test_service.py`

진행:

1. `CollectionStatus`와 `CollectionResult` 모델의 실패 테스트를 작성한다.
2. `CollectionResult`가 source, status, contents, fetched/accepted count, 안전한 오류 정보를 표현하도록 구현한다.
3. `runs` 테이블에 `fetched_count`, `accepted_count`, `error_code` 컬럼을 추가하는 마이그레이션을 작성한다.
4. 기존 DB 행을 보존할 수 있도록 새 수치 컬럼은 기본값을 갖게 한다.
5. Store가 구조화된 실행 결과를 저장하고 다시 읽는 테스트를 작성한 뒤 구현한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_service.py tests/test_storage_report.py
```

## 작업 2: 공통 HTTP 오류 분류와 재시도 계약 정리

대상 파일:

- `src/glean_ai/collectors/base.py`
- 신규 `tests/test_collector_base.py`

진행:

1. timeout/transport 오류는 세 번 재시도하고 `401`, `403`, `400`, `422`는 재시도하지 않는 테스트를 작성한다.
2. `429`가 `Retry-After`를 따르는 테스트를 작성한다.
3. 외부 응답 본문과 인증 정보가 사용자용 오류 문자열에 포함되지 않는 테스트를 작성한다.
4. 최소한의 typed exception과 오류 코드 매핑을 구현한다.
5. 기존 Collector가 공통 동작을 그대로 사용할 수 있는지 확인한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_collector_base.py
```

## 작업 3: GitHub 검색 복구

대상 파일:

- `src/glean_ai/collectors/github.py`
- `tests/test_collectors.py`

진행:

1. 7개 이상의 관심 키워드가 Boolean 연산자 5개 이하, 검색어 256자 이하의 묶음으로 분할되는 테스트를 작성한다.
2. 여러 요청의 repository ID 중복 제거와 병합 후 `source_limit` 적용 테스트를 작성한다.
3. 일부 쿼리가 실패하면 성공 결과와 `partial` 상태를 보존하는 서비스 테스트를 작성한다.
4. 쿼리 분할을 단일 목적 helper로 구현하고 기존 정규화 코드를 재사용한다.
5. GitHub 토큰이 GitHub 호스트에만 전달되는 기존 회귀 테스트를 유지한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_collectors.py tests/test_service.py -k github
```

## 작업 4: Reddit 공식 공개 RSS 조회 구현

대상 파일:

- `src/glean_ai/collectors/reddit.py`
- `src/glean_ai/service.py`
- `src/glean_ai/config.py`
- `tests/test_collectors.py`
- `tests/test_service.py`

진행:

1. multi-subreddit 공식 Atom RSS endpoint와 고유 User-Agent 사용을 검증한다.
2. 제목, 본문, 작성자, URL과 게시 시각의 정규화를 테스트한다.
3. 여러 RSS의 중복 제거와 전체 수집 상한을 검증한다.
4. 항목 URL에서 원래 subreddit을 복원한다.
5. Reddit Client ID와 Secret 설정을 제거한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_collectors.py tests/test_service.py -k reddit
```

## 작업 5: Hugging Face 인증 설정 연결

대상 파일:

- `src/glean_ai/collectors/huggingface.py`
- `src/glean_ai/service.py`
- `tests/test_collectors.py`

진행:

1. 토큰이 있을 때 Hugging Face 요청에만 Authorization 헤더가 붙는 테스트를 작성한다.
2. 토큰이 없어도 공개 모델 조회가 유지되는 테스트를 작성한다.
3. Collector 생성자에 선택적 토큰을 전달하고 요청 단위로 적용한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_collectors.py -k huggingface
```

## 작업 6: Threads Keyword Search Collector 추가

대상 파일:

- 신규 `src/glean_ai/collectors/threads.py`
- `src/glean_ai/collectors/__init__.py`
- `src/glean_ai/service.py`
- `src/glean_ai/config.py`
- `tests/test_collectors.py`
- `tests/test_service.py`

진행:

1. 관심 키워드별 `GET /keyword_search`, `search_type=RECENT`, 필요한 field 요청을 검증하는 테스트를 작성한다.
2. 응답의 ID, 본문, username, permalink, timestamp와 공개 지표가 `Content`로 정규화되는지 검증한다.
3. cursor 페이지네이션, 키워드 간 ID 중복 제거, 전체 `source_limit` 테스트를 작성한다.
4. `401`, `403`, `429`, 잘못된 응답 스키마와 일부 키워드 실패를 검증한다.
5. Threads Collector를 구현하고 서비스의 기본 활성 소스로 등록한다.
6. 토큰이 없으면 외부 호출 없이 `not_configured`를 반환한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_collectors.py tests/test_service.py -k threads
```

## 작업 7: 병렬 수집 상태와 전체 실패 정책 적용

대상 파일:

- `src/glean_ai/service.py`
- `src/glean_ai/cli.py`
- `tests/test_service.py`

진행:

1. 네 소스의 success, empty, partial, failed, disabled, not_configured 상태 조합 테스트를 작성한다.
2. 한 소스 실패가 나머지 콘텐츠 저장을 막지 않는지 검증한다.
3. 네 소스가 모두 failed/not_configured/disabled이면 daily가 실패하고 보고 단계가 호출되지 않는 테스트를 작성한다.
4. 네 소스가 모두 empty이면 오류가 아니라 항목 없는 보고 대상으로 유지하는 테스트를 작성한다.
5. `asyncio.gather`의 소스 격리를 유지하면서 `CollectionResult` 목록을 보고 단계에 전달하도록 구현한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_service.py
```

## 작업 8: 소스별 최소 노출과 Slack 상태 표시

대상 파일:

- `src/glean_ai/pipeline.py`
- `src/glean_ai/reporters.py`
- `src/glean_ai/cli.py`
- `tests/test_pipeline.py`
- `tests/test_storage_report.py`

진행:

1. 성공 또는 부분 성공한 각 소스에 적합한 후보가 있으면 최소 한 건이 선택되는 테스트를 작성한다.
2. 소스별 최소 항목을 확보한 뒤 나머지 자리에 40% 상한이 적용되는지 검증한다.
3. 적합한 후보가 없는 성공 소스에는 저품질 항목을 강제로 넣지 않는 테스트를 작성한다.
4. Slack 상단에 소스별 상태와 건수, 실패 또는 부분 실패 경고가 표시되는 테스트를 작성한다.
5. 오류 원문, 토큰, Authorization 값이 Slack payload에 없는지 검증한다.
6. 현재 daily 실행의 결과를 `build_blocks`에 명시적으로 전달하도록 구현한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest tests/test_pipeline.py tests/test_storage_report.py
```

## 작업 9: 운영 설정과 문서 정리

대상 파일:

- `.env.example`
- `.github/workflows/daily.yml`
- `src/glean_ai/config.py`
- `README.md`
- 설정을 참조하는 테스트

진행:

1. Threads가 기본 활성화되고 필요한 Secret이 workflow에 전달되는 설정 테스트를 작성한다.
2. `X_BEARER_TOKEN`과 `X_ENABLED`를 제거하고 참조가 남지 않았는지 확인한다.
3. Reddit User-Agent와 Hugging Face/Threads 토큰을 예제 및 workflow에 반영한다.
4. README의 지원 소스, 부분 실패 표시, 운영 준비와 알려진 제약을 갱신한다.
5. 운영 로그에서 `source_failed`, `source_partial`, `source_empty`를 구분해 찾는 방법을 문서화한다.

검증:

```bash
rg -n "X_BEARER_TOKEN|X_ENABLED|x_bearer_token|x_enabled" . --glob '!.git/**'
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest
```

첫 명령은 결과가 없어야 한다.

## 작업 10: 전체 회귀 및 배포 전 검증

진행:

1. 전체 테스트와 정적 검사를 실행한다.
2. Alembic migration이 빈 DB와 기존 0001 DB 모두에서 적용되는지 확인한다.
3. 실제 Secret 없이 dry-run하여 Reddit 공개 RSS와 GitHub, Hugging Face가 독립적으로 실행되고 Threads가 `not_configured`인지 확인한다.
4. GitHub Actions workflow 구문과 Docker Compose 설정을 검증한다.
5. 변경 파일이 승인된 설계 범위에만 해당하는지 diff를 검토한다.

검증:

```bash
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run pytest
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/glean-ai-uv-cache uv run mypy
docker compose config
git diff --check
```

## 배포 후 확인

1. Meta App Review 완료 후 장기 Threads Access Token을 등록한다.
2. workflow를 수동 실행하여 네 소스 상태와 수집 건수를 확인한다.
3. Slack에 각 성공 소스의 적합한 항목이 최소 한 건씩 포함되는지 확인한다.
4. 테스트용으로 Threads 자격증명을 일시적으로 제외한 실행에서 부분 실패 경고가 표시되는지 확인한다.
5. 정상 Secret으로 복구하고 재실행하여 최종 운영 상태를 확인한다.
