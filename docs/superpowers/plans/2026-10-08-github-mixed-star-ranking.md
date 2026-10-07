# GitHub stars 혼합 순위와 보고서 제목 정리 구현 계획

> **실행 방식:** 사용자 승인 후 CEO가 실행 checkout을 정하고 Coder에게 순차 배정한다. 이 문서는 현재 checkout이나 branch를 선택하지 않는다.

**목표:** 최근 관측으로 잰 GitHub stars 순증가 상위 최대 3개와 누적 stars 상위 최대 2개를 섞어 최대 5개를 표시하고, `오픈소스 업데이트`, `주목받는 모델`, `AI 커뮤니티 논의` 자동 제목 문구를 제거한다.

**구조:** 별도 `github_star_snapshots` 이력으로 관측값을 저장한다. 공통 계산·선정 로직을 정상 수집, 저장 보고서, dry-run이 공유한다. 보고서 후보 유효성은 최신 GitHub collection run과 그 snapshot으로 판단한다.

**기술:** Python 3.12+, SQLAlchemy 2.0.38, Alembic 1.14.1, 기존 pytest·respx 환경. 새 의존성은 추가하지 않는다.

**명세:** [2026-10-08 승인 설계](../specs/2026-10-08-github-mixed-star-ranking-design.md)

## 공통 제약

- 기존 GitHub 키워드 검색, 최소 10 stars, `sort="updated"`, `source_limit`을 유지한다. 기본 후보 한도는 100개다. 전체 GitHub 순위나 정확한 rolling 24시간 획득량을 뜻하지 않는다.
- 각 실행의 UTC `started_at`을 관측 시각으로 쓴다. 관측은 해당 `runs.id`와 연결하며, API에 유효한 stars가 없으면 snapshot을 만들지 않는다.
- baseline은 현재 관측보다 과거인 22–26시간 구간에서 24시간과 가장 가까운 관측이다. 동점이면 더 이른 snapshot을 고른다. 실제 간격을 표시하며 비례 환산하지 않는다.
- 미측정·0·음수·양수 변화를 구분한다. 증가량 단계는 양수 최대 3개, 누적 stars 단계는 중복 제외 최대 2개이며, 부족한 자리는 누적 순위로 채운다.
- snapshot은 7일 보존한다. dry-run은 DB의 content, snapshot, retention 상태를 변경하지 않는다.
- 기술 보고서 전체 24개 제한과 GitHub 혼합 순서를 보존한다. 기존 Fork 지표와 제목 100자 제한을 유지한다.
- 기존 저장소 content는 재수집 때 갱신한다. content 갱신, run 기록, snapshot 저장, 보존 정리는 하나의 transaction이어야 한다.
- 기존 데이터는 보존하고 stars를 과거 snapshot으로 backfill하지 않는다. migration downgrade는 새 snapshot 테이블만 제거한다.
- 자동 제목 문구는 fallback에서 제거한다. 원제목에 그 문구가 들어 있으면 보존하며 LLM 생성 제목은 문자열 치환하지 않는다.

## 순서와 파일 책임

작업은 순서대로 진행한다. 같은 파일을 여러 작업이 다루므로 한 Coder가 작업 1–6을 순차 수행하고, 다른 역할의 변경을 되돌리지 않는다.

| 순서 | 작업 범위 | 주요 파일 |
|---|---|---|
| 1 | snapshot 모델·migration·저장 조회 | `src/glean_ai/storage.py`, `migrations/versions/0004_github_star_snapshots.py`, `tests/test_migrations.py`, 새 `tests/test_github_storage.py` |
| 2 | baseline 계산·혼합 순위 | 새 `src/glean_ai/github_ranking.py`, 새 `tests/test_github_ranking.py` |
| 3 | GitHub 수집·원자적 저장·dry-run | `src/glean_ai/collectors/github.py`, `src/glean_ai/service.py`, `src/glean_ai/storage.py`, `tests/test_collectors.py`, `tests/test_service.py`, `tests/test_github_storage.py` |
| 4 | 전체 보고서에서 GitHub 순서·자리 보존 | `src/glean_ai/pipeline.py`, `tests/test_pipeline.py` |
| 5 | 최신 run 조회·저장 보고서·지표 | `src/glean_ai/storage.py`, `src/glean_ai/service.py`, `src/glean_ai/cli.py`, `src/glean_ai/reporters.py`, `tests/test_cli.py`, `tests/test_storage_report.py` |
| 6 | fallback 제목·HF 지침과 통합 회귀 | `src/glean_ai/summarizer.py`, `tests/test_storage_report.py` |

