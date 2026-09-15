import httpx
import pytest
import respx

from glean_ai.collectors.base import CollectorError
from glean_ai.collectors.huggingface import HuggingFaceCollector


@pytest.mark.asyncio
@respx.mock
async def test_http_error_is_classified_without_response_body():
    route = respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(401, text="secret response")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(CollectorError) as caught:
            await HuggingFaceCollector(client).collect({})
    assert route.call_count == 1
    assert caught.value.code == "authentication"
    assert "secret response" not in str(caught.value)
