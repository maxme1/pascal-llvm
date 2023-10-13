from collections import defaultdict
from contextlib import contextmanager
from typing import Sequence

from . import types
from .types import WrongType
from .magic import MagicFunction, MAGIC_FUNCTIONS
from ..visitor import Visitor
from ..parser import (
    Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Function,
    ExpressionStatement, GetField, Dereference
)


class TypeSystem(Visitor):
    @classmethod
    def analyze(cls, root):
        self = cls()
        self.visit(root)
        for key, value in self.desugar.items():
            assert value not in self.types
            self.types[value] = self.types[key]
        return self

    def __init__(self):
        self._scopes = []
        self._func_return_names = []
        self.types = {}
        self.casting = {}
        self.references = {}
        self.desugar = {}

    def after_visit(self, node, kind, expected=None, lvalue=None):
        self.types[node] = kind
        if expected is not None:
            if not self.can_cast(kind, expected):
                raise WrongType(kind, expected)

            if kind == expected:
                self.casting.pop(node, None)
            else:
                self.casting[node] = expected
                kind = expected

        return kind

    def can_cast(self, kind: types.DataType, to: types.DataType) -> bool:
        if to is None or kind == to:
            return True

        match kind, to:
            case types.Reference(src), _:
                return self.can_cast(src, to)
            case _, types.Reference(dst):
                return self.can_cast(kind, dst)
            case types.StaticArray(dims, src), types.DynamicArray(dst):
                return len(dims) == 1 and src == dst
            case types.SignedInt(_), types.Floating(_):
                return True
            case types.Nil, types.Pointer(_):
                return True

        for family in types.SignedInt, types.Floating:
            if isinstance(kind, family) and isinstance(to, family):
                return kind.bits <= to.bits

        return False

    # scope utils

    def _store_value(self, name: Name, kind: types.DataType):
        self._store(name.normalized, kind, name)
        self.types[name] = kind

    def _store(self, name: str, kind: types.DataType, payload):
        assert name not in self._scopes[-1]
        self._scopes[-1][name] = kind, payload

    def _resolve(self, name: str):
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]

        raise KeyError(name)

    def _bind(self, source, destination):
        self.references[source] = destination

    @contextmanager
    def _enter(self):
        self._scopes.append({})
        yield
        self._scopes.pop()

    # scope modification

    def _assignment(self, node: Assignment):
        kind = self.visit(node.target, expected=None, lvalue=True)
        # no need to cast to reference in this case
        if isinstance(kind, types.Reference):
            kind = kind.type

        self.visit(node.value, expected=kind, lvalue=False)

    def _program(self, node: Program):
        with self._enter():
            # magic
            for name, magic in MAGIC_FUNCTIONS.items():
                self._store(name, magic(), None)

            # vars
            for definitions in node.variables:
                for name in definitions.names:
                    self._store_value(name, definitions.type)

            # functions
            functions = defaultdict(list)
            for func in node.functions:
                functions[func.name.normalized].append(func)
            for name, funcs in functions.items():
                funcs = {f.signature: f for f in funcs}
                self._store(name, types.Function(tuple(funcs)), funcs)

            self.visit_sequence(node.functions)
            self.visit_sequence(node.body)

    def _function(self, node: Function):
        with self._enter():
            self._func_return_names.append((node.return_type, node.name))
            self.types[node.name] = node.return_type

            for arg in node.args:
                self._store_value(arg.name, arg.type)

            for definitions in node.variables:
                for name in definitions.names:
                    self._store_value(name, definitions.type)

            self.visit_sequence(node.body)
            self._func_return_names.pop()

    # statements

    def _if(self, node: If):
        self.visit(node.condition, expected=types.Boolean, lvalue=False)
        self.visit_sequence(node.then_)
        self.visit_sequence(node.else_)

    def _while(self, node: While):
        self.visit(node.condition, expected=types.Boolean, lvalue=False)
        self.visit_sequence(node.body)

    def _for(self, node: For):
        counter = self.visit(node.name, expected=None, lvalue=True)
        if not isinstance(counter, types.SignedInt):
            raise WrongType(counter)

        self.visit(node.start, expected=counter, lvalue=False)
        self.visit(node.stop, expected=counter, lvalue=False)
        self.visit_sequence(node.body)

    def _expression_statement(self, node: ExpressionStatement):
        self.visit(node.value, expected=None, lvalue=False)

    # expressions

    def _binary(self, node: Binary, expected: types.DataType, lvalue: bool):
        return self._dispatch([node.left, node.right], BINARY_SIGNATURES[node.op], expected).return_type

    def _unary(self, node: Unary, expected: types.DataType, lvalue: bool):
        if node.op == '@':
            if not isinstance(expected, types.Pointer) or lvalue:
                raise WrongType(node)
            return types.Pointer(self.visit(node.value, expected=expected.type, lvalue=lvalue))

        return self.visit(node.value, expected, lvalue)

    def _call(self, node: Call, expected: types.DataType, lvalue: bool):
        if not isinstance(node.target, Name):
            raise WrongType(node)

        # get all the functions with this name
        kind, targets = self._resolve(node.target.normalized)
        if isinstance(kind, MagicFunction):
            return kind.validate(node.args, self.visit)

        if not isinstance(kind, types.Function):
            raise WrongType(kind)

        signature = self._dispatch(node.args, kind.signatures, expected)
        # choose the right function
        self._bind(node.target, targets[signature])
        return signature.return_type

    def _dispatch(self, args: Sequence, signatures: Sequence[types.Signature], expected: types.DataType):
        for signature in signatures:
            if len(signature.args) != len(args):
                continue
            if not self.can_cast(signature.return_type, expected):
                continue

            try:
                for arg, kind in zip(args, signature.args, strict=True):
                    if isinstance(kind, types.Reference) and not isinstance(arg, Name):
                        raise WrongType('Only variables can be mutable arguments')

                    self.visit(arg, expected=kind, lvalue=isinstance(kind, types.Reference))

            except WrongType:
                continue

            return signature

        raise WrongType(args, expected, signatures)

    def _get_item(self, node: GetItem, expected: types.DataType, lvalue: bool):
        target = self.visit(node.target, expected=None, lvalue=True)
        if isinstance(target, types.Reference):
            target = target.type
        if not isinstance(target, (types.StaticArray, types.DynamicArray)):
            raise WrongType(target)

        ndims = len(target.dims) if isinstance(target, types.StaticArray) else 1
        if len(node.args) != ndims:
            raise WrongType(target, node.args)

        args = self.visit_sequence(node.args, expected=types.Integer, lvalue=False)
        args = [x.type if isinstance(x, types.Reference) else x for x in args]
        if not all(isinstance(x, types.SignedInt) for x in args):
            raise WrongType(node)

        return target.type

    def _get_field(self, node: GetField, expected: types.DataType, lvalue: bool):
        target = self.visit(node.target, expected=None, lvalue=False)
        if isinstance(target, types.Reference):
            target = target.type
        if not isinstance(target, types.Record):
            raise WrongType(target)

        for field in target.fields:
            if field.name == node.name:
                return field.type

        raise WrongType(target, node.name)

    def _dereference(self, node: Dereference, expected: types.DataType, lvalue: bool):
        target = self.visit(node.target, types.Pointer(expected), lvalue)
        return target.type

    def _name(self, node: Name, expected: types.DataType, lvalue: bool):
        # assignment to the function's name inside a function is definition of a return value
        if lvalue and self._func_return_names:
            kind, target = self._func_return_names[-1]
            if kind != types.Void and target.name == node.name:
                self._bind(node, target)
                return kind

        kind, target = self._resolve(node.normalized)
        if isinstance(kind, (types.Function, MagicFunction)):
            self.desugar[node] = new = Call(node, ())
            return self._call(new, expected, lvalue)

        self._bind(node, target)
        return kind

    def _const(self, node: Const, expected: types.DataType, lvalue: bool):
        if lvalue:
            raise WrongType(node)
        return node.type


_numeric = [*types.Ints, *types.Floats]
_homogeneous = {
    '+': _numeric,
    '*': _numeric,
    '-': _numeric,
    '/': _numeric,
    'and': [types.Boolean],
    'or': [types.Boolean],
}
_boolean = {
    '=': _numeric,
    '<': _numeric,
    '<=': _numeric,
    '>': _numeric,
    '>=': _numeric,
    '<>': _numeric,
}
BINARY_SIGNATURES = {
    k: [types.Signature((v, v), v) for v in vs]
    for k, vs in _homogeneous.items()
}
BINARY_SIGNATURES.update({
    k: [types.Signature((v, v), types.Boolean) for v in vs]
    for k, vs in _boolean.items()
})
