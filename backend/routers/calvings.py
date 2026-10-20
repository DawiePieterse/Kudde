"""A calving: the cow calved, and here is the calf - recorded as one thing.

Standing at a cow that has just calved, the natural action is one screen:
how it went, and the new animal. Before this it was two forms (a birth
event on the cow, then Add animal with the dam tag typed again) and the
calf ended up with no camp. One request records both, or neither: a calf
whose tag is already taken leaves the cow without a birth event too, so
nothing is half done when the phone replays it.

The Birth event kind keeps its meaning from the field app's comments: on
a cow it is a calving - the camp it happened in, and in the note what
matters afterwards (calving ease, pulled or not, up and sucking). The calf
gets a birth event of its own, dated and placed the same, which is what
makes it appear in that camp from day one.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator
from sqlmodel import Session, SQLModel, select

from campnames import ensure_camp
from db import get_session
from farmweather import stamp_weather, weather_for
from models import Animal, AnimalSex, Event, EventKind
from routers.animals import AnimalCreate
from tags import normalise_tag

router = APIRouter(prefix="/api/calvings", tags=["calvings"])


class CalfCreate(SQLModel):
    tag: str
    sex: AnimalSex
    name: str = ""
    breed: str = ""
    sire_tag: Optional[str] = None

    # Checked here, at request validation, so a blank calf tag is a 422
    # like any other bad field and not an exception inside the handler.
    @field_validator("tag")
    @classmethod
    def _tag_is_a_tag(cls, value: str) -> str:
        tag = normalise_tag(value)
        if tag is None:
            raise ValueError("the calf needs a tag number")
        return tag


class CalvingCreate(SQLModel):
    dam_tag: str
    event_date: date
    calf: CalfCreate
    camp: str = ""
    note: str = ""
    # Same contract as EventCreate.client_uuid: the phone's id for this
    # capture, so a replay records once.
    client_uuid: Optional[str] = None


def _calf_uuid(client_uuid: str) -> str:
    """The calf's birth event is the same capture as the cow's; one id per
    row, derived the way a camp move derives its members' ids."""
    return f"{client_uuid}:calf"


@router.post("")
def create_calving(payload: CalvingCreate, session: Session = Depends(get_session)):
    if payload.client_uuid:
        already = session.exec(
            select(Event).where(Event.client_uuid == payload.client_uuid)).first()
        if already is not None:
            calf = session.exec(select(Animal).where(Animal.tag == normalise_tag(payload.calf.tag))).first()
            return {"dam_event": already, "calf": calf, "replayed": True}

    dam = session.exec(select(Animal).where(Animal.tag == normalise_tag(payload.dam_tag))).first()
    if dam is None:
        raise HTTPException(404, f"No animal with tag {payload.dam_tag}")
    if dam.sex is not AnimalSex.female:
        raise HTTPException(422, f"{dam.tag} is a male and cannot calve")

    # Validated through AnimalCreate so the calf's tag follows every rule a
    # new animal's does (trimmed, not blank), and refused before anything is
    # written so the cow is not left with a birth event for a calf that
    # does not exist.
    calf_fields = AnimalCreate(tag=payload.calf.tag, sex=payload.calf.sex, name=payload.calf.name,
                               breed=payload.calf.breed, sire_tag=payload.calf.sire_tag,
                               dam_tag=dam.tag, birth_date=payload.event_date)
    if session.exec(select(Animal).where(Animal.tag == calf_fields.tag)).first() is not None:
        raise HTTPException(409, f"Tag {calf_fields.tag} is already in use")

    camp = (payload.camp or "").strip()
    location = ensure_camp(session, camp).name if camp else ""
    weather = weather_for(session, payload.event_date)

    note = f"Calf {calf_fields.tag}" + (f" - {payload.note.strip()}" if payload.note.strip() else "")
    dam_event = Event(animal_id=dam.id, kind=EventKind.birth, event_date=payload.event_date,
                      location=location, note=note, client_uuid=payload.client_uuid)
    stamp_weather(dam_event, weather)
    session.add(dam_event)

    calf = Animal(**calf_fields.model_dump(exclude={"camp"}))
    session.add(calf)
    session.flush()
    calf_event = Event(animal_id=calf.id, kind=EventKind.birth, event_date=payload.event_date,
                       location=location, note=f"Born to {dam.tag}",
                       client_uuid=_calf_uuid(payload.client_uuid) if payload.client_uuid else None)
    stamp_weather(calf_event, weather)
    session.add(calf_event)

    dam.updated_at = datetime.utcnow()
    session.add(dam)
    session.commit()
    session.refresh(dam_event)
    session.refresh(calf)
    return {"dam_event": dam_event, "calf": calf, "replayed": False}
