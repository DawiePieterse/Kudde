from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from db import get_session
from models import Animal, AnimalStatus, Event, EventKind

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Past this many days without a weight event, an alive animal shows up as
# needing attention. Six months: long enough that a farmer weighing every
# muster or two isn't nagged, short enough to actually catch an animal that
# fell through the cracks.
_STALE_WEIGHT_DAYS = 182


@router.get("")
def dashboard(session: Session = Depends(get_session)):
    counts = dict(session.exec(
        select(Animal.status, func.count()).group_by(Animal.status)
    ).all())
    herd_counts = {status.value: counts.get(status, 0) for status in AnimalStatus}

    # Joined to the animal rather than returning bare events: an activity
    # feed that says "death - 9/6/2026" without naming the animal is close
    # to useless to whoever is reading it, and the alternative is the admin
    # app making a lookup request per row.
    recent_rows = session.exec(
        select(Event, Animal.tag, Animal.name)
        .join(Animal, Animal.id == Event.animal_id)
        .order_by(Event.event_date.desc(), Event.id.desc())
        .limit(10)
    ).all()
    recent_events = [
        {**event.model_dump(), "tag": tag, "name": name}
        for event, tag, name in recent_rows
    ]

    cutoff = date.today() - timedelta(days=_STALE_WEIGHT_DAYS)
    last_weighed = dict(session.exec(
        select(Event.animal_id, func.max(Event.event_date))
        .where(Event.kind == EventKind.weight)
        .group_by(Event.animal_id)
    ).all())
    alive = session.exec(select(Animal).where(Animal.status == AnimalStatus.alive)).all()
    needs_weighing = [
        a for a in alive
        if last_weighed.get(a.id) is None or last_weighed[a.id] < cutoff
    ]

    return {
        "herd_counts": herd_counts,
        "total_alive": herd_counts[AnimalStatus.alive.value],
        "recent_events": recent_events,
        "needs_weighing": needs_weighing,
    }
