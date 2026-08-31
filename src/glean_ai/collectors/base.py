from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models import Content


class Collector(ABC):
    source: str

    def __init__(self, client: httpx.AsyncClient, limit: int = 100) -> None:
        self.client = client
        self.limit = limit

    @abstractmethod
    async def collect(self, interests: dict[str, list[str]]) -> list[Content]: ...

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.client.get(url, **kwargs)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    seconds = float(retry_after)
                except ValueError:
                    seconds = max(0, (parsedate_to_datetime(retry_after) - parsedate_to_datetime(
                        response.headers["Date"]
                    )).total_seconds())
                await _sleep(seconds)
            response.raise_for_status()
        response.raise_for_status()
        return response.json()


_sleep: Callable[[float], Awaitable[None]]
from asyncio import sleep as _sleep  # noqa: E402
