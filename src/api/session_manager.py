"""Automatically configures an API according to SQLAlchemy models in models.py

See utils.ApiExposed for information on what exposes a model to the API and
how to configure additional endpoints.

Author: Brenton Keats, 2023
"""

# Libraries
from flask import current_app as app
from flask_restx import Namespace, Resource
from werkzeug.exceptions import BadRequest

# Project-level modules
from auth import require_auth
from constants import AUTH_SUPER, AUTH_EDITOR, AUTH_VIEWER, DB_SESSION_TIMEOUT
from database import session_manager
from .models import session_response, session_action_response, new_session_model, session_objects_model, session_object_model
from .core import wrap_result

session_ns = Namespace('session', path='/session')

for model in (
    session_response,
    new_session_model,
    session_action_response,
    session_objects_model,
    session_object_model
):
    session_ns.model(model.name, model)


@session_ns.route('/new')
@session_ns.response(201, "DB session successfully started", session_response)
class StartSession(Resource):
    @require_auth(AUTH_EDITOR)
    def get(self):
        """Start a new DB session"""
        session_id, session = session_manager.get()
        app.logger.debug(f'Starting session with ID: {session_id}')
        return wrap_result(
            {
                'session_id': session_id
            },
            message=f"Successfully started new DB session. Cancelled after {DB_SESSION_TIMEOUT} minutes of inactivity."
        )


@session_ns.route('/save/<session_id>')
@session_ns.param('session_id', 'ID of session to commit all objects in')
@session_ns.response(200, "DB session successfully saved and closed.", session_action_response)
class CommitSession(Resource):
    @require_auth(AUTH_EDITOR)
    def get(self, session_id):
        """Commit all current changes in a session to the DB, and close the session."""
        app.logger.debug(f'Committing session with ID: {session_id}')
        try:
            result = session_manager.get_objects(session_id, as_dict=True)
            session_manager.commit_and_close(session_id)
            return wrap_result(
                result,
                message=f"Successfully committed all objects in session {session_id}. Session closed, actions recorded in `result`"
            )
        except ValueError as e:
            raise BadRequest(e.__str__())


@session_ns.route('/rollback/<session_id>')
@session_ns.param('session_id', 'ID of session to rollback all objects')
@session_ns.param('close', '(y/n) Whether or not to also close this session. Defaults to y')
@session_ns.response(200, "DB session successfully rolled back.", session_action_response)
class RollbackSession(Resource):
    @require_auth(AUTH_EDITOR)
    def get(self, session_id: str, close: str = 'y'):
        """Emit a ROLLBACK command for a DB session, undoing any current changes"""
        close = False if close.lower() == 'n' else True
        app.logger.debug(f'Rolling back session with ID: {session_id}')
        _, session = session_manager.get(session_id)
        try:
            result = session_manager.get_objects(session_id, as_dict=True)
            session.rollback()
            if close:
                session.close()
            return wrap_result(
                result,
                message=f"Successfully rolled back all objects in DB session {session_id}. Rolled-back actions recorded in `result`",
                session=session_id if not close else None
            )
        except ValueError as e:
            raise BadRequest(e.__str__())
