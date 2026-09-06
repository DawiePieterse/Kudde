from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, SQLModel

from db import get_session
from models import Farm

router = APIRouter(prefix="/api/farm", tags=["farm"])

# There's only one farm per Kudde server, so the row is always this id
# rather than something callers look up.
_SINGLETON_ID = 1


class FarmUpdate(SQLModel):
    farm_name: str = ""
    farmer_name: str = ""
    phone_number: str = ""
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


def _get_or_create(session: Session) -> Farm:
    farm = session.get(Farm, _SINGLETON_ID)
    if farm is None:
        farm = Farm(id=_SINGLETON_ID)
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
