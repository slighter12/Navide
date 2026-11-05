"""The autouse fixtures close only the SQLite handles they opened themselves.

A test that reloads ``app`` while ``AGENT_TEAM_DATA_DIR`` is its ``tmp_path``
builds a new shared ``app.database`` inside that directory and injects it into
``pty_registry``. The data-dir fixture used to close whatever ``pty_registry``
held when its path sat under ``tmp_path``, which closed that shared instance and
broke every later test that reached ``app.database`` (``trust_store`` among them).

The two tests depend on running in file order: the first leaves the reloaded
app behind, the second is the "later test" that must still find it usable.
"""

import importlib

from agent_team_backend import app, pty_registry, trust_store


def test_reloaded_app_database_is_injected_into_the_registry():
    importlib.reload(app)

    assert pty_registry._db is app.database


def test_app_database_survives_the_previous_tests_fixtures():
    # Raises sqlite3.ProgrammingError("Cannot operate on a closed database.")
    # when an earlier fixture closed the instance the app shares.
    trust_store._reset_for_test()
    app.database.kv_set("conftest-ownership-probe", {"ok": True}, now=0)

    assert app.database.kv_get("conftest-ownership-probe") == {"ok": True}
