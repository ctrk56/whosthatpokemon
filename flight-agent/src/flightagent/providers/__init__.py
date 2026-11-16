from .base import FlightProvider, ProviderError
from .mock import MockProvider

REGISTRY = {"mock": MockProvider}


def build_providers(names: list[str]) -> list[FlightProvider]:
    unknown = [n for n in names if n not in REGISTRY]
    if unknown:
        raise ValueError(f"Unknown or not-yet-implemented providers: {unknown}")
    return [REGISTRY[n]() for n in names]


__all__ = ["FlightProvider", "ProviderError", "build_providers"]
