"""Write the flows' configuration to the Prefect server from the environment (`.env`).

Run by `make prefect-deploy` (the `prefect-deploy` compose service) before `prefect deploy --all`.
Idempotent: every block and variable is overwritten with the current value. See config.py for
what goes where.
"""

from __future__ import annotations

import os
import sys

from prefect.blocks.system import Secret
from prefect.variables import Variable
from prefect_sqlalchemy import ConnectionComponents, SqlAlchemyConnector

from email_summarizer import config

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        sys.exit(f"bootstrap: {name} is not set (see .env.example)")
    return value


def _typed(raw: str, kind: type) -> object:
    if kind is bool:
        return raw.strip().lower() in _TRUE
    return kind(raw)


def main() -> None:
    SqlAlchemyConnector(
        connection_info=ConnectionComponents(
            driver="postgresql+psycopg",
            host=_env("APP_DB_HOST"),
            port=int(_env("APP_DB_PORT")),
            database=_env("APP_DB_NAME"),
            username=_env("APP_DB_USER"),
            password=_env("APP_DB_PASSWORD"),
        )
    ).save(config.APP_DB_BLOCK, overwrite=True)

    for block_name, env_name in config.SECRETS.items():
        Secret(value=_env(env_name)).save(block_name, overwrite=True)

    for variable_name, (env_name, kind) in config.VARIABLES.items():
        Variable.set(variable_name, _typed(_env(env_name), kind), overwrite=True)

    print(f"bootstrap: 1 database block, {len(config.SECRETS)} secret blocks, {len(config.VARIABLES)} variables")


if __name__ == "__main__":
    main()
