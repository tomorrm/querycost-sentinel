"""Custom exceptions for monthly ranking Lambda handlers."""


class InternalServerErrorException(Exception):
    """Raise when handler cannot safely continue."""
