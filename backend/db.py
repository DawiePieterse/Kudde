import os

from sqlmodel import Session, create_engine

# Where the farm's data lives. A path in the environment overrides the
# default, which is the only way the test suite can point the whole app at a
# throwaway database: db, backup and migrate all read this at import time,
# and main imports them, so there is no later moment to redirect them from.
# (Boord Owner does the same thing through its backend/config.py.)
# On a farm nothing sets it and the default below is used.
DATA_DIR = os.environ.get("KUDDE_DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "kudde.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def get_session():
    with Session(engine) as session:
        yield session
