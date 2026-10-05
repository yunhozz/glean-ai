# AI 기술 소스 수집·표시 보완 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. 이 저장소의 AGENTS.md와 yunho-harness에 따라 CEO가 작업을 순차 조정하고, Coder가 지정 파일을 수정하며 QA가 독립 검증한다.

**Goal:** 기술 블로그별 최신 글 fallback, GitHub와 Hugging Face의 각 최대 5개 표시, Reddit 하루 인기 1·2위의 수집과 순서 보존을 구현한다.

**Architecture:** 기존 수집기와 저장소를 유지한다. 보고서의 최근 후보에 없는 기술 블로그만 저장소에서 최신 글을 보충하고, AI 기술 선택기는 소스별 한도와 Reddit 순위를 적용한다. AI 뉴스 선택과 Slack 전송 구조는 유지한다.

**Tech Stack:** Python 3.12, SQLAlchemy, httpx, pytest, pytest-asyncio, respx.

**Spec:** CEO가 2026-10-05 전달한 사용자 승인 bounded 설계. 별도 architectural spec은 없다.

## Global Constraints

- 모든 코드 수정과 명령은 /Users/yunho/.codex/worktrees/ai-tech-source-limits/glean-ai에서 수행한다.
- 기술 블로그의 primary 최근 후보는 기존 Store.recent 의미인 published_at >= since OR collected_at >= since를 따른다.
- configured 블로그 소스에 primary 후보가 하나라도 있으면 저장된 오래된 글을 추가하지 않는다. 후보가 0개일 때만 그 소스의 저장 글 중 published_at 최신 최대 2개를 사용한다.
- 최근 후보 1개는 1개만 표시한다. fallback은 게시 시각 최신순이다.
- GitHub와 Hugging Face의 기존 검색·적격 조건 및 점수 계산은 유지하고, 보고에서 각각 최대 5개를 선택한다.
- Reddit은 설정 subreddit을 합친 기존 top/.rss?t=day 응답의 원래 daily_rank 1·2만 수집한다. 3위를 빈 자리에 승격하지 않는다.
- Reddit 1·2위는 일반 키워드 필터에 의해 제거되지 않으며, 재수집한 기존 항목과 dry-run에는 현재 rank가 반영되고 표시 순서는 1→2다.
- AI 기술 전체 상한은 24개, 블로그별 상한은 2개, Reddit 상한은 2개다. 후보가 부족하면 실제 후보 수만 표시한다.
- 기존 AI 뉴스 선택 규칙, 두 Slack 메시지, Block Kit 최대 50개, 섹션 텍스트 최대 2,900자, 보고 전송 정책을 유지한다.
- DB schema, migration, 외부 API, 새 의존성은 추가하지 않는다.
- Coder는 테스트를 먼저 추가하고 올바른 실패를 확인한 뒤 최소 구현과 통과 확인을 한다. 환경 오류는 RED 증거가 아니다.
- 확인된 기존 전체 테스트 실패 7개는 이 작업의 수정 범위가 아니다. 최종 suite에서 신규 실패가 생겼는지 baseline과 비교한다.
- CEO는 implementation/configuration/test-code를 직접 편집하지 않는다. Coder는 다른 작업자의 변경을 되돌리지 않는다.

## Review Focus

1. 최근 수집됐지만 오래전에 게시된 블로그 글은 기존 24시간 primary 후보 의미를 보존한다.
2. 최근 블로그 후보가 정확히 1개인 소스는 오래된 저장 글로 두 번째를 채우지 않는다.
3. fallback source의 저장 후보가 RSS 순서나 점수와 다른 게시일을 가지면 최신 게시일 2개를 고른다.
4. Reddit 원래 1위가 파싱되지 않으면 3위를 승격하지 않고 원래 2위만 반환한다.
5. Reddit rank와 일반 점수 순서가 다르면 보고 출력은 rank 1→2이며 저장된 이전 rank를 쓰지 않는다.
6. 24개 기술 항목을 모두 채워도 GitHub/Hugging Face 및 Slack 메시지 한도를 지킨다.

