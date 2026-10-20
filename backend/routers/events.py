from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import field_validator, model_validator
from sqlmodel import Session, SQLModel, select

from campnames import ensure_camp, fold
from db import get_session
from farmweather import stamp_weather as _stamp_weather, weather_for as _weather_for
from models import Animal, AnimalStatus, Camp, Event, EventKind
from tags import normalise_tag, tag_sort_key

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
    # Treatment only (see models.Event). Sent empty by the other kinds and
    # by any outbox entry captured before these existed.
    product: str = ""
    dose: str = ""
    withdrawal_days: Optional[int] = None
    # The id the capturing device gave this event. Optional: the admin app
    # has no outbox and sends none. See create_event().
    client_uuid: Optional[str] = None

    @field_validator("withdrawal_days")
    @classmethod
    def _withdrawal_is_days(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("withdrawal days cannot be negative")
        return value

    @model_validator(mode="after")
    def _treatment_fields_belong_to_treatments(self) -> "EventCreate":
        """Dropped, not refused, on any other kind: a form that carried a
        stale product across a kind change should not fail the save."""
        if self.kind is not EventKind.treatment:
            self.product, self.dose, self.withdrawal_days = "", "", None
        else:
            self.product = " ".join(self.product.split())
            self.dose = " ".join(self.dose.split())
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


def _camp_location(session: Session, kind: EventKind, location: str) -> str:
    """The location to store on an event: for the kinds that place an animal
    in a camp, the spelling Settings holds for it (added there if new, see
    campnames.ensure_camp). Other kinds keep whatever was typed - a
    treatment's location, if ever set, is where it happened, not a camp."""
    location = (location or "").strip()
    if not location or kind not in _LOCATION_EVENT_KINDS:
        return location
    return ensure_camp(session, location).name


def _animal_by_tag(session: Session, tag: str) -> Animal:
    animal = session.exec(select(Animal).where(Animal.tag == normalise_tag(tag))).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag}")
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
                   value=payload.value, note=payload.note,
                   location=_camp_location(session, payload.kind, payload.location),
                   product=payload.product, dose=payload.dose,
                   withdrawal_days=payload.withdrawal_days,
                   client_uuid=payload.client_uuid)

    # After the client_uuid check above, never before it: a replay of an
    # event that already landed returns the stored one without going near
    # the network, so a slow Open-Meteo cannot be walked into once per retry.
    _stamp_weather(event, _weather_for(session, payload.event_date))

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
                  event_date: date, build) -> list:
    """The same event for every animal in `animals`, written once however
    many times the batch is replayed.

    Shared by the bulk camp move and the bulk treatment. Idempotent on
    client_uuid exactly as create_event() is and for the same reason: the
    field app queues a whole batch in its outbox and replays it after an
    8-second timeout, so a slow-but-landed request would otherwise record
    every animal twice. `build(animal, key)` makes one animal's event.
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
        event = build(animal, key)
        events.append(event)
        fresh.append(event)

    if fresh:
        # One lookup for the whole batch: every animal in it shares the
        # farm's position and the date, so asking per animal would be the
        # same answer fetched once per head of cattle.
        weather = _weather_for(session, event_date)
        for event in fresh:
            _stamp_weather(event, weather)

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
    animals = [_animal_by_tag(session, tag) for tag in payload.tags]
    location = _camp_location(session, EventKind.movement, payload.location)
    if not location:
        raise HTTPException(422, "a camp move needs the camp it is moving to")
    return _record_batch(
        session, animals, payload.client_uuid, payload.event_date,
        lambda animal, key: Event(animal_id=animal.id, kind=EventKind.movement,
                                  event_date=payload.event_date, location=location,
                                  note=payload.note, client_uuid=key))


class BulkTreatmentCreate(SQLModel):
    """Treat a whole group at once: the dosing day, when every animal in a
    camp gets the same product. The same fields a single treatment
    carries (see EventCreate), applied to every tag."""
    tags: list[str]
    event_date: date
    product: str = ""
    dose: str = ""
    withdrawal_days: Optional[int] = None
    note: str = ""
    client_uuid: Optional[str] = None

    @field_validator("withdrawal_days")
    @classmethod
    def _withdrawal_is_days(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value < 0:
            raise ValueError("withdrawal days cannot be negative")
        return value


@router.post("/events/treatment/bulk")
def create_bulk_treatment(payload: BulkTreatmentCreate, session: Session = Depends(get_session)):
    """Record the same treatment for every listed animal in one call. Tags
    are resolved up front, as for a camp move: all or nothing."""
    if not payload.tags:
        raise HTTPException(400, "At least one tag is required")
    animals = [_animal_by_tag(session, tag) for tag in payload.tags]
    product = " ".join(payload.product.split())
    dose = " ".join(payload.dose.split())
    return _record_batch(
        session, animals, payload.client_uuid, payload.event_date,
        lambda animal, key: Event(animal_id=animal.id, kind=EventKind.treatment,
                                  event_date=payload.event_date, note=payload.note,
                                  product=product, dose=dose,
                                  withdrawal_days=payload.withdrawal_days, client_uuid=key))


def withdrawal_until(session: Session) -> dict:
    """{animal_id: date} of the latest day each animal is still inside a
    treatment's withdrawal period, for those that are. The longest of any
    overlapping treatments wins: an animal dosed twice is clear when the
    later-ending one is."""
    today = date.today()
    rows = session.exec(
        select(Event.animal_id, Event.event_date, Event.withdrawal_days)
        .where(Event.kind == EventKind.treatment)
        .where(Event.withdrawal_days.is_not(None))
    ).all()
    until: dict = {}
    for animal_id, on, days in rows:
        end = on + timedelta(days=days)
        if end >= today and end > until.get(animal_id, date.min):
            until[animal_id] = end
    return until


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
    without typing out every tag in it.

    Three things are in the list that were not before camps were managed:

    - a camp with nobody in it, so the farmer sees the whole farm and not
      just the camps that happen to hold an animal (the apps grey it out);
    - a camp that is on the events but was never set up (older records, or
      a device replaying an outbox from before the list existed), grouped
      under its own name after the managed ones, so no animal disappears
      from the picker;
    - last, a row with location None for the alive animals that have no
      camp recorded at all, so they can be moved INTO a camp - before this
      an animal with no history could never be picked.
    """
    latest_location = _current_locations(session)
    alive = session.exec(select(Animal).where(Animal.status == AnimalStatus.alive)).all()
    camps = session.exec(select(Camp).order_by(Camp.sort_order, Camp.id)).all()
    by_key = {fold(c.name): c for c in camps if not c.archived}

    grouped: dict = {fold(c.name): [] for c in by_key.values()}
    names = {key: camp.name for key, camp in by_key.items()}
    unplaced = []
    for animal in alive:
        location = latest_location.get(animal.id)
        if not location:
            unplaced.append({"tag": animal.tag, "name": animal.name})
            continue
        key = fold(location)
        names.setdefault(key, location.strip())
        grouped.setdefault(key, []).append({"tag": animal.tag, "name": animal.name})

    def row(key):
        return {"location": names[key],
                "animals": sorted(grouped[key], key=lambda a: tag_sort_key(a["tag"]))}

    managed = [row(fold(c.name)) for c in by_key.values()]
    stray = [row(key) for key in sorted(k for k in grouped if k not in by_key)]
    rows = managed + stray
    if unplaced:
        rows.append({"location": None,
                     "animals": sorted(unplaced, key=lambda a: tag_sort_key(a["tag"]))})
    return rows


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
