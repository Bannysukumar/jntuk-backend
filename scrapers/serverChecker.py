import requests
import urllib3

from config.redisConnection import redisConnection
from config.settings import (
    EXPIRY_TIME,
    FIVE_MINUTE_EXPIRY,
    JNTUK_RESULTS_API_BASE,
    REDIS_URL_KEY,
)
from utils.logger import scraping_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def check_url():
    url = check_valid_url_in_redis()
    if url is not None and url != ".":
        return url

    probe_url = JNTUK_RESULTS_API_BASE.rstrip("/")
    try:
        response = requests.post(
            f"{probe_url}/getresultsnotifications",
            json={},
            timeout=10,
            verify=False,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        payload = response.json() if response.content else {}
        if response.status_code == 200 and payload.get("status") == 200:
            scraping_logger.info(f"The JNTUK results API {probe_url} is working")
            if redisConnection.client:
                redisConnection.client.set(REDIS_URL_KEY, probe_url, ex=EXPIRY_TIME)
            return probe_url
        scraping_logger.warning(
            f"The JNTUK results API {probe_url} returned {response.status_code}"
        )
    except requests.exceptions.Timeout:
        scraping_logger.warning(f"The JNTUK results API {probe_url} timed out")
    except Exception:
        scraping_logger.warning(f"The JNTUK results API {probe_url} is not working")

    if redisConnection.client:
        redisConnection.client.set(REDIS_URL_KEY, ".", ex=FIVE_MINUTE_EXPIRY)
    return None


def check_valid_url_in_redis():
    if redisConnection.client:
        cached_url = redisConnection.client.get(REDIS_URL_KEY)
        if cached_url is not None:
            cached_url = (
                cached_url.decode("utf-8")
                if isinstance(cached_url, bytes)
                else cached_url
            )
            if cached_url:
                return str(cached_url)
    return None
