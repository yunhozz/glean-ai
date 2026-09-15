# 다중 플랫폼 수집 안정화 설계

## 목표

Glean AI가 GitHub, Reddit, Hugging Face, Threads를 매일 독립적으로 조회하고, 성공한 각 플랫폼에 적합한 항목이 있으면 Slack 브리핑에 최소 한 건씩 포함한다. 일부 플랫폼이 실패해도 나머지 결과는 전송하되 Slack 상단과 실행 이력에서 실패 사실을 명확히 알린다.

X는 종량제 비용이 발생하므로 운영 대상에서 제외한다. 비공식 스크래핑은 API 변경, 계정 차단, 약관 위반 위험 때문에 사용하지 않는다.

## 현재 문제

- GitHub 수집기는 관심 키워드 7개를 6개의 `OR`로 연결한다. GitHub Search API의 Boolean 연산자 최대 5개 제한을 초과해 요청이 실패한다.
- Reddit 자격증명은 설정과 GitHub Actions에 존재하지만 수집기가 사용하지 않는다. 수집기는 OAuth 없이 공개 JSON 주소를 호출한다.
- Hugging Face 토큰 설정이 실제 요청에 반영되지 않는다.
- Threads는 토큰과 활성화 설정만 있고 Collector가 없다.
- 소스별 예외를 삼키고 성공한 결과만으로 보고서를 만들기 때문에 Slack에서는 전체 작업이 정상처럼 보인다.
- 테스트는 외부 API의 성공 응답만 모킹하여 쿼리 제한, 인증, 부분 장애를 검증하지 않는다.

## 범위

### 포함

- GitHub 검색 쿼리 분할과 결과 병합
- Reddit 공식 공개 RSS 조회
- Hugging Face 토큰 적용
- Threads 공식 Keyword Search API 연동
- 소스별 구조화된 수집 상태
- Slack 부분 장애 경고와 소스별 최소 한 건 선별
- 인증, 오류 분류, 부분 실패에 대한 테스트
- X 관련 미사용 설정과 문서 정리

### 제외

- X API 연동
- 비공식 스크래핑 또는 브라우저 자동화
- Threads 토큰의 무인 자동 갱신
- Hugging Face dataset 및 space 수집
- GitHub release와 시계열 성장률 수집
- 새로운 외부 모니터링 서비스 도입

## 접근 방식

현재 `Collector → process → Store → report` 구조를 유지하고 각 플랫폼 Adapter를 독립적으로 수정하거나 추가한다. 범용 Connector 프레임워크로 재구축하지 않는다. 소스가 네 개뿐인 현재 단계에서는 기존 경계를 유지하는 편이 변경량과 운영 위험이 가장 작다.

모든 Collector는 기존 `Content` 모델을 반환한다. 인증 방식, API URL, 페이지네이션과 응답 정규화만 소스 내부에서 책임진다. 수집 오케스트레이션은 결과 건수와 상태를 공통 형식으로 변환한다.

## 소스별 설계

### GitHub

관심 키워드를 GitHub가 허용하는 Boolean 연산자 수와 쿼리 길이 안에서 여러 검색 쿼리로 분할한다. 각 묶음을 `search/repositories`에 요청하고 결과를 합친 뒤 repository ID로 중복 제거한다. 전체 `source_limit`은 병합 결과에 적용한다.

GitHub 토큰은 GitHub 요청의 `Authorization` 헤더에만 넣는다. 한 묶음만 실패하면 성공한 묶음의 결과를 보존하고 소스 상태를 `partial`로 기록한다.

### Reddit

Reddit의 신규 Legacy Data API 앱은 moderation 용도에 한해 별도 승인이 필요하므로 일반 AI 브리핑에는 사용하지 않는다. 설정된 subreddit의 공식 공개 Atom RSS에서 최신 게시물을 조회한다. `REDDIT_USER_AGENT`는 앱과 연락 주체를 구분할 수 있는 고유한 값이어야 한다.

여러 subreddit을 하나의 multi-subreddit RSS 요청으로 묶어 호출 제한을 줄인다. RSS가 제공하는 제목, 본문, 작성자, URL과 게시 시각을 정규화하고 URL에서 원래 subreddit을 복원한다. 좋아요와 댓글 지표는 RSS에서 제공하지 않으므로 0으로 둔다.

### Hugging Face

현재 모델 조회 방식을 유지한다. `HUGGINGFACE_TOKEN`이 있으면 Hugging Face 요청에만 Bearer Token을 전달하고, 없으면 공개 조회를 허용한다. 현재 범위에서는 model만 수집한다.

