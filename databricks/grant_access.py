"""Provision and grant the deployed App's service principal access to A3 DockLab.

Use ``--bootstrap-only`` after deploying the Lakebase instance and before the
full bundle deploy. Run without it after the full deploy to grant the App's
service principal access. Both modes are idempotent.

This step covers what the bundle cannot express declaratively:

* Unity Catalog: the App queries Delta replay tables through the SQL Warehouse
  as its own service principal, so it needs ``USE CATALOG`` on the catalog and
  ``USE SCHEMA`` + ``SELECT`` on the schema.
* Lakebase Postgres: the App writes mutable state to the ``public`` schema of
  the ``a3docklab`` database. PostgreSQL 16 does not grant ``CREATE`` on
  ``public`` to non-owners, so the service principal's Postgres role needs an
  explicit ``USAGE, CREATE`` grant. The database itself is created here if it
  does not already exist.

All operations use the deploying user's credentials, which own the catalog and
the Lakebase instance and therefore hold the authority to grant.
"""

from __future__ import annotations

import argparse

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.catalog import PermissionsChange, Privilege, SecurableType


def _ensure_schema(workspace: WorkspaceClient, catalog: str, schema: str) -> None:
    try:
        workspace.schemas.create(name=schema, catalog_name=catalog)
        print(f"Created schema {catalog}.{schema}")
    except DatabricksError as error:
        if "already exists" not in str(error).lower():
            raise
        print(f"Schema {catalog}.{schema} already exists")


def _grant_unity_catalog(
    workspace: WorkspaceClient, catalog: str, schema: str, principal: str
) -> None:
    workspace.grants.update(
        SecurableType.CATALOG.value,
        catalog,
        changes=[PermissionsChange(principal=principal, add=[Privilege.USE_CATALOG])],
    )
    workspace.grants.update(
        SecurableType.SCHEMA.value,
        f"{catalog}.{schema}",
        changes=[
            PermissionsChange(
                principal=principal, add=[Privilege.USE_SCHEMA, Privilege.SELECT]
            )
        ],
    )
    print(f"Granted USE CATALOG + USE SCHEMA/SELECT to {principal}")


def _provision_lakebase(
    workspace: WorkspaceClient,
    instance_name: str,
    database_name: str,
    principal: str | None = None,
) -> None:
    import psycopg
    from psycopg import sql

    instance = workspace.database.get_database_instance(instance_name)
    credential = workspace.database.generate_database_credential(
        instance_names=[instance_name]
    )
    user = workspace.current_user.me().user_name
    connection_kwargs = {
        "host": instance.read_write_dns,
        "port": 5432,
        "user": user,
        "password": credential.token,
        "sslmode": "require",
    }

    # CREATE DATABASE cannot run inside a transaction; use autocommit.
    with psycopg.connect(dbname="databricks_postgres", autocommit=True, **connection_kwargs) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database_name,)
        ).fetchone()
        if not exists:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )
            print(f"Created Lakebase database {database_name!r}")
        else:
            print(f"Lakebase database {database_name!r} already exists")

    if principal is None:
        return

    with psycopg.connect(dbname=database_name, **connection_kwargs) as database:
        database.execute(
            sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                sql.Identifier(principal)
            )
        )
        database.commit()
    print(f"Granted USAGE, CREATE ON SCHEMA public to {principal}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog")
    parser.add_argument("--schema")
    parser.add_argument("--app-name")
    parser.add_argument("--database-instance-name", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--bootstrap-only", action="store_true")
    arguments = parser.parse_args()

    workspace = WorkspaceClient()
    if arguments.bootstrap_only:
        _provision_lakebase(
            workspace, arguments.database_instance_name, arguments.database_name
        )
        return

    if not arguments.catalog or not arguments.schema or not arguments.app_name:
        parser.error("--catalog, --schema, and --app-name are required unless --bootstrap-only")
    app = workspace.apps.get(arguments.app_name)
    principal = app.service_principal_client_id
    if not principal:
        raise RuntimeError(
            f"App {arguments.app_name!r} has no service principal yet; "
            "deploy the bundle before granting access"
        )

    _ensure_schema(workspace, arguments.catalog, arguments.schema)
    _grant_unity_catalog(workspace, arguments.catalog, arguments.schema, principal)
    _provision_lakebase(
        workspace, arguments.database_instance_name, arguments.database_name, principal
    )
    print(f"Access provisioned for App service principal {principal}")


if __name__ == "__main__":
    main()
