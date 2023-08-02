"""Automatically configures an API according to SQLAlchemy models in models.py

See utils.ApiExposed for information on what exposes a model to the API and
how to configure additional endpoints.

Author: Brenton Keats, 2023
"""

# Builtins
from dataclasses import field as dfield
from datetime import date, datetime
import json
import traceback
from typing import get_type_hints, Tuple, Callable, Type

# Libraries
from flask import request, current_app as app
from flask_restx import Namespace, Resource, fields, Model
from flask_sqlalchemy import BaseQuery
from psycopg2.errors import UniqueViolation
import regex as re
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm.exc import NoResultFound
from werkzeug.exceptions import HTTPException, BadRequest, NotFound, InternalServerError, Conflict

# Project-level modules
from auth import require_auth
from constants import MAX_PAGE_SIZE, AUTH_SUPER, AUTH_EDITOR, AUTH_VIEWER
from database import db, session_manager, models
from database.views import generic_search
from util import ApiLink, ApiExposed, CustomApiQuery, SqlMany

# Map python types to Flask API types for rendering models.
_pytype_to_flasktype = {
    str: fields.String,
    bool: fields.Boolean,
    int: fields.Integer,
    float: fields.Float,
    date: fields.Date,
    datetime: fields.DateTime,
    SqlMany: fields.List(fields.Integer),  # Render [one/many]-to-many fields as a list of IDs
    ApiLink: fields.List(fields.Nested(ApiLink._flask_model))  # List representation of class
}
# Configure general API models (subsequent definitions may inherit from previous ones)
generic_api_models = {
    "_links": ApiLink._flask_model,
    "record-base": Model("record-base", {
        "id": fields.Integer(example=0),
        "_links": fields.List(fields.Nested(ApiLink._flask_model))
    }),
    "info-base": Model("info-base", {
        "code": fields.Integer(example=200),
        "message": fields.String(example="Human-facing description of action result"),
        "session": fields.String(example="DB session ID for atomicity")
    }),
    "pagination": Model("pagination", {
        "page": fields.Integer(example=1),
        "total_pages": fields.Integer(example=1),
        "total_results": fields.Integer(example=10),
    }),
}
generic_api_models = {
    **generic_api_models,
    "base": Model("base", {
        "result": fields.Nested(generic_api_models['record-base']),
        "success": fields.Boolean(True),
        "info": fields.Nested(generic_api_models['info-base']),
    }),
    "paginated-info": generic_api_models['info-base'].clone('paginated-info', generic_api_models['pagination']),
}
generic_api_models = {
    **generic_api_models,
    'base-paginated': generic_api_models['base'].clone('base-paginated', {
        'result': fields.List(fields.Nested(generic_api_models['record-base'])),
        'info': fields.Nested(generic_api_models['paginated-info'])
    })
}


def handle_bad_request(exc: HTTPException) -> Tuple[dict, int]:
    """Handler for exceptions raised by the API. Converts exception to JSON response

    Note - saved in util to make it accessible for use in DB methods.

    Args:
        exc (HTTPException): Exception to be handled.

    Returns:
        (dict, int): JSON and response code
    """

    return wrap_result(
        None,
        success=False,
        code=exc.code,
        message=exc.description
    ), exc.code


def wrap_result(result, *, success: bool = True, code: int = 200, message: str = "Successfully retrieved data", session: str = None, **metadata):
    return {
        'result': result,
        'success': success,
        'info': {
            'code': code,
            'message': message,
            'session': session,
            **metadata
        }
    }


