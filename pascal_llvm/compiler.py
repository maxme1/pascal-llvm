from llvmlite import ir

from . import types
from .parser import Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Procedure, Function
from .visitor import Visitor


class Compiler(Visitor):
    @classmethod
    def compile(cls, root):
        compiler = cls()
        compiler.visit(root)
        return compiler.module

    def __init__(self):
        self.module = ir.Module()

        # external and builtins
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], True), 'printf')
        self._writeln()

        self._builders = []
        self._scopes = []
        # FIXME: an ugly crutch for now
        self._func_return_names = []

    @property
    def _builder(self) -> ir.IRBuilder:
        return self._builders[-1]

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

    # scope utils

    def _resolve(self, name):
        return self._scopes[-1][name]

    def _store(self, name, value):
        self._builder.store(value, self._resolve(name))

    def _load(self, name):
        return self._builder.load(self._resolve(name))

    def _allocate(self, name, kind):
        # TODO: duplicates
        self._scopes[-1][name] = self._builder.alloca(kind)

    # scope modification

    def _assignment(self, node: Assignment):
        value = self.visit(node.value)
        match node.target:
            case Name(name):
                if self._func_return_names and name == self._func_return_names[-1]:
                    name = f'.{name}'
                self._store(name, value)
            case GetItem(name, args):
                assert len(args) == 1
                ptr = self._builder.gep(self._resolve(name), [ir.Constant(ir.IntType(32), 0), self.visit(args[0])])
                return self._builder.store(value, ptr)
            case default:
                raise TypeError(default)

    def _program(self, node: Program):
        main = ir.Function(self.module, ir.FunctionType(ir.VoidType(), ()), '.main')
        self._builders.append(ir.IRBuilder(main.append_basic_block()))
        self._scopes.append({})

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        for subroutine in node.subroutines:
            self.visit(subroutine)

        self.visit_sequence(node.body)
        self._builder.ret_void()

        self._builders.pop()
        self._scopes.pop()

    def _subroutine(self, func, node: Procedure):
        for f_arg, node_arg in zip(func.args, node.args, strict=True):
            # TODO
            # assert not node_arg.mutable, node_arg
            name = f_arg.name = node_arg.name
            self._allocate(name, resolve(node_arg.type))
            self._store(name, f_arg)

        for definitions in node.variables:
            for name in definitions.names:
                self._allocate(name, resolve(definitions.type))

        self.visit_sequence(node.body)

    def _function(self, node: Function):
        return_type = resolve(node.return_type)
        func = ir.Function(
            self.module, ir.FunctionType(return_type, [resolve(arg.type) for arg in node.args]), node.name
        )
        # TODO: util
        self._builders.append(ir.IRBuilder(func.append_basic_block()))
        self._scopes.append({})

        ret_name = f'.{node.name}'
        self._func_return_names.append(node.name)
        self._allocate(ret_name, return_type)

        self._subroutine(func, node)
        self._builder.ret(self._load(ret_name))

        self._func_return_names.pop()
        self._builders.pop()
        self._scopes.pop()

    def _procedure(self, node: Procedure):
        func = ir.Function(
            self.module, ir.FunctionType(ir.VoidType(), [resolve(arg.type) for arg in node.args]), node.name
        )
        # TODO: util
        self._builders.append(ir.IRBuilder(func.append_basic_block()))
        self._scopes.append({})

        self._subroutine(func, node)
        self._builder.ret_void()

        self._builders.pop()
        self._scopes.pop()

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
        self._store(name, start)

        check_block = self._builder.append_basic_block('check')
        loop_block = self._builder.append_basic_block('for')
        end_block = self._builder.append_basic_block('for-end')
        self._builder.branch(check_block)

        # check
        self._builder.position_at_end(check_block)
        condition = self._builder.icmp_signed('<=', self._load(name), stop, 'for-condition')
        self._builder.cbranch(condition, loop_block, end_block)

        # loop
        self._builder.position_at_end(loop_block)
        self.visit_sequence(node.body)
        # update
        self._store(name, self._builder.add(self._load(name), ir.Constant(resolve(types.Integer), 1), 'increment'))
        self._builder.branch(check_block)

        # exit
        self._builder.position_at_end(end_block)

    # expressions

    def _binary(self, node: Binary):
        left = self.visit(node.left)
        right = self.visit(node.right)
        # TODO: typing
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

    def _unary(self, node: Unary):
        value = self.visit(node.value)
        # TODO: typing
        match node.op:
            case '-':
                return self._builder.neg(value)
            case x:
                raise ValueError(x)

    def _call(self, node: Call):
        func = self.module.get_global(node.name)
        return self._builder.call(func, tuple(map(self.visit, node.args)))

    def _get_item(self, node: GetItem):
        assert len(node.args) == 1
        ptr = self._builder.gep(self._resolve(node.name), [ir.Constant(ir.IntType(32), 0), self.visit(node.args[0])])
        return self._builder.load(ptr)

    def _name(self, node: Name):
        return self._load(node.name)

    @staticmethod
    def _const(node: Const):
        return ir.Constant(resolve(node.type), node.value)


def resolve(kind):
    return TypeResolver().visit(kind)


class TypeResolver(Visitor):
    @staticmethod
    def _integer(value):
        return ir.IntType(32)

    def _array(self, value: types.Array):
        dims = value.dims
        assert len(dims) == 1
        return ir.ArrayType(self.visit(value.type), dims[0])
