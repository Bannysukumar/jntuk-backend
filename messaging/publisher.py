import aio_pika
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from config.redisConnection import redisConnection
from config.settings import (
    CLASS_RESULTS_QUEUE_MAX_MESSAGES,
    CLASS_RESULTS_QUEUE_NAME,
    FRESHNESS_KEY_SUFFIX,
    FRESHNESS_QUEUE_MAX_MESSAGES,
    FRESHNESS_SCRAPE_EXPIRY,
    NOTIFICATIONS_REDIS_KEY,
    QUEUE_DEDUP_EXPIRY,
    QUEUE_DEDUP_KEY_PREFIX,
    QUEUE_NAME,
    RABBITMQ_MAX_MESSAGES,
    RABBITMQ_ROLL_NUMBERS,
)
from scrapers.serverChecker import check_valid_url_in_redis
from utils.logger import rabbitmq_logger


async def publish_class_results_message(app: FastAPI, roll_number: str) -> bool:
    """Publish a class-results request to its dedicated RabbitMQ queue."""
    async with app.state.rabbitmq_connection.channel() as channel:
        queue = await channel.declare_queue(CLASS_RESULTS_QUEUE_NAME, durable=True)
        message_count = queue.declaration_result.message_count
        if message_count >= CLASS_RESULTS_QUEUE_MAX_MESSAGES:
            rabbitmq_logger.warning(
                f"Skipping {roll_number}; queue {CLASS_RESULTS_QUEUE_NAME} "
                f"already has {message_count} messages"
            )
            return False

        await channel.default_exchange.publish(
            aio_pika.Message(body=roll_number.encode()),
            routing_key=CLASS_RESULTS_QUEUE_NAME,
        )
    rabbitmq_logger.info(
        f"Published {roll_number} to queue: {CLASS_RESULTS_QUEUE_NAME}"
    )
    return True


def _queued_accepted():
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "success",
            "message": "Your roll number has been queued.",
        },
    )


def _release_queue_claim(rollNo: str) -> None:
    if not redisConnection.client or rollNo == NOTIFICATIONS_REDIS_KEY:
        return
    redisConnection.client.delete(f"{QUEUE_DEDUP_KEY_PREFIX}{rollNo}")
    redisConnection.client.srem(RABBITMQ_ROLL_NUMBERS, rollNo)


async def publish_message(
    app: FastAPI,
    rollNo: str,
    *,
    freshness: bool = False,
):
    """Publishes a message (roll number) to the RabbitMQ queue."""

    try:
        if redisConnection.client:
            url = check_valid_url_in_redis()

            if url == ".":
                return JSONResponse(
                    status_code=status.HTTP_424_FAILED_DEPENDENCY,
                    content={
                        "status": "failure",
                        "message": "JNTUK SERVERS ARE DOWN!!",
                    },
                )

        async with app.state.rabbitmq_connection.channel() as channel:
            queue = await channel.declare_queue(QUEUE_NAME, durable=True)
            message_count = queue.declaration_result.message_count

            if freshness and message_count >= FRESHNESS_QUEUE_MAX_MESSAGES:
                rabbitmq_logger.info(
                    f"Skipping freshness scrape for {rollNo}; "
                    f"queue already has {message_count} messages"
                )
                return _queued_accepted()

            if message_count > RABBITMQ_MAX_MESSAGES:
                rabbitmq_logger.warning("Server had execced the threshold level")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "status": "failure",
                        "message": "Server cannot handle the requests currently, please try again later",
                    },
                )

            if redisConnection.client and rollNo != NOTIFICATIONS_REDIS_KEY:
                queued_key = f"{QUEUE_DEDUP_KEY_PREFIX}{rollNo}"
                claimed = redisConnection.client.set(
                    queued_key, "1", nx=True, ex=QUEUE_DEDUP_EXPIRY
                )
                if not claimed:
                    rabbitmq_logger.info(
                        f"Skipping duplicate queue publish for {rollNo}"
                    )
                    return _queued_accepted()
                redisConnection.client.sadd(RABBITMQ_ROLL_NUMBERS, rollNo)

            await channel.default_exchange.publish(
                aio_pika.Message(body=rollNo.encode()),
                routing_key=QUEUE_NAME,
            )

        if (
            freshness
            and redisConnection.client
            and rollNo != NOTIFICATIONS_REDIS_KEY
        ):
            redisConnection.client.set(
                f"{rollNo}{FRESHNESS_KEY_SUFFIX}",
                "1",
                ex=FRESHNESS_SCRAPE_EXPIRY,
            )

        if rollNo == NOTIFICATIONS_REDIS_KEY:
            return {"status": "success", "message": "Notifications are been fetched"}

        return _queued_accepted()

    except Exception as e:
        _release_queue_claim(rollNo)
        rabbitmq_logger.error(f"Unknown Exception while publishing: {e}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "failure",
                "message": "Unknown Exception has occurred!!",
            },
        )