def handle_paginated_request(model: ApiExposed, record_format_func: Callable, **filter_args) -> dict:
    """Handles API requests for paginated lists of data.

    Args:
        model (ApiExposed): DB model to query
        record_format_func (Callable): One-arg method that formats record data for return.

    Raises:
        BadRequest: Invalid parameter in client request.

    Returns:
        dict: JSON response to return to client.
    """

    request_args = {**filter_args, **request.args.to_dict()}

    try:
        _page = int(request_args.pop('_page', 1))
        _pagelength = min(int(request_args.pop('_pagelength', 100)), MAX_PAGE_SIZE)
    except ValueError:
        raise BadRequest("Invalid pagination parameter! `_page` and `_pagelength` (if provided) must be integers.")
    _sortby = request_args.pop('_sortby', 'id')
    _query = request_args.pop('_query', None)
    if not hasattr(model, _sortby):
        raise BadRequest(f"Invalid sort-by parameter! `{_sortby}` is not a valid model attribute. {vars(model).get(_sortby, False)}")
    # Unused - passed by data-* endpoint
    _sort_asc = request_args.pop('_sort_asc', True)

    usr_session_id = request_args.pop('_session', None)
    session_id, session = session_manager.get(usr_session_id)

    errors = []
    query = BaseQuery(model, session=session)

    # Filter by any args appended to the URL. Note - arg name must exist in the model!
    for key, value in request_args.items():
        # For each, filter relation/attribute
        try:
            if key in model.Meta.queryable_relations:
                key = model.Meta.queryable_relations.get(key)
                query = query.filter(getattr(model, key).has(id=value))
            else:
                query = query.filter(getattr(model, key)==value)
        except AttributeError:
            errors.append(f"Attribute `{key}` not found")
    if errors:
        raise BadRequest(errors)

    if _query is not None:
        # Get a list of all the model attributes to filter against
        columns = [getattr(model, col) for col in model.__dataclass_fields__.keys()]
        query = generic_search(query, columns, _query)
    query = query.order_by(getattr(model, _sortby))
    results = query.paginate(page=_page, per_page=_pagelength)

    try:
        return wrap_result(
            [record_format_func(record) for record in results.items],
            page=results.page,
            total_pages=results.pages,
            total_results=query.count(),
            message=f"Successfully retrieved page ({results.page}/{results.pages}) of {model.Meta.endpoint_alias} data",
            session=usr_session_id
        )
    finally:
        if not usr_session_id:
            session_manager.commit_and_close(session_id)


