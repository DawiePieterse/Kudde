import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from migrate import run_migrations
from routers import animals, dashboard, events, settings, version
from security import TAILSCALE_ONLY_MESSAGE, is_tailscale_client
from version import prime as prime_version

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
app.include_router(settings.router)
app.include_router(version.router)

# The only thing that answers off Tailscale: proves the process is up
# without telling a farm-wifi device or an AnyDesk console anything about
# the herd. install.ps1's own post-install check depends on this - it runs
# before Tailscale is necessarily configured, from this PC's own console,
# which is otherwise refused exactly like anywhere else.
_HEALTH_PATH = "/healthz"


@app.get(_HEALTH_PATH)
def healthz():
    return {"status": "ok"}


@app.middleware("http")
async def kudde_is_not_on_the_farm_wifi(request: Request, call_next):
    """Every request - Field and Admin alike - is refused unless it arrived
    over Tailscale. See security.py for what "arrived over Tailscale" means
    and why it takes two signals to tell.

    A blanket check rather than one dependency per route: Field and Admin
    share almost the entire API (list/add animals, record events, the
    dashboard), so there is no non-admin slice of this app left to leave
    open - gating routes individually would mean remembering to add the
    dependency to every new endpoint rather than it being true by default.
    """
    if request.url.path == _HEALTH_PATH:
        return await call_next(request)
    if not is_tailscale_client(request):
        return PlainTextResponse(TAILSCALE_ONLY_MESSAGE, status_code=403)
    return await call_next(request)


@app.on_event("startup")
def on_startup():
    run_migrations()
    # Shell out to git once, here, rather than on every /api/version request -
    # and read it as of startup on purpose, so a checkout done by hand without
    # a restart shows up as a disagreement with the browser header instead of
    # quietly looking applied.
    prime_version()


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