## 환경 및 Baseline

확인된 테스트 환경: Python /usr/local/bin/python3.12, 임시 환경 /private/tmp/glean-ai-uv-env-312, uv cache /private/tmp/glean-ai-uv-cache-312.

테스트 명령 prefix와 pytest -p no:cacheprovider를 사용하고 대상 파일 및 -k 선택식을 추가한다.

PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/private/tmp/glean-ai-uv-cache-312 UV_PROJECT_ENVIRONMENT=/private/tmp/glean-ai-uv-env-312 uv run --no-sync --python /usr/local/bin/python3.12 pytest -p no:cacheprovider

변경 전 전체 suite는 35 passed, 7 failed다. 사용자는 기존 실패를 별도 기록하고 요청 변경을 진행하도록 승인했다.

- tests/test_cli.py::test_report_groups_sources_into_news_and_technology_messages
- tests/test_storage_report.py::test_huggingface_fallback_describes_trend_without_claiming_update
- tests/test_storage_report.py::test_llm_fallback_and_blocks
- tests/test_storage_report.py::test_blocks_show_every_item_in_highlighted_format
- tests/test_storage_report.py::test_blocks_show_github_metrics_and_reddit_daily_rank
- tests/test_storage_report.py::test_large_github_report_fits_block_limits_and_keeps_every_item
- tests/test_storage_report.py::test_slack_sends_two_group_messages_with_all_21_source_sections

최종 전체 실행에서 신규 실패가 없어야 한다. 기존 실패가 계속되면 baseline과 동일한 원인인지 기록한다.

## 파일 책임

- src/glean_ai/collectors/reddit.py: Reddit 원래 rank 1·2만 반환.
- src/glean_ai/storage.py: 소스별 최신 저장 글 조회와 Reddit 재수집 갱신.
- src/glean_ai/service.py: 최신 DB row 변환과 Reddit 통과 규칙.
- src/glean_ai/pipeline.py: AI 기술 소스별 선택 수·순서.
- src/glean_ai/cli.py: dry-run 현재 결과 병합, 블로그별 fallback, 선택기 연결.
- README.md: 승인 동작을 설명.
- tests/test_collectors.py, test_service.py, test_storage_report.py, test_pipeline.py, test_cli.py: 행동별 회귀 테스트.

## 작업 1: Reddit 상위 2개와 재수집 rank 보존

**파일:** collectors/reddit.py, service.py, storage.py. 테스트: test_collectors.py, test_service.py, test_storage_report.py.

기존 인터페이스는 RedditCollector.collect(interests: dict[str, list[str]]) -> list[Content], DailyService._collect_one, Store.upsert_many(contents: list[Content]) -> int를 유지한다.

- [ ] **실패 테스트 추가**
  - test_reddit_collects_only_original_top_two: 4개 entry에서 반환 ID와 daily_rank가 원래 1·2위뿐이고 통합 endpoint와 t=day 요청을 유지한다.
  - test_reddit_does_not_promote_rank_three: 원래 1위가 유효하지 않으면 유효한 2위만 남고 3위는 포함되지 않는다.
  - test_reddit_top_two_survive_keyword_filter: 키워드·category와 일치하지 않는 원래 1·2위도 accepted contents에 남는다.
  - test_reddit_recollection_updates_current_data: 같은 ID 재수집이 title/body/rank/scores/collected_at을 갱신하며 DB 행 수와 신규 insert 수는 늘지 않는다.
- [ ] **RED 확인:** 공통 prefix 뒤에 tests/test_collectors.py tests/test_service.py tests/test_storage_report.py -k 'original_top_two or promote_rank_three or top_two_survive or reddit_recollection'을 붙인다. 신규 assertion이 요구 동작 부재로 실패해야 한다.
- [ ] **최소 구현:** collector는 파싱 결과 앞 두 개가 아니라 raw_metadata.daily_rank가 원래 1 또는 2인 항목만 반환한다. DailyService._collect_one의 process include_unmatched 조건에 Reddit을 포함한다. Store.upsert_many의 기존 갱신 source 집합에 reddit을 포함한다.
- [ ] **GREEN 확인:** RED 명령을 재실행하고 기존 test_reddit_uses_one_combined_daily_top_rss_request도 통과시킨다.
- [ ] **인계:** 변경 파일과 RED/GREEN 증거를 CEO에게 전달한다.

