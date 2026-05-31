"""Custom exception hierarchy for fastrecon."""


class FastreconError(Exception):
    """Base exception for all fastrecon errors."""


class SourceError(FastreconError):
    """Raised when a source cannot be registered or read."""


class CompareError(FastreconError):
    """Raised when a comparison fails."""


class ConfigError(FastreconError):
    """Raised when reconciliation configuration is invalid."""