### Threads

Meta App에서 발급한 장기 Threads User Access Token을 사용한다. 앱에는 `threads_basic`과 `threads_keyword_search` 권한이 있어야 하며 운영 전 App Review를 완료해야 한다.

관심 키워드별로 공식 `GET /keyword_search`를 `search_type=RECENT`로 호출한다. 본문, 작성자, permalink, 게시 시각과 제공되는 공개 지표를 기존 `Content` 및 `Metrics` 구조로 정규화한다. 여러 키워드에 같은 게시물이 나타나면 Threads media ID로 중복 제거하고 전체 `source_limit`을 적용한다.

토큰 갱신은 첫 구현 범위에서 제외한다. 만료 또는 권한 부족 응답은 재시도하지 않고 `failed`로 기록하여 Slack과 GitHub Actions 로그에서 조치가 필요함을 알린다.

## 수집 결과 계약

오케스트레이터는 각 Collector 실행 결과를 다음 정보로 표현한다.

- `source`: 플랫폼 식별자
- `status`: `success`, `empty`, `partial`, `failed`, `disabled`, `not_configured` 중 하나
- `fetched_count`: API에서 정규화한 항목 수
- `accepted_count`: 필터링 후 저장 대상으로 인정한 항목 수
- `error_code`: 안전한 내부 오류 분류 또는 없음
- `error_message`: 비밀정보를 제거한 짧은 설명 또는 없음

상태 의미는 다음과 같다.

- `success`: 호출과 처리가 완료되고 유효 항목이 존재함
- `empty`: 호출은 성공했지만 유효 항목이 없음
- `partial`: 여러 요청 중 일부가 실패했지만 유효 결과가 존재함
- `failed`: 인증, 쿼터, 네트워크 또는 응답 오류로 결과를 사용할 수 없음
- `disabled`: 운영 설정으로 명시적으로 비활성화됨
- `not_configured`: 필수 자격증명이 없음

`empty`와 `partial`은 성공한 API 호출의 의미를 보존하기 위해 `failed`와 구분한다.

## 실행 및 데이터 흐름

1. daily 명령이 네 Collector를 병렬 실행한다.
2. 각 Collector의 콘텐츠와 상태를 `CollectionResult`로 받는다.
3. 정상 또는 부분 성공한 결과를 중복 제거, 분류, 점수화한다.
4. 콘텐츠와 소스별 실행 상태를 DB에 저장한다.
5. 최근 24시간의 적합한 후보에서 성공 또는 부분 성공한 소스별 최소 한 건을 먼저 선택한다.
6. 나머지 자리는 전체 점수순으로 채우되 한 소스가 전체 제한의 40%를 넘지 않게 한다.
7. Slack 상단에 소스별 상태와 건수를 표시한다.
8. 하나 이상의 소스가 유효 결과를 제공하면 부분 실패 경고와 함께 브리핑을 보낸다.
9. 네 소스가 모두 `failed`, `not_configured`, `disabled` 중 하나이면 빈 브리핑을 보내지 않고 daily 명령을 실패시킨다.

성공한 소스라도 적합한 후보가 없으면 저품질 항목을 억지로 포함하지 않는다. Slack 상태에는 해당 소스를 `검색 결과 없음`으로 표시한다. 모든 호출이 정상이나 네 소스가 모두 `empty`이면 항목 없는 상태 브리핑을 보내 API 장애와 실제 무소식을 구분한다.

## Slack 표시

브리핑 상단 context block에 다음 형태의 상태 요약을 넣는다.

```text
수집 상태: GitHub 23건 · Reddit 실패 · Hugging Face 18건 · Threads 7건
주의: Reddit RSS 응답 오류로 이번 브리핑에서 제외됐습니다
```

사용자 메시지에는 플랫폼, 상태, 짧은 조치 가능 원인만 포함한다. URL, 토큰, Authorization 헤더, 외부 응답 본문은 포함하지 않는다. 상세 오류는 비밀정보를 정제한 후 실행 이력과 GitHub Actions 로그에 남긴다.

## 오류와 재시도

- timeout과 일시적 transport 오류는 지수 backoff로 최대 세 번 재시도한다.
- HTTP `429`는 `Retry-After`를 따르되 workflow 제한 시간을 넘기지 않는다.
- HTTP `401`과 `403`은 인증 또는 권한 오류로 분류하고 재시도하지 않는다.
- HTTP `400`과 `422`는 요청 또는 쿼리 오류로 분류하고 재시도하지 않는다.
- 응답 스키마가 예상과 다르면 해당 소스만 실패 처리한다.
- 필수 Secret이 없으면 외부 요청 없이 `not_configured`를 반환한다.
- 여러 키워드나 쿼리 묶음 중 일부만 실패하면 성공 결과를 보존하고 `partial`로 기록한다.

