"""Contains utility methods used throughout the application.

This is a placeholder file that configures basic models showcasing what can be done and how to define

Author: Brenton Keats, 2023
"""

# Builtins
from dataclasses import dataclass

# Libraries
from flask import current_app as app, url_for, request
from flask_restx import Model, fields, Resource
from werkzeug.exceptions import BadRequest

# Project-level modules
from database import db, session_manager
from constants import MAX_PAGE_SIZE


class SqlMany:
    """Reference class for rendering relational data in the API. No functional implementation.
    Should be used to annotate an x-to-many relationship attribute.
    """

    pass


class ApiLink:
    _flask_model = Model("_links", {
        "href": fields.String("URL slug"),
        "rel": fields.String("Description"),
        "method": fields.String("HTTP method to use"),
    })

    def __init__(self, endpoint: str, rel: str = "link", method: str = "GET", **record_args) -> None:
        """Preloads metadata for a Flask-based endpoint, ready to be called later for URL generation.

        Args:
            endpoint (str): Flask endpoint name. Typically of the form "api.MODEL-etc" i.e. "api.project-list"
            rel (str): Describes how the endpoint is related to this record.
            method (str, optional): Describes expected request method to be used - has no functional impact. Defaults to "GET".
            record_args: Record-specific arguments to be used in link generation.
                         Each kwarg should be of the form `endpoint_variable_name = "record_attribute_name"`.
                         Note: Any arguments set here will override kwargs with the same name passed to `__call__`.
        """

        self.endpoint: str = endpoint
        self.rel: str = rel
        self.method: str = method
        self.record_args: dict = record_args
        self.record: db.Model = None
        self.kwargs: dict = {}

    def for_record(self, record: db.Model) -> 'ApiLink':
        """Register a record to load keyword arguments from later.

        Args:
            record (db.Model): Record to load data from.

        Returns:
            ApiLink: self
        """

        self.record = record
        return self

    def add_args(self, **kwargs) -> 'ApiLink':
        """Register any keyword arguments to be loaded later.

        Args:
            kwargs: Additional arguments to use for generating the URL. Arguments here will be overwritten
            by any record-specific arguments defined at instantiation with the same name.

        Returns:
            ApiLink: self
        """

        self.kwargs.update(kwargs)
        return self

    def finalise(self) -> dict:
        """Generate the endpoint URL. Requires `self.record` to be set.

        Args:
            kwargs: Additional arguments to use for generating the URL. Arguments here will be overwritten
            by any record-specific arguments defined at instantiation with the same name.

        Returns:
            dict: Generated link, along with metadata for it, in the form:
            {
                "href": generated_link,
                "rel": self.rel,
                "method": self.method
            }
        """

        result = {}
        try:
            if self.record is None:
                raise ValueError('Record has not been defined for ApiLink!')
            for k, v in self.record_args.items():
                self.kwargs[k] = getattr(self.record, v)
            # Ensure an endpoint alias is set - should be done sooner, but can't find a time where the model name is available.
            if self.record.Meta.endpoint_alias is None:
                self.record.Meta.endpoint_alias = self.record.__class__.__name__.lower()
            endpoint = self.endpoint.format(**self.record.Meta.__dict__)
            result = {
                "href": url_for(endpoint, **self.kwargs),
                "rel": self.rel,
                "method": self.method
            }
        except Exception as e:
            # Link data is sometimes incomplete. Catch the exception and pass it to the result instead.
            result = {
                "href": f"Error generating link: {e.__class__.__name__} {e} {e.args}",
                "rel": None,
                "method": None
            }
        finally:
            return result

    def __repr__(self) -> str:
        return f'<ApiLink {self.method} {self.rel} - {self.endpoint}>'


