"""Custom exceptions for realtime monitor Lambda handlers."""


class InternalServerErrorException(Exception):
    """Raise when handler cannot safely continue."""
