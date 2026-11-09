"""update_server.bat: what has to stay true of the updater itself."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_updater_starts_the_new_file_after_the_checkout():
    """cmd reads a batch file off the disk as it runs, so once the checkout has
    replaced the updater it must start the new file from the top, as the last
    thing in that block: anything after it would be read at a stale byte of
    the new file (Boord Packhouse v0.66 ended "failed" that way, the server never restarted)."""
    with open(os.path.join(ROOT, "update_server.bat"), encoding="utf-8") as fh:
        text = fh.read()
    assert re.search(r'git checkout --force "!NEWTAG!"\n.*?\n    "%~f0" %\* --checked-out\n\)\n\n'
                     r':: The run that checked out is gone', text, re.S)
    # ...and a second time round stops, rather than checking out for ever.
    assert '"%%~A"=="--checked-out"' in text
    # ...and the rollback advice still names the release that was running.
    assert "HEAD@{1}" in text
