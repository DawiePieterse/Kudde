from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from classes import CLASSES, animal_class
from db import get_session
from herd import camp_groups, herd_context
from models import Animal, AnimalStatus, Event
from tags import tag_sort_key

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

    ctx = herd_context(session)
    alive = ctx.alive   # already in tag order: a worklist the farmer walks down at the crush

    cutoff = date.today() - timedelta(days=_STALE_WEIGHT_DAYS)
    needs_weighing = [a for a in alive
                      if a.id not in ctx.weights or ctx.weights[a.id][1] < cutoff]

    # Alive animals still inside a withdrawal period, soonest clear first:
    # the ones that must not go to the sale this week.
    in_withdrawal = sorted(
        ({"tag": a.tag, "name": a.name, "until": ctx.withdrawal[a.id]}
         for a in alive if a.id in ctx.withdrawal),
        key=lambda row: (row["until"], tag_sort_key(row["tag"])),
    )

    # The live herd as a farmer counts it: so many cows, so many heifers.
    # Every class is present, at zero if need be, so the card is stable.
    class_counts = {name: 0 for name in CLASSES}
    for a in alive:
        class_counts[animal_class(a, a.id in ctx.calved)] += 1

    # Where the live herd is, camp by camp, in Settings order - the same
    # groups the Move picker shows, reduced to a count. The None row is the
    # animals with no camp recorded.
    camp_counts = [{"camp": name, "alive": len(members)}
                   for name, members in camp_groups(session, alive, ctx.locations)]

    return {
        "herd_counts": herd_counts,
        "class_counts": class_counts,
        "camp_counts": camp_counts,
        "in_withdrawal": in_withdrawal,
        "total_alive": herd_counts[AnimalStatus.alive.value],
        "recent_events": recent_events,
        "needs_weighing": needs_weighing,
    }
