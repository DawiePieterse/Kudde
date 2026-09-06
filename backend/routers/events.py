from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import Animal, AnimalStatus, Event, EventKind

router = APIRouter(prefix="/api", tags=["events"])

# What Animal.status becomes when an event of this kind is recorded, if
# anything - a death or sale event is also the moment the animal leaves the
# herd, and a farmer capturing one in the field shouldn't have to also
# remember to go flip a separate status field.
_STATUS_ON_EVENT = {
    EventKind.death: AnimalStatus.dead,
    EventKind.sale: AnimalStatus.sold,
}


class EventCreate(SQLModel):
    """What the client sends: a tag, not an animal_id - the field app knows
    the ear tag it just read, not this database's internal id."""
    tag: str
    kind: EventKind
    event_date: date
    value: Optional[float] = None
    note: str = ""
    location: str = ""


def _animal_by_tag(session: Session, tag: str) -> Animal:
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag!r}")
    return animal


@router.post("/events")
def create_event(payload: EventCreate, session: Session = Depends(get_session)):
    animal = _animal_by_tag(session, payload.tag)
    event = Event(animal_id=animal.id, kind=payload.kind, event_date=payload.event_date,
                   value=payload.value, note=payload.note, location=payload.location)
    session.add(event)

    new_status = _STATUS_ON_EVENT.get(payload.kind)
    if new_status is not None and animal.status != new_status:
        animal.status = new_status
        session.add(animal)

    session.commit()
    session.refresh(event)
    return event


@router.get("/animals/{tag}/events")
def list_animal_events(tag: str, session: Session = Depends(get_session)):
    animal = _animal_by_tag(session, tag)
    return session.exec(
        select(Event).where(Event.animal_id == animal.id).order_by(Event.event_date.desc())
    ).all()


@router.get("/events")
def list_events(kind: Optional[EventKind] = None, limit: int = 50,
                 session: Session = Depends(get_session)):
    """Recent events across the whole herd, newest first - what the admin
    dashboard's activity feed shows."""
    statement = select(Event)
    if kind is not None:
        statement = statement.where(Event.kind == kind)
    statement = statement.order_by(Event.event_date.desc(), Event.id.desc()).limit(limit)
    return session.exec(statement).all()
