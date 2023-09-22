from __future__ import annotations

from typing import Type

from jboc import composed
from llvmlite import ir

from . import types
from .types import WrongType

MAGIC_FUNCTIONS = {}


class MagicFunction:
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        pass

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        pass

    @classmethod
    @property
    def name(cls):
        return cls.__name__.lower()

    @classmethod
    def get(cls, name: str) -> Type[MagicFunction]:
        return MAGIC_FUNCTIONS.get(name.lower())

    @classmethod
    def all(cls):
        return MAGIC_FUNCTIONS.values()

    def __init_subclass__(cls, **kwargs):
        name = cls.name
        assert name not in MAGIC_FUNCTIONS
        MAGIC_FUNCTIONS[name] = cls


class Write(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        if not args:
            raise WrongType

        for arg in args:
            visit(arg, None, False)
        return types.Void

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        ptr = compiler.string_pointer(format_io(kinds) + b'\00')
        return compiler.builder.call(compiler.module.get_global('printf'), [ptr, *compiler.visit_sequence(args, False)])


class WriteLn(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        for arg in args:
            visit(arg, None, False)
        return types.Void

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        ptr = compiler.string_pointer(format_io(kinds) + b'\n\00')
        return compiler.builder.call(compiler.module.get_global('printf'), [ptr, *compiler.visit_sequence(args, False)])


class Read(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        if not args:
            raise WrongType

        for arg in args:
            visit(arg, None, True)
        return types.Void

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        ptr = compiler.string_pointer(format_io(kinds) + b'\00')
        return compiler.builder.call(compiler.module.get_global('scanf'), [ptr, *compiler.visit_sequence(args, True)])


class ReadLn(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        for arg in args:
            visit(arg, None, True)
        return types.Void

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        ptr = compiler.string_pointer(format_io(kinds) + b'\n\00')
        return compiler.builder.call(compiler.module.get_global('scanf'), [ptr, *compiler.visit_sequence(args, True)])


class Chr(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        if len(args) != 1:
            raise WrongType
        visit(args[0], types.Integer, False)
        return types.Char

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        value, = args
        return compiler.builder.trunc(compiler.visit(value, False), ir.IntType(8))


class Inc(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        if len(args) != 1:
            raise WrongType
        visit(args[0], types.Integer, True)
        return types.Void

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        ptr, = args
        ptr = compiler.visit(ptr, True)
        # FIXME
        value = compiler.builder.add(compiler.builder.load(ptr), ir.Constant(ir.IntType(32), 0))
        compiler.builder.store(value, ptr)


class Random(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        if len(args) > 1:
            raise WrongType
        if args:
            visit(args[0], types.Integer, False)
        return types.Integer

    @classmethod
    def evaluate(cls, args, kinds, compiler):
        # TODO
        return ir.Constant(ir.IntType(32), 0)


class Randomize(MagicFunction):
    @classmethod
    def validate(cls, args, visit) -> types.DataType:
        if args:
            raise WrongType
        return types.Void


@composed(b' '.join)
def format_io(args):
    for arg in args:
        match arg:
            case types.SignedInt(_):
                yield b'%d'
            case types.Floating(_):
                yield b'%f'
            case types.Char:
                yield b'%c'
            case types.StaticArray(dims, types.Char) if len(dims) == 1:
                yield b'%s'
            case types.DynamicArray(types.Char):
                yield b'%s'
            case kind:
                raise TypeError(kind)
