from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..models import Content


class CollectorError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_retryable(error: BaseException) -> bool:
    return isinstance(error, (httpx.TimeoutException, httpx.TransportError)) or (
        isinstance(error, CollectorError) and error.code == "rate_limited"
    )


class Collector(ABC):
    source: str

    def __init__(self, client: httpx.AsyncClient, limit: int = 100) -> None:
        self.client = client
        self.limit = limit
        self.partial_errors: list[CollectorError] = []

    @abstractmethod
    async def collect(self, interests: dict[str, list[str]]) -> list[Content]: ...

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def get_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.client.get(url, **kwargs)
        return await self._response_json(response)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def get_text(self, url: str, **kwargs: Any) -> str:
        response = await self.client.get(url, **kwargs)
        await self._prepare_response(response)
        return response.text

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def post_json(self, url: str, **kwargs: Any) -> Any:
        response = await self.client.post(url, **kwargs)
        return await self._response_json(response)

    async def _response_json(self, response: httpx.Response) -> Any:
        await self._prepare_response(response)
        try:
            return response.json()
        except ValueError as exc:
            raise CollectorError("invalid_response", "Invalid JSON response") from exc

    async def _prepare_response(self, response: httpx.Response) -> None:
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
        self.check_response(response)

    @staticmethod
    def check_response(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        code = {
            400: "invalid_request",
            401: "authentication",
            403: "permission",
            422: "invalid_query",
            429: "rate_limited",
        }.get(response.status_code, "http_error")
        raise CollectorError(code, f"HTTP {response.status_code}")


_sleep: Callable[[float], Awaitable[None]]
from asyncio import sleep as _sleep  # noqa: E402