새 migration revision은 `0004_github_star_snapshots`, `down_revision="0003_report_deliveries"`를 사용한다. 공통 계산 모듈은 `github_ranking.py`로 둔다.

## 작업 1: snapshot 저장 구조와 migration

**수락 기준:** AC3, AC4, AC11의 최신 실행 조회 기반.

`GitHubStarSnapshotRow`는 `id`, `external_id`, `observation_id`, `observed_at`, `stars`를 가진다. `observation_id`는 `runs.id` 외래 키이며 `(external_id, observation_id)` 유일 제약과 `(external_id, observed_at)` 조회 인덱스를 둔다. 같은 run에서 중복 저장소는 한 번만 저장하고 서로 다른 run의 관측은 각각 남긴다.

저장소 조회 인터페이스는 저장소 ID와 시간 구간별 observations를 반환하고, 최신 `kind="collect", source="github"` run을 status 포함 조회한다. SQLite가 timezone 정보를 보존하지 않는 기존 테스트 패턴을 고려해 naive DB 시각은 읽을 때 UTC로 복원한다.

- [ ] **실패 테스트 작성:** schema의 열·FK·unique·index, run별 중복 방지, 기존 `0003` DB 데이터 보존, downgrade 시 snapshot 테이블만 제거, `create_all()`로 만들어진 기존 schema에서 migration 중복 생성 방지를 확인한다. 모두 격리된 임시 SQLite DB에서 수행한다.
- [ ] **RED 확인:** 추가한 migration·storage 테스트만 실행하고, 신규 동작 부재로 실패하는지 확인한다. 환경/수집 실패는 RED 근거로 쓰지 않는다.
- [ ] **구현:** `0004` migration을 추가한다. 이미 snapshot 테이블이 있으면 구조를 확인해 완전한 schema만 채택하고 부분 schema는 거부한다. 과거 `metrics.stars`를 backfill하지 않는다. run 조회는 `started_at DESC, id DESC` 순으로 최신 1건을 찾되 success만 미리 필터링하지 않는다.
- [ ] **GREEN 확인:** 신규 storage/migration 테스트와 기존 migration 테스트를 재실행한다.

## 작업 2: baseline 계산과 혼합 선정

**수락 기준:** AC5–AC8.

`github_ranking.py`에 immutable `StarObservation(observed_at, stars)`와 `StarChange(baseline_at, delta, elapsed_seconds)` 값을 둔다. 공통 함수는 관측 이력에서 변화를 계산하고 content에 측정 근거를 복사해 붙이며, GitHub 후보를 결정적으로 혼합 선정한다. 근거는 `raw_metadata["github_star_observation"]`에 UTC `observed_at`, nullable `baseline_at`, nullable `delta`, nullable `elapsed_seconds`로 둔다.

- [ ] **실패 테스트 작성:** 22/26시간 양 끝과 범위 밖, 미래 snapshot, 다수 baseline 및 24시간과의 거리 동점, 첫 실행/신규/48시간/짧은 재실행 미측정, 양수·0·음수 구분, 증가량과 누적 순위 충돌, tie-break, 중복, 0–4개 후보, 입력 불변을 검증한다.
- [ ] **RED 확인:** 신규 계산·선정 테스트에서 구현 누락에 의한 실패를 확인한다.
- [ ] **구현:** 허용 baseline과 tie-break를 적용하고 순변화는 `현재 stars - baseline stars`로만 계산한다. 양수 증가량 상위 최대 3개, 미선택 누적 stars 상위 최대 2개, 남은 자리는 누적 순위로 채운다. 증가량 동점은 delta 내림차순, stars 내림차순, `external_id` 오름차순이며 누적 동점은 stars 내림차순, `external_id` 오름차순이다.
- [ ] **GREEN 확인:** 신규 계산·선정 테스트와 snapshot 조회 테스트를 실행한다.

## 작업 3: 수집 관측·원자적 저장·dry-run

**수락 기준:** AC1, AC4, AC8–AC10의 수집 경로.

`DailyService._collect_one()`이 이미 정한 UTC `started_at`을 관측 시각으로 공통 로직과 저장 함수에 전달한다. `CollectionResult`에 불필요한 중복 시각을 추가하지 않는다. 하나의 `Store.persist_github_collection(contents, result, started_at)` 경로가 run을 만들고, content를 upsert하며, success/partial 후보 snapshot을 저장하고, 7일보다 오래된 이력을 정리한다.

