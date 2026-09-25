import httpx
import pytest
import respx

from glean_ai.collectors import (
    GitHubCollector,
    HuggingFaceCollector,
    RedditCollector,
)
from glean_ai.collectors.github import build_queries
from glean_ai.config import Settings
from glean_ai.service import DailyService
from glean_ai.storage import Store

REDDIT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>t3_p</id><title>AI news</title><published>2026-08-31T00:00:00Z</published>
    <author><name>/u/a</name></author>
    <link href="https://www.reddit.com/r/artificial/comments/p/ai_news/" />
    <content type="html">&lt;p&gt;news&lt;/p&gt;</content>
  </entry>
</feed>"""


def github_item() -> dict[str, object]:
    return {
        "id": 1,
        "owner": {"login": "a"},
        "full_name": "a/b",
        "description": "AI",
        "html_url": "https://github.com/a/b",
        "updated_at": "2026-08-31T00:00:00Z",
        "language": "Python",
        "stargazers_count": 12,
        "forks_count": 1,
        "open_issues_count": 0,
        "topics": ["ai"],
    }


@pytest.mark.asyncio
@respx.mock
async def test_three_collectors_normalize():
    respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": [github_item()]})
    )
    respx.get("https://huggingface.co/api/models").mock(return_value=httpx.Response(
        200, json=[{
            "id": "a/m", "author": "a", "tags": ["text-generation"],
            "pipeline_tag": "text-generation", "trendingScore": 1,
            "lastModified": "2026-08-31T00:00:00Z", "likes": 50, "downloads": 3,
        }]
    ))
    respx.get("https://www.reddit.com/r/artificial/top/.rss?t=day").mock(
        return_value=httpx.Response(200, text=REDDIT_FEED)
    )
    async with httpx.AsyncClient() as client:
        interests = {"keywords": ["AI"], "subreddits": ["artificial"]}
        results = (
            await GitHubCollector(client).collect(interests),
            await HuggingFaceCollector(client).collect(interests),
            await RedditCollector(client).collect(interests),
        )
    assert [result[0].source for result in results] == [
        "github", "huggingface", "reddit",
    ]


@pytest.mark.asyncio
@respx.mock
async def test_huggingface_requires_trend_and_popularity():
    def model(name: str, trending: int, likes: int, downloads: int) -> dict[str, object]:
        return {
            "id": f"example/{name}", "author": "example", "tags": ["text-generation"],
            "pipeline_tag": "text-generation", "trendingScore": trending,
            "lastModified": "2026-08-31T00:00:00Z", "likes": likes,
            "downloads": downloads,
        }

    respx.get("https://huggingface.co/api/models").mock(return_value=httpx.Response(
        200, json=[
            model("weak", 10, 2, 3),
            model("likes", 1, 50, 3),
            model("downloads", 1, 2, 5_000),
            model("not-trending", 0, 100, 10_000),
        ]
    ))
    async with httpx.AsyncClient() as client:
        result = await HuggingFaceCollector(client).collect({"keywords": []})

    assert [item.external_id for item in result] == [
        "example/likes", "example/downloads",
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
    reddit = respx.get("https://www.reddit.com/r/artificial/top/.rss?t=day").mock(
        return_value=httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"/>')
    )
    settings = Settings(database_url="sqlite://", github_token="github-secret")
    async with httpx.AsyncClient() as client:
        collectors = DailyService(settings, Store(settings.database_url), client).collectors()
        interests = {"keywords": ["AI"], "subreddits": ["artificial"]}
        await collectors["github"].collect(interests)  # type: ignore[attr-defined]
        await collectors["huggingface"].collect(interests)  # type: ignore[attr-defined]
        await collectors["reddit"].collect(interests)  # type: ignore[attr-defined]
    assert github.calls[0].request.headers["Authorization"] == "Bearer github-secret"
    assert "Authorization" not in huggingface.calls[0].request.headers
    assert "Authorization" not in reddit.calls[0].request.headers


def test_github_queries_respect_api_limits():
    queries = build_queries([
        "artificial intelligence", "generative AI", "LLM", "AI agent",
        "RAG", "MCP", "multimodal",
    ])
    assert len(queries) == 2
    assert all(query.count(" OR ") <= 5 for query in queries)
    assert all(len(query) <= 256 for query in queries)
    assert all(query.endswith(" stars:>=10") for query in queries)


@pytest.mark.asyncio
@respx.mock
async def test_github_excludes_repositories_below_minimum_stars():
    low_interest = github_item()
    low_interest["stargazers_count"] = 9
    respx.get("https://api.github.com/search/repositories").mock(
        return_value=httpx.Response(200, json={"items": [low_interest]})
    )

    async with httpx.AsyncClient() as client:
        result = await GitHubCollector(client).collect({"keywords": ["AI"]})

    assert result == []


@pytest.mark.asyncio
@respx.mock
async def test_github_preserves_results_when_one_query_fails():
    route = respx.get("https://api.github.com/search/repositories").mock(
        side_effect=[
            httpx.Response(200, json={"items": [github_item()]}),
            httpx.Response(422),
        ]
    )
    async with httpx.AsyncClient() as client:
        collector = GitHubCollector(client)
        result = await collector.collect({"keywords": [f"keyword-{i}" for i in range(7)]})
    assert route.call_count == 2
    assert [item.external_id for item in result] == ["1"]
    assert collector.partial_errors[0].code == "invalid_query"


@pytest.mark.asyncio
@respx.mock
async def test_reddit_uses_one_combined_daily_top_rss_request():
    listing = respx.get(
        "https://www.reddit.com/r/artificial+MachineLearning/top/.rss?t=day"
    ).mock(
        return_value=httpx.Response(200, text=REDDIT_FEED)
    )
    async with httpx.AsyncClient() as client:
        collector = RedditCollector(
            client, user_agent="glean-ai/0.1 (by /u/operator)"
        )
        result = await collector.collect({"subreddits": ["artificial", "MachineLearning"]})
    assert [item.external_id for item in result] == ["t3_p"]
    assert result[0].body == "news"
    assert result[0].raw_metadata["subreddit"] == "artificial"
    assert result[0].raw_metadata["daily_rank"] == 1
    assert listing.call_count == 1
    assert listing.calls[0].request.url.params["t"] == "day"
    assert listing.calls[0].request.headers["User-Agent"] == "glean-ai/0.1 (by /u/operator)"
