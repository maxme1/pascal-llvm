from contextlib import contextmanager
from typing import Sequence

from . import types
from ..visitor import Visitor
from ..parser import (
    Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Function,
    ExpressionStatement, ArgDefinition, GetField
)


class WrongType(Exception):
    pass


class TypeSystem(Visitor):
    def __init__(self):
        self._scopes = []
        self._func_return_names = []
        self._signatures = {}
        self.types = {}
        self.casting = {}
        self.references = {}
        # FIXME
        self._writeln = Function(Name('WRITELN'), (ArgDefinition(Name('x'), types.Integer),), (), (), types.Void)

    def after_visit(self, node, value):
        if value is not None:
            self.types[node] = value
        return value

    # TODO: better way to return 3 states
    def can_cast(self, kind: types.DataType, to: types.DataType) -> int:
        if to is None or kind == to:
            return 2

        if isinstance(kind, types.Reference):
            if self.can_cast(kind.type, to):
                return 1
            return 0
        if isinstance(to, types.Reference):
            if self.can_cast(kind, to.type):
                return 1
            return 0

        # FIXME
        if not getattr(kind, 'family', None) or not getattr(to, 'family', None):
            return 0

        family = kind.family
        if family == to.family:
            return int(family.index(kind) < family.index(to))
        if family == types.Ints and to.family == types.Floats:
            return 1
        return 0

    def cast(self, node, kind: types.DataType, to: types.DataType):
        match self.can_cast(kind, to):
            case 0:
                raise WrongType(kind, to)
            case 1:
                self.casting[node] = kind, to
            case 2:
                self.casting.pop(node, None)

    # scope utils

    def _resolve_function(self, name: Name):
        return self._scopes[0][name.name.lower()]

    def _choose_signature(self, name: Name, signature):
        self.references[name] = self._signatures[name.name.lower(), signature]

    def _store_signature(self, node: Function, signature):
        scope, = self._scopes
        name = node.name.name.lower()
        if name not in scope:
            scope[name] = types.Function(())
        self.types[name] = scope[name] = types.Function((*scope[name].signatures, signature))
        self._signatures[name, signature] = node

    def _store_value(self, name: Name, kind: types.DataType):
        assert name not in self._scopes[-1]
        self._scopes[-1][name.name.lower()] = name, kind
        self.types[name] = kind

    def _resolve_value(self, name: Name):
        for scope in reversed(self._scopes):
            lower = name.name.lower()
            if lower in scope:
                node, kind = scope[lower]
                self.references[name] = node
                return kind

        raise KeyError(name)

    @contextmanager
    def _new_scope(self):
        self._scopes.append({})
        yield
        self._scopes.pop()

    # scope modification

    def _assignment(self, node: Assignment):
        kind = self.visit(node.target, expected=None, lvalue=True)
        self.visit(node.value, expected=kind, lvalue=False)

    def _program(self, node: Program):
        with self._new_scope():
            # FIXME
            self._store_signature(self._writeln, self._writeln.signature)

            for definitions in node.variables:
                for name in definitions.names:
                    self._store_value(name, definitions.type)

            for func in node.functions:
                self._store_signature(func, func.signature)

            self.visit_sequence(node.functions)
            self.visit_sequence(node.body)

    def _function(self, node: Function):
        with self._new_scope():
            if node.return_type == types.Void:
                self._func_return_names.append((None, None))
            else:
                self._func_return_names.append((node.name, node.return_type))
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
        counter = self._resolve_value(node.name)
        if counter not in types.Ints:
            raise WrongType(counter)

        self.visit(node.start, expected=counter, lvalue=False)
        self.visit(node.stop, expected=counter, lvalue=False)
        self.visit_sequence(node.body)

    def _expression_statement(self, node: ExpressionStatement):
        self.visit(node.value, expected=None, lvalue=False)

    # expressions

    def _binary(self, node: Binary, expected: types.DataType, lvalue: bool):
        # TODO: global
        arithmetics = {
            '+': [*types.Ints, *types.Floats, types.String],
            '*': [*types.Ints, *types.Floats],
        }
        comparison = {
            '=': [*types.Ints, *types.Floats, types.String],
        }
        signatures = {
            k: [types.Signature([v, v], v) for v in vs]
            for k, vs in arithmetics.items()
        }
        signatures.update({
            k: [types.Signature([v, v], types.Boolean) for v in vs]
            for k, vs in comparison.items()
        })
        return self._dispatch([node.left, node.right], signatures[node.op], expected).return_type

    def _unary(self, node: Unary, expected: types.DataType):
        return self.visit(node.value, expected)

    def _call(self, node: Call, expected: types.DataType, lvalue: bool):
        # get all the functions with this name
        target = self._resolve_function(node.name)
        if not isinstance(target, types.Function):
            raise WrongType(target)

        signature = self._dispatch(node.args, target.signatures, expected)
        # choose the right function
        self._choose_signature(node.name, signature)
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

                    actual = self.visit(arg, expected=kind, lvalue=False)
                    self.cast(arg, actual, kind)

            except WrongType:
                continue

            return signature

        raise WrongType(args, expected, signatures)

    def _get_item(self, node: GetItem, expected: types.DataType, lvalue: bool):
        target = self.visit(node.target, expected=None, lvalue=True)
        if isinstance(target, types.Reference):
            target = target.type
        if not isinstance(target, types.Array):
            raise WrongType(target)
        if len(node.args) != len(target.dims):
            raise WrongType(target, node.args)
        # TODO
        args = self.visit_sequence(node.args, expected=types.Integer, lvalue=lvalue)
        if not all(x in types.Ints for x in args):
            raise WrongType(node.args)

        return target.type

    def _get_field(self, node: GetField, expected: types.DataType, lvalue: bool):
        target = self.visit(node.target, expected=None, lvalue=True)
        if isinstance(target, types.Reference):
            target = target.type
        if not isinstance(target, types.Record):
            raise WrongType(target)

        for field in target.fields:
            if field.name == node.name:
                return field.type

        raise WrongType(target, node.name)

    def _name(self, node: Name, expected: types.DataType, lvalue: bool):
        # assignment to the function's name inside a function is definition of a return value
        if (
                lvalue and self._func_return_names and
                self._func_return_names[-1][0] and node.name == self._func_return_names[-1][0].name
        ):
            ref, kind = self._func_return_names[-1]
            self.references[node] = ref
            return kind

        return self._resolve_value(node)

    @staticmethod
    def _const(node: Const, expected: types.DataType, lvalue: bool):
        return node.type
