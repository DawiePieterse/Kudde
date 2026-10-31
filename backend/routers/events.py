from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, field_validator, model_validator
from sqlmodel import Session, SQLModel, select

from campnames import ensure_camp
from db import get_session
from farmweather import stamp_weather, weather_for
from herd import LOCATION_EVENT_KINDS, animal_by_tag, animals_by_tags, camp_groups, current_locations
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

# The most recent events any one request will return. A farmer's activity
# feed shows ten; nothing in either app asks for more than a screenful, so
# an unbounded limit only ever serves a mistake - and SQLite reads a
# NEGATIVE limit as "no limit at all", so `?limit=-1` used to return the
# whole event history of the herd.
_MAX_EVENT_LIMIT = 500

# How far ahead of the server's day an event or birth date may be. Kudde
# records what happened, not what is planned, so a date in the future is
# a date picker scrolled to the wrong month or year - and once saved it
# poisons every derivation: a weighing dated next year keeps the animal off
# the needs-weighing list for good, a treatment dated next year puts it in
# withdrawal for months. One day of grace, not zero: the phone's clock and
# this PC's are not the same clock, and a record made just after midnight
# on one must not be refused by the other.
FUTURE_GRACE = timedelta(days=1)


def not_in_the_future(value: Optional[date], what: str = "date") -> Optional[date]:
    """A pydantic validator body shared by every schema that carries a date
    the farmer typed. Returns the date, or raises the message the apps show."""
    if value is not None and value > date.today() + FUTURE_GRACE:
        raise ValueError(f"the {what} is in the future")
    return value


class TreatmentFields(SQLModel):
    """What a treatment records besides the date and note (see
    models.Event): the product, the dose, and the withdrawal in days off
    the label. One definition for the single event and the group
    treatment, so the rules cannot drift between them. All optional: a
    treatment that is only a note is still a treatment, and every outbox
    entry captured before these existed sends none."""
    product: str = ""
    dose: str = ""
    withdrawal_days: Optional[int] = Field(default=None, ge=0)

    @field_validator("product", "dose")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return " ".join(value.split())


class EventCreate(TreatmentFields):
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

    @field_validator("event_date")
    @classmethod
    def _dated_in_the_past(cls, value: date) -> date:
        return not_in_the_future(value)

    @model_validator(mode="after")
    def _a_move_names_a_camp(self) -> "EventCreate":
        """A movement with no camp moves the animal nowhere: the derived
        camp (herd.current_locations) skips an empty location, so the save
        said "Event recorded" and changed nothing anyone could see. The bulk
        move already refuses this; the single one has to as well. A birth
        may still be unplaced - the calving screen allows that on purpose."""
        if self.kind is EventKind.movement and not (self.location or "").strip():
            raise ValueError("a camp move needs the camp it is moving to")
        return self

    @model_validator(mode="after")
    def _treatment_fields_belong_to_treatments(self) -> "EventCreate":
        """Dropped, not refused, on any other kind: a form that carried a
        stale product across a kind change should not fail the save."""
        if self.kind is not EventKind.treatment:
            self.product, self.dose, self.withdrawal_days = "", "", None
        return self

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
    # The id the capturing device gave this batch. Optional for the same
    # reason it is on EventCreate: the admin app has no outbox and sends
    # none. See create_bulk_movement() for what it is derived into.
    client_uuid: Optional[str] = None

    @field_validator("event_date")
    @classmethod
    def _dated_in_the_past(cls, value: date) -> date:
        return not_in_the_future(value)


def _camp_location(session: Session, kind: EventKind, location: str) -> str:
    """The location to store on an event: for the kinds that place an animal
    in a camp, the spelling Settings holds for it (added there if new, see
    campnames.ensure_camp). Other kinds keep whatever was typed - a
    treatment's location, if ever set, is where it happened, not a camp."""
    location = (location or "").strip()
    if not location or kind not in LOCATION_EVENT_KINDS:
        return location
    return ensure_camp(session, location).name


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

    animal = animal_by_tag(session, payload.tag)
    event = Event(animal_id=animal.id, kind=payload.kind, event_date=payload.event_date,
                   value=payload.value, note=payload.note,
                   location=_camp_location(session, payload.kind, payload.location),
                   product=payload.product, dose=payload.dose,
                   withdrawal_days=payload.withdrawal_days,
                   client_uuid=payload.client_uuid)

    # After the client_uuid check above, never before it: a replay of an
    # event that already landed returns the stored one without going near
    # the network, so a slow Open-Meteo cannot be walked into once per retry.
    stamp_weather(event, weather_for(session, payload.event_date))

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


def _batch_member_uuid(client_uuid: str, animal_id: int) -> str:
    """The client_uuid stored on one animal's event within a batch.

    Event.client_uuid is unique per row, so a batch cannot store the same id
    on all of its events. Deriving one per animal keeps the column doing its
    job for every row a bulk move writes, and makes a batch that half landed
    - some rows committed, then the connection dropped - resolve on replay
    rather than duplicating what did land.
    """
    return f"{client_uuid}:{animal_id}"


