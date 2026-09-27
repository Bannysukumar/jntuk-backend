import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse

for name, value in {
    "RABBITMQ_URL": "amqp://guest:guest@localhost/",
    "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/jntuk",
    "QUEUE_NAME": "test",
    "REDIS_URL": "redis://localhost:6379/0",
    "VAPID_PUBLIC_KEY": "test",
    "VAPID_PRIVATE_KEY": "test",
    "TELEGRAM_TOKEN": "test",
    "TELEGRAM_CHAT_ID": "test",
    "AWS_ACCESS_KEY_ID": "test",
    "AWS_SECRET_ACCESS_KEY": "test",
    "AWS_REGION": "us-east-1",
    "S3_BUCKET_NAME": "test",
    "GRACE_MARKS_ADMIN_KEY": "test",
}.items():
    os.environ.setdefault(name, value)

from config.redisConnection import redisConnection  # noqa: E402
from config.settings import (  # noqa: E402
    FRESHNESS_SCRAPE_EXPIRY,
    QUEUE_DEDUP_EXPIRY,
    RABBITMQ_ROLL_NUMBERS,
)
from messaging.publisher import publish_message  # noqa: E402


class _ChannelContext:
    def __init__(self, channel):
        self.channel = channel

    async def __aenter__(self):
        return self.channel

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None


def _app(message_count=0, publish=None):
    exchange = SimpleNamespace(publish=publish or AsyncMock())
    queue = SimpleNamespace(
        declaration_result=SimpleNamespace(message_count=message_count),
    )
    channel = SimpleNamespace(
        declare_queue=AsyncMock(return_value=queue),
        default_exchange=exchange,
    )
    connection = SimpleNamespace(
        channel=MagicMock(return_value=_ChannelContext(channel))
    )
    return SimpleNamespace(
        state=SimpleNamespace(rabbitmq_connection=connection)
    ), exchange


def _redis(**overrides):
    client = SimpleNamespace(
        get=MagicMock(return_value=None),
        set=MagicMock(return_value=True),
        sadd=MagicMock(),
        srem=MagicMock(),
        delete=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def test_publish_skips_duplicate_roll():
    app, exchange = _app()
    redis_client = _redis(set=MagicMock(return_value=None))

    with (
        patch.object(redisConnection, "client", redis_client),
        patch("messaging.publisher.check_valid_url_in_redis", return_value="https://ok"),
    ):
        response = asyncio.run(publish_message(app, "226Q1A4304"))

    assert response.status_code == 202
    exchange.publish.assert_not_awaited()
    redis_client.sadd.assert_not_called()


def test_publish_claims_and_enqueues_new_roll():
    app, exchange = _app()
    redis_client = _redis()

    with (
        patch.object(redisConnection, "client", redis_client),
        patch("messaging.publisher.check_valid_url_in_redis", return_value="https://ok"),
    ):
        response = asyncio.run(publish_message(app, "226Q1A4304"))

    assert response.status_code == 202
    exchange.publish.assert_awaited_once()
    redis_client.set.assert_any_call(
        "queued:226Q1A4304", "1", nx=True, ex=QUEUE_DEDUP_EXPIRY
    )
    redis_client.sadd.assert_called_once_with(RABBITMQ_ROLL_NUMBERS, "226Q1A4304")


def test_freshness_publish_skips_when_queue_is_busy():
    app, exchange = _app(message_count=10)
    redis_client = _redis()

    with (
        patch.object(redisConnection, "client", redis_client),
        patch("messaging.publisher.check_valid_url_in_redis", return_value="https://ok"),
    ):
        response = asyncio.run(
            publish_message(app, "226Q1A4304", freshness=True)
        )

    assert response.status_code == 202
    exchange.publish.assert_not_awaited()
    redis_client.set.assert_not_called()


def test_freshness_publish_sets_throttle_key():
    app, exchange = _app()
    redis_client = _redis()

    with (
        patch.object(redisConnection, "client", redis_client),
        patch("messaging.publisher.check_valid_url_in_redis", return_value="https://ok"),
    ):
        response = asyncio.run(
            publish_message(app, "226Q1A4304", freshness=True)
        )

    assert response.status_code == 202
    exchange.publish.assert_awaited_once()
    redis_client.set.assert_any_call(
        "226Q1A4304Freshness", "1", ex=FRESHNESS_SCRAPE_EXPIRY
    )


def _import_result_services():
    import pytest

    try:
        import prisma.types  # noqa: F401
    except ImportError:
        pytest.skip("Prisma client is not generated in this environment")
    from service.getResultsService import fetch_results
    from service.hardrefresh import fetch_results_using_hard_refresh

    return fetch_results, fetch_results_using_hard_refresh


def test_fetch_results_skips_freshness_when_throttled():
    fetch_results, _ = _import_result_services()
    redis_client = _redis(get=MagicMock(side_effect=[None, "1"]))
    publish = AsyncMock()
    student = SimpleNamespace()
    marks = []

    with (
        patch.object(redisConnection, "client", redis_client),
        patch("service.getResultsService.check_valid_url_in_redis", return_value="."),
        patch(
            "service.getResultsService.get_details",
            new=AsyncMock(return_value=[student, marks]),
        ),
        patch(
            "service.getResultsService.studentDetailsModel",
            return_value={"rollNo": "226Q1A4304"},
        ),
        patch(
            "service.getResultsService.studentResultsModel",
            return_value={"credits": 1},
        ),
        patch("service.getResultsService.isbpharmacyr22", return_value=False),
        patch("service.getResultsService.publish_message", new=publish),
    ):
        response = asyncio.run(fetch_results(SimpleNamespace(), "226Q1A4304"))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    publish.assert_not_awaited()


def test_fetch_results_queues_freshness_on_db_hit():
    fetch_results, _ = _import_result_services()
    redis_client = _redis()
    publish = AsyncMock()
    student = SimpleNamespace()
    marks = []
    app = SimpleNamespace()

    with (
        patch.object(redisConnection, "client", redis_client),
        patch("service.getResultsService.check_valid_url_in_redis", return_value="."),
        patch(
            "service.getResultsService.get_details",
            new=AsyncMock(return_value=[student, marks]),
        ),
        patch(
            "service.getResultsService.studentDetailsModel",
            return_value={"rollNo": "226Q1A4304"},
        ),
        patch(
            "service.getResultsService.studentResultsModel",
            return_value={"credits": 1},
        ),
        patch("service.getResultsService.isbpharmacyr22", return_value=False),
        patch("service.getResultsService.publish_message", new=publish),
    ):
        response = asyncio.run(fetch_results(app, "226Q1A4304"))

    assert response.status_code == 200
    publish.assert_awaited_once_with(app, "226Q1A4304", freshness=True)


def test_hard_refresh_clears_queue_guards():
    _, fetch_results_using_hard_refresh = _import_result_services()
    redis_client = _redis()
    publish = AsyncMock(return_value=JSONResponse(status_code=202, content={}))

    with (
        patch("service.hardrefresh.invalidate_all_cache") as invalidate,
        patch.object(redisConnection, "client", redis_client),
        patch("service.hardrefresh.publish_message", new=publish),
    ):
        response = asyncio.run(
            fetch_results_using_hard_refresh(SimpleNamespace(), "226Q1A4304")
        )

    invalidate.assert_called_once_with("226Q1A4304")
    redis_client.delete.assert_called_once_with(
        "226Q1A4304Freshness", "queued:226Q1A4304"
    )
    redis_client.srem.assert_called_once_with(RABBITMQ_ROLL_NUMBERS, "226Q1A4304")
    publish.assert_awaited_once()
    assert response.status_code == 202
