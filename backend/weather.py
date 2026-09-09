"""Fetches the day's weather for the farm's GPS position from Open-Meteo, so
every field event carries the conditions it happened in without the farmer
having to note them down by hand.

Open-Meteo needs no API key, but splits history across two endpoints: the
forecast API answers for roughly 92 days in the past through 16 days ahead
(today plus the usual field-recording lag), while the archive API covers
everything older than that, at the cost of a few days' delay before a given
day's data lands there.
"""
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_DAILY_FIELDS = "temperature_2m_max,temperature_2m_min,precipitation_sum"
_FORECAST_PAST_LIMIT = timedelta(days=92)
_FORECAST_FUTURE_LIMIT = timedelta(days=16)
_TIMEOUT = 10


@dataclass
class DailyWeather:
    temp_max: Optional[float]
    temp_min: Optional[float]
    precipitation: Optional[float]


def fetch_daily_weather(latitude: float, longitude: float, event_date: date) -> Optional[DailyWeather]:
    """Best-effort lookup - returns None rather than raising on any failure
    (no internet on the farm's server, Open-Meteo down, an out-of-range
    date), since a weather lookup must never stop a field event from being
    recorded."""
    offset = event_date - date.today()
    url = _FORECAST_URL if -_FORECAST_PAST_LIMIT <= offset <= _FORECAST_FUTURE_LIMIT else _ARCHIVE_URL
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": event_date.isoformat(),
        "end_date": event_date.isoformat(),
        "daily": _DAILY_FIELDS,
        "timezone": "auto",
    }
    try:
        response = httpx.get(url, params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        daily = response.json()["daily"]
        return DailyWeather(
            temp_max=daily["temperature_2m_max"][0],
            temp_min=daily["temperature_2m_min"][0],
            precipitation=daily["precipitation_sum"][0],
        )
    except Exception:
        logger.warning(
            "weather lookup failed for %s,%s on %s", latitude, longitude, event_date, exc_info=True
        )
        return None