@dataclass
class ApiExposed:
    """Defines common properties and defaults for models that are exposed to the API. Note that this class
    is intended for use in the @expose_to_api decorator method defined below.

    Any additional properties here will also need to be explicitly added to the decorated class in expose_to_api.
    """

    class Meta:
        """Default values for the API metadata. This should be overwritten in cls._config"""
        _api_exposed: bool = True
        _links: tuple = (
            ApiLink('api.{endpoint_alias}-one', 'edit record', 'POST', id='id'),
            ApiLink('api.{endpoint_alias}-one', 'delete record', 'DELETE', id='id')
        )
        auto_api_endpoints: bool = True
        endpoint_alias: str = None
        queryable_relations: dict = {}
        read_only_fields: tuple = ('id', '_links')
        url_prefix: str = ""
        custom_endpoints: tuple = tuple()


    @property
    def json(self) -> dict:
        """ETL DB -> JSON response.
        Transforms relational data from the DB to a suitable JSON representation.

        Returns:
            dict: JSON representation of this DB record.
        """

        result = {}
        for field, value in self.__dataclass_fields__.items():
            if value.type in (SqlMany, 'SqlMany'):
                # Transform list of SQLA objects to list of IDs.
                result[field] = [record.id for record in getattr(self, field)]
            else:
                # Use raw value.
                result[field] = getattr(self, field)
        return result


    def to_json(self, *, truncate: bool = False, trunc_length: int = 255) -> dict:
        """Returns record content encoded to JSON, optionally truncated (i.e. for debugging).

        Args:
            truncate (bool, optional): Whether or not to truncate string data. Defaults to False.
            trunc_length (int, optional): How many characters to truncate strings to. Defaults to 255.

        Returns:
            dict: Record data encoded to a JSON-suitable representation.
        """

        result = self.json
        if truncate:
            for key, value in result.items():
                if isinstance(value, str) and len(value) > trunc_length:
                    result[key] = result[key][:trunc_length]
        return result

    def ingest_payload(self, payload: dict, *, usr_session_id: str = None) -> None:
        """ETL payload -> DB.
        Transforms relational data from the payload and loads it to the record.

        Args:
            payload (dict): Payload fed by API. This should already be checked to
                validate fields against the expected payload model.
            usr_session_id (str, optional): ID of DB session to use for any DB actions. Defaults to None.
        """

        session_id, session = session_manager.get(usr_session_id)
        for key, value in payload.items():
            if self.__dataclass_fields__[key].type in (SqlMany, SqlMany.__name__):  # Works for str-like annotations too
                # Transform list of IDs to SQLA objects.
                record_model = getattr(self.__class__, key).property.mapper.entity
                setattr(self, key, session.query(record_model).filter(record_model.id.in_(value)).all())
                # Confirm all values were transformed. Throw error if otherwise.
                if len(getattr(self, key)) != len(value):
                    missed_ids = [str(id) for id in value if id not in (record.id for record in getattr(self, key))]
                    raise ValueError(f'Could not find `{self.__class__.__tablename__}.{key}` records with value(s) {", ".join(missed_ids)}')
            else:
                # Use raw value.
                setattr(self, key, value)
        if not usr_session_id:
            session_manager.commit_and_close(session_id)


    # Expose Meta._links as cls._links by creating a reference alias with @property
    _links: ApiLink
    @property
    def _links(self) -> ApiLink:
        result = []
        for link in self.Meta._links:
            if link.record is None:
                link.for_record(self)
            result.append(link.finalise())
        return result


    def finalise_links(self, **kwargs) -> None:
        """Registers links against this record and records kwargs to be used for URL generation."""

        for link in self.Meta._links:
            link.for_record(self).add_args(**kwargs)


class CustomApiQuery:
    resource: Resource

    def __init__(self, name, query_method, api_docstring) -> None:
        self.name = name
        self.query_method = query_method

        class CustomApiResource(Resource):
            __doc__ = api_docstring
            def get(self, **kwargs):
                request_args = request.args.to_dict()
                try:
                    _page = int(request_args.pop('_page', 1))
                    _pagelength = min(int(request_args.pop('_pagelength', 100)), MAX_PAGE_SIZE)
                except ValueError:
                    raise BadRequest("Invalid pagination parameter! `_page` and `_pagelength` (if provided) must be integers.")
                query = query_method(**kwargs, **request_args)
                results = query.paginate(page=_page, per_page=_pagelength)
                return {
                    "page": results.page,
                    "total_pages": results.pages,
                    "total_results": query.count(),
                    "results": [*results.items]
                }

        self.resource = CustomApiResource


def expose_to_api(cls: Model) -> Model:
    """Decorator method to add necessary metadata and methods for exposing a database model to the API.

    To customise the metadata values, declare a child class in the following form. Note that any values not defined will be given defaults.

    class Cls:  # The class passed to this decorator
        class Meta:
            auto_api_endpoints: bool = Whether or not to automatically generate the endpoints
            queryable_relations: dict = {api_alias: model_relation} pairs for queryable ID-based relational attributes
            read_only_fields: tuple = ('names', 'of', 'read-only', 'fields')
            endpoint_alias: str = "endpoint-and-url-name-to-use"
            url_prefix: str = "preceding/url-with/<required_args>/
            _links: tuple = (ApiLink(...), ... )

    Args:
        cls (Model): SQLAlchemy model to be exposed to the API.

    Returns:
        class: Wrapped cls with API metadata and methods
    """

    cls = dataclass(cls)

    class Meta(ApiExposed.Meta, getattr(cls, 'Meta', object)):
        endpoint_alias = cls.__name__.lower()
        if hasattr(cls, 'Meta'):
            _links = tuple(sorted(set((*ApiExposed.Meta._links, *getattr(cls.Meta, '_links', []))), key=lambda link: link.rel))
            auto_api_endpoints = getattr(cls.Meta, 'auto_api_endpoints', ApiExposed.Meta.auto_api_endpoints)
            endpoint_alias = getattr(cls.Meta, 'endpoint_alias', ApiExposed.Meta.endpoint_alias) or endpoint_alias
            queryable_relations = getattr(cls.Meta, 'queryable_relations', {})
            read_only_fields = tuple(set((*ApiExposed.Meta.read_only_fields, *getattr(cls.Meta, 'read_only_fields', []))))
            custom_endpoints = tuple(getattr(cls.Meta, 'custom_endpoints', ApiExposed.Meta.custom_endpoints))

    def ingest_payload(self, payload, *, usr_session_id: str = None):
        """Wraps the payload ingestion methods at the Model and ApiExposed level.
        Model method is called first, and the result is passed to the ApiExposed method.

        Args:
            payload (dict): Payload to ingest.
            usr_session_id (str, optional): ID of DB session to use for any DB actions. Defaults to None.
        """

        if hasattr(cls, '_ingest_payload') and callable(cls._ingest_payload):
            payload = cls._ingest_payload(self, payload, usr_session_id=usr_session_id)
        ApiExposed.ingest_payload(self, payload, usr_session_id=usr_session_id)

    cls.Meta = Meta
    cls.ingest_payload = ingest_payload
    cls.finalise_links = ApiExposed.finalise_links
    cls.json = ApiExposed.json
    cls.to_json = ApiExposed.to_json
    cls._links = ApiExposed._links
    return cls