## 설정과 비밀정보

GitHub Actions Secrets에서 다음 값을 주입한다.

- `GITHUB_TOKEN`
- `REDDIT_USER_AGENT`
- `HUGGINGFACE_TOKEN`
- `THREADS_ACCESS_TOKEN`

GitHub, Reddit, Hugging Face, Threads는 기본 활성화한다. 필수 자격증명이 없는 Threads는 `not_configured`가 된다. GitHub는 공개 검색이 가능하지만 인증 토큰 사용을 운영 기본값으로 한다. Reddit과 Hugging Face는 자격증명 없이 공개 조회한다.

X 관련 토큰과 enable 설정은 제거하고 README에 비용 때문에 운영 범위에서 제외한다고 기록한다.

## 저장 구조

기존 콘텐츠 테이블과 `source + external_id` 고유 제약을 유지한다. 실행 이력은 기존 `runs` 테이블을 확장하여 구조화된 상태와 건수를 보존한다. 세부 구현에서는 기존 `status` 문자열을 계속 조합하지 않고 상태 값과 수치 필드를 분리하며, 마이그레이션으로 기존 행을 보존한다.

보고서에는 해당 daily 실행의 `CollectionResult`를 전달한다. 오래된 실패 이력이 현재 브리핑에 섞이지 않도록 보고서가 임의로 최근 run을 재조회하지 않는다.

## 테스트 전략

### Collector 단위 테스트

- GitHub 키워드가 API 제한 이하의 여러 쿼리로 분할되는지 검증한다.
- GitHub 검색 묶음 간 중복 repository가 한 번만 남는지 검증한다.
- Reddit이 subreddit별 공식 RSS를 조회하고 Atom 항목을 정규화하는지 검증한다.
- Reddit이 여러 subreddit을 하나의 RSS 요청으로 묶고 원래 subreddit을 복원하는지 검증한다.
- Hugging Face 토큰이 해당 호스트에만 전달되며 토큰 없는 공개 조회도 가능한지 검증한다.
- Threads의 `RECENT` 검색, 페이지네이션, 정규화와 키워드 간 중복 제거를 검증한다.
- 각 소스의 `401`, `403`, `429`, `422`, timeout과 잘못된 JSON 응답을 검증한다.

### 통합 테스트

- 네 소스가 모두 성공하면 적합한 항목이 있는 각 소스에서 최소 한 건을 선택한다.
- 한 소스가 실패해도 나머지 결과와 경고를 Slack payload에 포함한다.
- 호출 성공 후 결과가 없으면 `empty`로 표시한다.
- 일부 쿼리만 실패하면 `partial`과 유효 결과를 함께 보존한다.
- 네 소스가 모두 `failed`, `not_configured`, `disabled` 중 하나이면 Slack을 보내지 않고 실행이 실패한다.
- 네 소스가 모두 `empty`이면 항목 없는 상태 브리핑을 전송한다.
- 토큰과 인증 헤더가 로그 및 Slack payload에 포함되지 않는다.

외부 API를 직접 호출하는 테스트는 필수 CI에 넣지 않는다. 모든 계약 테스트는 실제 URL, 요청 파라미터, 인증 헤더와 오류 응답을 정확하게 모킹한다.

## 완료 기준

- GitHub, Reddit, Hugging Face, Threads가 매일 독립적으로 수집된다.
- 성공 또는 부분 성공한 각 플랫폼에서 적합한 항목이 있으면 Slack에 최소 한 건 포함된다.
- 부분 실패가 Slack 상단과 DB 실행 이력에 나타난다.
- 전체 실패 시 Slack을 보내지 않고 GitHub Actions가 실패한다.
- X는 실행, 설정, 문서상 운영 대상에서 제외된다.
- 비밀정보가 로그, DB 오류 메시지, Slack에 노출되지 않는다.
- 단위 및 통합 테스트, Ruff, mypy가 모두 통과한다.

## 운영 준비 사항

- Reddit 공개 RSS 요청에 사용할 고유 User-Agent를 확인한다.
- Meta App에 Threads 사용 사례를 추가하고 필요한 권한에 대한 App Review를 완료한다.
- 장기 Threads User Access Token을 발급하고 만료 전에 교체하는 운영 절차를 정한다.
- 배포 후 첫 daily 실행에서 네 소스의 상태와 건수를 확인한다.
