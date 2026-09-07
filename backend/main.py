import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from migrate import run_migrations
from routers import animals, dashboard, events
from security import TAILSCALE_ONLY_MESSAGE, is_tailscale_client

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


@app.get("/healthz")
def healthz():
    """Deliberately not gated below - install.ps1 polls this from the server
    machine itself, straight after start_server.bat launches it, to confirm
    the process came up before declaring the install done. Everything else
    in the app is Tailscale-only (see security.py), so that poll would
    otherwise fail on a perfectly healthy install running on someone's
    laptop with no Tailscale-facing address to hit yet. Returns nothing
    about the herd - just that the process is alive."""
    return {"status": "ok"}


@app.middleware("http")
async def kudde_is_not_on_the_farm_wifi(request: Request, call_next):
    """The whole app, gated the same way Boord gates just its Admin screens -
    see security.py for why Kudde draws that line differently. Applied here
    rather than per-router so the static frontend files (mounted below) are
    covered too, not only /api.
    """
    if request.url.path != "/healthz" and not is_tailscale_client(request):
        return PlainTextResponse(TAILSCALE_ONLY_MESSAGE, status_code=403)
    return await call_next(request)


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
