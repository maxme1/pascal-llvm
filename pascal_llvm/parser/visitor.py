import types
from tokenize import TokenInfo

from jboc import composed
from more_itertools import peekable

from ..tokenizer import TokenType
from .nodes import *


def parse(tokens):
    return Parser(tokens)._program()


class Parser:
    def __init__(self, tokens):
        self.tokens = peekable(tokens)

    def _program(self):
        self.consume(TokenType.NAME, string='program')
        self.consume(TokenType.NAME)
        if self.consumed(TokenType.LPAR):
            self.consume(TokenType.NAME)
            self.consume(TokenType.RPAR)

        self.consume(TokenType.SEMI)

        variables = self._variables()
        functions, prototypes = [], []
        while self.peek().string.lower() in ['function', 'procedure', 'external']:
            if self.peek().string.lower() == 'external':
                self.consume()
                prototypes.append(self._prototype())
            else:
                functions.append(self._function())

        statements = self._body()

        self.consume(TokenType.DOT)
        if self.tokens:
            raise ParseError(self.tokens[0])

        return Program(variables, tuple(prototypes), tuple(functions), statements)

    @composed(tuple)
    def _variables(self):
        while self.consumed(TokenType.NAME, string='var'):
            while self.peek().string.lower() not in ('var', 'function', 'procedure', 'external', 'begin'):
                yield self._definition()

    def _prototype(self):
        is_func = self.consumed(TokenType.NAME, string='function')
        if not is_func:
            self.consume(TokenType.NAME, string='procedure')

        name = Name(self.consume(TokenType.NAME).string)

        args = []
        if self.consumed(TokenType.LPAR):
            while not self.matches(TokenType.RPAR):
                mutable = self.consumed(TokenType.NAME, string='var')
                group = [Name(self.consume(TokenType.NAME).string)]
                while self.consumed(TokenType.COMMA):
                    group.append(Name(self.consume(TokenType.NAME).string))
                self.consume(TokenType.COLON)
                kind = self._type()
                if mutable:
                    kind = types.Reference(kind)
                args.extend(ArgDefinition(x, kind) for x in group)
                # TODO: potential problem
                self.consumed(TokenType.COMMA, TokenType.SEMI)

            self.consume(TokenType.RPAR)

        if is_func:
            self.consume(TokenType.COLON)
            ret = self._type()
        else:
            ret = types.Void
        self.consume(TokenType.SEMI)
        return Prototype(name, tuple(args), ret)

    def _function(self):
        proto = self._prototype()
        variables = self._variables()
        body = self._body()
        self.consume(TokenType.SEMI)
        return Function(proto.name, proto.args, variables, body, proto.return_type)

    @composed(tuple)
    def _body(self):
        self.consume(TokenType.NAME, string='begin')
        while not self.matches(TokenType.NAME, string='end'):
            if self.matches(TokenType.NAME, string='begin'):
                # TODO: is this ok?
                yield from self._body()
            else:
                yield self._statement()
            # the semicolon is optional in the last statement
            if not self.matches(TokenType.NAME, string='end'):
                self.consume(TokenType.SEMI)
        self.consume(TokenType.NAME, string='end')

    def _definition(self):
        names = [Name(self.consume(TokenType.NAME).string)]
        while self.consumed(TokenType.COMMA):
            names.append(Name(self.consume(TokenType.NAME).string))
        self.consume(TokenType.COLON)
        kind = self._type()
        self.consume(TokenType.SEMI)
        return Definitions(tuple(names), kind)

    def _type(self):
        if self.consumed(TokenType.CIRCUMFLEX):
            return types.Pointer(self._type())

        if self.consumed(TokenType.NAME, string='array'):
            if self.consumed(TokenType.LSQB):
                # true array
                dims = [self._array_dims()]
                while self.consumed(TokenType.COMMA):
                    dims.append(self._array_dims())
                self.consume(TokenType.RSQB)

                self.consume(TokenType.NAME, string='of')
                internal = self._type()
                return types.StaticArray(tuple(dims), internal)

            # just a pointer
            self.consume(TokenType.NAME, string='of')
            internal = self._type()
            return types.DynamicArray(internal)

        # string is just a special case of an array
        if self.consumed(TokenType.NAME, string='string'):
            if self.consumed(TokenType.LSQB):
                dims = self._array_dims(),
                self.consume(TokenType.RSQB)
                return types.StaticArray(dims, types.Char)

            return types.DynamicArray(types.Char)

        if self.consumed(TokenType.NAME, string='record'):
            fields = []
            while not self.consumed(TokenType.NAME, string='end'):
                definition = self._definition()
                for name in definition.names:
                    fields.append(types.Field(name.name, definition.type))
            return types.Record(tuple(fields))

        kind = self.consume(TokenType.NAME).string.lower()
        return types.dispatch(kind)

    def _int(self):
        neg = self.consumed(TokenType.OP, string='-')
        value = int(self.consume(TokenType.NUMBER).string)
        if neg:
            return -value
        return value

    def _array_dims(self):
        first = self._int()
        if self.consumed(TokenType.DOT):
            self.consume(TokenType.DOT)
            return first, self._int()
        return 0, first

    def _statement(self):
        if self.matches(TokenType.NAME, string='if'):
            return self._if()
        if self.matches(TokenType.NAME, string='for'):
            return self._for()
        if self.matches(TokenType.NAME, string='while'):
            return self._while()

        value = self._expression()
        if isinstance(value, (Name, GetItem, GetField, Dereference)) and self.consumed(TokenType.COLONEQUAL):
            value = Assignment(value, self._expression())
        else:
            value = ExpressionStatement(value)
        return value

    def _flexible_body(self):
        if self.matches(TokenType.NAME, string='begin'):
            return self._body()
        return self._statement(),

    def _if(self):
        self.consume(TokenType.NAME, string='if')
        condition = self._expression()
        self.consume(TokenType.NAME, string='then')
        left = self._flexible_body()
        if self.consumed(TokenType.NAME, string='else'):
            right = self._flexible_body()
        else:
            right = ()
        return If(condition, left, right)

    def _while(self):
        self.consume(TokenType.NAME, string='while')
        condition = self._expression()
        self.consume(TokenType.NAME, string='do')
        body = self._flexible_body()
        return While(condition, body)

    def _for(self):
        self.consume(TokenType.NAME, string='FOR')
        name = Name(self.consume(TokenType.NAME).string)
        self.consume(TokenType.COLONEQUAL)
        start = self._expression()
        self.consume(TokenType.NAME, string='TO')
        stop = self._expression()
        self.consume(TokenType.NAME, string='do')
        body = self._flexible_body()
        return For(name, start, stop, body)

    def _expression(self):
        # TODO: for now this will be the FFI
        if self.consumed(TokenType.VBAR):
            name = Name('|' + self.consume(TokenType.NAME).string)
            args = self._args()
            return Call(name, args)

        return self._binary(MAX_PRIORITY)

    def _binary(self, priority):
        if priority <= 0:
            return self._unary()

        left = self._binary(priority - 1)
        while self.matches(TokenType.OP, TokenType.NAME) and self.peek().string in PRIORITIES:
            op = self.peek().string
            current = PRIORITIES.get(op)
            # only consume the operation with the same priority
            if current != priority:
                break

            self.consume()
            right = self._binary(current - 1)
            left = Binary(op, left, right)

        return left

    def _unary(self):
        # FIXME
        if self.peek().string in ('@', 'not', '-'):
            return Unary(self.consume().string, self._unary())
        return self._tail()

    def _tail(self):
        target = self._primary()
        while self.matches(TokenType.LSQB, TokenType.DOT, TokenType.CIRCUMFLEX):
            match self.consume().type:
                case TokenType.LSQB:
                    args = [self._expression()]
                    while self.matches(TokenType.COMMA):
                        self.consume()
                        args.append(self._expression())
                    self.consume(TokenType.RSQB)
                    target = GetItem(target, tuple(args))

                case TokenType.DOT:
                    name = self.consume(TokenType.NAME).string
                    target = GetField(target, name)

                case TokenType.CIRCUMFLEX:
                    target = Dereference(target)

        return target

    def _primary(self):
        match self.peek().type:
            case TokenType.NUMBER:
                body = self.consume().string
                if '.' not in body:
                    value = int(body)
                    for kind in types.Ints:
                        if value.bit_length() < kind.bits:
                            return Const(value, kind)

                return Const(float(body), types.Real)

            case TokenType.STRING:
                value = self.consume().string
                if not value.startswith("'"):
                    raise ParseError('Strings must start and end with apostrophes')
                value = eval(value).encode() + b'\00'
                return Const(value, types.StaticArray(((0, len(value)),), types.Char))

            case TokenType.LPAR:
                self.consume()
                value = self._expression()
                self.consume(TokenType.RPAR)
                return value

            case TokenType.NAME:
                # TOdo: keywords?
                name = Name(self.consume().string)
                if self.matches(TokenType.LPAR):
                    return Call(name, self._args())
                return name

            case _:
                raise ParseError(self.peek())

    def _args(self):
        args = []
        self.consume(TokenType.LPAR)
        while not self.matches(TokenType.RPAR):
            if args:
                self.consume(TokenType.COMMA)
            args.append(self._expression())

        self.consume(TokenType.RPAR)
        return tuple(args)

    # internals
    def consume(self, *types: TokenType, string: str | None = None) -> TokenInfo:
        if types and self.peek().type not in types:
            raise ParseError(self.peek(), types)
        token = next(self.tokens)
        if string is not None and token.string.lower() != string.lower():
            raise ParseError(token, string)
        return token

    def consumed(self, *types: TokenType, string: str | None = None) -> bool:
        success = self.matches(*types, string=string)
        if success:
            self.consume()
        return success

    def peek(self) -> TokenInfo:
        if not self.tokens:
            raise ParseError
        return self.tokens.peek()

    def matches(self, *types: TokenType, string: str | None = None) -> bool:
        if not self.tokens:
            return False
        token = self.peek()
        if types and token.type not in types:
            return False
        if string is not None and token.string.lower() != string.lower():
            return False
        return True


PRIORITIES = {
    '*': 1,
    '/': 1,
    'div': 1,
    'mod': 1,
    'and': 1,
    '+': 2,
    '-': 2,
    'or': 2,
    '>': 3,
    '>=': 3,
    '<=': 3,
    '<': 3,
    '=': 4,
    '<>': 4,
    'in': 4,
}
MAX_PRIORITY = max(PRIORITIES.values())
