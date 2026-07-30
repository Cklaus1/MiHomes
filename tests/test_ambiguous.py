"""W.4 · M40 — AmbiguousIdentifierError must subclass ValueError.

`EntityNotFoundError` already subclasses `ValueError`, so any `except ValueError`
in a route catches an unknown slug. `AmbiguousIdentifierError` subclassing plain
`Exception` created a cross-cutting inconsistency: an ambiguous prefix would slip
past those same guards and surface as a 500. Both should be `ValueError`
subclasses so the two resolution failures are caught uniformly.
"""

from mihomes.models.property import Property
from mihomes.services import property as prop_svc
from mihomes.services.slug import (
    AmbiguousIdentifierError,
    EntityNotFoundError,
    resolve_identifier,
)


def test_ambiguous_is_valueerror_subclass():
    assert issubclass(AmbiguousIdentifierError, ValueError)


def test_entity_not_found_is_valueerror_subclass():
    assert issubclass(EntityNotFoundError, ValueError)


def test_ambiguous_caught_by_except_valueerror(session):
    prop_svc.create_property(session, "Lakeside Cottage")
    prop_svc.create_property(session, "Lakefront Villa")
    session.commit()

    caught = None
    try:
        resolve_identifier(session, Property, "lake")
    except ValueError as exc:  # must catch AmbiguousIdentifierError
        caught = exc
    assert isinstance(caught, AmbiguousIdentifierError)
