"""Session-level runtime selection; never silently fall back to another harness."""
from sandbox.harness_registry import HARNESSES, resolve


def validate_harness(harness, model):
    definition = resolve(harness)
    if not definition.accepts(model):
        raise ValueError(f'{definition.name} requires a model beginning with {definition.model_prefix}.')
    return harness


def choices():
    return [definition.public() for definition in HARNESSES.values()]
