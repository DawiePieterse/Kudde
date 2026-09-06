from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import model_validator
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import Animal, AnimalStatus, Event, EventKind, Farm
from routers.animals import normalise_tag
from routers.settings import SINGLETON_ID as FARM_ID
from weather import fetch_daily_weather

router = APIRouter(prefix="/api", tags=["events"])

# Locations a camp move needs to reason about: only movement and birth
# events actually place an animal somewhere the herd grazes (a weight or
# treatment event's location, if ever set, is incidental to where it
# happened, not a place the animal is considered to live).
_LOCATION_EVENT_KINDS = [EventKind.movement, EventKind.birth]


def _current_locations(session: Session) -> dict:
    """Latest known camp/location per animal_id, from movement/birth events
    that actually recorded one. Powers both the /locations picker and any
    "where is this animal now" question - there's no stored current-location
    field, it's always derived from event history."""
    rows = session.exec(
        select(Event.animal_id, Event.location)
        .where(Event.kind.in_(_LOCATION_EVENT_KINDS))
        .where(Event.location != "")
        .order_by(Event.animal_id, Event.event_date.desc(), Event.id.desc())
    ).all()
    latest: dict = {}
    for animal_id, location in rows:
        latest.setdefault(animal_id, location)  # first row per animal_id is the latest, by the order_by above
    return latest


# What Animal.status becomes when an event of this kind is recorded, if
# anything - a death or sale event is also the moment the animal leaves the
# herd, and a farmer capturing one in the field shouldn't have to also
# remember to go flip a separate status field.
_STATUS_ON_EVENT = {
    EventKind.death: AnimalStatus.dead,
    EventKind.sale: AnimalStatus.sold,
}

# The most recent events any one request will return. A farmer's activity
# feed shows ten; nothing in either app asks for more than a screenful, so
# an unbounded limit only ever serves a mistake - and SQLite reads a
# NEGATIVE limit as "no limit at all", so `?limit=-1` used to return the
# whole event history of the herd.
_MAX_EVENT_LIMIT = 500


class EventCreate(SQLModel):
    """What the client sends: a tag, not an animal_id - the field app knows
    the ear tag it just read, not this database's internal id."""
    tag: str
    kind: EventKind
    event_date: date
    value: Optional[float] = None
    note: str = ""
    location: str = ""
    # The id the capturing device gave this event. Optional: the admin app
    # has no outbox and sends none. See create_event().
    client_uuid: Optional[str] = None

    @model_validator(mode="after")
    def _weight_events_carry_a_weight(self) -> "EventCreate":
        """A weight event with no weight in it is worse than no event at all.

        The dashboard's "needs weighing" list is driven by the date of the
        most recent weight event, so a blank one silences the reminder for
        another six months while recording nothing - the animal is then
        invisible to the one screen meant to catch it. Both apps send
        `parseFloat(input) || null`, so an empty box, "abc" and a typed 0
        all arrive here as null.
        """
        if self.kind is EventKind.weight and (self.value is None or self.value <= 0):
            raise ValueError("a weight event needs a weight in kg")
        return self


class BulkMovementCreate(SQLModel):
    """Move a whole camp at once: the normal case is every animal in a
    field moving together, not one tag typed in at a time."""
    tags: list[str]
    event_date: date
    location: str
    note: str = ""


def _animal_by_tag(session: Session, tag: str) -> Animal:
    animal = session.exec(select(Animal).where(Animal.tag == normalise_tag(tag))).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag!r}")
    return animal


