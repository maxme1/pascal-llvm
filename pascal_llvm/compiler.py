from functools import reduce
from operator import mul

from jboc import composed
from llvmlite import ir

from .type_system import types, TypeSystem
from .parser import (
    Program, Binary, Call, Const, Assignment, Name, If, Unary, For, GetItem, While, Function,
    ExpressionStatement, GetField, Dereference
)
from .type_system.visitor import MAGIC_FUNCTIONS
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
        self._names = {}

        # external and builtins
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], True), 'printf')
        ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()], True), 'scanf')

        # TODO: some of these functions can be defined in pascal
        self._magic = {
            'writeln': self._writeln,
            'write': self._write,
            'readln': self._readln,
            'read': self._read,
            'chr': self._chr,
            'random': self._random,
            'inc': self._inc,
            # FIXME
            'randomize': lambda: None,
        }
        self._string_idx = 0

    @property
    def _builder(self) -> ir.IRBuilder:
        return self._builders[-1]

    def _deduplicate(self, node: Function):
        if node not in self._names:
            # TODO: pretify
            self._names[node] = f'function.{len(self._names)}.{node.name.name}'
        return self._names[node]

    def _string_pointer(self, value: bytes):
        kind = ir.ArrayType(ir.IntType(8), len(value))
        global_string = ir.GlobalVariable(self.module, kind, name=f'string.{self._string_idx}.global')
        global_string.initializer = ir.Constant(kind, [ir.Constant(ir.IntType(8), x) for x in value])
        self._string_idx += 1
        return self._builder.gep(
            global_string, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]
        )

    @composed(b' '.join)
    def _format_io(self, args):
        for arg in args:
            match self._type(arg):
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

    def _writeln(self, *args):
        ptr = self._string_pointer(self._format_io(args) + b'\n\00')
        return self._builder.call(self.module.get_global('printf'), [ptr, *self.visit_sequence(args, False)])

    def _write(self, *args):
        ptr = self._string_pointer(self._format_io(args) + b'\00')
        return self._builder.call(self.module.get_global('printf'), [ptr, *self.visit_sequence(args, False)])

    def _readln(self, *args):
        ptr = self._string_pointer(self._format_io(args) + b'\n\00')
        return self._builder.call(self.module.get_global('scanf'), [ptr, *self.visit_sequence(args, True)])

    def _read(self, *args):
        ptr = self._string_pointer(self._format_io(args) + b'\00')
        return self._builder.call(self.module.get_global('scanf'), [ptr, *self.visit_sequence(args, True)])

    def _chr(self, *args):
        value, = args
        return self._builder.trunc(self.visit(value, False), ir.IntType(8))

    def _random(self, *args):
        # FIXME
        return ir.Constant(ir.IntType(32), 0)

    def _inc(self, *args):
        ptr, = args
        ptr = self.visit(ptr, True)
        # FIXME
        value = self._builder.add(self._builder.load(ptr), ir.Constant(ir.IntType(32), 0))
        self._builder.store(value, ptr)

    # typing

    def _type(self, node):
        if node in self._ts.casting:
            return self._ts.casting[node][1]
        return self._ts.types[node]

    def _cast(self, value, src: types.DataType, dst: types.DataType, lvalue: bool):
        # references are just fancy pointers
        if isinstance(src, types.Reference) and not lvalue:
            src = src.type
            value = self._builder.load(value)

        if src == dst:
            return value

        if src in types.Ints and dst in types.Floats:
            return self._builder.sitofp(value, resolve(dst))

        if src in types.Ints and dst in types.Ints:
            return self._builder.sext(value, resolve(dst))

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
        return self._builder.load(self._allocas[self._references[name]])

    def _assign(self, name: Name, value):
        target = self._references[name]
        ptr = self._allocas[target]
        if isinstance(self._type(target), types.Reference):
            ptr = self._builder.load(ptr)
        self._builder.store(value, ptr)

    def _allocate(self, name: Name, kind: ir.Type, initial=None):
        self._allocas[name] = self._builder.alloca(kind, name=name.name)
        if initial is not None:
            self._builder.store(initial, self._allocas[name])

    def _allocate_global(self, name: Name, kind: ir.Type):
        var = ir.GlobalVariable(self.module, kind, name=name.name)
        var.linkage = 'internal'
        self._allocas[name] = var

    # scope modification

    def _assignment(self, node: Assignment):
        ptr = self.visit(node.target, lvalue=True)
        value = self.visit(node.value, lvalue=False)
        if isinstance(self._type(node.value), types.Reference):
            value = self._builder.load(value)

        self._builder.store(value, ptr)

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
        condition = self.visit(node.condition, lvalue=False)
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
        condition = self.visit(node.condition, False)
        self._builder.cbranch(condition, loop_block, end_block)

        # loop
        self._builder.position_at_end(loop_block)
        self.visit_sequence(node.body)
        self._builder.branch(check_block)

        # exit
        self._builder.position_at_end(end_block)

    def _for(self, node: For):
        name = node.name
        start = self.visit(node.start, lvalue=False)
        stop = self.visit(node.stop, lvalue=False)
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
        kind = self._type(node.left)
        assert kind == self._type(node.right), (kind, self._type(node.right))

        # TODO: simplify
        match kind:
            case types.SignedInt(_):
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
                    case '<>':
                        return self._builder.icmp_signed('!=', left, right)
                    case x:
                        raise ValueError(x)
            case types.Floating(_):
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

            case types.Boolean:
                match node.op:
                    case 'and':
                        return self._builder.and_(left, right)
                    case 'or':
                        return self._builder.or_(left, right)
                    case x:
                        raise ValueError(x)
            case _:
                raise TypeError(node.op, kind)

    def _unary(self, node: Unary, lvalue: bool):
        # getting the address is a special case
        if node.op == '@':
            # just get the name's address
            return self.visit(node.value, lvalue=True)

        value = self.visit(node.value, lvalue)
        # TODO: typing
        match node.op:
            case '-':
                return self._builder.neg(value)
            case x:
                raise ValueError(x, node)

    def _call(self, node: Call, lvalue: bool):
        # TODO: mb need a special node type for magic calls?
        lower = node.name.name.lower()
        if lower in MAGIC_FUNCTIONS:
            return self._magic[lower](*node.args)

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
            local = self._builder.sub(local, ir.Constant(ir.IntType(32), start))
            # multiply by stride
            local = self._builder.mul(local, ir.Constant(ir.IntType(32), stride))
            # add to index
            idx = self._builder.add(idx, local)
            stride *= stop - start

        ptr = self._builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), idx])
        if lvalue:
            return ptr
        return self._builder.load(ptr)

    def _get_field(self, node: GetField, lvalue: bool):
        ptr = self.visit(node.target, True)
        kind = self._type(node.target)
        if isinstance(kind, types.Reference):
            kind = kind.type
        idx, = [i for i, field in enumerate(kind.fields) if field.name == node.name]
        ptr = self._builder.gep(
            ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), idx)]
        )
        if lvalue:
            return ptr
        return self._builder.load(ptr)

    def _dereference(self, node: Dereference, lvalue: bool):
        ptr = self._builder.load(self.visit(node.target, True))
        if lvalue:
            return ptr
        return self._builder.load(ptr)

    def _name(self, node: Name, lvalue: bool):
        if node in self._desugar:
            return self.visit(self._desugar[node], lvalue)

        target = self._references[node]
        ptr = self._allocas[target]
        if lvalue:
            if isinstance(self._type(target), types.Reference):
                ptr = self._builder.load(ptr)
            return ptr
        return self._builder.load(ptr)

    def _const(self, node: Const, lvalue: bool):
        # TODO: f32 doesn't work
        kind = node.type
        value = node.value
        if isinstance(kind, types.StaticArray) and kind.type == types.Char:
            return self._string_pointer(value)

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
