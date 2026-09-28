import asyncio
from collections.abc import Iterator

import aio_pika

from config.connection import prismaConnection
from config.redisConnection import redisConnection
from config.settings import (
    CLASS_RESULTS_PROCESSED_EXPIRY_TIME,
    CLASS_RESULTS_QUEUE_NAME,
    HARD_REFRESH_KEY_SUFFIX,
    NOTIFICATIONS_REDIS_KEY,
    QUEUE_DEDUP_KEY_PREFIX,
    QUEUE_NAME,
    RABBITMQ_URL,
    RABBITMQ_ROLL_NUMBERS,
    SCRAPE_FAIL_COOLDOWN_EXPIRY,
    SCRAPE_FAIL_COOLDOWN_PREFIX,
)
from database.operations import get_exam_codes_from_database, save_to_database
from scrapers.resultNotificationScraper import refresh_notifications
from scrapers.resultScraper import ResultScraper
from scrapers.jntukClient import jntuk_circuit_ttl
from scrapers.serverChecker import check_url
from subscriptions.send_notification import send_push_notification_to_particular_user
from subscriptions.mobile_notification import notify_student_result_updated
from utils.logger import rabbitmq_logger, logger, scraping_logger
from utils.caching import invalidate_all_cache


_scrape_lock = asyncio.Lock()


def get_class_prefixes(roll_number: str) -> tuple[str, str]:
    """Return the requested and paired class prefixes."""
    class_prefix = roll_number[:8]
    admission_type = class_prefix[4]

    if admission_type == "1":
        paired_year = str(int(class_prefix[:2]) + 1).zfill(2)
        paired_admission_type = "5"
    elif admission_type == "5":
        paired_year = str(int(class_prefix[:2]) - 1).zfill(2)
        paired_admission_type = "1"
    else:
        raise ValueError(
            f"Unsupported admission type in class roll number: {roll_number}"
        )

    paired_prefix = (
        paired_year
        + class_prefix[2:4]
        + paired_admission_type
        + class_prefix[5:8]
    )
    return class_prefix, paired_prefix


def iter_class_roll_numbers(roll_number: str) -> Iterator[str]:
    """Yield every roll number in the requested and paired class cohorts."""
    class_prefixes = get_class_prefixes(roll_number)

    for prefix in class_prefixes:
        for number in range(1, 100):
            yield f"{prefix}{number:02d}"
        for letter_code in range(ord("A"), ord("Z") + 1):
            letter = chr(letter_code)
            for number in range(10):
                yield f"{prefix}{letter}{number}"


def _set_scrape_cooldown(roll_number: str) -> None:
    if not redisConnection.client or roll_number == NOTIFICATIONS_REDIS_KEY:
        return
    redisConnection.client.set(
        f"{SCRAPE_FAIL_COOLDOWN_PREFIX}{roll_number}",
        "1",
        ex=SCRAPE_FAIL_COOLDOWN_EXPIRY,
    )


async def _pending_student_jobs(connection) -> int:
    if connection is None:
        return 0
    async with connection.channel() as channel:
        queue = await channel.declare_queue(QUEUE_NAME, durable=True)
        return int(queue.declaration_result.message_count or 0)


async def _wait_for_student_queue(connection) -> None:
    while True:
        pending = await _pending_student_jobs(connection)
        if pending <= 0:
            return
        rabbitmq_logger.info(
            f"Pausing class scrape; {pending} student jobs waiting"
        )
        await asyncio.sleep(5)


async def process_class_results_message(message_body: str, connection=None) -> None:
    """Scrape a class unless either paired cohort was processed in the last day."""
    rabbitmq_logger.info(f"Processing class results message: {message_body}")
    class_prefixes = get_class_prefixes(message_body)
    processed_keys = [
        f"class_results_processed:{prefix}" for prefix in class_prefixes
    ]

    if redisConnection.client and any(
        redisConnection.client.exists(key) for key in processed_keys
    ):
        rabbitmq_logger.info(
            f"Skipping class results for {message_body[:8]}; "
            "already processed within 24 hours"
        )
        return

    consecutive_empty_results = 0
    for student_roll_number in iter_class_roll_numbers(message_body):
        if jntuk_circuit_ttl() > 0:
            rabbitmq_logger.warning(
                f"Stopping class results for {message_body[:8]}; "
                "JNTUK is rate-limiting"
            )
            return
        await _wait_for_student_queue(connection)
        has_results = await process_message(student_roll_number)
        if has_results:
            consecutive_empty_results = 0
            continue

        consecutive_empty_results += 1
        if consecutive_empty_results >= 20:
            rabbitmq_logger.info(
                f"Stopping class results for {message_body[:8]} after "
                "20 consecutive roll numbers returned no results"
            )
            break

    if redisConnection.client:
        for key in processed_keys:
            redisConnection.client.set(
                key, "1", ex=CLASS_RESULTS_PROCESSED_EXPIRY_TIME
            )


