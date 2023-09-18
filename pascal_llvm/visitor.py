import re

# credit: https://stackoverflow.com/a/1176023
first_cap = re.compile(r'(.)([A-Z][a-z]+)')
all_cap = re.compile(r'([a-z\d])([A-Z])')


def snake_case(name):
    name = first_cap.sub(r'\1_\2', name)
    return all_cap.sub(r'\1_\2', name).lower()


class Visitor:
    def visit(self, node):
        return getattr(self, f'_{snake_case(type(node).__name__)}')(node)

    def visit_sequence(self, nodes):
        for node in nodes:
            self.visit(node)
