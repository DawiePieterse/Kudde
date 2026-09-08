from fastapi import APIRouter

from version import version_info

router = APIRouter(prefix="/api/version", tags=["version"])


@router.get("")
def get_version():
    """What release this server is running, and whether a newer one is out.

    Read by the Admin app so the farm PC can say "v0.2 is available" instead
    of nobody finding out for a season, and by whoever is standing in front
    of a tablet on the farm WiFi trying to work out why a screen looks
    wrong. It reports a tag, a schema revision and what the last update
    check found - nothing about an animal and nothing about a person.
    """
    return version_info()
