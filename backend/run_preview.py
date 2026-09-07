import os

# Local development only. There is no `tailscale serve` in front of this, so
# without it security.is_tailscale_client refuses the whole app to the
# developer's own browser exactly as it refuses a farm PC's own console.
# start_server.bat, which is what actually runs a real Kudde server, never
# sets this - see security.DEV_LOOPBACK_ENV.
os.environ.setdefault("KUDDE_ALLOW_LOOPBACK", "1")

_here = os.path.dirname(os.path.abspath(__file__))

import uvicorn
uvicorn.run("main:app", host="127.0.0.1", port=8811, app_dir=_here)