# Define a function to process messages
async def process_message(message_body: str) -> bool:
    async with _scrape_lock:
        return await _process_message(message_body)


async def _process_message(message_body: str) -> bool:
    try:
        """
        Process the consumed message.
        Replace this logic with your custom processing code.
        """
        rabbitmq_logger.info(f"Processing message: {message_body}")

        url = check_url()
        if not url:
            rabbitmq_logger.warning("No url found, skipping processing...")
            return False

        force_refresh = False
        if redisConnection.client:
            hard_refresh_key = f"{message_body}{HARD_REFRESH_KEY_SUFFIX}"
            force_refresh = bool(redisConnection.client.get(hard_refresh_key))
            if force_refresh:
                redisConnection.client.delete(hard_refresh_key)

        exam_codes = (
            set()
            if force_refresh
            else await get_exam_codes_from_database(message_body)
        )
        exam_codes_rcrv = (
            set()
            if force_refresh
            else await get_exam_codes_from_database(message_body, True)
        )

        # intializeing the scraper
        scraper = ResultScraper(message_body, exam_codes, exam_codes_rcrv, url)

        # running the scraper
        rabbitmq_logger.info(f"Started scraper for {message_body}")
        results = await scraper.run()

        # log if it fails to get the results

        if results is None:
            logger.warning(f"Failed to get results: {message_body}")
            _set_scrape_cooldown(message_body)
            return False

        logger.info(f"Results was successfully extracted: {message_body}")

        # Database save
        rabbitmq_logger.info(f"Saving results to database for {message_body}")
        inserted_count = await save_to_database(results)
        invalidate_all_cache(message_body)
        if inserted_count > 0:
            await send_push_notification_to_particular_user(message_body)
            try:
                await notify_student_result_updated(message_body)
            except Exception as error:
                logger.error(
                    f"Mobile student result notification failed for {message_body}: {error}"
                )
        return True

    except Exception as e:
        scraping_logger.error(f"Error while scarping results: {e}")
        _set_scrape_cooldown(message_body)
        return False

    """Consume messages from RabbitMQ and pass them to the processing function."""


async def _consume_default_queue(queue) -> None:
    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            try:
                async with message.process():
                    body = message.body.decode()

                    if body == NOTIFICATIONS_REDIS_KEY:
                        await refresh_notifications()
                    else:
                        await process_message(body)

                    if redisConnection.client:
                        redisConnection.client.srem(RABBITMQ_ROLL_NUMBERS, body)
                        redisConnection.client.delete(
                            f"{QUEUE_DEDUP_KEY_PREFIX}{body}"
                        )
                        rabbitmq_logger.info(
                            f"Removed roll number {body} from Redis."
                        )
                    else:
                        rabbitmq_logger.warning("Redis is not found")

            except Exception as error:
                rabbitmq_logger.error(
                    f"Error processing message: {error},{message.body}"
                )
                if not message.processed:
                    await message.reject(requeue=False)


async def _consume_class_results_queue(queue, connection) -> None:
    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            try:
                async with message.process():
                    await process_class_results_message(
                        message.body.decode(), connection
                    )
            except Exception as error:
                rabbitmq_logger.error(
                    f"Error processing class results message: {error},{message.body}"
                )
                if not message.processed:
                    await message.reject(requeue=False)


async def consume_messages():
    try:
        # connection = app.state.rabbitmq_connection
        logger.info("Starting rabbitmq connection for consumer")

        connection = await aio_pika.connect_robust(RABBITMQ_URL)

        logger.info("Starting database connection for consumer")
        await prismaConnection.connect()

        logger.info("Starting redis connection for consumer")
        redisConnection.connect()

        async with connection:
            channel = await connection.channel()
            class_results_channel = await connection.channel()

            await channel.set_qos(prefetch_count=1)
            # Only one class batch may run at a time. Each batch also awaits
            # every individual roll number before starting the next one.
            await class_results_channel.set_qos(prefetch_count=1)

            queue = await channel.declare_queue(QUEUE_NAME, durable=True)
            class_results_queue = await class_results_channel.declare_queue(
                CLASS_RESULTS_QUEUE_NAME,
                durable=True,
            )
            rabbitmq_logger.info(f"Waiting for messages in queue: {QUEUE_NAME}")
            rabbitmq_logger.info(
                f"Waiting for messages in queue: {CLASS_RESULTS_QUEUE_NAME}"
            )

            await asyncio.gather(
                _consume_default_queue(queue),
                _consume_class_results_queue(class_results_queue, connection),
            )

    except asyncio.CancelledError:
        rabbitmq_logger.info("Message consumption was cancelled.")
    except Exception as e:
        rabbitmq_logger.error(f"An error occurred: {e}")
    finally:
        rabbitmq_logger.info("Shutting down gracefully...")
