"""Test package marker.

Some tests import shared fakes via ``tests.conftest``. Keeping ``tests`` as an
explicit package makes that import stable for both ``python -m pytest`` and the
direct ``.venv/bin/pytest`` console-script launcher.
"""
