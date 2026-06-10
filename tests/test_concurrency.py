"""Concurrent first calls to one stub must generate exactly once, not race or cycle-error."""

import asyncio
import threading
import time
from typing import Any

from fakes import ScriptedClient, submit

from jiti import jiti
from jiti.agent.engine import Engine
from jiti.core.store import JitiStore

TESTS = "def test_f():\n    assert f(2) == 4"


class SlowClient(ScriptedClient):
    """Holds each completion long enough for the other caller to pile up on the stub."""

    def __call__(self, **kwargs: Any) -> Any:
        time.sleep(0.05)
        return super().__call__(**kwargs)


def test_concurrent_threads_share_one_generation(tmp_path):
    client = SlowClient([submit("f", "return x * 2", TESTS)])
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"))

    @jiti(engine=engine)
    def f(x: int) -> int:
        """Return x * 2."""
        ...

    results: list[int] = []
    errors: list[BaseException] = []

    def call() -> None:
        try:
            results.append(f(3))
        except BaseException as error:  # surfaced by the assert below
            errors.append(error)

    threads = [threading.Thread(target=call) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert results == [6, 6, 6, 6]
    assert client.calls == 1


def test_concurrent_async_tasks_share_one_generation(tmp_path):
    tests = "async def test_double() -> None:\n    assert await g(2) == 4"
    client = SlowClient([submit("g", "return x * 2", tests)])
    engine = Engine(completion=client, store=JitiStore(tmp_path / ".jiti"))

    @jiti(engine=engine)
    async def g(x: int) -> int:
        """Return x * 2."""
        ...

    async def both() -> list[int]:
        return list(await asyncio.gather(g(3), g(4)))

    assert asyncio.run(both()) == [6, 8]
    assert client.calls == 1
