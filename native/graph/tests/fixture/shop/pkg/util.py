"""Text helpers."""


def normalize(text):
    """Return text trimmed and lower-cased."""
    return text.strip().lower()


def slug(text):
    return normalize(text).replace(" ", "-")
