"""Entry point of the fixture."""

from pkg.service import Catalog


def main():
    catalog = Catalog()
    catalog.add("  Apple ")
    return catalog.names()
