from dataclasses import dataclass
from typing import Any

from ..type_system import types


class ParseError(Exception):
    pass


unique = dataclass(eq=False)


@unique
class Const:
    value: Any
    type: types.DataType


@unique
class Name:
    name: str


@unique
class GetItem:
    target: Any
    args: tuple[Any]


@unique
class GetField:
    target: Any
    name: str


@unique
class Dereference:
    target: Any


@unique
class Call:
    target: Any
    args: tuple[Any]


@unique
class Unary:
    op: str
    value: Any


@unique
class Binary:
    op: str
    left: Any
    right: Any


@unique
class Assignment:
    target: Name | GetItem | GetField | Dereference
    value: Any


@unique
class Definitions:
    names: tuple[Name]
    type: types.DataType


@unique
class ExpressionStatement:
    value: Any


@unique
class If:
    condition: Any
    then_: tuple[Any]
    else_: tuple[Any]


@unique
class While:
    condition: Any
    body: tuple[Any]


@unique
class For:
    name: Name
    start: Any
    stop: Any
    body: tuple[Any]


@unique
class ArgDefinition:
    name: Name
    type: types.DataType


@unique
class Function:
    name: Name
    args: tuple[ArgDefinition]
    variables: tuple[Definitions]
    body: tuple[Any]
    return_type: types.DataType

    @property
    def signature(self):
        return types.Signature(tuple(x.type for x in self.args), self.return_type)


@unique
class Program:
    variables: tuple[Definitions]
    functions: tuple[Function]
    body: tuple[Any]
