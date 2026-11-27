"""A service that uses the text helpers."""

from pkg.util import normalize


class Catalog:
    def __init__(self):
        self.items = []

    def add(self, name):
        self.items.append(normalize(name))
        return len(self.items)

    def names(self):
        return list(self.items)
