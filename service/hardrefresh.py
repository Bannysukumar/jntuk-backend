from fastapi import FastAPI

from config.redisConnection import redisConnection
from config.settings import (
    EXPIRY_TIME,
    FRESHNESS_KEY_SUFFIX,
    HARD_REFRESH_KEY_SUFFIX,
    QUEUE_DEDUP_KEY_PREFIX,
    RABBITMQ_ROLL_NUMBERS,
)
from messaging.publisher import publish_message
from utils.caching import invalidate_all_cache


async def fetch_results_using_hard_refresh(app: FastAPI, roll_number: str):
    invalidate_all_cache(roll_number)
    if redisConnection.client:
        redisConnection.client.delete(
            f"{roll_number}{FRESHNESS_KEY_SUFFIX}",
            f"{QUEUE_DEDUP_KEY_PREFIX}{roll_number}",
        )
        redisConnection.client.srem(RABBITMQ_ROLL_NUMBERS, roll_number)
        redisConnection.client.set(
            f"{roll_number}{HARD_REFRESH_KEY_SUFFIX}",
            "1",
            ex=EXPIRY_TIME,
        )
    return await publish_message(app, roll_number)
