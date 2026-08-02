"""Dependency-free exception types shared with the hosted encryption keyring.

The local standalone backend intentionally runs without ``cryptography``.  Code
that only needs to classify keyring failures must import this module instead of
the hosted-only ``keyring`` implementation.
"""

from __future__ import annotations


class KeyringError(Exception):
    """Base class for all keyring failures."""