## 작업 2: 최근 후보가 없는 블로그에 저장된 최신 글 보충

**파일:** storage.py, service.py, cli.py. 테스트: test_storage_report.py, test_service.py, test_cli.py.

추가 인터페이스는 Store.latest_for_source(source: str, limit: int = 2) -> list[ContentRow]와 DailyService.latest_for_source(source: str, limit: int = 2) -> list[Content]다. Store.recent 및 DailyService.recent(hours=24)의 기존 조건·반환 계약은 유지한다. timezone-naive DB 시각은 기존 변환 경로대로 UTC 보정한다.

- [ ] **실패 테스트 추가**
  - test_latest_blog_posts_use_publication_order: 저장 글 3개 중 score와 collected_at이 달라도 게시 시각 최신 2개를 반환하며 동률은 external_id로 안정화한다.
  - test_blog_fallback_when_recent_candidates_empty: configured source의 기존 recent 후보가 0개일 때 저장된 최신 2개를 보고한다.
  - test_blog_one_recent_candidate_is_not_backfilled: primary 후보가 1개면 해당 1개만 쓴다.
  - test_recently_collected_old_blog_post_is_primary: 최근 수집된 오래된 게시물은 기존 recent 후보로 취급하고 fallback하지 않는다.
  - test_blog_fallback_remains_available_next_day: 최근 기간을 벗어난 뒤에도 최신 저장 글 조회로 fallback한다.
- [ ] **RED 확인:** 공통 prefix 뒤에 tests/test_storage_report.py tests/test_service.py tests/test_cli.py -k 'latest_blog or blog_fallback or one_recent_candidate or old_blog_post_is_primary'를 붙인다.
- [ ] **최소 구현:** Store.latest_for_source는 source의 published_at DESC, external_id ASC로 정렬해 limit개를 반환한다. DailyService.latest_for_source는 기존 row→Content 변환과 UTC 보정을 재사용한다. CLI는 recent 후보와 이번 실행 collection_results를 먼저 병합하고, configured 블로그별 후보가 비었을 때만 latest_for_source(source, 2)를 추가한다. dry-run은 동일 (source, external_id)의 현재 결과가 저장 후보를 대체하게 한다.
- [ ] **GREEN 확인:** 같은 명령과 기존 test_newly_discovered_tech_blog_post_appears_once를 실행한다.
- [ ] **인계:** primary/fallback 판단과 최신 순서 증거를 CEO에게 전달한다.

## 작업 3: AI 기술별 한도와 Reddit 출력 순서 연결

**파일:** pipeline.py, cli.py. 테스트: test_pipeline.py, test_cli.py, test_storage_report.py.

추가 인터페이스: select_technology_report_items(items: list[Content], tech_blog_sources: list[str], fallback_sources: set[str] | None = None, limit: int = 24) -> list[Content]. _make_report signature 및 AI 뉴스의 rank_ai_news/select_report_items 규칙은 유지한다.

- [ ] **실패 테스트 추가**
  - test_technology_source_limits: 충분한 후보에서 GitHub 5, Hugging Face 5, Reddit 2, 블로그별 최대 2, 전체 최대 24개.
  - test_technology_uses_available_candidates: 후보가 부족하면 있는 수만 선택한다.
  - test_technology_fallback_preserves_latest_order: fallback은 게시일 내림차순, primary는 기존 final_score 내림차순.
  - test_reddit_output_preserves_daily_rank: rank 2의 score가 더 높아도 1→2 순서이며 rank 3은 제외.
  - test_dry_run_replaces_same_id_with_current_reddit: DB rank 대신 이번 실행 rank를 쓰고 저장소는 변경하지 않는다.
  - test_successful_reddit_result_replaces_cached_candidates: 현재 실행이 success/partial/empty이면 저장된 이전 Reddit 항목을 현재 accepted contents로 대체하며, empty에서는 이전 항목을 표시하지 않는다.
  - test_current_reddit_top_two_survive_report_selection: 현재 두 항목이 낮은 score나 저장 Reddit 후보 때문에 누락되지 않는다.
  - test_news_selection_is_unchanged: 뉴스 최대 28, 소스당 최대 2, 기존 점수 선택을 유지한다.
  - test_twenty_four_technology_items_fit_slack: 링크 24개를 유지하고 Block Kit 50개·section 2,900자 한도를 지킨다.
