"""
cortex/instrumentation.py
--------------------------
Centralized Arize AX tracing setup.

Import and call setup_tracing() ONCE at the top of any entry point
(mcp_server.py, ui.py, capture.py) BEFORE any Anthropic/Voyage clients
are created. Auto-instrumentation then traces every Claude API call.
"""

import os
from opentelemetry import trace

_tracer = None


def setup_tracing(project_name: str = "cortex") -> bool:
    """
    Initialize Arize AX tracing. Returns True if successful.
    Safe to call multiple times — only initializes once.
    """
    global _tracer
    if _tracer is not None:
        return True

    space_id = os.environ.get("ARIZE_SPACE_ID", "")
    api_key = os.environ.get("ARIZE_API_KEY", "")

    if not space_id or not api_key:
        print("⚠️  Arize tracing disabled — ARIZE_SPACE_ID or ARIZE_API_KEY not set")
        return False

    try:
        from arize.otel import register
        from openinference.instrumentation.anthropic import AnthropicInstrumentor

        tracer_provider = register(
            space_id=space_id,
            api_key=api_key,
            project_name=project_name,
        )

        # Auto-instrument all Anthropic (Claude) API calls
        AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)

        _tracer = trace.get_tracer(project_name)
        print(f"✅ Arize tracing enabled → project '{project_name}'")
        return True

    except Exception as e:
        print(f"⚠️  Arize tracing setup failed: {e}")
        return False


def get_tracer():
    """Get the OpenTelemetry tracer. Returns a no-op tracer if not initialized."""
    return trace.get_tracer("cortex")
