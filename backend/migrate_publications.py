"""Explicitly copy local component release snapshots to the configured shared bucket.

Run from the project root: .venv/Scripts/python.exe -m backend.migrate_publications
No package IDs, payloads, hashes or dependency references are rewritten.
"""
import json

from .app.components.publications import ComponentPublications
from .app.components.repository import DraftRepository
from .app.components.service import ComponentError
from .app.config import load_settings


def main():
    settings = load_settings()
    try:
        result = ComponentPublications(DraftRepository(settings.draft_db), settings.minio).migrate()
    except ComponentError as error:
        raise SystemExit(error.code) from None
    print(json.dumps({"migratedOrAlreadyShared": result["count"]}))


if __name__ == "__main__":
    main()
