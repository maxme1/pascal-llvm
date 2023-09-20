from __future__ import annotations

from dataclasses import dataclass
from typing import Type, NamedTuple


def instance(family=None):
    def decorator(v: Type[DataType]) -> DataType:
        v = v()
        if family is not None:
            family.append(v)
        v.family = family
        return v

    return decorator


class DataType:
    family: list | None


Ints = []
Floats = []


@instance()
class Void(DataType):
    pass


class Signature(NamedTuple):
    args: tuple[DataType]
    return_type: DataType


@dataclass
class Function(DataType):
    signatures: tuple[Signature]


@instance()
class Boolean(DataType):
    pass


@instance(Ints)
class Integer(DataType):
    pass


@instance()
class String(DataType):
    pass


@instance(Floats)
class Real(DataType):
    pass


@dataclass(unsafe_hash=True)
class Array(DataType):
    dims: tuple[int]
    type: DataType


class Field(NamedTuple):
    name: str
    type: DataType


@dataclass(unsafe_hash=True, eq=True, frozen=True)
class Record(DataType):
    fields: tuple[Field]


@dataclass(unsafe_hash=True)
class Reference(DataType):
    type: DataType


def dispatch(name: str):
    # TODO: smarter
    kinds = {
        'integer': Integer,
        'string': String,
        'real': Real,
    }
    return kinds[name]
