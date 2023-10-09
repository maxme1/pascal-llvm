from __future__ import annotations

from dataclasses import dataclass

hashable = dataclass(unsafe_hash=True, repr=True)


class WrongType(Exception):
    pass


class DataType:
    """ Base class for all data types """


class VoidType(DataType):
    pass


class NilType(DataType):
    pass


class BooleanType(DataType):
    pass


class CharType(DataType):
    pass


@hashable
class SignedInt(DataType):
    bits: int


@hashable
class Floating(DataType):
    bits: int


@hashable
class Pointer(DataType):
    type: DataType


@hashable
class Reference(DataType):
    type: DataType


@hashable
class StaticArray(DataType):
    dims: tuple[tuple[int, int]]
    type: DataType


@hashable
class DynamicArray(DataType):
    type: DataType


@hashable
class Field:
    name: str
    type: DataType


@hashable
class Record(DataType):
    fields: tuple[Field]


@hashable
class Signature:
    args: tuple[DataType]
    return_type: DataType


@hashable
class Function(DataType):
    signatures: tuple[Signature]


# TODO: 32 and 64 is not always true
Ints = Byte, Integer = SignedInt(8), SignedInt(32)
Floats = Real, = Floating(64),
Void, Boolean, Char, Nil = VoidType(), BooleanType(), CharType(), NilType()
TYPE_NAMES = {
    'integer': Integer,
    'real': Real,
    'char': Char,
    'byte': Byte,
    'boolean': Boolean,
}
