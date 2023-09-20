from llvmlite import ir

from .type_system import types, TypeSystem
from .parser import (
    Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Function,
    ExpressionStatement, GetField
)
from .visitor import Visitor


class Compiler(Visitor):
    @classmethod
    def compile(cls, root):
        ts = TypeSystem()
        ts.visit(root)
        compiler = cls(ts)
        compiler.visit(root)
        return compiler.module

    def __init__(self, ts: TypeSystem):
        self.module = ir.Module()

        # external and builtins
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], True), 'printf')
        self._writeln()

        self._builders = []
        self._cast = ts.casting
        self._types = ts.types
        self._references = ts.references
        self._allocas = {}
        self._names = {ts._writeln: 'WRITELN'}

    @property
    def _builder(self) -> ir.IRBuilder:
        return self._builders[-1]

    def _deduplicate(self, node: Function):
        if node not in self._names:
            # TODO: pretify
            self._names[node] = f'{len(self._names)}.{node.name.name}'
        return self._names[node]

    def _writeln(self):
        func = ir.Function(self.module, ir.FunctionType(ir.VoidType(), [ir.IntType(32)]), 'WRITELN')
        builder = ir.IRBuilder(func.append_basic_block())

        fmt = b'%d\n\00'
        local = builder.alloca(ir.ArrayType(ir.IntType(8), len(fmt)))
        builder.store(ir.Constant(
            ir.ArrayType(ir.IntType(8), len(fmt)),
            [ir.Constant(ir.IntType(8), x) for x in fmt],
        ), local)
        pointer = builder.gep(local, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

        builder.call(self.module.get_global('printf'), [pointer, *func.args])
        builder.ret_void()

    # typing

    def after_visit(self, node, value):
        if node in self._cast:
            src, dst = self._cast[node]
            assert not isinstance(dst, types.Reference)

            # references are just fancy pointers
            if isinstance(src, types.Reference):
                src = src.type
                value = self._builder.load(value)

            if src != dst:
                value = self._builder.sitofp(value, resolve(dst))
        return value

    # scope utils

    def _access(self, name: Name):
        return self._builder.load(self._allocas[self._references[name]])

    def _assign(self, name: Name, value):
        target = self._references[name]
        ptr = self._allocas[target]
        if isinstance(self._types[target], types.Reference):
            ptr = self._builder.load(ptr)
        self._builder.store(value, ptr)

    def _allocate(self, name: Name, kind: ir.Type, initial=None):
        self._allocas[name] = self._builder.alloca(kind, name=name.name)
        if initial is not None:
            self._builder.store(initial, self._allocas[name])

    # scope modification

    def _assignment(self, node: Assignment):
        ptr = self.visit(node.target, lvalue=True)
        value = self.visit(node.value, lvalue=False)
        if isinstance(self._types[node.value], types.Reference):
            value = self._builder.load(value)

        self._builder.store(value, ptr)

    def _program(self, node: Program):
        main = ir.Function(self.module, ir.FunctionType(ir.VoidType(), ()), '.main')
        self._builders.append(ir.IRBuilder(main.append_basic_block()))

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        for func in node.functions:
            ir.Function(
                self.module, ir.FunctionType(resolve(func.return_type), [resolve(arg.type) for arg in func.args]),
                self._deduplicate(func),
            )

        self.visit_sequence(node.functions)
        self.visit_sequence(node.body)
        self._builder.ret_void()

        self._builders.pop()

    def _function(self, node: Function):
        ret = node.name
        func = self.module.get_global(self._deduplicate(node))
        self._builders.append(ir.IRBuilder(func.append_basic_block()))

        if node.return_type != types.Void:
            self._allocate(ret, resolve(node.return_type))

        for arg, param in zip(func.args, node.args, strict=True):
            name = param.name
            arg.name = name.name
            self._allocate(name, resolve(param.type), arg)

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        self.visit_sequence(node.body)

        if node.return_type != types.Void:
            self._builder.ret(self._builder.load(self._allocas[ret]))
        else:
            self._builder.ret_void()

        self._builders.pop()

    # statements

    def _if(self, node: If):
        condition = self.visit(node.condition)
        then_block = self._builder.append_basic_block()
        else_block = self._builder.append_basic_block()
        merged_block = self._builder.append_basic_block()
        self._builder.cbranch(condition, then_block, else_block)

        # then
        self._builder.position_at_end(then_block)
        self.visit_sequence(node.then_)
        self._builder.branch(merged_block)
        # else
        self._builder.position_at_end(else_block)
        self.visit_sequence(node.else_)
        self._builder.branch(merged_block)
        # phi
        self._builder.position_at_end(merged_block)

    def _while(self, node: While):
        check_block = self._builder.append_basic_block('check')
        loop_block = self._builder.append_basic_block('for')
        end_block = self._builder.append_basic_block('for-end')
        self._builder.branch(check_block)

        # check
        self._builder.position_at_end(check_block)
        condition = self.visit(node.condition)
        self._builder.cbranch(condition, loop_block, end_block)

        # loop
        self._builder.position_at_end(loop_block)
        self.visit_sequence(node.body)
        self._builder.branch(check_block)

        # exit
        self._builder.position_at_end(end_block)

    def _for(self, node: For):
        name = node.name
        start = self.visit(node.start)
        stop = self.visit(node.stop)
        self._assign(name, start)

        check_block = self._builder.append_basic_block('check')
        loop_block = self._builder.append_basic_block('for')
        end_block = self._builder.append_basic_block('for-end')
        self._builder.branch(check_block)

        # check
        self._builder.position_at_end(check_block)
        # TODO: type
        condition = self._builder.icmp_signed('<=', self._access(name), stop, 'for-condition')
        self._builder.cbranch(condition, loop_block, end_block)

        # loop
        self._builder.position_at_end(loop_block)
        self.visit_sequence(node.body)
        # update
        # TODO: type
        self._assign(name, self._builder.add(self._access(name), ir.Constant(resolve(types.Integer), 1), 'increment'))
        self._builder.branch(check_block)

        # exit
        self._builder.position_at_end(end_block)

    def _expression_statement(self, node: ExpressionStatement):
        self.visit(node.value, lvalue=False)

    # expressions

    def _binary(self, node: Binary, lvalue: bool):
        left = self.visit(node.left, lvalue)
        right = self.visit(node.right, lvalue)
        family = self._types[node].family

        # TODO: simplify
        match family:
            case types.Ints:
                match node.op:
                    case '+':
                        return self._builder.add(left, right)
                    case '-':
                        return self._builder.sub(left, right)
                    case '*':
                        return self._builder.mul(left, right)
                    case '<' | '<=' | '>' | '>=' as x:
                        return self._builder.icmp_signed(x, left, right)
                    case '=':
                        return self._builder.icmp_signed('==', left, right)
                    case x:
                        raise ValueError(x)
            case types.Floats:
                match node.op:
                    case '+':
                        return self._builder.fadd(left, right)
                    case '-':
                        return self._builder.fsub(left, right)
                    case '*':
                        return self._builder.fmul(left, right)
                    case '<' | '<=' | '>' | '>=' as x:
                        return self._builder.fcmp_ordered(x, left, right)
                    case '=':
                        return self._builder.fcmp_ordered('==', left, right)
                    case x:
                        raise ValueError(x)

            case default:
                raise TypeError(default)

    def _unary(self, node: Unary, lvalue: bool):
        value = self.visit(node.value, lvalue)
        # TODO: typing
        match node.op:
            case '-':
                return self._builder.neg(value)
            case x:
                raise ValueError(x)

    def _call(self, node: Call, lvalue: bool):
        func = self.module.get_global(self._deduplicate(self._references[node.name]))
        signature = self._references[node.name].signature

        args = []
        for arg, kind in zip(node.args, signature.args, strict=True):
            if isinstance(kind, types.Reference):
                assert isinstance(arg, Name)
                value = self._allocas[self._references[arg]]
            else:
                value = self.visit(arg, False)
            args.append(value)

        return self._builder.call(func, args)

    def _get_item(self, node: GetItem, lvalue: bool):
        assert len(node.args) == 1
        # we always want a pointer from the parent
        ptr = self.visit(node.target, True)
        ptr = self._builder.gep(
            ptr, [ir.Constant(ir.IntType(32), 0), self.visit(node.args[0], False)]
        )
        if lvalue:
            return ptr
        return self._builder.load(ptr)

    def _get_field(self, node: GetField, lvalue: bool):
        ptr = self.visit(node.target, True)
        kind = self._types[node.target]
        if isinstance(kind, types.Reference):
            kind = kind.type
        idx, = [i for i, field in enumerate(kind.fields) if field.name == node.name]
        ptr = self._builder.gep(
            ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
        )
        if lvalue:
            return ptr
        return self._builder.load(ptr)

    def _name(self, node: Name, lvalue: bool):
        target = self._references[node]
        ptr = self._allocas[target]
        if lvalue:
            if isinstance(self._types[target], types.Reference):
                ptr = self._builder.load(ptr)
            return ptr
        return self._builder.load(ptr)

    @staticmethod
    def _const(node: Const, lvalue: bool):
        return ir.Constant(resolve(node.type), node.value)


def resolve(kind):
    return TypeResolver().visit(kind)


# TODO: remove the hardcoded 32
class TypeResolver(Visitor):
    @staticmethod
    def _void(value):
        return ir.VoidType()

    @staticmethod
    def _integer(value):
        return ir.IntType(32)

    @staticmethod
    def _real(value):
        return ir.FloatType()

    @staticmethod
    def _string(value):
        return ir.ArrayType(ir.IntType(8))

    def _reference(self, value: types.Reference):
        return ir.PointerType(self.visit(value.type))

    def _array(self, value: types.Array):
        dims = value.dims
        assert len(dims) == 1
        return ir.ArrayType(self.visit(value.type), dims[0])

    def _record(self, value: types.Record):
        return ir.LiteralStructType([self.visit(field.type) for field in value.fields])