def _register_api_model(namespace: Namespace, model: Type[ApiExposed], model_name: str, _recursed_models: list = []) -> Tuple[Model, Model, Model]:
    """Register an API model (JSON) for a DB model (SQLAlchemy) under a given name.

    Args:
        namespace (Namespace): API Namespace to register the model to.
        model (ApiExposed): DB model to generate the API model for.
        model_name (str): Name of the resulting API model in the namespace.
        _recursed_models (list): Names of models that have already been checked in this recursion.
                                 Used to prevent infinite recursion. Should not be used directly.

    Returns:
        (Model, Model, Model): Payload, response, and detailed-list response models.
    """

    assert model.Meta._api_exposed, f'`{model.__name__}` must be exposed to the API!'
    if model_name in _recursed_models:
        namespace.logger.warn(f'Recursive model definition generated. Halting loop: {" -> ".join(_recursed_models)}')
        return

    api_fields = {}  # Resulting API model dictionary

    # Select type-annotated fields on the model for use on the API
    model_fields = model.__dataclass_fields__

    # Add hybrid properties to the list of model fields, as long as they have also been type-annotated
    model_hybrid_properties = [p for p in inspect(model).all_orm_descriptors if isinstance(p, hybrid_property)]

    annotated_hybrid_properties = [i for i in model_hybrid_properties if get_type_hints(i).get('return', None)]
    for prop in annotated_hybrid_properties:
        # Confirm the hybrid property is configured for use on the API
        # Requires a class-level expression, or
        try:
            if prop.expr is None:  # No class-level expression has been defined
                prop.fget(model)   # Try fetching the property on the class
                # Success: Default property method is suitable for class-level use
        except AttributeError:
            # Type-annotated hybrid property attempts to access instance-specific values
            # without an explicit expression method. This is not allowed on the API.
            raise AssertionError(
                f'Hybrid property {model.__name__}.{prop.__name__} requires '
                f'instance-level attribute access, but does not implement a '
                f'SQLAlchemy expression at the class-level. Consider removing '
                f'this field from the API, or adding a method decorated with '
                f'@{prop.__name__}.expression that returns a scalar query result.'
            )
        except Exception as e:
            raise AssertionError(
                f'Hybrid property {model.__name__}.{prop.__name__} failed '
                f'to be configured for API use. Unexpected Error: {e}'
            )

        # Hybrid property is suitable for inclusion on the API. Configure field.
        f = dfield(default=prop)
        f.name = prop.__name__
        f.type = get_type_hints(prop).get('return', None)
        model_fields[prop.__name__] = f

    # Now that we've collated all the model attributes we want, it's time to actually generate the Flask RestX API model
    for key, field in model_fields.items():
        flask_type = None
        try:
            field_type = getattr(field, 'type', None) or field.get('type', None)
            if isinstance(field_type, str):
                type_strings = [cls.__name__ for cls in _pytype_to_flasktype.keys()]
                if field_type in type_strings:
                    field_type = eval(field_type)
            flask_type = _pytype_to_flasktype[field_type]
        except KeyError:
            # Type is not mapped - may be a model field.
            # Convert (if applicable) string-based typehint to actual class i.e. attr: 'SomeModel' -> attr: SomeModel.
            field_model = object
            if isinstance(field_type, type):
                field_model = field_type
            elif isinstance(field_type, str):
                field_model = vars(models)[field_type]

            # Only accept SQLAlchemy Model classes defined in the `models` module
            assert field_model.__module__ == 'models', f'API sub-model `{field_model.__qualname__}` must be defined in models.py'
            assert issubclass(field_model, db.Model), f'API sub-model `{field_model.__qualname__} must be a SQLAlchemy Model'

            field_name = field_model.__tablename__

            # Ensure the sub-model is registered in this API namespace
            if field_name not in namespace.models:
                try:
                    _register_api_model(namespace, field_model, field_name, _recursed_models + [model_name])
                except AssertionError as e:
                    # Pass the exception to the top of the recursion.
                    if _recursed_models:
                        raise
                    namespace.logger.error(f'Error generating model `{model_name}`. Field `{field_name}` excluded due to error: {e}')
                    continue

            # Build the Flask RestX representation of the field.
            flask_type = fields.Nested(namespace.models[field_name + '-payload'])
            if getattr(model, key).property.uselist is True:
                flask_type = fields.List(flask_type)

        assert flask_type is not None, f'Model field {model_name}.{key} failed type-annotation conversion to API field'

        # Now that we've converted the Python type to its respective Flask RestX field, add it to the model
        api_fields[key] = flask_type

    # Create a subset for use as request payloads i.e. only the editable fields
    payload_fields = {
        k: v                                                    # Keep fields...
        for k, v in api_fields.items()                          # ...from api_fields...
        if k not in [                                           # ...as long as they aren't...
            *model.Meta.read_only_fields,                       # ...flagged as read-only...
            *(i.__name__ for i in annotated_hybrid_properties if i.fset is None)  # ...or hybrid properties without setter methods
        ]
    }

    # Finally, register the models to the namespace
    payload = namespace.model(model_name + '-payload', payload_fields)
    # full_record = namespace.add_model(model_name, api_fields)
    full_record = namespace.inherit(model_name, namespace.models['record-base'], api_fields)
    response = namespace.clone(model_name + '-response', namespace.models['base'], {'result': fields.Nested(full_record)})
    detailed = namespace.clone(model_name + '-detailed', namespace.models['base-paginated'], {'result': fields.List(fields.Nested(full_record))})

    return payload, response, detailed


def _validate_args(model, payload_model) -> dict:
    """Confirm all of the POSTed arguments are registered on the payload model.

    Args:
        model (db.Model): SQLAlchemy model to confirm fields exist on
        payload_model (Model): Flask API model to confirm fields are listed on.

    Raises:
        BadRequest: If any arguments are invalid (don't exist on model, or are read-only (not in payload_model))

    Returns:
        dict: POSTed arguments, all of which correspond to an appropriate attribute.
    """

    errors = []
    posted_args = request.get_json(silent=True)
    if posted_args is None:
        if request.args:
            raise BadRequest("Update parameters should be passed as JSON in the request body.")
        else:
            raise BadRequest(f"No update parameters found. Dids you pass valid JSON in the request body?")

    for key in posted_args.keys():
        if not hasattr(model, key):
            errors.append(f"Attribute `{key}` not found on model.")
            continue
        if key not in payload_model:
            errors.append(f"Attribute `{key}` is read-only.")
            continue
    if errors:
        raise BadRequest(errors)
    return posted_args


