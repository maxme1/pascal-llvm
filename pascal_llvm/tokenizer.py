import tokenize as tknz
from contextlib import suppress
from enum import IntEnum
from token import EXACT_TOKEN_TYPES

from more_itertools import peekable


class TokenError(Exception):
    pass


# FIXME: this is to help the IDE
class TokenType(IntEnum):
    ENDMARKER = 0
    NAME = 1
    NUMBER = 2
    STRING = 3
    NEWLINE = 4
    INDENT = 5
    DEDENT = 6
    LPAR = 7
    RPAR = 8
    LSQB = 9
    RSQB = 10
    COLON = 11
    COMMA = 12
    SEMI = 13
    PLUS = 14
    MINUS = 15
    STAR = 16
    SLASH = 17
    VBAR = 18
    AMPER = 19
    LESS = 20
    GREATER = 21
    EQUAL = 22
    DOT = 23
    PERCENT = 24
    LBRACE = 25
    RBRACE = 26
    EQEQUAL = 27
    NOTEQUAL = 28
    LESSEQUAL = 29
    GREATEREQUAL = 30
    TILDE = 31
    CIRCUMFLEX = 32
    LEFTSHIFT = 33
    RIGHTSHIFT = 34
    DOUBLESTAR = 35
    PLUSEQUAL = 36
    MINEQUAL = 37
    STAREQUAL = 38
    SLASHEQUAL = 39
    PERCENTEQUAL = 40
    AMPEREQUAL = 41
    VBAREQUAL = 42
    CIRCUMFLEXEQUAL = 43
    LEFTSHIFTEQUAL = 44
    RIGHTSHIFTEQUAL = 45
    DOUBLESTAREQUAL = 46
    DOUBLESLASH = 47
    DOUBLESLASHEQUAL = 48
    AT = 49
    ATEQUAL = 50
    RARROW = 51
    ELLIPSIS = 52
    COLONEQUAL = 53
    OP = 54
    AWAIT = 55
    ASYNC = 56
    TYPE_IGNORE = 57
    TYPE_COMMENT = 58
    SOFT_KEYWORD = 59
    # These aren't used by the C tokenizer but are needed for tokenize.py
    ERRORTOKEN = 60
    COMMENT = 61
    NL = 62
    ENCODING = 63
    N_TOKENS = 64


FIX_EXACT = ';', '(', ')', ',', ':', ':=', '[', ']', '^', '@', '|'


def tokenize(text):
    def generator():
        for x in text.splitlines():
            x = x.strip() or '//empty'
            yield x

    tokens = peekable(tknz.generate_tokens(generator().__next__))
    while tokens:
        token: tknz.TokenInfo = next(tokens)
        if token.string in FIX_EXACT:
            token = token._replace(type=EXACT_TOKEN_TYPES[token.string])
        token = token._replace(type=TokenType(token.type))

        # consume the comment
        if token.string == '//':
            start = token.start[0]
            with suppress(StopIteration):
                while tokens.peek().start[0] == start:
                    next(tokens)

        # and the multiline comment
        elif token.string == '{':
            nesting = 1

            while nesting > 0:
                while token.string != '}':
                    if token.string == '{':
                        nesting += 1

                    try:
                        token = next(tokens)
                    except StopIteration:
                        raise TokenError

                nesting -= 1

        # and irrelevant stuff
        elif token.type in (
                TokenType.INDENT, TokenType.DEDENT, TokenType.ENCODING, TokenType.ENDMARKER, TokenType.NEWLINE
        ):
            pass

        # unpack floats
        elif token.type == TokenType.NUMBER and token.string.startswith('.') or token.string.endswith('.'):
            body = token.string
            split = (
                token._replace(string='.', type=TokenType.DOT),
                token._replace(string=token.string.strip('.')),
            )
            if body.startswith('.'):
                yield from split
            else:
                yield from split[::-1]

        # fix the `<>` operator
        elif token.string == '<' and tokens and tokens[0].string == '>':
            # consume the second half
            next(tokens)
            yield token._replace(string='<>')

        else:
            yield token
