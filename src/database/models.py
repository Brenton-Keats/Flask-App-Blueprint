"""Defines SQLAlchemy objects for all database classes to be used in the application.

This is a placeholder file that configures basic models showcasing what can be done and how to
configure what is exposed to the API, and what isn't.

Author: Brenton Keats, 2023
"""

# Libraries
from sqlalchemy.ext.hybrid import hybrid_property

# Project-level modules
from util import ApiLink, SqlMany, expose_to_api, CustomApiQuery
from .core import db, session_manager

Model = db.Model
Column = db.Column
ForeignKey = db.ForeignKey

@expose_to_api
class Parent(Model):
    name: str = Column(db.String)
    some_int: int = Column(db.Integer)

    children: SqlMany = db.relationship('Child', uselist=True, back_populates='parent')

@expose_to_api
class Child(Model):
    name: str = Column(db.String)
    some_bool: bool = Column(db.Boolean)
    parent_id: int = Column(db.Integer, ForeignKey('parent.id'))

    # This won't appear on the API because it is not type-annotated
    secret_id = Column(db.Integer, ForeignKey('secret.id'))

    parent = db.relationship('Parent', uselist=False, back_populates='children')

    @hybrid_property
    def siblings(self) -> SqlMany:
        if not self.parent:
            return []
        return self.parent.children - self

    @siblings.expression
    def siblings(self):
        return ["Collection of children with the same parent"]

# This class won't have API endpoints created, because it is not decorated with `expose_to_api`
class Secret(Model):
    name = Column(db.String)
    secret = Column(db.String)