def _api_routes(model: ApiExposed, endpoint_alias: str = None, url_prefix: str = "") -> Namespace:
    """Set up generic API routes for the model with automatically generated documentation:
        -> List
            -> GET many record references
            -> POST new record
        -> ListDetailed
            -> GET many record details
        -> One
            -> GET record data
            -> POST updates to record
            -> DELETE record

    Args:
        model (ApiExposed): SQLAlchemy model that has API metadata (i.e. decorated with @expose_to_api).
        endpoint_alias (str, optional): Name to use for the endpoint. Defaults to model classname.
        url_prefix (str, optional): Text to prefix to the URL i.e. for required params/parent models. Defaults to "".

    Generated endpoints may raise:
        NotFound: Resource or record not found
        BadRequest: Invalid client request data
        Conflict: Resource attempted to be created, but a conflicting record already exists (i.e. Unique constraints)
        InternalServerError: Unexpected error processing data

    Returns:
        Namespace: Collection of routes to be registered to an API.
    """

    if endpoint_alias is None:
        endpoint_alias = model.Meta.endpoint_alias
    url_prefix = (url_prefix or model.Meta.url_prefix).strip('/')

    _api = Namespace(endpoint_alias, path=f'{"/" if url_prefix else ""}{url_prefix}/{endpoint_alias}')

    for name, generic_model in generic_api_models.items():
        _api.model(name, generic_model)
    payload_model, response_model, detailed_model = _register_api_model(_api, model, endpoint_alias)

    @_api.route('/', endpoint=f'{endpoint_alias}-list')
    @_api.response(400, "Failure", "base")  # Set the default 400 response model to generic "error" model.
    @_api.doc(
        params={
            "_session": "(Optional) DB session to add changes to",
        }
    )
    class List(Resource):
        @classmethod
        @_api.doc(
            # List expected responses. format - `CODE: (description, modelname)`
            responses={
                400: "Filter invalid"  # Overrides the class-level 400 response.
            },
            # List expected parameters. format - `"param_name": "description"`
            params={
                "_page": "(Optional) Results page to view. Default 1",
                "_pagelength": "(Optional) Number of results to display per page. Default 100, maximum 1000"
            }
        )
        @_api.marshal_with(_api.models['base-paginated'], description="Successfully retrieved record references")
        @require_auth(AUTH_VIEWER)
        def get(cls):
            """Fetch a list of record IDs and their endpoint links"""
            app.logger.debug(f'{endpoint_alias}.list called')

            def format_record(record):
                return {
                    "id": record.id,
                    # Manually build a reference link, instead of returning `record._links`
                    '_links': [
                        ApiLink(
                            endpoint=f'api.{endpoint_alias}-one',
                            rel='view record data'
                        ).for_record(record)
                        .add_args(id=record.id)
                        .finalise()
                    ]
                }

            return handle_paginated_request(model, format_record)


        @classmethod
        @_api.expect(payload_model)
        @_api.marshal_with(response_model, code=201, description='Successfully created record')
        @require_auth(AUTH_EDITOR)
        def post(cls):
            """Create new record"""
            app.logger.debug(f'{endpoint_alias}.new called')
            try:
                posted_args = _validate_args(model, payload_model)

                debug_payload = posted_args.copy()
                for key, value in debug_payload.items():
                    if isinstance(value, str) and len(value) > 255:
                        debug_payload[key] = debug_payload[key][:255] + '... (truncated)'

                app.logger.debug(f'PAYLOAD (truncated): {json.dumps(debug_payload, indent=4)}')

                usr_session_id = request.args.get('_session', None)
                session_id, session = session_manager.get(usr_session_id)

                session.begin_nested()
                # Create the record, then set attributes
                record: ApiExposed = model()
                try:
                    record.ingest_payload(posted_args, usr_session_id=session_id)  # Transforms relational fields
                    app.logger.debug('Pre-add')
                    session.add(record)
                    session.commit()
                    session_manager.cache_record(session_id, 'CREATE', record)
                except IntegrityError as e:
                    session.rollback()
                    if isinstance(e.orig, UniqueViolation):
                        error_message = e.orig.diag.message_detail
                        if error_message.startswith("Key (") and error_message.endswith(' already exists.'):
                            key, value = re.findall(r'\((.*?)\)', error_message)
                            error_message = f'The "{key}" value must be unique, and another record with "{key}" = "{value}" already exists.'
                        raise Conflict(error_message)
                    raise
                # By now, either session.commit or session.rollback has been called; the SAVEPOINT is in a completed state.
            except (BadRequest, Conflict):
                raise
            except Exception as e:
                app.logger.error(f"Unexpected error in creating new {model} record: {e}")
                app.logger.debug(f'{"-"*40} BEGIN TRACEBACK {"-"*40}')
                app.logger.debug(traceback.format_exc())
                app.logger.debug(f'{"-"*41} END TRACEBACK {"-"*41}')
                raise InternalServerError(f"Unexpected error occurred: {type(e).__name__} {e}")

            try:
                record.finalise_links()
                return wrap_result(
                    record.json,
                    code=201,
                    message=f"Successfully created {endpoint_alias} record {record.id}",
                    session=usr_session_id
                )
            finally:
                if not usr_session_id:
                    session_manager.commit_and_close(session_id)


    @_api.route('/details', endpoint=f'{endpoint_alias}-list-detailed')
    @_api.response(400, "Failure", "base")  # Set the default 400 response model to generic "error" model.
    @_api.doc(
        # List expected responses. format - `CODE: (description, modelname)`
        responses={
            400: "Filter invalid"  # Overrides the class-level 400 response.
        },
        # List expected parameters. format - `"param_name": "description"`
        params={
            "_session": "(Optional) DB session to read results from"
        }
    )
    class ListDetailed(Resource):
        @classmethod
        @_api.doc(
            # List expected responses. format - `CODE: (description, modelname)`
            responses={
                400: "Filter invalid"  # Overrides the class-level 400 response.
            },
            # List expected parameters. format - `"param_name": "description"`
            params={
                "_page": "(Optional) Results page to view. Default 1",
                "_pagelength": "(Optional) Number of results to display per page. Default 100, maximum 1000",
                "_sortby": "(Optional) Record attribute to sort results by. Must appear on the model. Default 'id'",
                "_query": "(Optional) Text to require a match in _any_ model attribute. ",
            }
        )
        @_api.marshal_with(detailed_model, description="Successfully retrieved record references")
        @require_auth(AUTH_VIEWER)
        def get(cls, **kwargs):
            """Fetch data for many records at once."""
            app.logger.debug(
                f'{endpoint_alias}.details called'
                f'\nWith args: {kwargs}' if kwargs else ''
            )
            return handle_paginated_request(model, model.to_json, **kwargs)


    @_api.route('/<int:id>', endpoint=f'{endpoint_alias}-one')
    @_api.doc(
        params={
            'id': 'Unique identifier',
            "_session": "(Optional) DB session to use"
        },
        responses={
            400: ("Failure", "base")
        }
    )
    class One(Resource):
        """View, edit, or delete a record, given its identifier"""
        @classmethod
        @_api.marshal_with(response_model, description="Successfully retrieved record")
        @require_auth(AUTH_VIEWER)
        def get(cls, id, **kwargs):
            """View record"""
            app.logger.debug(
                f'{endpoint_alias}.one.get called (ID: {id})'
                f'\nWith args: {kwargs}' if kwargs else ''
            )
            try:
                usr_session_id = request.args.get('_session', None)
                session_id, session = session_manager.get(usr_session_id)

                record: ApiExposed = BaseQuery(model, session=session).filter(model.id == id).one()
                record.finalise_links(**kwargs)
            except NoResultFound:
                raise NotFound(f"Record with ID `{id}` not found")

            try:
                return wrap_result(
                    record.json,
                    message=f"Successfully retrieved {endpoint_alias} record {id}",
                    session=usr_session_id
                )
            finally:
                if not usr_session_id:
                    session_manager.commit_and_close(session_id)


        @classmethod
        @_api.expect(payload_model)
        @_api.marshal_with(response_model, description="Successfully updated record")
        @require_auth(AUTH_EDITOR)
        def post(cls, id):
            """Edit record"""
            app.logger.debug(f'{endpoint_alias}.one.edit called (ID: {id})')
            try:
                posted_args = _validate_args(model, payload_model)

                debug_payload = posted_args.copy()
                for key, value in debug_payload.items():
                    if isinstance(value, str) and len(value) > 255:
                        debug_payload[key] = debug_payload[key][:255] + '... (truncated)'

                app.logger.debug(f'PAYLOAD (truncated): {json.dumps(debug_payload, indent=4)}')

                usr_session_id = request.args.get('_session', None)
                session_id, session = session_manager.get(usr_session_id)

                session.begin_nested()
                try:
                    record: ApiExposed = BaseQuery(model, session=session).filter(model.id == id).one()
                except NoResultFound:
                    raise NotFound(f"Record with ID `{id}` not found")
                try:
                    record.ingest_payload(posted_args, usr_session_id=session_id)  # Update (and transform relational) passed attributes.
                    session.add(record)
                    session.commit()
                    session_manager.cache_record(session_id, 'UPDATE', record)
                except IntegrityError as e:
                    session.rollback()
                    if isinstance(e.orig, UniqueViolation):
                        error_message = e.orig.diag.message_detail
                        if error_message.startswith("Key (") and error_message.endswith(' already exists.'):
                            key, value = re.findall(r'\((.*?)\)', error_message)
                            error_message = f'The "{key}" value must be unique, and another record with "{key}" = "{value}" already exists.'
                        raise Conflict(error_message)
                    raise
            except (BadRequest, Conflict):
                raise
            except ValueError as e:
                raise BadRequest(str(e))
            except Exception as e:
                app.logger.error(f"Unexpected error in updating {model} record with ID {id}: {e}")
                app.logger.debug(f'{"-"*40} BEGIN TRACEBACK {"-"*40}')
                app.logger.debug(traceback.format_exc())
                app.logger.debug(f'{"-"*41} END TRACEBACK {"-"*41}')
                raise InternalServerError(f"Unexpected error occurred: {type(e).__name__} {e}")

            try:
                record.finalise_links()
                return wrap_result(
                    record.json,
                    message=f"Successfully updated {endpoint_alias} record {id}",
                    session=usr_session_id
                )
            finally:
                if not usr_session_id:
                    session_manager.commit_and_close(session_id)


        @classmethod
        @_api.marshal_with(_api.models['base'], description="Successfully deleted record")
        @require_auth(AUTH_SUPER)
        def delete(cls, id):
            """Delete record"""
            app.logger.debug(f'{endpoint_alias}.one.delete called (ID: {id})')

            usr_session_id = request.args.get('_session', None)
            session_id, session = session_manager.get(usr_session_id)

            session.begin_nested()
            try:
                record: ApiExposed = BaseQuery(model, session=session).filter(model.id == id).one()
                session.delete(record)
                session.commit()
                session_manager.cache_record(session_id, 'DELETE', record)
            except NoResultFound:
                raise NotFound(f"Record with ID {id} not found")
            except Exception as e:
                session.rollback()
                app.logger.error(f"Unexpected error in deleting {model} record with ID {id}: {e}")
                app.logger.debug(f'{"-"*40} BEGIN TRACEBACK {"-"*40}')
                app.logger.debug(traceback.format_exc())
                app.logger.debug(f'{"-"*41} END TRACEBACK {"-"*41}')
                raise InternalServerError(f"Unexpected error occurred: {type(e).__name__} {e}")
            try:
                return wrap_result(
                    record.json,
                    message=f"Successfully deleted {endpoint_alias} record {id}",
                    session=usr_session_id
                )
            finally:
                if not usr_session_id:
                    session_manager.commit_and_close(session_id)

    saved_custom_endpoints = {}
    for custom_endpoint in model.Meta.custom_endpoints:
        custom_endpoint: CustomApiQuery
        custom_endpoint.resource.get = require_auth(AUTH_VIEWER)(custom_endpoint.resource.get)

        # Manually apply API decorators to the resource
        decorated = \
            _api.doc(
                # List expected responses. format - `CODE: (description, modelname)`
                responses={
                    400: "Filter invalid"  # Overrides the class-level 400 response.
                },
                # List expected parameters. format - `"param_name": "description"`
                params={
                    "_page": "(Optional) Results page to view. Default 1",
                    "_pagelength": "(Optional) Number of results to display per page. Default 100, maximum 1000"
                }
            )(
                _api.route(f'/{custom_endpoint.name}', endpoint=f'{endpoint_alias}-{custom_endpoint.name}')(
                    custom_endpoint.resource
                )
            )

    return _api
