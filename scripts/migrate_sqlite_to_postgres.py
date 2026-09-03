"""Copy the production SQLite database into Postgres, table by table.

Run once during the cutover, against a fresh Postgres that `flask init-db` has already
created the schema on. It reads rows out of the SQLite file and writes them into the
same tables in Postgres, then verifies the counts match.

    python scripts/migrate_sqlite_to_postgres.py \\
        --sqlite /path/to/gluck.db \\
        --postgres postgresql://user:pass@host/db

Add `--dry-run` to report what WOULD be copied without writing anything.

Only `site_texts` is irreplaceable — it holds the copy edited from the admin. The
Tienda Nube mirror can be rebuilt at any time with a sync, and `products`/`product_media`
are the legacy admin catalogue, which production no longer reads (CATALOG_SOURCE is
`tiendanube`) but which is the documented rollback path, so it is copied too.

Refuses to write into a table that already has rows unless `--truncate` is passed: this
runs against a database that may already have been partially migrated, and silently
doubling `site_texts` would corrupt the site's copy in a way that is tedious to unpick.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.factory import _normalize_database_url  # noqa: E402

# Ordered so a table's foreign keys already exist when it is written.
TABLES = ("products", "product_media", "tiendanube_products", "site_texts")

# SQLite has no JSON type: these columns are TEXT holding JSON, and Postgres will reject
# that string for a json/jsonb column unless it is parsed back into a Python object.
JSON_COLUMNS = {
    "product_media": ("widths",),
    "tiendanube_products": ("variants", "images", "raw"),
}

# Likewise for booleans: SQLite stores 0/1 integers, Postgres wants real booleans.
BOOLEAN_COLUMNS = {
    "products": ("is_published",),
    "product_media": ("is_cover",),
    "tiendanube_products": ("published",),
}


def _read_table(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[dict]]:
    """Every row of `table` as dicts, or ([], []) when the table isn't in this file."""
    exists = conn.execute(
        "select 1 from sqlite_master where type='table' and name=?", (table,)
    ).fetchone()
    if not exists:
        return [], []
    cursor = conn.execute(f'select * from "{table}"')
    columns = [d[0] for d in cursor.description]
    return columns, [dict(zip(columns, row)) for row in cursor.fetchall()]


def _coerce(table: str, rows: list[dict]) -> list[dict]:
    """Turn SQLite's stand-ins back into the types Postgres actually declares."""
    json_columns = JSON_COLUMNS.get(table, ())
    boolean_columns = BOOLEAN_COLUMNS.get(table, ())
    for row in rows:
        for column in json_columns:
            value = row.get(column)
            if isinstance(value, (str, bytes)):
                try:
                    row[column] = json.loads(value)
                except (ValueError, TypeError):
                    row[column] = None
        for column in boolean_columns:
            if column in row and row[column] is not None:
                row[column] = bool(row[column])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="path to the gluck.db file")
    parser.add_argument("--postgres", required=True, help="target connection string")
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="delete existing rows in the target tables before copying",
    )
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    if not os.path.exists(args.sqlite):
        print(f"No existe el archivo SQLite: {args.sqlite}", file=sys.stderr)
        return 1

    import sqlalchemy

    source = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    engine = sqlalchemy.create_engine(_normalize_database_url(args.postgres))

    exit_code = 0
    try:
        with engine.begin() as target:
            existing_tables = set(sqlalchemy.inspect(engine).get_table_names())
            missing = [t for t in TABLES if t not in existing_tables]
            if missing:
                print(
                    f"Faltan tablas en el destino: {', '.join(missing)}. "
                    "Corré `flask init-db` primero.",
                    file=sys.stderr,
                )
                return 1

            for table in TABLES:
                columns, rows = _read_table(source, table)
                if not columns:
                    print(f"{table}: no está en el SQLite, se omite")
                    continue

                already = target.execute(
                    sqlalchemy.text(f'select count(*) from "{table}"')  # noqa: S608
                ).scalar_one()
                if already and not args.truncate:
                    print(
                        f"{table}: el destino ya tiene {already} filas. "
                        "Usá --truncate para reemplazarlas.",
                        file=sys.stderr,
                    )
                    exit_code = 1
                    continue

                if args.dry_run:
                    print(f"{table}: copiaría {len(rows)} filas")
                    continue

                if already and args.truncate:
                    target.execute(sqlalchemy.text(f'delete from "{table}"'))  # noqa: S608

                if rows:
                    _coerce(table, rows)
                    placeholders = ", ".join(f":{c}" for c in columns)
                    quoted = ", ".join(f'"{c}"' for c in columns)
                    statement = sqlalchemy.text(
                        f'insert into "{table}" ({quoted}) values ({placeholders})'  # noqa: S608
                    )
                    # Typed explicitly, or the driver infers from the Python value and
                    # sends a list as a Postgres ARRAY — which a json column rejects
                    # with "expression is of type smallint[]".
                    json_params = [c for c in JSON_COLUMNS.get(table, ()) if c in columns]
                    if json_params:
                        statement = statement.bindparams(
                            *[
                                sqlalchemy.bindparam(c, type_=sqlalchemy.JSON)
                                for c in json_params
                            ]
                        )
                    target.execute(statement, rows)

                copied = target.execute(
                    sqlalchemy.text(f'select count(*) from "{table}"')  # noqa: S608
                ).scalar_one()
                status = "OK" if copied == len(rows) else "DESAJUSTE"
                if copied != len(rows):
                    exit_code = 1
                print(f"{table}: {len(rows)} leídas -> {copied} escritas [{status}]")

            if not args.dry_run:
                # Every id came from SQLite, so Postgres' sequences still point at 1 and
                # the next insert would collide with a row we just copied. Skipped for
                # site_texts, whose primary key is the copy `key` and which has no id
                # column at all — asking for max(id) there is an error, not a no-op.
                inspector = sqlalchemy.inspect(engine)
                for table in TABLES:
                    if table not in existing_tables:
                        continue
                    if "id" not in {c["name"] for c in inspector.get_columns(table)}:
                        continue
                    target.execute(
                        sqlalchemy.text(
                            "select setval(pg_get_serial_sequence(:t, 'id'), "
                            f'coalesce((select max(id) from "{table}"), 1))'  # noqa: S608
                        ),
                        {"t": table},
                    )
    finally:
        source.close()
        engine.dispose()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
