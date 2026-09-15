import httpx
import pytest
import respx

from glean_ai.collectors import (
    GitHubCollector,
    HuggingFaceCollector,
    RedditCollector,
    ThreadsCollector,
)
from glean_ai.collectors.github import build_queries
from glean_ai.config import Settings
from glean_ai.service import DailyService
from glean_ai.storage import Store


@pytest.mark.asyncio
@respx.mock
async def test_four_collectors_normalize():
    respx.get("https://api.github.com/search/repositories").mock(return_value=httpx.Response(200, json={"items": [{"id": 1, "owner": {"login": "a"}, "full_name": "a/b", "description": "AI", "html_url": "https://github.com/a/b", "updated_at": "2026-08-31T00:00:00Z", "language": "Python", "stargazers_count": 2, "forks_count": 1, "open_issues_count": 0, "topics": ["ai"]}]}))
    respx.get("https://huggingface.co/api/models").mock(return_value=httpx.Response(200, json=[{"id": "a/m", "author": "a", "tags": ["text-generation"], "lastModified": "2026-08-31T00:00:00Z", "likes": 2, "downloads": 3}]))
    respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "reddit-token"})
    )
    respx.get("https://oauth.reddit.com/r/artificial/new").mock(return_value=httpx.Response(200, json={"data": {"children": [{"data": {"id": "p", "author": "a", "title": "AI", "selftext": "news", "permalink": "/r/artificial/p", "created_utc": 1788134400, "score": 2, "num_comments": 1, "subreddit": "artificial"}}]}}))
    respx.get("https://graph.threads.net/v1.0/keyword_search").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "t", "text": "AI news", "username": "a", "permalink": "https://threads.net/@a/post/t", "timestamp": "2026-08-31T00:00:00Z", "media_type": "TEXT_POST"}]})
    )
    async with httpx.AsyncClient() as client:
        interests = {"keywords": ["AI"], "subreddits": ["artificial"]}
        results = (
            await GitHubCollector(client).collect(interests),
            await HuggingFaceCollector(client).collect(interests),
            await RedditCollector(client, client_id="id", client_secret="secret").collect(interests),
            await ThreadsCollector(client, token="threads-token").collect(interests),
        )
    assert [result[0].source for result in results] == [
        "github", "huggingface", "reddit", "threads",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_github_token_is_only_sent_to_github():
    github = respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    huggingface = respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "reddit-token"})
    )
    reddit = respx.get("https://oauth.reddit.com/r/artificial/new").mock(
        return_value=httpx.Response(200, json={"data": {"children": []}})
    )
    settings = Settings(
        database_url="sqlite://",
        github_token="github-secret",
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
    )

    async with httpx.AsyncClient() as client:
        collectors = DailyService(settings, Store(settings.database_url), client).collectors()
        interests = {"keywords": ["AI"], "subreddits": ["artificial"]}
        await collectors["github"].collect(interests)  # type: ignore[attr-defined]
        await collectors["huggingface"].collect(interests)  # type: ignore[attr-defined]
        await collectors["reddit"].collect(interests)  # type: ignore[attr-defined]

    assert github.calls[0].request.headers["Authorization"] == "Bearer github-secret"
    assert "Authorization" not in huggingface.calls[0].request.headers
    assert reddit.calls[0].request.headers["Authorization"] == "Bearer reddit-token"


def test_github_queries_respect_api_limits():
    queries = build_queries([
        "artificial intelligence", "generative AI", "LLM", "AI agent",
        "RAG", "MCP", "multimodal",
    ])
    assert len(queries) == 2
    assert all(query.count(" OR ") <= 5 for query in queries)
    assert all(len(query) <= 256 for query in queries)


@pytest.mark.asyncio
@respx.mock
async def test_threads_deduplicates_keyword_results():
    route = respx.get("https://graph.threads.net/v1.0/keyword_search").mock(
        return_value=httpx.Response(200, json={"data": [{
            "id": "t", "text": "AI news", "username": "a",
            "permalink": "https://threads.net/@a/post/t",
            "timestamp": "2026-08-31T00:00:00Z", "media_type": "TEXT_POST",
        }]})
    )
    async with httpx.AsyncClient() as client:
        result = await ThreadsCollector(client, token="token").collect(
            {"keywords": ["AI", "LLM"]}
        )
    assert route.call_count == 2
    assert [item.external_id for item in result] == ["t"]
    assert route.calls[0].request.url.params["search_type"] == "RECENT"


@pytest.mark.asyncio
@respx.mock
async def test_github_preserves_results_when_one_query_fails():
    route = respx.get("https://api.github.com/search/repositories").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{
                "id": 1, "owner": {"login": "a"}, "full_name": "a/b",
                "description": "AI", "html_url": "https://github.com/a/b",
                "updated_at": "2026-08-31T00:00:00Z", "language": "Python",
                "stargazers_count": 2, "forks_count": 1, "open_issues_count": 0,
                "topics": ["ai"],
            }]}),
            httpx.Response(422),
        ]
    )
    collector: GitHubCollector
    async with httpx.AsyncClient() as client:
        collector = GitHubCollector(client)
        result = await collector.collect({"keywords": [f"keyword-{i}" for i in range(7)]})
    assert route.call_count == 2
    assert [item.external_id for item in result] == ["1"]
    assert collector.partial_errors[0].code == "invalid_query"


@pytest.mark.asyncio
@respx.mock
async def test_reddit_uses_oauth_and_unique_user_agent():
    token = respx.post("https://www.reddit.com/api/v1/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "token"})
    )
    listing = respx.get("https://oauth.reddit.com/r/artificial/new").mock(
        return_value=httpx.Response(200, json={"data": {"children": []}})
    )
    async with httpx.AsyncClient() as client:
        await RedditCollector(
            client,
            client_id="client-id",
            client_secret="client-secret",
            user_agent="glean-ai/0.1 (by /u/operator)",
        ).collect({"subreddits": ["artificial"]})
    assert token.calls[0].request.headers["Authorization"].startswith("Basic ")
    assert listing.calls[0].request.headers["Authorization"] == "Bearer token"
    assert listing.calls[0].request.headers["User-Agent"] == "glean-ai/0.1 (by /u/operator)"
