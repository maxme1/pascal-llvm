from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Type


def instance(v: Type[DataType]) -> DataType:
    return v()


class DataType:
    pass


@instance
class Integer(DataType):
    pass


@instance
class String(DataType):
    pass


@instance
class Real(DataType):
    pass


@dataclass
class Array(DataType):
    dims: list[int]
    type: Any
