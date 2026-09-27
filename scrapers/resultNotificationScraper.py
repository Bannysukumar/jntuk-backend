import asyncio
import json

from config.redisConnection import redisConnection
from config.settings import (
    JNTUK_RESULTS_PORTAL,
    NOTIFICATIONS_EXPIRY_TIME,
    NOTIFICATIONS_REDIS_KEY,
)
from database.operations import save_exam_codes
from scrapers.jntukClient import fetch_notifications
from subscriptions.mobile_notification import broadcast_result_notifications
from utils.helpers import send_telegram_notification
from utils.jntuk import (
    is_rcrv_title,
    map_year_semester,
    normalize_course,
    result_page_url,
)
from utils.logger import logger


def categorize_degree(course: str) -> str:
    normalized = normalize_course(course)
    if "BTECH" in normalized:
        return "btech"
    if "BPHARM" in normalized or "BPHARMCY" in normalized or "BPHARMAY" in normalized:
        return "bpharmacy"
    if "MTECH" in normalized:
        return "mtech"
    if "MPHARM" in normalized:
        return "mpharmacy"
    if "MBA" in normalized:
        return "mba"
    if "MCA" in normalized:
        return "mca"
    if "PHARMD" in normalized or "PHARMAD" in normalized:
        return "pharmd"
    if "BARCH" in normalized:
        return "barch"
    if "PHD" in normalized:
        return "phd"
    return normalized.lower() or "unknown"


def map_notification(item: dict) -> dict:
    title = str(item.get("exam_details") or item.get("title") or "").strip()
    result_id = str(item.get("uuid") or item.get("examCode") or "").strip()
    publish_date = str(item.get("publish_date") or item.get("date") or "")
    return {
        "title": title,
        "link": result_page_url(result_id, JNTUK_RESULTS_PORTAL) if result_id else "",
        "date": publish_date,
        "releaseDate": publish_date,
        "degree": categorize_degree(str(item.get("course") or "")),
        "regulation": str(item.get("regulations") or "") or None,
        "semesterCode": map_year_semester(item.get("year_semistore")),
        "examCode": result_id or None,
        "rcrv": is_rcrv_title(title),
    }


def get_exam_codes(results):
    """Keep the previous hook name so notification tests can patch mapping."""
    return [map_notification(item) for item in results]


async def refresh_notifications():
    """Fetch JNTUK published-result notifications and persist new exam rows."""
    try:
        notifications = await fetch_notifications(use_cache=False)
        if not notifications:
            return None

        results = get_exam_codes(notifications)
        persistable = [
            item
            for item in results
            if item.get("examCode") and item.get("title") and item.get("date")
        ]

        if redisConnection.client:
            redisConnection.client.set(
                NOTIFICATIONS_REDIS_KEY,
                json.dumps(persistable),
                ex=NOTIFICATIONS_EXPIRY_TIME,
            )

        new_exams = await save_exam_codes(persistable)
        if new_exams:
            send_telegram_notification(new_exams)
            await broadcast_result_notifications(new_exams)
        return persistable
    except Exception as error:
        logger.info(f"Error while fetching notifications:{error}")
        return None


async def refresh_notifications_periodically(interval_seconds=60):
    while True:
        await refresh_notifications()
        await asyncio.sleep(interval_seconds)
