from tokenize import TokenInfo

from jboc import collect
from more_itertools import peekable

from ..tokenizer import TokenType
from .nodes import *


def parse(tokens):
    return Parser(tokens)._program()


class Parser:
    def __init__(self, tokens):
        self.tokens = peekable(tokens)

    def _program(self):
        self.consume(TokenType.NAME, string='PROGRAM')
        name = self.consume(TokenType.NAME).string
        if self.matches(TokenType.LPAR):
            self.consume()
            self.consume(TokenType.NAME)
            self.consume(TokenType.RPAR)

        self.consume(TokenType.SEMI)

        variables = self._variables()
        subroutines = []
        while self.peek().string in ['FUNCTION', 'PROCEDURE']:
            subroutines.append(self._subroutine())

        statements = self._body()

        self.consume(TokenType.DOT)
        return Program(name, variables, subroutines, statements)

    @collect
    def _variables(self):
        while self.matches(TokenType.NAME, string='VAR'):
            self.consume()
            yield self._definition()

    def _subroutine(self):
        if self.matches(TokenType.NAME, string='FUNCTION'):
            self.consume()
            cls = Function
        else:
            self.consume(TokenType.NAME, string='PROCEDURE')
            cls = Procedure

        name = self.consume(TokenType.NAME).string

        args = []
        self.consume(TokenType.LPAR)
        while not self.matches(TokenType.RPAR):
            mutable = self.matches(TokenType.NAME, string='VAR')
            if mutable:
                self.consume()
            arg = self.consume(TokenType.NAME).string
            self.consume(TokenType.COLON)
            kind = self._type()
            args.append(ArgDefinition(arg, mutable, kind))
            if self.matches(TokenType.COMMA):
                self.consume()

        self.consume(TokenType.RPAR)

        if cls is Function:
            self.consume(TokenType.COLON)
            ret = self._type()
        self.consume(TokenType.SEMI)

        variables = self._variables()
        body = self._body()
        self.consume(TokenType.SEMI)
        # FIXME
        if cls is Function:
            return cls(name, args, variables, body, ret)
        return cls(name, args, variables, body)

    def _body(self):
        statements = []
        self.consume(TokenType.NAME, string='BEGIN')
        while not self.matches(TokenType.NAME, string='END'):
            statements.append(self._statement())
            # the semicolon is optional in the last statement
            if not self.matches(TokenType.NAME, string='END'):
                self.consume(TokenType.SEMI)
        self.consume(TokenType.NAME, string='END')
        return statements

    def _definition(self):
        names = [self.consume(TokenType.NAME).string]
        while self.matches(TokenType.COMMA):
            self.consume()
            names.append(self.consume(TokenType.NAME).string)

        self.consume(TokenType.COLON)
        kind = self._type()
        self.consume(TokenType.SEMI)
        return Definitions(names, kind)

    def _type(self):
        if self.matches(TokenType.NAME, string='ARRAY'):
            self.consume()

            self.consume(TokenType.LSQB)
            dims = [int(self.consume(TokenType.NUMBER).string)]
            while self.matches(TokenType.COMMA):
                self.consume()
                dims.append(int(self.consume(TokenType.NUMBER).string))
            self.consume(TokenType.RSQB)

            self.consume(TokenType.NAME, string='OF')
            internal = self._type()
            return types.Array(dims, internal)

        kind = self.consume(TokenType.NAME).string.lower()
        kinds = {
            'integer': types.Integer,
            # 'real': types.Real,
        }
        return kinds[kind]

    def _statement(self):
        if self.matches(TokenType.NAME, string='IF'):
            return self._if()
        if self.matches(TokenType.NAME, string='FOR'):
            return self._for()
        if self.matches(TokenType.NAME, string='WHILE'):
            return self._while()

        value = self._expression()
        if isinstance(value, (Name, GetItem)) and self.matches(TokenType.COLONEQUAL):
            self.consume()
            value = Assignment(value, self._expression())
        return value

    def _flexible_body(self):
        if self.matches(TokenType.NAME, string='BEGIN'):
            return self._body()
        return [self._statement()]

    def _if(self):
        self.consume(TokenType.NAME, string='IF')
        condition = self._expression()
        self.consume(TokenType.NAME, string='THEN')
        left = self._flexible_body()
        if self.matches(TokenType.NAME, string='ELSE'):
            self.consume()
            right = self._flexible_body()
        else:
            right = []
        return If(condition, left, right)

    def _while(self):
        self.consume(TokenType.NAME, string='WHILE')
        condition = self._expression()
        self.consume(TokenType.NAME, string='DO')
        body = self._body()
        return While(condition, body)

    def _for(self):
        self.consume(TokenType.NAME, string='FOR')
        name = self.consume(TokenType.NAME).string
        self.consume(TokenType.COLONEQUAL)
        start = self._expression()
        self.consume(TokenType.NAME, string='TO')
        stop = self._expression()
        self.consume(TokenType.NAME, string='DO')
        body = self._body()
        return For(name, start, stop, body)

    def _expression(self):
        return self._binary(MAX_PRIORITY)

    def _binary(self, priority):
        if priority <= 0:
            return self._unary()

        left = self._binary(priority - 1)
        while self.matches(TokenType.OP):
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
        if self.matches(TokenType.OP):
            return Unary(self.consume().string, self._unary())
        return self._tail()

    def _tail(self):
        match self.peek().type:
            case TokenType.NUMBER:
                body = self.consume().string
                if '.' in body:
                    return Const(float(body), types.Real)
                return Const(int(body), types.Integer)

            case TokenType.STRING:
                return Const(self.consume().string, types.String)

            case TokenType.LPAR:
                self.consume()
                value = self._expression()
                self.consume(TokenType.RPAR)
                return value

            case TokenType.NAME:
                # TODO: keywords?
                name = self.consume().string
                if self.matches(TokenType.LPAR):
                    return Call(name, self._args())
                if self.matches(TokenType.LSQB):
                    self.consume()
                    args = [self._expression()]
                    while self.matches(TokenType.COMMA):
                        self.consume()
                        args.append(self._expression())
                    self.consume(TokenType.RSQB)
                    return GetItem(name, args)

                return Name(name)

            case _:
                raise ParseError(self.peek())

    def _args(self):
        self.consume(TokenType.LPAR)

        args = []
        while self.peek().type != TokenType.RPAR:
            args.append(self._expression())
            if self.peek().type == TokenType.COMMA:
                self.consume()

        self.consume(TokenType.RPAR)
        return args

    # internals
    def consume(self, *types: TokenType, string: str | None = None) -> TokenInfo:
        if types and self.peek().type not in types:
            raise ParseError(f'{self.peek()} vs {types}')
        token = next(self.tokens)
        if string is not None and token.string != string:
            raise ParseError(f'{token} vs {string}')
        return token

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
        if string is not None and token.string != string:
            return False
        return True


PRIORITIES = {
    '*': 1,
    '/': 1,
    '+': 2,
    '-': 2,
    '>': 3,
    '>=': 3,
    '<=': 3,
    '<': 3,
    '=': 4,
    '<>': 4,
}
MAX_PRIORITY = max(PRIORITIES.values())
