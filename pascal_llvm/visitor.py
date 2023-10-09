import re

# credit: https://stackoverflow.com/a/1176023
first_cap = re.compile(r'(.)([A-Z][a-z]+)')
all_cap = re.compile(r'([a-z\d])([A-Z])')


def snake_case(name):
    name = first_cap.sub(r'\1_\2', name)
    return all_cap.sub(r'\1_\2', name).lower()


class Visitor:
    def visit(self, node, *args, **kwargs):
        node = self.before_visit(node, *args, **kwargs)
        value = getattr(self, f'_{snake_case(type(node).__name__)}')(node, *args, **kwargs)
        value = self.after_visit(node, value, *args, **kwargs)
        return value

    def visit_sequence(self, nodes, *args, **kwargs):
        return tuple(self.visit(node, *args, **kwargs) for node in nodes)

    def before_visit(self, node, *args, **kwargs):
        return node

    def after_visit(self, node, value, *args, **kwargs):
        return value
