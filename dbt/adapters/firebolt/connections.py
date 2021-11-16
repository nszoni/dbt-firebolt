from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional, Dict, List, Any, Iterable
from urllib.parse import quote, urlencode
import os
import json
import agate

import dbt.exceptions
from dbt.adapters.base import Credentials
from dbt.contracts.connection import AdapterResponse
from dbt.adapters.sql import SQLConnectionManager
from dbt.clients.agate_helper import table_from_rows

from firebolt.db import connect
from firebolt.client import DEFAULT_API_URL

@dataclass
class FireboltCredentials(Credentials):
    # These values all come from either profiles.yml or dbt_project.yml.
    user: str
    password: str
    api_endpoint: Optional[str] = DEFAULT_API_URL
    driver: str = 'com.firebolt.FireboltDriver'
    engine: Optional[str] = None
    account: Optional[str] = None

    @property
    def type(self):
        return 'firebolt'

    def _connection_keys(self):
        """
        Return list of keys (i.e. not values) to display
        in the `dbt debug` output.
        """
        return ('api_endpoint', 'user', 'engine', 'database',
                'account', 'schema')

    @property
    def unique_field(self):
        """
        Return a field that can be hashed to uniquely identify one
        team/organization building with this adapter. This is called by
        `hashed_unique_field()`.
        """
        # Is this safe, or is it too much information? It should only be
        # called by `hashed_unique_field()` as stated in the docstring,
        # but I'm asking here for noting in the PR of this branch.
        return self.engine

class FireboltConnectionManager(SQLConnectionManager):
    """Methods to implement:
        - exception_handler
        - cancel_open
        - open
        - begin
        - commit
        - clear_transaction
        - execute
    """
    TYPE = 'firebolt'

    @classmethod
    def open(cls, connection):
        if connection.state == 'open':
            return connection
        credentials = cls.get_credentials(connection.credentials)

        try:
            # Create a connection based on provided credentials.
            connection.handle = connect(
                engine_name=credentials.engine,
                database=credentials.database,
                username=credentials.user,
                password=credentials.password,
                api_endpoint=credentials.api_endpoint,
                account_name=credentials.account,
            )
            connection.state = 'open'
        except Exception as e:
            connection.handle = None
            connection.state = 'fail'
            # If we get a 502 or 503 error, maybe engine isn't running.
            if '502' in f'{e}' or '503' in f'{e}':
                if credentials.engine is None:
                    engine = 'default'
                    error_msg_append = ('\nTo specify a non-default engine, '
                    'add an engine field into the appropriate target in your '
                    'profiles.yml file.')
                else:
                    engine = credentials.engine
                    error_msg_append = ''
                raise EngineOfflineException(
                    f'Failed to connect to the database. Is the {engine} engine for '
                    + f'{credentials.database} running? '
                    + error_msg_append
                )

            raise dbt.exceptions.FailedToConnectException(str(e))
        return connection

    @contextmanager
    def exception_handler(self, sql: str):
        try:
            yield
        except Exception as e:
            self.release()
            raise dbt.exceptions.RuntimeException(str(e))

    # TODO: Decide how much metadata we want to return.
    # For now, returning "_message" hard-coded as "OK", and
    # the rows_affected, which I suspect isn't working properly.
    @classmethod
    def get_response(cls, cursor) -> AdapterResponse:
        """
        Return adapter-specific information about the last executed
        command. Ideally, the return value is an AdapterResponse object
        that includes items such as code, rows_affected, bytes_processed,
        and a summary _message for logging to stdout.
        """
        return AdapterResponse(
            # TODO: get an actual status message and "code" from the cursor.
            _message='OK',
            rows_affected=cursor.rowcount
        )

    def begin(self):
        """
        Passing `SQLConnectionManager.begin()` because
        Firebolt does not yet support transactions.
        """
        pass

    def commit(self):
        """
        Passing `SQLConnectionManager.begin()` because
        Firebolt does not yet support transactions.
        """
        pass

    @classmethod
    def get_credentials(cls, credentials):
        return credentials

    def cancel(self, connection):
        """Cancel the last query on the given connection."""
        raise dbt.exceptions.NotImplementedException(
            '`cancel` is not implemented for this adapter!'
        )

    @classmethod
    def get_status(cls, cursor):
        return 'OK'


class EngineOfflineException(Exception):
    CODE = 10003
    MESSAGE = 'Connection Error'

    def process_stack(self):
        lines = []

        if hasattr(self.node, 'build_path') and self.node.build_path:
            lines.append(f'compiled SQL at {self.node.build_path}')

        return lines + RuntimeException.process_stack(self)

    @property
    def type(self):
        return 'firebolt'
