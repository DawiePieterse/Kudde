"""The day's weather at the farm, stamped onto an event.

One place on purpose: a second way of recording an event is exactly how
weather quietly stops being carried, which is what the camp move did
until it was pointed at this. Lives outside routers/events.py because
routers/animals.py now records an event too (the camp a new animal is
entered in) and must stamp it the same way.
"""
import time
from datetime import date
from typing import Dict, Optional, Tuple

from sqlmodel import Session

from models import Event, Farm
from routers.settings import SINGLETON_ID as FARM_ID
from weather import DailyWeather, fetch_daily_weather

# The last answers, by (lat, lng, date): {key: (expires_at, weather)}.
#
# A weigh session is forty head through a crush on one day, and a calving
# records two or three events on the same date - every one of them used to
# go out to Open-Meteo for the same answer, each inside the request the
# phone is waiting on. Worse, with no internet at the farm each one waited
# out the whole timeout. So a day's answer is kept for a while, and a
# failure is kept briefly too, so a dead connection costs one slow save a
# minute rather than one per animal. Today's figures are a forecast until
# the day is over, which is why a hit is not kept for the whole day.
# Process memory only: a restart simply asks again.
_cache: Dict[Tuple[float, float, date], Tuple[float, Optional[DailyWeather]]] = {}
KEEP_HIT_S = 10 * 60
KEEP_MISS_S = 60


def weather_for(session: Session, on: date) -> Optional[DailyWeather]:
    """The day's weather at the farm's position, or None when no position is
    set or the lookup failed."""
    farm = session.get(Farm, FARM_ID)
    if farm is None or farm.gps_lat is None or farm.gps_lng is None:
        return None
    key = (farm.gps_lat, farm.gps_lng, on)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    weather = fetch_daily_weather(farm.gps_lat, farm.gps_lng, on)
    _cache[key] = (now + (KEEP_HIT_S if weather is not None else KEEP_MISS_S), weather)
    return weather


def stamp_weather(event: Event, weather: Optional[DailyWeather]) -> None:
    if weather is None:
        return
    event.weather_temp_max = weather.temp_max
    event.weather_temp_min = weather.temp_min
    event.weather_precipitation = weather.precipitation
