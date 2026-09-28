from .github import GitHubCollector
from .hacker_news import HackerNewsCollector
from .huggingface import HuggingFaceCollector
from .reddit import RedditCollector
from .rss import RSSCollector

__all__ = [
    "GitHubCollector",
    "HackerNewsCollector",
    "HuggingFaceCollector",
    "RedditCollector",
    "RSSCollector",
]
