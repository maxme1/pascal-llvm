from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Type, NamedTuple


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


@dataclass
class Array(DataType):
    dims: tuple[int]
    type: Any