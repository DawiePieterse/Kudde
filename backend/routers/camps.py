"""The farm's camps, as Settings manages them. See models.Camp and
campnames.py for why an event's location is still plain text."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import field_validator
from sqlmodel import Session, SQLModel, select

from campnames import find_camp, next_sort_order, normalise_camp_name
from db import get_session
from models import Animal, AnimalStatus, Camp
from routers.events import _current_locations

router = APIRouter(prefix="/api/camps", tags=["camps"])


class CampCreate(SQLModel):
    name: str

    @field_validator("name")
    @classmethod
    def _named(cls, value: str) -> str:
        name = normalise_camp_name(value)
        if not name:
            raise ValueError("a camp needs a name")
        return name


class CampUpdate(SQLModel):
    name: Optional[str] = None
    archived: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _named(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        name = normalise_camp_name(value)
        if not name:
            raise ValueError("a camp needs a name")
        return name


class CampOrder(SQLModel):
    ids: List[int]


def _alive_counts(session: Session) -> dict:
    """Alive animals per camp name, by the same derivation the Move picker
    uses, keyed by the folded name so a count finds its camp whatever was
    typed on the events."""
    where = _current_locations(session)
    counts: dict = {}
    for animal in session.exec(select(Animal).where(Animal.status == AnimalStatus.alive)).all():
        location = where.get(animal.id)
        if location:
            key = normalise_camp_name(location).casefold()
            counts[key] = counts.get(key, 0) + 1
    return counts


def _present(camp: Camp, counts: dict) -> dict:
    return {**camp.model_dump(), "alive_count": counts.get(camp.name.casefold(), 0)}


@router.get("")
def list_camps(include_archived: bool = False, session: Session = Depends(get_session)):
    """In the farmer's order, with how many alive animals each holds."""
    camps = session.exec(select(Camp).order_by(Camp.sort_order, Camp.id)).all()
    if not include_archived:
        camps = [c for c in camps if not c.archived]
    counts = _alive_counts(session)
    return [_present(c, counts) for c in camps]


@router.post("")
def create_camp(payload: CampCreate, session: Session = Depends(get_session)):
    existing = find_camp(session, payload.name)
    if existing is not None and not existing.archived:
        raise HTTPException(409, f"There is already a camp called {existing.name}")
    if existing is not None:
        # Adding a camp that was archived brings it back under its old row,
        # so the events that named it and this entry stay one camp.
        existing.archived = False
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return _present(existing, _alive_counts(session))
    camp = Camp(name=payload.name, sort_order=next_sort_order(session))
    session.add(camp)
    session.commit()
    session.refresh(camp)
    return _present(camp, _alive_counts(session))


@router.patch("/{camp_id}")
def update_camp(camp_id: int, payload: CampUpdate, session: Session = Depends(get_session)):
    camp = session.get(Camp, camp_id)
    if camp is None:
        raise HTTPException(404, "No such camp")
    if payload.name is not None:
        other = find_camp(session, payload.name)
        if other is not None and other.id != camp.id:
            raise HTTPException(409, f"There is already a camp called {other.name}")
        # A rename rewrites no events: history keeps the name it was recorded
        # under, and the pickers show the new one from now on.
        camp.name = payload.name
    if payload.archived is not None:
        if payload.archived and not camp.archived:
            held = _alive_counts(session).get(camp.name.casefold(), 0)
            if held:
                raise HTTPException(
                    409, f"{camp.name} still holds {held} animal{'' if held == 1 else 's'} - move them first")
        camp.archived = payload.archived
    session.add(camp)
    session.commit()
    session.refresh(camp)
    return _present(camp, _alive_counts(session))


@router.put("/order")
def reorder_camps(payload: CampOrder, session: Session = Depends(get_session)):
    """The whole order in one call, so two quick taps on the arrows cannot
    interleave into a list with two camps at the same position."""
    camps = {c.id: c for c in session.exec(select(Camp)).all()}
    unknown = [i for i in payload.ids if i not in camps]
    if unknown:
        raise HTTPException(404, f"No such camp: {unknown[0]}")
    for position, camp_id in enumerate(payload.ids):
        camps[camp_id].sort_order = position
        session.add(camps[camp_id])
    # Anything not listed (an archived camp, say) keeps a place after them.
    tail = sorted((c for c in camps.values() if c.id not in payload.ids),
                  key=lambda c: (c.sort_order, c.id))
    for offset, camp in enumerate(tail):
        camp.sort_order = len(payload.ids) + offset
        session.add(camp)
    session.commit()
    return list_camps(include_archived=True, session=session)
