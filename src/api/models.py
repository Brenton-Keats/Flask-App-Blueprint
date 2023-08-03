from flask_restx import fields, Model
from util import ApiLink

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

new_session_model = Model('new-session', {
    "session_id": fields.String
})
session_response = generic_api_models['base'].clone('session-response', {'result': fields.Nested(new_session_model)})

# For modelling commits or rollbacks
session_object_model = Model('session-object', {
    'TYPE': fields.String(example="Object name"),
    'DATA': fields.Nested(generic_api_models['record-base'])
})
session_objects_model = Model('session-objects', {
    'CREATE': fields.List(fields.Nested(session_object_model)),
    'UPDATE': fields.List(fields.Nested(session_object_model)),
    'DELETE': fields.List(fields.Nested(session_object_model)),
})
session_action_response = generic_api_models['base'].clone('session-action-response', {'result': fields.Nested(session_objects_model)})

