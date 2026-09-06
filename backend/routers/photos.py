"""Photo capture and storage for animals.

Files live on disk under PHOTOS_DIR/<animal_id>/<uuid>.<ext> - the database
only ever stores that filename, never raw bytes, so the sqlite file stays
small and the photos remain plain files a farmer could browse directly if
the app ever went away.
"""
import mimetypes
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from db import PHOTOS_DIR, get_session
from models import Animal, AnimalPhoto

router = APIRouter(prefix="/api", tags=["photos"])

# Capped well above what a phone camera photo needs (a few MB) but far below
# anything that could fill the farm server's disk from one bad upload.
_MAX_PHOTO_BYTES = 15 * 1024 * 1024


def _animal_by_tag(session: Session, tag: str) -> Animal:
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag!r}")
    return animal


def _photo_out(photo: AnimalPhoto) -> dict:
    return {**photo.model_dump(), "url": f"/api/photos/{photo.id}/file"}


@router.post("/animals/{tag}/photos")
async def add_animal_photo(tag: str, file: UploadFile = File(...), caption: str = Form(""),
                            session: Session = Depends(get_session)):
    animal = _animal_by_tag(session, tag)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Only image uploads are accepted")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload")
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is too large")

    ext = mimetypes.guess_extension(file.content_type) or ".jpg"
    if ext == ".jpe":  # mimetypes' historical alias for jpeg - not what anything else expects
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"

    animal_dir = os.path.join(PHOTOS_DIR, str(animal.id))
    os.makedirs(animal_dir, exist_ok=True)
    with open(os.path.join(animal_dir, filename), "wb") as f:
        f.write(data)

    photo = AnimalPhoto(animal_id=animal.id, filename=filename,
                         content_type=file.content_type, caption=caption.strip())
    session.add(photo)
    session.commit()
    session.refresh(photo)
    return _photo_out(photo)


@router.get("/animals/{tag}/photos")
def list_animal_photos(tag: str, session: Session = Depends(get_session)):
    animal = _animal_by_tag(session, tag)
    photos = session.exec(
        select(AnimalPhoto).where(AnimalPhoto.animal_id == animal.id).order_by(AnimalPhoto.created_at.desc())
    ).all()
    return [_photo_out(p) for p in photos]


@router.get("/photos/{photo_id}/file")
def get_photo_file(photo_id: int, session: Session = Depends(get_session)):
    photo = session.get(AnimalPhoto, photo_id)
    if photo is None:
        raise HTTPException(404, "No such photo")
    path = os.path.join(PHOTOS_DIR, str(photo.animal_id), photo.filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "Photo file is missing on disk")
    return FileResponse(path, media_type=photo.content_type)


@router.delete("/photos/{photo_id}")
def delete_photo(photo_id: int, session: Session = Depends(get_session)):
    photo = session.get(AnimalPhoto, photo_id)
    if photo is None:
        raise HTTPException(404, "No such photo")
    path = os.path.join(PHOTOS_DIR, str(photo.animal_id), photo.filename)
    if os.path.isfile(path):
        os.remove(path)
    session.delete(photo)
    session.commit()
    return {"ok": True}
