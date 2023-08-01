"""Core database frameworks and queries

Author: Brenton Keats, 2023
"""

# Builtins
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Tuple
import uuid

# Libraries
from flask_sqlalchemy import SQLAlchemy, Model
from sqlalchemy import Column, Integer
from sqlalchemy.orm import Session, sessionmaker, scoped_session

# Project-level modules
from constants import DB_SESSION_TIMEOUT

@dataclass
class BaseModel(Model):
    id: int = Column(Integer, primary_key=True)


class SessionManager:
    """Handles custom-scoped DB sessions i.e. for persisting sessions across client requests.
    """

    def __init__(self, db: SQLAlchemy, timeout: int = DB_SESSION_TIMEOUT):
        """Create a SessionManager bound to a specific DB instance.

        Args:
            db (SQLAlchemy): SQLAlchemy DB instance to bind this class instance to.
        """

        self.timeout_limit = timedelta(minutes=timeout)
        self.__db: SQLAlchemy = db
        self.__session: scoped_session = None
        self.__open_sessions: dict = {}
        self.__last_action_timestamps = {}
        self.__last_timeout_check = datetime.now()
        self._objects = {}

    @property
    def session(self):
        """Generates/returns a SQLAlchemy.scoped_session (call to get a DB session).

        Returns:
            scoped_session: SQLAlchemy.session generator to be called.
        """

        if not self.__session:
            __sessionmaker = sessionmaker(self.__db.get_engine())
            self.__session = scoped_session(__sessionmaker)
        return self.__session

    def _do_timeout(self, session_id: str):
        """Handle a timeout for a session. Rolls back any pending actions and cleans up the instance.

        Args:
            session_id (str): ID of existing session object.
        """

        session = self.__open_sessions[session_id]
        session.rollback()
        session.close()
        self._cleanup_session(session_id)

    def _check_timeouts(self):
        """Check current sessions and timeout any that have breanched the limit.
        """

        now = datetime.now()

        # Debounce timeout checks to occur at most, once per timeout limit.
        if now - self.__last_timeout_check < self.timeout_limit:
            return

        for session_id, last_action in self.__last_action_timestamps.copy().items():
            if now - last_action >= self.timeout_limit:
                self._do_timeout(session_id)
        self.__last_timeout_check = now

    def _cleanup_session(self, session_id: str):
        """Remove all metadata in this instance related to a session.

        Args:
            session_id (str): ID of existing session object.

        Raises:
            ValueError: Supplied ID does not match any open session.
        """

        if session_id not in self.__open_sessions:
            raise ValueError(f"No DB session was found with ID '{session_id}'")
        self.__open_sessions.pop(session_id)
        self.__last_action_timestamps.pop(session_id)
        self._objects.pop(session_id, None)

    def get(self, session_id: str = None) -> Tuple[str, Session]:
        """Get a DB session, optionally an existing one specified by its object ID.

        Args:
            session_id (str, optional): ID of existing session object. Defaults to None.

        Raises:
            ValueError: Supplied ID does not match any open session.

        Returns:
            Tuple[str, Session]: Session ID, and SQLAlchemy session
        """

        session = None

        # TODO: Check session timeouts on a set interval instead of on request.
        self._check_timeouts()

        if session_id is not None:
            if session_id in self.__open_sessions:
                session = self.__open_sessions[session_id]
            else:
                raise ValueError(f"No active DB session was found with ID '{session_id}'")
        else:
            session = self.session()
            session_id = uuid.uuid4().hex
            self.__open_sessions[session_id] = session

        self.__last_action_timestamps[session_id] = datetime.now()

        return session_id, session

    def commit_and_close(self, session_id: str):
        """Commits all objects in a session and removes it from the session manager instance.

        Args:
            session_id (str): Internal `id` of the session object.

        Raises:
            ValueError: Supplied ID does not match any open session.
        """

        if session_id not in self.__open_sessions:
            raise ValueError(f"No DB session was found with ID '{session_id}'")

        session: Session = self.__open_sessions[session_id]
        session.commit()
        session.close()
        self._cleanup_session(session_id)

    def cache_record(self, session_id: str, action: str, record: Model):
        """Save a reference to a SQLAlchemy object for use when returning session data.

        Note this appears to already have an implementation in SQLAlchemy.Session with attributes
        `new`, `dirty`, and `deleted`, but these attributes are empty in our instance.

        Args:
            session_id (str): ID of the session to cache a record for
            action (str): 'CREATE', 'UPDATE', or 'DELETE'
            record (Model): Object to save a reference to

        Raises:
            ValueError: Session ID not valid
            TypeError: Record is not exposed to the API
        """

        if session_id not in self._objects:
            self._objects[session_id] = {
                'CREATE': [],
                'UPDATE': [],
                'DELETE': []
            }
        if action not in ('CREATE', 'UPDATE', 'DELETE'):
            raise ValueError("Action must be one of ('CREATE', 'UPDATE', 'DELETE')")
        try:
            obj = {
                'TYPE': record.__class__.Meta.endpoint_alias,
                'DATA': record
            }
        except AttributeError:
            # Shouldn't be possible as this is only used by API methods.
            raise TypeError(f'Record of type {record.__class__} may not be exposed to the API.')
        if obj not in self._objects[session_id][action]:
            self._objects[session_id][action].append(obj)

    def get_objects(self, session_id: str, as_dict: bool = False) -> dict:
        """Fetch a list of objects added/changed/deleted in a DB session.

        Args:
            session_id (str): ID of session to fetch objects for.
            as_dict (bool, optional): Toggle to encode record objects to dict. Defaults to False.

        Returns:
            dict: Collection of DB objects, sorted into groups CREATE, UPDATE, and DELETE.
        """

        result = self._objects.get(session_id, {})
        if as_dict:
            # Convert the results to their JSON/dict representation.
            ## This requires JSONEncoder injection to properly convert the
            ## Models (done here in util.py) using the custom `to_json` method
            result = json.loads(json.dumps(result))
        return result


db = SQLAlchemy(model_class=BaseModel)
session_manager = SessionManager(db)
