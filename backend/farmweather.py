"""The day's weather at the farm, stamped onto an event.

One place on purpose: a second way of recording an event is exactly how
weather quietly stops being carried, which is what the camp move did
until it was pointed at this. Lives outside routers/events.py because
routers/animals.py now records an event too (the camp a new animal is
entered in) and must stamp it the same way.
"""
from datetime import date
from typing import Optional

from sqlmodel import Session

from models import Event, Farm
from routers.settings import SINGLETON_ID as FARM_ID
from weather import DailyWeather, fetch_daily_weather


def weather_for(session: Session, on: date) -> Optional[DailyWeather]:
    """The day's weather at the farm's position, or None when no position is
    set or the lookup failed."""
    farm = session.get(Farm, FARM_ID)
    if farm is None or farm.gps_lat is None or farm.gps_lng is None:
        return None
    return fetch_daily_weather(farm.gps_lat, farm.gps_lng, on)


def stamp_weather(event: Event, weather: Optional[DailyWeather]) -> None:
    if weather is None:
        return
    event.weather_temp_max = weather.temp_max
    event.weather_temp_min = weather.temp_min
    event.weather_precipitation = weather.precipitation
