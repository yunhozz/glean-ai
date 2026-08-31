import httpx
import pytest
import respx

from glean_ai.collectors import GitHubCollector, HuggingFaceCollector, RedditCollector


@pytest.mark.asyncio
@respx.mock
async def test_three_collectors_normalize():
    respx.get("https://api.github.com/search/repositories").mock(return_value=httpx.Response(200, json={"items": [{"id": 1, "owner": {"login": "a"}, "full_name": "a/b", "description": "AI", "html_url": "https://github.com/a/b", "updated_at": "2026-08-31T00:00:00Z", "language": "Python", "stargazers_count": 2, "forks_count": 1, "open_issues_count": 0, "topics": ["ai"]}]}))
    respx.get("https://huggingface.co/api/models").mock(return_value=httpx.Response(200, json=[{"id": "a/m", "author": "a", "tags": ["text-generation"], "lastModified": "2026-08-31T00:00:00Z", "likes": 2, "downloads": 3}]))
    respx.get("https://www.reddit.com/r/artificial/new.json").mock(return_value=httpx.Response(200, json={"data": {"children": [{"data": {"id": "p", "author": "a", "title": "AI", "selftext": "news", "permalink": "/r/artificial/p", "created_utc": 1788134400, "score": 2, "num_comments": 1, "subreddit": "artificial"}}]}}))
    async with httpx.AsyncClient() as client:
        interests = {"keywords": ["AI"], "subreddits": ["artificial"]}
        results = await GitHubCollector(client).collect(interests), await HuggingFaceCollector(client).collect(interests), await RedditCollector(client).collect(interests)
    assert [result[0].source for result in results] == ["github", "huggingface", "reddit"]
