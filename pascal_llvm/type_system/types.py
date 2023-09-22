from __future__ import annotations

from dataclasses import dataclass
from typing import Type, NamedTuple


def instance(v: Type[DataType]) -> DataType:
    return v()


class DataType:
    @property
    def family(self) -> tuple | None:
        for family in Ints, Floats:
            if self in family:
                return family


@instance
class Void(DataType):
    pass


class Signature(NamedTuple):
    args: tuple[DataType]
    return_type: DataType


@dataclass
class Function(DataType):
    signatures: tuple[Signature]


@instance
class Boolean(DataType):
    pass


@instance
class Char(DataType):
    pass


@dataclass(unsafe_hash=True)
class SignedInt(DataType):
    bits: int


@dataclass(unsafe_hash=True)
class Floating(DataType):
    bits: int


# TODO: 32 and 64 is not always true
Ints = Byte, Integer = SignedInt(8), SignedInt(32)
Floats = Real, = Floating(64),


@dataclass(unsafe_hash=True, repr=True)
class Pointer(DataType):
    type: DataType


@dataclass(unsafe_hash=True)
class StaticArray(DataType):
    dims: tuple[tuple[int, int]]
    type: DataType


@dataclass(unsafe_hash=True)
class DynamicArray(DataType):
    type: DataType


class Field(NamedTuple):
    name: str
    type: DataType


@dataclass(unsafe_hash=True, frozen=True)
class Record(DataType):
    fields: tuple[Field]


@dataclass(unsafe_hash=True)
class Reference(DataType):
    type: DataType


def dispatch(name: str):
    # TODO: smarter
    kinds = {
        'integer': Integer,
        'real': Real,
        'char': Char,
        'byte': Byte,
        'boolean': Boolean,
    }
    return kinds[name]
