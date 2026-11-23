"""ASGI entrypoint: ``uvicorn cloudsentinel.asgi:app``."""

from cloudsentinel.main import create_app

app = create_app()
