import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from migrate import run_migrations
from routers import animals, dashboard, events
from security import ADMIN_ONLY_MESSAGE, is_admin_client

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


@app.middleware("http")
async def admin_app_is_not_on_the_farm_wifi(request: Request, call_next):
    """The Admin screens themselves, guarded the same way as PATCH /api/animals.

    Most of what Admin shows (herd list, dashboard, event history) is the
    same data Field already reaches over farm wifi - so this isn't about
    hiding data the API doesn't already allow. It's about not handing a
    phone on the farm wifi a page whose only exclusive action (correcting a
    record) will 403 no matter what it taps, with nothing on screen to
    explain why. Blocking the static files says what is actually true, once,
    at the address bar.

    Matched on the path prefix rather than on the mounted app, because
    /admin/ is served by the catch-all StaticFiles mount at "/" - there is no
    separate mount to hang a dependency on. "/admin" itself is included:
    html=True redirects it to "/admin/", and a redirect is a perfectly good
    way in.
    """
    path = request.url.path
    if (path == "/admin" or path.startswith("/admin/")) and not is_admin_client(request):
        return PlainTextResponse(ADMIN_ONLY_MESSAGE, status_code=403)
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
