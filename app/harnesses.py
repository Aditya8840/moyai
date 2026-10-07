"""Session-level runtime selection; never silently fall back to another harness."""
from sandbox.harness_registry import HARNESSES, resolve


def validate_harness(harness, model):
    # Model authorization belongs to Settings.resolve_model, not the harness.
    # The gateway handles compatibility with each harness's native protocol.
    resolve(harness)
    return harness


def choices():
    return [definition.public() for definition in HARNESSES.values()]
