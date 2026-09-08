"""SQLModel schema for Kudde - small-scale cattle recording.

Deliberately not the full ICAR Animal Data Exchange standard: that defines
~75 resource/event types (breeding values, embryo flushing, carcass
ultrasound, DNA, formal conformation scoring...) built for stud/champion
breeding operations. A small-scale farm needs a fraction of that - who an
animal is, and the handful of things that happen to it. Everything below is
a deliberate trim of the ICAR resources, not a from-scratch guess: Animal
mirrors icarAnimalBaseResource's identity fields, and Event's `kind` values
cover the ICAR event types (birth, weight, treatment, movement, death,
sale) that actually apply here, collapsed into one table instead of one
schema per kind.
"""
from datetime import datetime, date
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


class AnimalSex(str, Enum):
    male = "male"
    female = "female"


class AnimalStatus(str, Enum):
    alive = "alive"
    dead = "dead"
    sold = "sold"


class EventKind(str, Enum):
    birth = "birth"
    weight = "weight"
    treatment = "treatment"
    movement = "movement"
    death = "death"
    sale = "sale"


class Animal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # The ear tag number - what the farmer actually reads off the animal.
    # ICAR calls this managementTag, as distinct from a formal herdbook
    # registration number this app has no use for.
    tag: str = Field(index=True, unique=True)
    name: str = ""
    breed: str = ""  # free text - no ICAR breed-code lookup, this isn't a stud registry
    sex: AnimalSex
    birth_date: Optional[date] = None
    # Parentage by tag rather than a foreign key: the sire/dam may have been
    # sold, died, or never been recorded in this herd at all (bought-in
    # animal, bull borrowed from a neighbour), so it must be recordable even
    # when it points at nothing in this database.
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    status: AnimalStatus = AnimalStatus.alive
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # The id the capturing device gave this event, before it had ever reached
    # a server. It is what makes recording an event idempotent: the field app
    # gives up on a request after 8 seconds and puts it back in the outbox,
    # so a request that was slow but actually landed gets replayed, and
    # without this the herd quietly acquires a second identical weighing.
    # Nothing else in an event distinguishes it from a real duplicate - the
    # same animal genuinely can be treated twice on one day.
    #
    # Nullable, because an event captured in the admin app never passes
    # through an outbox and has no such id. Boord does the same thing for
    # harvest records (backend/routers/sync.py: "idempotent upsert by
    # client-generated uuid - safe for a field device").
    client_uuid: Optional[str] = Field(default=None, index=True, unique=True)
    animal_id: int = Field(foreign_key="animal.id", index=True)
    kind: EventKind
    event_date: date
    value: Optional[float] = None  # weight in kg for a "weight" event; unused by other kinds
    note: str = ""
    location: str = ""  # camp/paddock name - mainly for "movement" events
    created_at: datetime = Field(default_factory=datetime.utcnow)
