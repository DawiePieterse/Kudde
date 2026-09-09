"""Photo capture and storage for animals.

Files live on disk under PHOTOS_DIR/<animal_id>/<uuid>.<ext> - the database
only ever stores that filename, never raw bytes, so the sqlite file stays
small and the photos remain plain files a farmer could browse directly if
the app ever went away.

Every upload is re-encoded server-side (see _process_image) rather than
trusted to arrive pre-shrunk: a phone camera photo is routinely 3-8MB, and
that's true whether it came from the field app's own capture or a browser
file picker, so shrinking has to happen here to actually bound storage.
"""
import io
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlmodel import Session, select

from db import PHOTOS_DIR, get_session
from models import Animal, AnimalPhoto

router = APIRouter(prefix="/api", tags=["photos"])

# Capped well above what a phone camera photo needs (a few MB) but far below
# anything that could fill the farm server's disk from one bad upload.
_MAX_PHOTO_BYTES = 15 * 1024 * 1024

# Long edge, in pixels, after resizing - plenty to fill a phone or admin
# screen; a farm animal photo doesn't need to be printable. Everything is
# re-encoded as JPEG regardless of the source format, since that's what
# actually gets small file sizes for a photograph (unlike PNG, which a lot
# of phone/browser upload paths default to).
_MAX_DIMENSION = 1600
_JPEG_QUALITY = 82


def _animal_by_tag(session: Session, tag: str) -> Animal:
    animal = session.exec(select(Animal).where(Animal.tag == tag)).first()
    if animal is None:
        raise HTTPException(404, f"No animal with tag {tag!r}")
    return animal


def _photo_out(photo: AnimalPhoto) -> dict:
    return {**photo.model_dump(), "url": f"/api/photos/{photo.id}/file"}


def _shrink_to_jpeg(data: bytes) -> bytes:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise HTTPException(400, "Could not read that as an image")

    # A camera photo's pixels are usually stored "sideways" with an EXIF tag
    # telling the viewer to rotate it - transpose them for real now, since
    # re-encoding below discards that tag and would otherwise bake in a
    # photo that displays rotated everywhere from here on.
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")  # JPEG has no alpha channel
    image.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return out.getvalue()


@router.post("/animals/{tag}/photos")
async def add_animal_photo(tag: str, file: UploadFile = File(...), caption: str = Form(""),
                            client_uuid: Optional[str] = Form(None),
                            session: Session = Depends(get_session)):
    """Store a photo against an animal, once, however many times this is asked.

    Idempotent on client_uuid for the same reason create_event() is: the field
    app cannot tell a request that failed from one that succeeded slowly. It
    gives up after 8 seconds (Kudde.NETWORK_TIMEOUT_MS) and puts the upload
    back in its outbox, so a request that was slow but actually landed is
    replayed - and a several-MB photo over farm wifi crosses 8 seconds often,
    which makes this the ordinary case here rather than the rare one. Nothing
    else about a photo distinguishes that replay from a real second one: a
    farmer genuinely can take two pictures of the same animal a minute apart.

    The check sits before the re-encode and the disk write, so a replay costs
    a lookup rather than another pass through Pillow and another file.
    """
    animal = _animal_by_tag(session, tag)

    if client_uuid:
        already = session.exec(
            select(AnimalPhoto).where(AnimalPhoto.client_uuid == client_uuid)).first()
        if already is not None:
            # Answered with the photo that is already on file, so the device
            # takes it off its outbox rather than retrying forever.
            return _photo_out(already)

    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Only image uploads are accepted")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty upload")
    if len(data) > _MAX_PHOTO_BYTES:
        raise HTTPException(413, "Photo is too large")

    data = _shrink_to_jpeg(data)
    filename = f"{uuid.uuid4().hex}.jpg"

    animal_dir = os.path.join(PHOTOS_DIR, str(animal.id))
    os.makedirs(animal_dir, exist_ok=True)
    with open(os.path.join(animal_dir, filename), "wb") as f:
        f.write(data)

    photo = AnimalPhoto(animal_id=animal.id, filename=filename,
                         content_type="image/jpeg", caption=caption.strip(),
                         client_uuid=client_uuid or None)
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
