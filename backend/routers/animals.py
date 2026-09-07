from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, SQLModel, select

from db import get_session
from models import Animal, AnimalSex, AnimalStatus
from security import require_admin_client

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


class AnimalUpdate(SQLModel):
    name: Optional[str] = None
    breed: Optional[str] = None
    sex: Optional[AnimalSex] = None
    birth_date: Optional[date] = None
    sire_tag: Optional[str] = None
    dam_tag: Optional[str] = None
    status: Optional[AnimalStatus] = None


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
def update_animal(tag: str, payload: AnimalUpdate, session: Session = Depends(get_session),
                   _admin=Depends(require_admin_client)):
    """Partial update - only the fields the client sends are touched, so a
    field-app edit ("mark as sold") can't clobber data an admin edit made
    around the same time.

    Admin-only: correcting an existing record (renaming, fixing a birth
    date, reassigning parentage) is exclusively an Admin-app action today -
    see security.py. Recording what happened to an animal in the field
    still goes through POST /api/events, which stays open to the farm wifi."""
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
