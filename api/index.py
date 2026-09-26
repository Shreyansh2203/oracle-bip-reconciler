"""Vercel serverless entry point.

Vercel's Python builder only discovers an ASGI application from a module inside ``api/``,
so this re-exports the real app from ``src.main``. It is a deliberate one-line shim, not a
second copy of the application: ``vercel.json`` rewrites every route to this module.
"""

from src.main import app  # noqa: F401

