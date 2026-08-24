"""Abstract base comparator and comparator registry."""

from abc import ABC, abstractmethod

from ..models import CompareResult, CompareStats, CompareMode


class BaseComparator(ABC):
    """All comparators return list[CompareResult] + CompareStats."""

    mode: CompareMode

    @abstractmethod
    def compare(self, path_a: str, path_b: str, **kwargs) -> tuple[list[CompareResult], CompareStats]:
        ...


_registry: dict[CompareMode, type[BaseComparator]] = {}


def register(mode: CompareMode):
    """Decorator to register a comparator class."""
    def decorator(cls):
        _registry[mode] = cls
        cls.mode = mode
        return cls
    return decorator


def get_comparator(mode: CompareMode) -> type[BaseComparator]:
    """Get comparator class by mode."""
    if mode not in _registry:
        raise ValueError(f"Unknown compare mode: {mode}")
    return _registry[mode]


def run_comparison(
    mode: CompareMode, path_a: str, path_b: str, **kwargs
) -> tuple[list[CompareResult], CompareStats]:
    """Run a comparison by mode."""
    cls = get_comparator(mode)
    comp = cls()
    return comp.compare(path_a, path_b, **kwargs)