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
from herd import animal_by_tag
from models import Animal, AnimalSex, Event, EventKind
from routers.animals import AnimalCreate, new_animal
from routers.events import not_in_the_future
from tags import normalise_tag

router = APIRouter(prefix="/api/calvings", tags=["calvings"])


class CalfCreate(AnimalCreate):
    """The calf as the form sends it: a new animal, minus what the calving
    itself supplies. Its dam, birth date and camp are set from the calving
    below whatever was sent, so they cannot disagree with the cow."""


class CalvingCreate(SQLModel):
    dam_tag: str
    event_date: date
    calf: CalfCreate
    camp: str = ""
    note: str = ""
    # Same contract as EventCreate.client_uuid: the phone's id for this
    # capture, so a replay records once.
    client_uuid: Optional[str] = None

    @field_validator("event_date")
    @classmethod
    def _dated_in_the_past(cls, value: date) -> date:
        return not_in_the_future(value)


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

    dam = animal_by_tag(session, payload.dam_tag)
    if dam.sex is not AnimalSex.female:
        raise HTTPException(422, f"{dam.tag} is a male and cannot calve")

    # The calf first, through the same path as any new animal: a taken tag
    # is refused before anything is added, so the cow is not left with a
    # birth event for a calf that does not exist. Its birth event is placed
    # in the camp the calving names.
    camp = (payload.camp or "").strip()
    calf_fields = payload.calf.model_copy(update={
        "dam_tag": dam.tag, "birth_date": payload.event_date, "camp": camp or None})
    calf = new_animal(session, calf_fields, placed_kind=EventKind.birth, on=payload.event_date,
                      note=f"Born to {dam.tag}",
                      client_uuid=_calf_uuid(payload.client_uuid) if payload.client_uuid else None)
    if not camp:
        # No camp named: the calf still gets its birth event, just unplaced.
        session.flush()
        unplaced = Event(animal_id=calf.id, kind=EventKind.birth, event_date=payload.event_date,
                         note=f"Born to {dam.tag}",
                         client_uuid=_calf_uuid(payload.client_uuid) if payload.client_uuid else None)
        stamp_weather(unplaced, weather_for(session, payload.event_date))
        session.add(unplaced)

    location = ensure_camp(session, camp).name if camp else ""
    note = f"Calf {calf_fields.tag}" + (f" - {payload.note.strip()}" if payload.note.strip() else "")
    dam_event = Event(animal_id=dam.id, kind=EventKind.birth, event_date=payload.event_date,
                      location=location, note=note, client_uuid=payload.client_uuid)
    stamp_weather(dam_event, weather_for(session, payload.event_date))
    session.add(dam_event)

    dam.updated_at = datetime.utcnow()
    session.add(dam)
    session.commit()
    session.refresh(dam_event)
    session.refresh(calf)
    return {"dam_event": dam_event, "calf": calf, "replayed": False}
