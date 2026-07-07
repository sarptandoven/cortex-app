"""cortex-client: a stdlib-only Python client for the Cortex local memory server."""

from .client import CortexClient, CortexError

__all__ = ["CortexClient", "CortexError"]
__version__ = "0.1.0"
