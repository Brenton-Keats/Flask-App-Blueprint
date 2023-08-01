"""API module root, handles module content and provides methods for generating API components.

Author: Brenton Keats, 2023
"""

# Builtins
from inspect import isclass

# Libraries
from flask import Blueprint
from flask_restx import Api
from werkzeug.exceptions import HTTPException

# Project-level modules
from constants import AUTH_TOKEN_NAME
from .core import _api_routes as __api_routes, handle_bad_request as __handle_bad_request


def generate_endpoints_for_models(module: object, api: Api):
    """Generate API endpoints for all ApiExposed-like classes defined in `module`.

    Args:
        module (object): Object with a __dict__ to check for Model classes.
        api (Api): The API instance to register the endpoints on.
    """

    for cls in vars(module).values():
        if not isclass(cls): continue
        if hasattr(cls, 'Meta') and cls.Meta._api_exposed and cls.Meta.auto_api_endpoints:
            api.add_namespace(__api_routes(cls))


def api_factory(*, authorizations: dict = None, bp_kwargs: dict = None, api_kwargs: dict = {}) -> Blueprint:
    """Create API with default values and attach it to a Blueprint (returned).

    Args:
        authorizations (dict, optional): Authorizations dict to pass to Api if required. Defaults to None.
        bp_kwargs (dict, optional): Additional/override values to initialise the `Blueprint` with. Defaults to None.
        api_kwargs (dict, optional): Additional/override values to initialise the `Api` with. Defaults to {}.

    Returns:
        Blueprint: Flask Blueprint with registered `Api` instance attached.
    """

    # Set defaults
    if authorizations is None:
        authorizations = {
            'apikey': {
                'type': 'apiKey',
                'in': 'header',
                'name': AUTH_TOKEN_NAME
            }
        }
    use_bp_kwargs = dict(
        name="api",
        import_name=__name__,
        url_prefix="/api"
    )
    use_api_kwargs = dict(
        title=f'Generated API',
        version='0.1',
        description='Declaratively-generated API. Written by Brenton Keats.',
        endpoint='api',
        authorizations=authorizations,
        security='apikey'
    )

    # Update default kwargs with params
    use_bp_kwargs.update(bp_kwargs or {})
    use_api_kwargs.update(api_kwargs or {})

    api_bp = Blueprint(**use_bp_kwargs)
    api = Api(api_bp, **use_api_kwargs)
    api.errorhandler(HTTPException)(__handle_bad_request)

    return api_bp, api


def autoconfigure() -> Blueprint:
    """Default setup for application API.

    Returns:
        Blueprint: API blueprint with registered endpoints from database.models
    """

    from database import models
    bp, api = api_factory()
    generate_endpoints_for_models(models, api)
    return bp