- [ ] **RED 확인:** 공통 prefix 뒤에 tests/test_pipeline.py tests/test_cli.py tests/test_storage_report.py -k 'technology_source_limits or available_candidates or latest_order or preserves_daily_rank or same_id_with_current_reddit or survive_report_selection or news_selection_is_unchanged or twenty_four_technology'를 붙인다.
- [ ] **최소 구현:** AI 기술 selector는 GitHub/Hugging Face 각 score순 최대 5개, 블로그별 최대 2개, Reddit 원래 rank 1·2를 선택한다. fallback 블로그는 게시일순, Reddit은 rank 오름차순을 renderer까지 보존한다. daily의 Reddit collection result가 success/partial/empty이면 저장된 이전 Reddit 후보를 해당 실행의 accepted contents로 대체한다(empty면 표시 후보 없음). failed/disabled의 캐시 정책과 standalone report는 기존 동작을 유지한다. 전체 상한은 24개이며 기존 source coverage 우선순위를 유지한다. AI 뉴스에는 새 selector를 적용하지 않는다.
- [ ] **GREEN 확인:** 같은 명령과 관련 collector, service, storage, pipeline, CLI 테스트를 실행한다.
- [ ] **인계:** 소스별 개수, Reddit 순서, Slack 한도 결과를 CEO에게 전달한다.

## 작업 4: 문서, 전체 suite 비교, 독립 QA

**파일:** README.md.

- [ ] **README 갱신:** 기존 24시간 후보가 없는 기술 블로그는 저장된 게시일 최신 2개를 표시하며, 최근 후보 1개는 그대로 1개만 표시한다고 쓴다. GitHub/Hugging Face 각각 최대 5개, Reddit 통합 하루 인기 1·2위, AI 기술 전체 최대 24개로 표시 정책을 갱신한다. 기존 후보 조건 및 제한 설명은 유지한다.
- [ ] **전체 suite 실행:** 위 prefix로 pytest 전체를 실행한다. 신규 회귀가 통과하고 baseline 대비 새 실패가 없어야 한다.
- [ ] **Diff 확인:** git diff --check와 변경 파일 목록을 CEO에게 전달한다.
- [ ] **독립 QA:** QA가 같은 워크트리·환경에서 관련 테스트를 실행하고 README와 실제 출력의 일치를 확인한다.
- [ ] **CEO acceptance:** 수용 기준과 QA evidence 확인 뒤 선택적 commit을 결정한다. acceptance 전에 commit하지 않는다.

## 의존성, 가정 및 자체 검토

- 작업 순서는 1→2→3→4다. 작업 1의 저장 갱신은 Reddit 보고에, 작업 2의 최신 조회는 블로그 fallback에 선행한다.
- latest lookup은 configured 기술 블로그에만 호출한다. 수집 실패 상태 표시 계약은 유지한다.
- standalone report용 Reddit snapshot 또는 RunRow 정책은 추가하지 않는다.
- 전체 suite 기존 7개 실패는 별도 승인 없이 수정하지 않는다.
- 6개 configured 블로그의 12개, GitHub 5개, Hugging Face 5개, Reddit 2개가 전체 24개를 채운다.
- 자체 검토: 승인된 세 요구 모두 코드·테스트·README에 대응한다. 기존 recent 판정을 보존하고 published-only 판정, schema 변경, 실패 실행 후 저장 후보 폐기 정책은 넣지 않았다. 변경 파일이 겹치므로 같은 Coder가 순차로 구현하고 ordinary behavior 기준 독립 QA를 마지막에 수행한다.
