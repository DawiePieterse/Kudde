import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from migrate import run_migrations
from routers import animals, dashboard, events

app = FastAPI(title="Kudde")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(animals.router)
app.include_router(events.router)
app.include_router(dashboard.router)


@app.on_event("startup")
def on_startup():
    run_migrations()


class NoCacheStaticFiles(StaticFiles):
    """Same reasoning as Boord's: field/admin devices keep the page open for
    days, so without this a browser can silently keep serving JS/HTML from
    before the last update."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