def _record_batch(session: Session, animals: list, client_uuid: Optional[str],
                  event_date: date, **fields) -> list:
    """The same event for every animal in `animals`, written once however
    many times the batch is replayed.

    Shared by the bulk camp move and the bulk treatment. Idempotent on
    client_uuid exactly as create_event() is and for the same reason: the
    field app queues a whole batch in its outbox and replays it after an
    8-second timeout, so a slow-but-landed request would otherwise record
    every animal twice. `fields` are the Event columns every member shares.
    """
    already = {}
    if client_uuid:
        keys = [_batch_member_uuid(client_uuid, a.id) for a in animals]
        already = {
            event.client_uuid: event
            for event in session.exec(select(Event).where(Event.client_uuid.in_(keys))).all()
        }

    events = []
    fresh = []
    for animal in animals:
        key = _batch_member_uuid(client_uuid, animal.id) if client_uuid else None
        if key is not None and key in already:
            # Already on file from an earlier attempt at this same batch,
            # weather and all - leave it exactly as it was recorded.
            events.append(already[key])
            continue
        event = Event(animal_id=animal.id, event_date=event_date, client_uuid=key, **fields)
        events.append(event)
        fresh.append(event)

    if fresh:
        # One lookup for the whole batch: every animal in it shares the
        # farm's position and the date, so asking per animal would be the
        # same answer fetched once per head of cattle.
        weather = weather_for(session, event_date)
        for event in fresh:
            stamp_weather(event, weather)

    session.add_all(events)
    session.commit()
    for event in events:
        session.refresh(event)
    return events


@router.post("/events/movement/bulk")
def create_bulk_movement(payload: BulkMovementCreate, session: Session = Depends(get_session)):
    """Record the same movement for every listed animal in one call, so a
    camp move lands as one save instead of one request per tag. Tags are
    resolved up front - if any one of them doesn't exist, nothing is
    written, rather than moving half a camp and failing partway through.
    """
    if not payload.tags:
        raise HTTPException(400, "At least one tag is required")
    animals = animals_by_tags(session, payload.tags)
    location = _camp_location(session, EventKind.movement, payload.location)
    if not location:
        raise HTTPException(422, "a camp move needs the camp it is moving to")
    return _record_batch(session, animals, payload.client_uuid, payload.event_date,
                         kind=EventKind.movement, location=location, note=payload.note)


class BulkTreatmentCreate(TreatmentFields):
    """Treat a whole group at once: the dosing day, when every animal in a
    camp gets the same product. The same fields a single treatment
    carries, applied to every tag."""
    tags: list[str]
    event_date: date
    note: str = ""
    client_uuid: Optional[str] = None

    @field_validator("event_date")
    @classmethod
    def _dated_in_the_past(cls, value: date) -> date:
        return not_in_the_future(value)


@router.post("/events/treatment/bulk")
def create_bulk_treatment(payload: BulkTreatmentCreate, session: Session = Depends(get_session)):
    """Record the same treatment for every listed animal in one call. Tags
    are resolved up front, as for a camp move: all or nothing."""
    if not payload.tags:
        raise HTTPException(400, "At least one tag is required")
    animals = animals_by_tags(session, payload.tags)
    return _record_batch(session, animals, payload.client_uuid, payload.event_date,
                         kind=EventKind.treatment, note=payload.note, product=payload.product,
                         dose=payload.dose, withdrawal_days=payload.withdrawal_days)


# How many distinct products the pickers offer. The farm's medicine cupboard
# is a dozen bottles; the list is there to save typing, not to be searched.
_RECENT_PRODUCTS = 20


@router.get("/products")
def list_products(session: Session = Depends(get_session)):
    """The products given before, most recently used first, each with the
    dose and withdrawal it was last recorded with - so the next treatment
    with the same bottle is one tap, and a withdrawal typed once is typed
    once. Nothing to set up: the list is the treatments already on file."""
    rows = session.exec(
        select(Event.product, Event.dose, Event.withdrawal_days)
        .where(Event.kind == EventKind.treatment)
        .where(Event.product != "")
        .order_by(Event.event_date.desc(), Event.id.desc())
    ).all()
    seen = set()
    products = []
    for product, dose, days in rows:
        key = product.casefold()
        if key in seen:
            continue
        seen.add(key)
        products.append({"product": product, "dose": dose, "withdrawal_days": days})
        if len(products) >= _RECENT_PRODUCTS:
            break
    return products


@router.get("/locations")
def list_locations(session: Session = Depends(get_session)):
    """Every camp the farm has, in Settings order, with the alive animals in
    each - what the "move whole camp" picker shows, so a camp can be chosen
    without typing out every tag in it. See herd.camp_groups for the order
    and for why empty camps, stray names and a None row are in it."""
    alive = session.exec(select(Animal).where(Animal.status == AnimalStatus.alive)).all()
    return [{"location": name, "animals": [{"tag": a.tag, "name": a.name} for a in members]}
            for name, members in camp_groups(session, alive, current_locations(session))]


@router.get("/animals/{tag}/events")
def list_animal_events(tag: str, session: Session = Depends(get_session)):
    animal = animal_by_tag(session, tag)
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
