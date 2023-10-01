from functools import reduce
from operator import mul

from llvmlite import ir

from .type_system.visitor import TypeSystem
from .type_system import types, MagicFunction
from .parser import (
    Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Function,
    ExpressionStatement, GetField, Dereference
)
from .visitor import Visitor


class Compiler(Visitor):
    @classmethod
    def compile(cls, root):
        compiler = cls(TypeSystem.analyze(root))
        compiler.visit(root)
        return compiler.module

    def __init__(self, ts: TypeSystem):
        self.module = ir.Module()
        self._builders = []

        self._ts = ts
        self._desugar = ts.desugar
        self._references = ts.references
        self._allocas = {}
        self._function_names = {}
        self._string_idx = 0

        # external and builtins
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], True), 'printf')
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], True), 'scanf')
        ir.Function(self.module, ir.FunctionType(ir.IntType(8), []), 'getchar')
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), []), 'rand')
        ir.Function(self.module, ir.FunctionType(ir.VoidType(), [ir.IntType(32)]), 'srand')
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(32)]), 'time')

    @property
    def builder(self) -> ir.IRBuilder:
        return self._builders[-1]

    def _deduplicate(self, node: Function):
        if node not in self._function_names:
            # TODO: pretify
            self._function_names[node] = f'function.{len(self._function_names)}.{node.name.name}'
        return self._function_names[node]

    def string_pointer(self, value: bytes):
        kind = ir.ArrayType(ir.IntType(8), len(value))
        global_string = ir.GlobalVariable(self.module, kind, name=f'string.{self._string_idx}.global')
        global_string.initializer = ir.Constant(kind, [ir.Constant(ir.IntType(8), x) for x in value])
        self._string_idx += 1
        return self.builder.gep(
            global_string, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]
        )

    # typing

    def _type(self, node):
        if node in self._ts.casting:
            return self._ts.casting[node][1]
        return self._ts.types[node]

    def _cast(self, value, src: types.DataType, dst: types.DataType, lvalue: bool):
        # references are just fancy pointers
        if isinstance(src, types.Reference) and not lvalue:
            src = src.type
            value = self.builder.load(value)

        if src == dst:
            return value

        if src in types.Ints and dst in types.Floats:
            return self.builder.sitofp(value, resolve(dst))

        if src in types.Ints and dst in types.Ints:
            return self.builder.sext(value, resolve(dst))

        if isinstance(src, types.StaticArray) and isinstance(dst, types.DynamicArray):
            return value

        raise NotImplementedError(value, src, dst)

    def after_visit(self, node, value, lvalue=None):
        if node in self._ts.casting:
            assert lvalue is not None
            return self._cast(value, *self._ts.casting[node], lvalue)
        return value

    # scope utils

    def _access(self, name: Name):
        return self.builder.load(self._allocas[self._references[name]])

    def _assign(self, name: Name, value):
        target = self._references[name]
        ptr = self._allocas[target]
        if isinstance(self._type(target), types.Reference):
            ptr = self.builder.load(ptr)
        self.builder.store(value, ptr)

    def _allocate(self, name: Name, kind: ir.Type, initial=None):
        self._allocas[name] = self.builder.alloca(kind, name=name.name)
        if initial is not None:
            self.builder.store(initial, self._allocas[name])

    def _allocate_global(self, name: Name, kind: ir.Type):
        var = ir.GlobalVariable(self.module, kind, name=name.name)
        var.linkage = 'internal'
        self._allocas[name] = var

    # scope modification

    def _assignment(self, node: Assignment):
        ptr = self.visit(node.target, lvalue=True)
        value = self.visit(node.value, lvalue=False)
        if isinstance(self._type(node.value), types.Reference):
            value = self.builder.load(value)

        self.builder.store(value, ptr)

    def _program(self, node: Program):
        main = ir.Function(self.module, ir.FunctionType(ir.VoidType(), ()), '.main')
        self._builders.append(ir.IRBuilder(main.append_basic_block()))

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate_global(name, resolve(definitions.type))

        for func in node.functions:
            ir.Function(
                self.module, ir.FunctionType(resolve(func.return_type), [resolve(arg.type) for arg in func.args]),
                self._deduplicate(func),
            )

        self.visit_sequence(node.functions)
        self.visit_sequence(node.body)
        self.builder.ret_void()

        self._builders.pop()

    def _function(self, node: Function):
        ret = node.name
        func = self.module.get_global(self._deduplicate(node))
        self._builders.append(ir.IRBuilder(func.append_basic_block()))

        if node.return_type != types.Void:
            self._allocate(ret, resolve(node.return_type))

        for arg, param in zip(func.args, node.args, strict=True):
            name = param.target
            arg.target = name.target
            self._allocate(name, resolve(param.type), arg)

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        self.visit_sequence(node.body)

        if node.return_type != types.Void:
            self.builder.ret(self.builder.load(self._allocas[ret]))
        else:
            self.builder.ret_void()

        self._builders.pop()

    # statements

    def _if(self, node: If):
        condition = self.visit(node.condition, lvalue=False)
        then_block = self.builder.append_basic_block()
        else_block = self.builder.append_basic_block()
        merged_block = self.builder.append_basic_block()
        self.builder.cbranch(condition, then_block, else_block)

        # then
        self.builder.position_at_end(then_block)
        self.visit_sequence(node.then_)
        self.builder.branch(merged_block)
        # else
        self.builder.position_at_end(else_block)
        self.visit_sequence(node.else_)
        self.builder.branch(merged_block)
        # phi
        self.builder.position_at_end(merged_block)

    def _while(self, node: While):
        check_block = self.builder.append_basic_block('check')
        loop_block = self.builder.append_basic_block('for')
        end_block = self.builder.append_basic_block('for-end')
        self.builder.branch(check_block)

        # check
        self.builder.position_at_end(check_block)
        condition = self.visit(node.condition, False)
        self.builder.cbranch(condition, loop_block, end_block)

        # loop
        self.builder.position_at_end(loop_block)
        self.visit_sequence(node.body)
        self.builder.branch(check_block)

        # exit
        self.builder.position_at_end(end_block)

    def _for(self, node: For):
        name = node.name
        start = self.visit(node.start, lvalue=False)
        stop = self.visit(node.stop, lvalue=False)
        self._assign(name, start)

        check_block = self.builder.append_basic_block('check')
        loop_block = self.builder.append_basic_block('for')
        end_block = self.builder.append_basic_block('for-end')
        self.builder.branch(check_block)

        # check
        self.builder.position_at_end(check_block)
        # TODO: type
        condition = self.builder.icmp_signed('<=', self._access(name), stop, 'for-condition')
        self.builder.cbranch(condition, loop_block, end_block)

        # loop
        self.builder.position_at_end(loop_block)
        self.visit_sequence(node.body)
        # update
        # TODO: type
        self._assign(name, self.builder.add(self._access(name), ir.Constant(resolve(types.Integer), 1), 'increment'))
        self.builder.branch(check_block)

        # exit
        self.builder.position_at_end(end_block)

    def _expression_statement(self, node: ExpressionStatement):
        self.visit(node.value, lvalue=False)

    # expressions

    def _binary(self, node: Binary, lvalue: bool):
        left = self.visit(node.left, lvalue)
        right = self.visit(node.right, lvalue)
        kind = self._type(node.left)
        assert kind == self._type(node.right), (kind, self._type(node.right))

        match kind:
            case types.SignedInt(_):
                if node.op in COMPARISON:
                    return self.builder.icmp_signed(COMPARISON[node.op], left, right)
                return {
                    '+': self.builder.add,
                    '-': self.builder.sub,
                    '*': self.builder.mul,
                    '/': self.builder.sdiv,
                }[node.op](left, right)

            case types.Floating(_):
                if node.op in COMPARISON:
                    return self.builder.fcmp_ordered(COMPARISON[node.op], left, right)
                return {
                    '+': self.builder.fadd,
                    '-': self.builder.fsub,
                    '*': self.builder.fmul,
                    '/': self.builder.fdiv,
                }[node.op](left, right)

            case types.Boolean:
                return {
                    'and': self.builder.and_,
                    'or': self.builder.or_,
                }[node.op](left, right)

            case x:
                raise TypeError(x)

    def _unary(self, node: Unary, lvalue: bool):
        # getting the address is a special case
        if node.op == '@':
            # just get the name's address
            return self.visit(node.value, lvalue=True)

        value = self.visit(node.value, lvalue)
        match node.op:
            case '-':
                return self.builder.neg(value)
            case 'not':
                return self.builder.not_(value)
            case x:
                raise ValueError(x, node)

    def _call(self, node: Call, lvalue: bool):
        magic = MagicFunction.get(node.target.name)
        if magic is not None:
            return magic.evaluate(node.args, list(map(self._type, node.args)), self)

        func = self.module.get_global(self._deduplicate(self._references[node.target]))
        signature = self._references[node.target].signature

        args = []
        for arg, kind in zip(node.args, signature.args, strict=True):
            # FIXME
            if isinstance(kind, types.Reference):
                assert isinstance(arg, Name)
                value = self._allocas[self._references[arg]]
            else:
                value = self.visit(arg, False)
            args.append(value)

        return self.builder.call(func, args)

    def _get_item(self, node: GetItem, lvalue: bool):
        # we always want a pointer from the parent
        ptr = self.visit(node.target, True)
        # TODO: desugar this in the type system?
        stride = 1
        dims = self._type(node.target).dims
        idx = ir.Constant(ir.IntType(32), 0)
        for (start, stop), arg in reversed(list(zip(dims, node.args, strict=True))):
            local = self.visit(arg, lvalue=False)
            # upcast to i32
            local = self._cast(local, self._type(arg), types.Integer, False)
            # extract the origin
            local = self.builder.sub(local, ir.Constant(ir.IntType(32), start))
            # multiply by stride
            local = self.builder.mul(local, ir.Constant(ir.IntType(32), stride))
            # add to index
            idx = self.builder.add(idx, local)
            stride *= stop - start

        ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), idx])
        if lvalue:
            return ptr
        return self.builder.load(ptr)

    def _get_field(self, node: GetField, lvalue: bool):
        ptr = self.visit(node.target, True)
        kind = self._type(node.target)
        if isinstance(kind, types.Reference):
            kind = kind.type
        idx, = [i for i, field in enumerate(kind.fields) if field.target == node.name]
        ptr = self.builder.gep(
            ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
        )
        if lvalue:
            return ptr
        return self.builder.load(ptr)

    def _dereference(self, node: Dereference, lvalue: bool):
        ptr = self.builder.load(self.visit(node.target, True))
        if lvalue:
            return ptr
        return self.builder.load(ptr)

    def _name(self, node: Name, lvalue: bool):
        if node in self._desugar:
            return self.visit(self._desugar[node], lvalue)

        target = self._references[node]
        ptr = self._allocas[target]
        if lvalue:
            if isinstance(self._type(target), types.Reference):
                ptr = self.builder.load(ptr)
            return ptr
        return self.builder.load(ptr)

    def _const(self, node: Const, lvalue: bool):
        # TODO: f32 doesn't work
        kind = node.type
        value = node.value
        if isinstance(kind, types.StaticArray) and kind.type == types.Char:
            return self.string_pointer(value)

        if not isinstance(kind, (types.SignedInt, types.Floating)):
            raise ValueError(value)

        return ir.Constant(resolve(kind), value)


def resolve(kind):
    match kind:
        case types.Void:
            return ir.VoidType()
        case types.Char:
            return ir.IntType(8)
        case types.SignedInt(bits):
            return ir.IntType(bits)
        case types.Floating(bits):
            assert bits == 64
            return ir.DoubleType()
        case types.Reference(kind) | types.Pointer(kind) | types.DynamicArray(kind):
            return ir.PointerType(resolve(kind))
        case types.StaticArray(dims, kind):
            size = reduce(mul, [b - a for a, b in dims], 1)
            return ir.ArrayType(resolve(kind), size)
        case types.Record(fields):
            return ir.LiteralStructType([resolve(field.type) for field in fields])

    raise ValueError(kind)


COMPARISON = {
    '<': '<',
    '<=': '<=',
    '>': '>',
    '>=': '>=',
    '=': '==',
    '<>': '!=',
}
