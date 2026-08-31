from datetime import datetime, timezone

import pytest

from glean_ai.models import Content, Metrics


@pytest.fixture
def sample() -> Content:
    return Content(source="github", external_id="1", content_type="repository", author="dev", title="New AI agent MCP SDK", body="Open source workflow automation", url="https://github.com/acme/agent?utm_source=x", published_at=datetime.now(timezone.utc), metrics=Metrics(stars=100, forks=10))
