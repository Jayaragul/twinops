"""Core object model.

Everything in a twin is a :class:`TwinObject` arranged in a tree, the same way a
Godot scene is a tree of nodes. Behaviour comes from subclasses; structure comes
from the tree; coupling between objects comes from :class:`Signal`.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any, Callable


class Signal:
    """A named event other objects can subscribe to.

    Industrial systems are event-driven, so this is the primary way objects talk
    to each other. Handlers are called synchronously in connection order.

        machine.cycle_completed.connect(conveyor.receive)
    """

    __slots__ = ("name", "_handlers")

    def __init__(self, name: str) -> None:
        self.name = name
        self._handlers: list[Callable[..., Any]] = []

    def connect(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        if handler not in self._handlers:
            self._handlers.append(handler)
        return handler

    def disconnect(self, handler: Callable[..., Any]) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        # iterate a copy: a handler may disconnect itself while firing
        for handler in list(self._handlers):
            handler(*args, **kwargs)

    def __len__(self) -> int:
        return len(self._handlers)

    def __repr__(self) -> str:
        return f"<Signal {self.name} ({len(self._handlers)} connected)>"


_ids = itertools.count(1)


class TwinObject:
    """Base class for every object in a twin.

    A TwinObject owns its children, knows its parent, carries free-form
    ``properties``, and exposes ``signals``. Subclasses add behaviour by
    overriding :meth:`setup` and reacting to simulation events.
    """

    #: Short type tag used in ``.twin`` files. Subclasses override this.
    type_name = "TwinObject"

    def __init__(self, name: str | None = None, **properties: Any) -> None:
        self.id = next(_ids)
        self.name = name or f"{self.type_name}_{self.id}"
        self.parent: TwinObject | None = None
        self.children: list[TwinObject] = []
        self.properties: dict[str, Any] = dict(properties)
        self.metadata: dict[str, Any] = {}
        self.signals: dict[str, Signal] = {}
        self.engine = None  # set when the tree is bound to a SimulationEngine

    # ---------------------------------------------------------------- tree

    def add(self, *children: TwinObject) -> TwinObject:
        """Add children and return self, so calls can be chained."""
        for child in children:
            if child.parent is not None:
                child.parent.remove(child)
            child.parent = self
            self.children.append(child)
        return self

    def remove(self, child: TwinObject) -> None:
        if child in self.children:
            self.children.remove(child)
            child.parent = None

    def walk(self) -> Iterator[TwinObject]:
        """Depth-first iteration over this object and all descendants."""
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, name: str) -> TwinObject | None:
        """Find a descendant by name. Returns None if absent."""
        for obj in self.walk():
            if obj.name == name:
                return obj
        return None

    def find_all(self, kind: type) -> list[TwinObject]:
        """All descendants that are instances of ``kind``."""
        return [o for o in self.walk() if isinstance(o, kind)]

    @property
    def path(self) -> str:
        """Slash-separated path from the root, e.g. ``Factory/Line_A/CNC_01``."""
        parts = [self.name]
        node = self.parent
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))

    @property
    def root(self) -> TwinObject:
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    # ------------------------------------------------------------- signals

    def signal(self, name: str) -> Signal:
        """Get or create a signal by name."""
        sig = self.signals.get(name)
        if sig is None:
            sig = Signal(f"{self.name}.{name}")
            self.signals[name] = sig
        return sig

    def emit(self, name: str, *args: Any, **kwargs: Any) -> None:
        sig = self.signals.get(name)
        if sig is not None:
            sig.emit(*args, **kwargs)

    # ----------------------------------------------------------- lifecycle

    def bind(self, engine) -> None:
        """Attach this subtree to a simulation engine and run ``setup``."""
        for obj in self.walk():
            obj.engine = engine
        for obj in self.walk():
            obj.setup()

    def setup(self) -> None:
        """Called once before the simulation starts. Override in subclasses."""

    def reset(self) -> None:
        """Restore pre-run state. Override in subclasses."""

    # ------------------------------------------------------ serialisation

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type_name, "name": self.name}
        if self.properties:
            data["properties"] = dict(self.properties)
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data

    def __repr__(self) -> str:
        return f"<{self.type_name} {self.name!r} children={len(self.children)}>"