- [ ] **실패 테스트 작성:** collector 검색 query/sort/source_limit 및 최소 stars 유지, 유효하지 않은 stars snapshot 제외, 기존 GitHub row 최신화와 중복 방지, run/content/snapshot 시각 일치, 동일 run 중복 snapshot 1건, 7일 보존 경계, 부분·empty 상태, snapshot 저장 예외 시 content 및 성공 run rollback, dry-run 결과 일치와 전체 DB 불변을 검증한다.
- [ ] **RED 확인:** collector/service/storage 관련 신규 테스트를 실행한다.
- [ ] **구현:** GitHub 기존 row를 갱신한다. run 생성·content upsert·snapshot 기록·retention 삭제는 하나의 DB transaction에서 처리한다. transaction 실패는 기존 오류 경로에서 failed run으로 기록하고 부분 성공 상태를 남기지 않는다. partial은 현재 성공 후보만 snapshot한다. empty/failed/disabled/not_configured에는 snapshot을 기록하지 않는다. dry-run은 이력만 읽고 저장·정리를 건너뛴다.
- [ ] **GREEN 확인:** collector/service/storage와 계산 테스트를 실행한다.

## 작업 4: 기술 보고서 GitHub 자리 보존

**수락 기준:** AC7, AC12.

- [ ] **실패 테스트 작성:** score가 낮더라도 혼합 선정 GitHub 5개와 지정 순서가 남는지, 전체 상한 24개가 유지되는지, 후보 부족·limit 0–4 동작과 비-GitHub 기존 분배가 유지되는지 확인한다.
- [ ] **RED 확인:** pipeline 테스트에서 새 동작 부재를 확인한다.
- [ ] **구현:** `select_technology_report_items()`가 공통 GitHub selector로 먼저 최대 5개를 확보하고, 남은 예산을 기존 비-GitHub 분배 규칙에 배정한다. 전체 한도는 `min(limit, 24)`다. GitHub 순서를 이후 score 정렬이나 탈락 과정에서 다시 바꾸지 않는다. AI 뉴스 selector 동작은 유지한다.
- [ ] **GREEN 확인:** pipeline과 GitHub 순위 테스트를 실행한다.

## 작업 5: 최신 run 조회·저장 보고서·지표

**수락 기준:** AC2, AC10–AC13.

`Store`/`DailyService`에 최신 GitHub report candidates 조회 경로를 추가한다. 보고 시각 `R`에서 최신 GitHub run의 시각이 `[R-24h, R]` 범위 밖이거나 status가 success/partial이 아니면 후보를 비운다. 유효하면 해당 run의 snapshot만 연결 content와 함께 읽고 snapshot stars·관측 시각을 사용한다. 일반 `published_at` 조회와 `--hours`는 GitHub 후보를 제한하지 않는다.

- [ ] **실패 테스트 작성:** 30일 전 `updated_at` 저장소가 `--hours` 차이와 무관하게 남는지, 24시간 freshness 경계, 미래 관측 제외, latest success/partial/empty/failed/disabled/not_configured 상태 처리, 현재 실행 empty/failure가 이전 후보를 대체하는지, 저장 보고서와 dry-run 일치, 저장된 snapshot 시각 기준 재보고, measured/unmeasured 지표와 Fork 표시, 최종 24개 보고서에서 GitHub 5개 보존을 검증한다.
- [ ] **RED 확인:** storage/service/CLI/reporter 및 pipeline 관련 테스트를 실행한다.
- [ ] **구현:** 현재 GitHub 수집 결과가 report builder에 전달되면 해당 결과만 사용한다. empty/failure일 때도 저장된 이전 GitHub 항목을 남기지 않는다. 전달된 실행이 없으면 최신 실행·snapshot 조회를 사용한다. 저장 보고서와 dry-run은 동일한 baseline과 ranking logic을 공유한다. measured 지표는 `⭐ 누적 · 실제 비교시간 순증가/순변화`; 미측정은 누적 stars만 표시하고 Fork는 유지한다.
- [ ] **GREEN 확인:** storage/service/CLI/reporter 및 pipeline 관련 테스트를 재실행한다.

## 작업 6: 제목 문구 제거와 최종 회귀

**수락 기준:** AC14 및 전체 통합.

