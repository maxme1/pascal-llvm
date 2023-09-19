from llvmlite import ir

from .type_system import types, TypeSystem
from .parser import (
    Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Procedure, Function,
    ExpressionStatement
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

    def _deduplicate(self, node: Procedure):
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
            value = self._builder.sitofp(value, resolve(self._cast[node]))
        return value

    # scope utils

    def _access(self, name: Name):
        return self._builder.load(self._allocas[self._references[name]])

    def _assign(self, name: Name, value):
        self._builder.store(value, self._allocas[self._references[name]])

    def _allocate(self, name: Name, kind: ir.Type):
        self._allocas[name] = self._builder.alloca(kind, name=name.name)

    # scope modification

    def _assignment(self, node: Assignment):
        target = node.target
        value = self.visit(node.value)
        if isinstance(target, Name):
            self._assign(target, value)
        else:
            assert isinstance(target, GetItem)
            assert len(target.args) == 1
            ptr = self._builder.gep(
                self._access(target.name), [ir.Constant(ir.IntType(32), 0), self.visit(target.args[0])]
            )
            self._builder.store(value, ptr)

    def _program(self, node: Program):
        main = ir.Function(self.module, ir.FunctionType(ir.VoidType(), ()), '.main')
        self._builders.append(ir.IRBuilder(main.append_basic_block()))

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        for func in node.subroutines:
            ir.Function(
                self.module, ir.FunctionType(resolve(func.return_type), [resolve(arg.type) for arg in func.args]),
                self._deduplicate(func),
            )

        self.visit_sequence(node.subroutines)
        self.visit_sequence(node.body)
        self._builder.ret_void()

        self._builders.pop()

    def _subroutine(self, func, node: Procedure):
        for arg, param in zip(func.args, node.args, strict=True):
            # TODO
            # assert not node_arg.mutable, node_arg
            name = param.name
            arg.name = name.name
            self._allocate(name, resolve(param.type))
            self._builder.store(arg, self._allocas[name])

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        self.visit_sequence(node.body)

    def _function(self, node: Function):
        name = node.name
        func = self.module.get_global(self._deduplicate(node))
        # TODO: util
        self._builders.append(ir.IRBuilder(func.append_basic_block()))

        self._allocate(name, resolve(node.return_type))
        self._subroutine(func, node)
        self._builder.ret(self._builder.load(self._allocas[name]))

        self._builders.pop()

    def _procedure(self, node: Procedure):
        func = self.module.get_global(self._deduplicate(node))
        # TODO: util
        self._builders.append(ir.IRBuilder(func.append_basic_block()))

        self._subroutine(func, node)
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
        self.visit(node.value)

    # expressions

    def _binary(self, node: Binary):
        left = self.visit(node.left)
        right = self.visit(node.right)
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

    def _unary(self, node: Unary):
        value = self.visit(node.value)
        # TODO: typing
        match node.op:
            case '-':
                return self._builder.neg(value)
            case x:
                raise ValueError(x)

    def _call(self, node: Call):
        func = self.module.get_global(self._deduplicate(self._references[node.name]))
        return self._builder.call(func, tuple(map(self.visit, node.args)))

    def _get_item(self, node: GetItem):
        assert len(node.args) == 1
        ptr = self._builder.gep(self._access(node.name), [ir.Constant(ir.IntType(32), 0), self.visit(node.args[0])])
        return self._builder.load(ptr)

    def _name(self, node: Name):
        return self._access(node)

    @staticmethod
    def _const(node: Const):
        return ir.Constant(resolve(node.type), node.value)


def resolve(kind):
    return TypeResolver().visit(kind)


# TODO: remove the hardcoded 32
class TypeResolver(Visitor):
    @staticmethod
    def _integer(value):
        return ir.IntType(32)

    @staticmethod
    def _real(value):
        return ir.FloatType()

    def _array(self, value: types.Array):
        dims = value.dims
        assert len(dims) == 1
        return ir.ArrayType(self.visit(value.type), dims[0])
