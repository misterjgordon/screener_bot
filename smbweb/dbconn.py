"""Direct Postgres access for bulk loads (e.g. pandas to_sql), mirroring jambot dbconn."""

import os
import re
import threading
import warnings
from functools import cached_property
from urllib.parse import quote

import sqlalchemy as sa
from django.conf import settings

warnings.filterwarnings('ignore', 'pandas only supports SQLAlchemy')


class DB:
    """SQLAlchemy engine built from Django DATABASES (same DB as the ORM)."""

    def __init__(self, db_name: str = 'default') -> None:
        self.db_name = db_name

    @cached_property
    def thread_num(self) -> str:
        match = re.search(r'\d+', threading.current_thread().name)
        return match.group() if match else '0'

    @cached_property
    def thread_identity(self) -> str:
        return f'{os.getpid()}-{threading.get_native_id()}'

    def _con_from_django_settings(self, db_name: str) -> dict[str, str]:
        """Normalize Django DB config keys to lowercase for the URL template.

        NAME (e.g. database_smb), USER, PASSWORD, HOST, PORT come from
        smbweb.settings.DATABASES — same database the ORM uses.
        """
        m = settings.DATABASES[db_name]
        return {k.lower(): v for k, v in m.items()}

    @cached_property
    def con_str(self) -> str:
        m = self._con_from_django_settings(self.db_name)
        m['engine'] = 'postgresql'
        m['application_name'] = f'smbweb-{self.thread_num}-{self.thread_identity}'
        query_timeout = os.getenv('QUERY_TIMEOUT', '60000')
        m['options'] = quote(f'-c statement_timeout={query_timeout}', safe='')

        return (
            '{engine}://{user}:{password}@{host}:{port}/{name}'
            '?application_name={application_name}&options={options}'
        ).format(**m)

    @cached_property
    def engine(self) -> sa.Engine:
        con_str = self.con_str.replace('postgresql', 'postgresql+psycopg')
        return sa.create_engine(con_str)


db = DB()
