"""The herd and its history as CSV files - what the accountant, the vet
and the stud society ask for, and the one format every one of them can
open. Two files, animals and events, each a plain table with a header row.

UTF-8 with a byte-order mark, because that is what makes Excel on a
Windows PC read "Noordkamp" with its diacritic-bearing cousins correctly
instead of as mojibake; every other reader ignores the mark. Commas, not
semicolons: a South African Excel set to a comma decimal will offer the
import wizard either way, and every other tool expects commas.
"""
import csv
import io
from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlmodel import Session, select

from db import get_session
from herd import herd_context
from models import Animal, Event

router = APIRouter(prefix="/api/export", tags=["export"])


def _csv_response(filename: str, header: list, rows) -> Response:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    stamped = f"{filename}-{date.today().isoformat()}.csv"
    return Response(
        content="\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{stamped}"'},
    )


@router.get("/animals.csv")
def export_animals(session: Session = Depends(get_session)):
    ctx = herd_context(session)

    def row(a: Animal):
        d = ctx.describe(a)
        return [a.tag, a.name, a.breed, a.sex.value, d["class"],
                "yes" if a.castrated else "", a.birth_date, a.sire_tag, a.dam_tag, a.status.value,
                d["camp"] or "", d["last_weight"], d["last_weight_date"], d["withdrawal_until"]]

    return _csv_response("kudde-animals",
                         ["tag", "name", "breed", "sex", "class", "castrated", "birth_date", "sire_tag",
                          "dam_tag", "status", "camp", "last_weight_kg", "last_weight_date",
                          "withdrawal_until"],
                         (row(a) for a in ctx.animals))


@router.get("/events.csv")
def export_events(session: Session = Depends(get_session)):
    """Every event, oldest first, with the animal's tag and name on each
    row so the file reads on its own without a join."""
    rows = session.exec(
        select(Event, Animal.tag, Animal.name)
        .join(Animal, Animal.id == Event.animal_id)
        .order_by(Event.event_date, Event.id)
    ).all()
    return _csv_response("kudde-events",
                         ["tag", "name", "kind", "event_date", "weight_kg", "camp", "product", "dose",
                          "withdrawal_days", "note", "temp_min_c", "temp_max_c", "rain_mm", "recorded_at"],
                         ([tag, name, e.kind.value, e.event_date, e.value, e.location, e.product, e.dose,
                           e.withdrawal_days, e.note, e.weather_temp_min, e.weather_temp_max,
                           e.weather_precipitation, e.created_at.isoformat(timespec="seconds")]
                          for e, tag, name in rows))
