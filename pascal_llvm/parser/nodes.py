from dataclasses import dataclass
from typing import NamedTuple, Any

from ..type_system import types


class ParseError(Exception):
    pass


class Const(NamedTuple):
    value: Any
    type: types.DataType


class Name:
    name: str

    def __init__(self, name: str):
        self.name = name

    def __repr__(self):
        return f'<Name {self.name}>'


class GetItem(NamedTuple):
    target: Any
    args: tuple[Any]


class GetField(NamedTuple):
    target: Any
    name: str


class Assignment(NamedTuple):
    target: Name | GetItem | GetField
    value: Any


class Unary(NamedTuple):
    op: str
    value: Any


class Binary(NamedTuple):
    op: str
    left: Any
    right: Any


class Call(NamedTuple):
    name: Name
    args: tuple[Any]


class Definitions(NamedTuple):
    names: tuple[Name]
    type: types.DataType


class ExpressionStatement(NamedTuple):
    value: Any


class If(NamedTuple):
    condition: Any
    then_: tuple[Any]
    else_: tuple[Any]


class While(NamedTuple):
    condition: Any
    body: tuple[Any]


class For(NamedTuple):
    name: Name
    start: Any
    stop: Any
    body: tuple[Any]


class ArgDefinition(NamedTuple):
    name: Name
    type: types.DataType


class Prototype(NamedTuple):
    name: Name
    args: tuple[ArgDefinition]
    return_type: types.DataType


@dataclass(unsafe_hash=True)
class Function:
    name: Name
    args: tuple[ArgDefinition]
    variables: tuple[Definitions]
    body: tuple[Any]
    return_type: types.DataType

    @property
    def signature(self):
        return types.Signature(tuple(x.type for x in self.args), self.return_type)


class Program(NamedTuple):
    variables: tuple[Definitions]
    prototypes: tuple[Prototype]
    functions: tuple[Function]
    body: tuple[Any]