@router.post("/events")
def create_event(payload: EventCreate, session: Session = Depends(get_session)):
    """Record an event, once, however many times this is asked.

    Recording is idempotent on client_uuid because the field app cannot tell
    a request that failed from one that succeeded slowly: it gives up after 8
    seconds (Kudde.NETWORK_TIMEOUT_MS) and puts the event back in its outbox,
    so a request that was slow but actually landed is replayed. Nothing else
    about an event could tell that replay from a real second event - the same
    animal genuinely can be treated twice on one day - so without this the
    farm's records quietly gain a duplicate every time the wifi is slow.

    Boord solves the same problem the same way for harvest records
    (backend/routers/sync.py).
    """
    if payload.client_uuid:
        already = session.exec(
            select(Event).where(Event.client_uuid == payload.client_uuid)).first()
        if already is not None:
            # Answered with the event that is already on file, so the device
            # takes it off its outbox rather than retrying forever.
            return already

    animal = _animal_by_tag(session, payload.tag)
    event = Event(animal_id=animal.id, kind=payload.kind, event_date=payload.event_date,
                   value=payload.value, note=payload.note, location=payload.location,
                   client_uuid=payload.client_uuid)

    # After the client_uuid check above, never before it: a replay of an
    # event that already landed returns the stored one without going near
    # the network, so a slow Open-Meteo cannot be walked into once per retry.
    farm = session.get(Farm, FARM_ID)
    if farm is not None and farm.gps_lat is not None and farm.gps_lng is not None:
        weather = fetch_daily_weather(farm.gps_lat, farm.gps_lng, payload.event_date)
        if weather is not None:
            event.weather_temp_max = weather.temp_max
            event.weather_temp_min = weather.temp_min
            event.weather_precipitation = weather.precipitation

    session.add(event)

    new_status = _STATUS_ON_EVENT.get(payload.kind)
    if new_status is not None and animal.status != new_status:
        animal.status = new_status
        # The animal really did change here, so its updated_at has to say so.
        # Leaving it at the old value made a death or sale the one edit that
        # was invisible to anything comparing timestamps.
        animal.updated_at = datetime.utcnow()
        session.add(animal)

    session.commit()
    session.refresh(event)
    return event


@router.post("/events/movement/bulk")
def create_bulk_movement(payload: BulkMovementCreate, session: Session = Depends(get_session)):
    """Record the same movement for every listed animal in one call, so a
    camp move lands as one save instead of one request per tag. Tags are
    resolved up front - if any one of them doesn't exist, nothing is
    written, rather than moving half a camp and failing partway through."""
    if not payload.tags:
        raise HTTPException(400, "At least one tag is required")
    animals = [_animal_by_tag(session, tag) for tag in payload.tags]
    events = [
        Event(animal_id=a.id, kind=EventKind.movement, event_date=payload.event_date,
              location=payload.location, note=payload.note)
        for a in animals
    ]
    session.add_all(events)
    session.commit()
    for event in events:
        session.refresh(event)
    return events


@router.get("/locations")
def list_locations(session: Session = Depends(get_session)):
    """Every camp/location currently holding at least one alive animal,
    grouped with who's there - what the "move whole camp" picker uses so a
    location can be selected without typing out every tag in it."""
    latest_location = _current_locations(session)
    alive = session.exec(select(Animal).where(Animal.status == AnimalStatus.alive)).all()
    grouped: dict = {}
    for animal in alive:
        location = latest_location.get(animal.id)
        if not location:
            continue
        grouped.setdefault(location, []).append({"tag": animal.tag, "name": animal.name})
    return [
        {"location": location, "animals": sorted(members, key=lambda a: a["tag"])}
        for location, members in sorted(grouped.items())
    ]


@router.get("/animals/{tag}/events")
def list_animal_events(tag: str, session: Session = Depends(get_session)):
    animal = _animal_by_tag(session, tag)
    return session.exec(
        # id descending as the tiebreaker, matching list_events below. Dates
        # here are days, not timestamps, so several events sharing one date
        # is the normal case (weighed and treated at the same muster) - and
        # without a tiebreaker SQLite is free to return them in a different
        # order each time the screen is opened.
        select(Event).where(Event.animal_id == animal.id)
        .order_by(Event.event_date.desc(), Event.id.desc())
    ).all()


@router.get("/events")
def list_events(kind: Optional[EventKind] = None,
                 limit: int = Query(50, ge=1, le=_MAX_EVENT_LIMIT),
                 session: Session = Depends(get_session)):
    """Recent events across the whole herd, newest first - what the admin
    dashboard's activity feed shows."""
    statement = select(Event)
    if kind is not None:
        statement = statement.where(Event.kind == kind)
    statement = statement.order_by(Event.event_date.desc(), Event.id.desc()).limit(limit)
    return session.exec(statement).all()
