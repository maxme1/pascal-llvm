import token as _token
import tokenize as tknz
from contextlib import suppress

from more_itertools import peekable

# a nice namespace for token types
TokenType = _token
FIX_EXACT = ';', '(', ')', ',', ':', ':=', '[', ']', '^', '@', '.'


def tokenize(text):
    def generator():
        for x in text.splitlines():
            # `generate_tokens` treats too many blank lines as "end of stream", so we'll patch that
            x = x.strip() or '//empty'
            yield x

    tokens = peekable(tknz.generate_tokens(generator().__next__))
    while tokens:
        token: tknz.TokenInfo = next(tokens)
        if token.string in FIX_EXACT:
            token = token._replace(type=_token.EXACT_TOKEN_TYPES[token.string])

        # consume the comment
        if token.string == '//':
            start = token.start[0]
            with suppress(StopIteration):
                while tokens.peek().start[0] == start:
                    next(tokens)

        # and the multiline comment
        elif token.string == '{':
            nesting = 1

            try:
                while nesting > 0:
                    token = next(tokens)
                    while token.string != '}':
                        if token.string == '{':
                            nesting += 1
                        token = next(tokens)

                    nesting -= 1

            except StopIteration:
                raise SyntaxError('Unmatched "{"') from None

        # and irrelevant stuff
        elif token.type in (
                TokenType.INDENT, TokenType.DEDENT, TokenType.ENCODING, TokenType.ENDMARKER, TokenType.NEWLINE
        ):
            pass

        # unpack floats
        elif token.type == TokenType.NUMBER and (token.string.startswith('.') or token.string.endswith('.')):
            body = token.string
            split = (
                token._replace(string='.', type=TokenType.DOT),
                token._replace(string=body.strip('.')),
            )
            if body.startswith('.'):
                yield from split
            else:
                yield from split[::-1]

        # fix the `<>` operator
        elif token.string == '<' and tokens and tokens.peek().string == '>':
            # consume the second half
            next(tokens)
            yield token._replace(string='<>')

        else:
            yield token
