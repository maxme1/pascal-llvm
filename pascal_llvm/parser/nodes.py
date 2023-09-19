from dataclasses import dataclass
from typing import NamedTuple, Any

from .. import types


class ParseError(Exception):
    pass


class Const(NamedTuple):
    value: Any
    type: types.DataType


class Name(NamedTuple):
    name: str


class Assignment(NamedTuple):
    target: Any
    value: Any


class Unary(NamedTuple):
    op: str
    value: Any


class Binary(NamedTuple):
    op: str
    left: Any
    right: Any


class Call(NamedTuple):
    name: str
    args: list[Any]


class GetItem(NamedTuple):
    name: str
    args: list[Any]


class Definitions(NamedTuple):
    names: list[str]
    type: types.DataType


class If(NamedTuple):
    condition: Any
    then_: list[Any]
    else_: list[Any]


class While(NamedTuple):
    condition: Any
    body: list[Any]


class For(NamedTuple):
    name: str
    start: Any
    stop: Any
    body: list[Any]


class ArgDefinition(NamedTuple):
    name: str
    mutable: bool
    type: types.DataType


@dataclass
class Procedure:
    name: str
    args: list[ArgDefinition]
    variables: list[Definitions]
    body: list[Any]


@dataclass
class Function(Procedure):
    return_type: types.DataType


class Program(NamedTuple):
    name: str
    variables: list[Definitions]
    subroutines: list[Function | Procedure]
    body: list[Any]
