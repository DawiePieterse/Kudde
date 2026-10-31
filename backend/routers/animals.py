from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator
from sqlmodel import Session, SQLModel, select

from campnames import ensure_camp
from db import get_session
from farmweather import stamp_weather, weather_for
from herd import herd_context
from models import Animal, AnimalSex, AnimalStatus, Event, EventKind
from routers.events import not_in_the_future
from tags import normalise_tag

router = APIRouter(prefix="/api/animals", tags=["animals"])


# Plain (non-table) input schemas, deliberately not `Animal` itself.
# SQLModel table=True classes don't get full pydantic type coercion on
# their fields - a birth_date sent as "2024-03-15" arrives at SQLAlchemy
# still a str instead of a date, which SQLite's Date type then rejects at
# insert time. A plain SQLModel (table=False) coerces it properly, so the
# request is validated through one of these and then copied into the real
# table model.
class AnimalCreate(SQLModel):
    tag: str
    name: str = ""
    breed: str = ""
    sex: AnimalSex
    birth_date: Optional[date] = None
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    castrated: bool = False
    # Where the animal is when it is entered. Optional, and not a column on
    # the animal: a camp is only ever derived from the event history (see
    # events._current_locations), so this becomes the animal's first
    # movement event. Without it a new animal has no camp at all, is
    # invisible to the Move camp picker, and a herd keyed in from Admin
    # needed one hand-typed movement per head before Move camp worked.
    camp: Optional[str] = None

    @field_validator("tag")
    @classmethod
    def _tag_is_a_tag(cls, value: str) -> str:
        tag = normalise_tag(value)
        if tag is None:
            raise ValueError("a tag number is required")
        return tag

    # Parentage is stored as free text rather than a foreign key (see
    # models.py), so nothing downstream would ever catch a stray space here.
    @field_validator("sire_tag", "dam_tag")
    @classmethod
    def _tidy_parent_tag(cls, value: Optional[str]) -> Optional[str]:
        return normalise_tag(value)

    # An animal born next year reads as a calf for ever (classes.py counts
    # days from its birth date), so the same rule the events apply.
    @field_validator("birth_date")
    @classmethod
    def _born_already(cls, value: Optional[date]) -> Optional[date]:
        return not_in_the_future(value, "birth date")


class AnimalUpdate(SQLModel):
    name: Optional[str] = None
    breed: Optional[str] = None
    sex: Optional[AnimalSex] = None
    birth_date: Optional[date] = None
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    castrated: Optional[bool] = None
    status: Optional[AnimalStatus] = None

    @field_validator("sire_tag", "dam_tag")
    @classmethod
    def _tidy_parent_tag(cls, value: Optional[str]) -> Optional[str]:
        return normalise_tag(value)

    @field_validator("birth_date")
    @classmethod
    def _born_already(cls, value: Optional[date]) -> Optional[date]:
        return not_in_the_future(value, "birth date")


@router.get("")
def list_animals(q: Optional[str] = None, status: Optional[AnimalStatus] = None,
                  session: Session = Depends(get_session)):
    """q matches the start of a tag or anywhere in a name - the two things a
    farmer actually has in hand when they're looking for an animal. Each
    animal comes with its camp, last weight, withdrawal and class (see
    herd.HerdContext.describe). Already in tag order (herd_context)."""
    ctx = herd_context(session)
    animals = ctx.animals
    if status is not None:
        animals = [a for a in animals if a.status is status]
    if q:
        needle = q.strip().lower()
        animals = [a for a in animals if a.tag.lower().startswith(needle) or needle in a.name.lower()]
    return [ctx.describe(a) for a in animals]


@router.get("/{tag}")
def get_animal(tag: str, session: Session = Depends(get_session)):
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag}")
    return animal


def new_animal(session: Session, fields: AnimalCreate, *, placed_kind: EventKind,
               on: date, note: str, client_uuid: Optional[str] = None) -> Animal:
    """Adds an animal to the session (not committed) and, if `fields.camp`
    names one, the event that places it there: a movement dated today for
    an animal entered by hand, a birth event on its birth date for a calf.
    Shared by create_animal and the calving, so the rules on a new animal
    (unique tag, the camp snapped to Settings) live once. Refuses before
    anything is added, so a caller can commit nothing on a 409."""
    if session.exec(select(Animal).where(Animal.tag == fields.tag)).first() is not None:
        raise HTTPException(409, f"Tag {fields.tag} is already in use")
    animal = Animal(**fields.model_dump(exclude={"camp"}))
    session.add(animal)
    camp_name = (fields.camp or "").strip()
    if camp_name:
        # Through ensure_camp so a camp typed on a phone snaps to the
        # spelling in Settings, or is added there if it is new.
        session.flush()  # animal.id, for the event's foreign key
        placed = Event(animal_id=animal.id, kind=placed_kind, event_date=on,
                       location=ensure_camp(session, camp_name).name, note=note,
                       client_uuid=client_uuid)
        stamp_weather(placed, weather_for(session, on))
        session.add(placed)
    return animal


@router.post("")
def create_animal(payload: AnimalCreate, session: Session = Depends(get_session)):
    # Placed today, not at the birth date: this records where the animal is
    # as it is entered, not a claim about where it was born.
    animal = new_animal(session, payload, placed_kind=EventKind.movement, on=date.today(),
                        note="Entered into Kudde")
    session.commit()
    session.refresh(animal)
    return animal


@router.patch("/{tag}")
def update_animal(tag: str, payload: AnimalUpdate, session: Session = Depends(get_session)):
    """Partial update - only the fields the client sends are touched, so a
    field-app edit ("mark as sold") can't clobber data an admin edit made
    around the same time."""
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag}")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(animal, key, value)
    animal.updated_at = datetime.utcnow()
    session.add(animal)
    session.commit()
    session.refresh(animal)
    return animal
