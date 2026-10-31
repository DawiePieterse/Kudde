from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlmodel import Session, SQLModel

from db import get_session
from models import Farm

router = APIRouter(prefix="/api/farm", tags=["farm"])

# There's only one farm per Kudde server, so the row is always this id
# rather than something callers look up. Public because events.py reads the
# same row to stamp each event's weather - two modules agreeing on "1" by
# coincidence is exactly how they would later stop agreeing.
SINGLETON_ID = 1


class FarmUpdate(SQLModel):
    farm_name: str = ""
    farmer_name: str = ""
    phone_number: str = ""
    # Bounded, because a coordinate outside the globe is not refused by
    # anything downstream: Open-Meteo answers it with an error that
    # weather.py swallows, so a latitude typed as 338.9 saved fine and every
    # event from then on simply had no weather, with nothing on any screen
    # to say why. The Admin boxes carry the same min/max, but parseFloat
    # does not read them.
    gps_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    gps_lng: Optional[float] = Field(default=None, ge=-180, le=180)


def _get_or_create(session: Session) -> Farm:
    farm = session.get(Farm, SINGLETON_ID)
    if farm is None:
        farm = Farm(id=SINGLETON_ID)
        session.add(farm)
        session.commit()
        session.refresh(farm)
    return farm


@router.get("")
def get_farm(session: Session = Depends(get_session)):
    return _get_or_create(session)


@router.put("")
def update_farm(payload: FarmUpdate, session: Session = Depends(get_session)):
    farm = _get_or_create(session)
    for key, value in payload.model_dump().items():
        setattr(farm, key, value)
    farm.updated_at = datetime.utcnow()
    session.add(farm)
    session.commit()
    session.refresh(farm)
    return farm