- [ ] **실패 테스트 작성:** GitHub/HF/Reddit fallback 제목이 원제목 앞 100자를 유지하고, 원문에 고정 문구가 있는 경우 보존하며, HF prompt에 `주목받는 모델` 고정 문구가 없고 근거 없는 공개·업데이트 시점 단정 금지가 유지되는지 확인한다. 변경으로 의미가 바뀌는 기존 fallback assertion만 새 요구에 맞춰 고친다.
- [ ] **RED 확인:** summarizer/fallback 관련 테스트에서 기존 문구가 여전히 생성되는 실패를 확인한다.
- [ ] **구현:** 세 fallback 자동 label을 제거하고 HF `SYSTEM_PROMPT`에서 고정 카피를 없앤다. 원제목 길이 제한과 나머지 근거 지침은 보존한다.
- [ ] **집중 회귀:** migration, GitHub collector/service/storage/ranking, pipeline, CLI, report/summarizer 테스트를 실행한다.
- [ ] **전체 회귀:** 전체 suite를 실행해 신규 실패와 기존 baseline 실패를 구분한다. baseline은 실행 checkout 선택 후 작업 전에 기록한다. `git diff --check`도 확인한다.

## 독립 검증과 완료 게이트

1. 사용자 계획 승인 후 CEO가 현재 상태를 재확인하고 실행 checkout 또는 요청된 새 branch를 확정해 workspace handoff를 작성한다. Coder는 그 handoff에서만 구현한다.
2. Coder는 TDD 순서로 각 작업의 RED → 최소 구현 → GREEN 근거를 기록하고, 다른 변경을 되돌리지 않는다.
3. 구현 완료 후 QA 역할이 별도로 수락 기준별 검증을 수행하고 보고서 출력과 migration 결과를 확인한다. QA는 코드를 수정하지 않는다.
4. DB schema·공유 상태·수명주기 변경이므로 significant risk로 분류한다. QA 뒤 Reviewer가 최종 전체 diff를 독립 검토하고 migration, 원자성, 최신 run 상태, dry-run 비변경, 24개 제한을 중점 확인한다.
5. QA 또는 Reviewer에서 결함이 발견되면 CEO가 Coder에게 수정 범위를 배정하고 영향을 받은 QA/Review를 재실행한다. CEO는 수락·완료 전에 미검증 범위와 실패 원인을 정리한다.
6. 운영 PostgreSQL migration과 실제 Slack 발송은 이 계획에서 실행하지 않는다. PostgreSQL 환경에서 별도 검증하지 못하면 최종 보고에서 미검증으로 밝힌다.

## 수락 기준 연결

| 설계 AC | 구현 작업 |
|---|---|
| AC1–AC2 검색 범위·오래된 `updated_at` | 3, 5 |
| AC3–AC4 migration·snapshot·보존 | 1, 3 |
| AC5–AC8 baseline·혼합 순위·warm-up | 2, 3 |
| AC9 원자적 저장·기존 row 갱신 | 3 |
| AC10 저장 보고서·dry-run 일치 | 3, 5 |
| AC11 최신 run 조회와 상태 처리 | 1, 5 |
| AC12 24개 제한의 GitHub 보존 | 4, 5 |
| AC13 지표·Fork | 5 |
| AC14 자동 제목 문구·HF 지침 | 6 |

## 계획 자체 검토

- 승인된 14개 수락 기준을 모두 작업 및 검증에 연결했다.
- snapshot의 `runs.id`, UTC `started_at`, 22–26시간 baseline, 7일 보존 정책이 명세와 맞는다.
- `CollectionResult`에 중복 시각 필드를 더하지 않고 기존 서비스가 관리하는 `started_at`을 공유하도록 정리했다.
- 같은 run 후보 중복은 저장·선정 단계에서 한 건으로 처리하며, content와 snapshot 일부만 저장되는 상태를 허용하지 않는다.
- 최신 empty/failed 실행은 과거 후보를 숨기고, dry-run은 DB를 바꾸지 않는다.
- 저장 보고서의 GitHub 후보는 `published_at` 및 `--hours`에서 분리되며 전체 보고서에서도 최대 5개가 보존된다.
- 작업 간 파일 소유권 겹침을 고려해 단일 Coder가 순차 진행한다. 독립 QA와 Reviewer를 최종 게이트로 둔다.
- 실행 checkout·branch, 실제 운영 DB 적용 여부, Slack 발송은 계획 작성 단계에서 임의로 결정하지 않았다.
