from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import Animal, AnimalSex, AnimalStatus

router = APIRouter(prefix="/api/animals", tags=["animals"])


def normalise_tag(value: Optional[str]) -> Optional[str]:
    """An ear tag as it should be stored: trimmed, or None if there is
    nothing left.

    The tag is this app's identity key - it is what the unique index is on,
    what every event is recorded against, and what a farmer reads off the
    animal. Both front ends already .trim() before sending, so nothing
    normal reaches here untrimmed; what does is the offline outbox replaying
    an entry captured by an older build, and any other client. Without this
    " A1 " lands beside "A1" as a second animal that looks identical on
    every screen and cannot be told apart, and "" lands as an animal with no
    tag at all - both confirmed against the API before this was added.
    """
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


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


class AnimalUpdate(SQLModel):
    name: Optional[str] = None
    breed: Optional[str] = None
    sex: Optional[AnimalSex] = None
    birth_date: Optional[date] = None
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    status: Optional[AnimalStatus] = None

    @field_validator("sire_tag", "dam_tag")
    @classmethod
    def _tidy_parent_tag(cls, value: Optional[str]) -> Optional[str]:
        return normalise_tag(value)


@router.get("")
def list_animals(q: Optional[str] = None, status: Optional[AnimalStatus] = None,
                  session: Session = Depends(get_session)):
    """q matches the start of a tag or anywhere in a name - the two things a
    farmer actually has in hand when they're looking for an animal."""
    statement = select(Animal)
    if status is not None:
        statement = statement.where(Animal.status == status)
    animals = session.exec(statement.order_by(Animal.tag)).all()
    if q:
        needle = q.strip().lower()
        animals = [a for a in animals if a.tag.lower().startswith(needle) or needle in a.name.lower()]
    return animals


@router.get("/{tag}")
def get_animal(tag: str, session: Session = Depends(get_session)):
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag!r}")
    return animal


@router.post("")
def create_animal(payload: AnimalCreate, session: Session = Depends(get_session)):
    if session.exec(select(Animal).where(Animal.tag == payload.tag)).first() is not None:
        raise HTTPException(409, f"Tag {payload.tag!r} is already in use")
    animal = Animal(**payload.model_dump())
    session.add(animal)
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
        raise HTTPException(404, f"No animal with tag {tag!r}")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(animal, key, value)
    animal.updated_at = datetime.utcnow()
    session.add(animal)
    session.commit()
    session.refresh(animal)
    return animal
