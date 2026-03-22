"""Data models used by the Bodhi Update Manager."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateItem:
    """Represent a single available package update."""

    name: str
    installed_version: str
    candidate_version: str
    size: int
    origin: str
    backend: str
    category: str
    description: str = ""
