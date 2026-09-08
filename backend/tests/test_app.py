"""The server as a farm device meets it: the screens it serves and the
headers it serves them with."""
import os
import re

import pytest

FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "..", "frontend")


@pytest.mark.parametrize("path", ["/", "/field/", "/admin/"])
def test_every_screen_the_installer_prints_actually_loads(client, path):
    """/field/ and /admin/ are the two addresses install.ps1 prints. "/" is
    the one somebody actually types into a phone on the farm wifi, and it
    used to be a bare 404 from the static mount - which reads as "the server
    is down" rather than "you are one word short"."""
    r = client.get(path)
    assert r.status_code == 200, path
    assert "text/html" in r.headers["content-type"]


def test_the_landing_page_points_at_both_apps(client):
    body = client.get("/").text
    assert 'href="field/"' in body and 'href="admin/"' in body


def test_static_files_are_never_cached_by_a_browser(client):
    """Field and admin devices keep the page open for days, so without this a
    browser can silently keep serving JS/HTML from before the last update."""
    for path in ("/field/", "/field/app.js", "/shared/api.js"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path


def test_the_api_is_not_swallowed_by_the_static_mount(client):
    """StaticFiles is mounted at "/" and is a catch-all, so an /api route
    that stopped being registered would come back as a 404 from the file
    mount rather than an obvious error."""
    r = client.get("/api/animals")
    assert r.status_code == 200 and r.json() == []
    assert "application/json" in r.headers["content-type"]


def _service_worker_shell(name):
    with open(os.path.join(FRONTEND, name, "service-worker.js"), encoding="utf-8") as fh:
        body = fh.read()
    listed = re.search(r"const SHELL = \[(.*?)\];", body, re.S).group(1)
    return body, re.findall(r'"([^"]+)"', listed)


@pytest.mark.parametrize("app", ["field", "admin"])
def test_every_cached_shell_file_exists(app, client):
    """cache.addAll() is atomic: one 404 in the list and the whole install
    fails, leaving the device with no offline shell at all and no error
    anybody sees. A renamed or deleted file is exactly how that happens."""
    _, shell = _service_worker_shell(app)
    for entry in shell:
        if entry == "./":
            continue
        served = os.path.normpath(os.path.join("/", app, entry))
        assert client.get(served).status_code == 200, f"{app}: {entry} is cached but not served"


@pytest.mark.parametrize("app", ["field", "admin"])
def test_the_icon_font_is_cached_not_just_its_stylesheet(app):
    """Every control on these screens is a Font Awesome glyph - the event
    buttons, the offline banner's wifi symbol, the activity feed. A shell
    cached without the font file loads offline as a page of empty boxes."""
    _, shell = _service_worker_shell(app)
    assert any(entry.endswith("fa-solid-900.woff2") for entry in shell)


@pytest.mark.parametrize("app", ["field", "admin"])
def test_the_shell_is_refreshed_as_one_unit(app):
    """Writing each revalidated file back on its own can leave a device
    holding one release's index.html beside another release's app.js: the
    older JavaScript reaches for an element the newer HTML no longer has,
    throws before anything renders, and the screen paints white. It happened
    to Boord on a farm server on 2026-09-04 and was indistinguishable from
    the server being down."""
    body, _ = _service_worker_shell(app)
    assert "refreshShell" in body
    assert "REVALIDATE_TIMEOUT_MS" in body, "background revalidation needs a deadline"


def test_both_apps_report_the_same_version(client):
    """Each screen prints this in its header, and staleness is only visible
    because of it - so the two apps must not drift apart into two numbers."""
    api_js = client.get("/shared/api.js").text
    assert re.search(r'VERSION:\s*"[\d.]+"', api_js), "no version to print"
