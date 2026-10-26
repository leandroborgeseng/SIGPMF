from alembic import context
from sqlalchemy import create_engine, pool, text

from app.core.config import get_settings
from app.models import Base

target_metadata = Base.metadata
SCHEMAS = {"bronze", "silver", "gold", "controle"}


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in SCHEMAS
    return True


def run_migrations_online() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or get_settings().database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        # A tabela de versão do Alembic vive em controle; o schema precisa existir antes.
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS controle"))
        conn.commit()
        context.configure(connection=conn, target_metadata=target_metadata, include_schemas=True,
                          include_name=include_name, version_table_schema="controle")
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
