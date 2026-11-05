"""The Alembic migrations make exactly the tables that models.py describes."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from dfjc import create_app, db
from dfjc.db import Base


def test_upgrade_on_an_empty_database_matches_the_models(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "fresh.sqlite"), "DATABASE_URL": None})
    with app.app_context():
        db.upgrade_db()
        db.upgrade_db()  # running it again changes nothing
        with db.engine().connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
            tables = set(inspect(connection).get_table_names())
    # Nothing left for "alembic revision --autogenerate" to write: the
    # migrations and the models agree. If this fails after changing
    # models.py, make a new migration (see AGENTS.md).
    assert differences == []
    assert {"stores", "users", "products", "product_units", "alembic_version"} <= tables


def test_upgrade_db_command(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "cli.sqlite"), "DATABASE_URL": None})
    result = app.test_cli_runner().invoke(args=["upgrade-db"])
    assert result.exit_code == 0, result.output
    assert "up to date" in result.output


def test_the_database_refuses_impossible_stock(tmp_path):
    """Even with a bug in the code, stock can't go below zero (a CHECK in the database)."""
    import pytest
    from sqlalchemy.exc import IntegrityError
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "check.sqlite"), "DATABASE_URL": None})
    with app.app_context():
        db.upgrade_db()
        with db.engine().begin() as connection:
            connection.exec_driver_sql("INSERT INTO stores (id, name, tagline, address, phone, hours, pickup_note, "
                                       "is_demo, created_at) VALUES (1, 'S', '', '', '', '', '', 0, '2026-11-05')")
        with pytest.raises(IntegrityError), db.engine().begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO products (store_id, name, description, is_active, stock_pieces, reserved_pieces, "
                "low_stock_pieces, version, created_at, updated_at) "
                "VALUES (1, 'P', '', 1, 2, 5, 0, 1, '2026-11-05', '2026-11-05')")
