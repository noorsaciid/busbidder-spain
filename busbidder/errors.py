"""Custom exceptions: one class per failure category the user must be told about."""


class BusBidderError(Exception):
    """Base class, so the CLI can report every expected failure in one place."""


class DataFileError(BusBidderError):
    """A source or booking file is missing, unreadable, corrupt or has the wrong columns."""


class InvalidRecordError(BusBidderError):
    """A single record holds a missing, impossible or inconsistent value."""

    def __init__(self, record_id: str, reason: str):
        super().__init__(f"{record_id}: {reason}")
        self.record_id = record_id
        self.reason = reason


class ConfigError(BusBidderError):
    """A traveller profile, request or weight set is invalid."""


class BookingError(BusBidderError):
    """A booking cannot be made, e.g. unknown offer or not enough seats left."""
