"""The ``.twin`` file format.

A twin is a plain YAML (or JSON) tree. The format is deliberately boring so
other tools can read and write it without depending on TwinForge:

    version: 1
    twin:
      type: Factory
      name: Demo
      children:
        - type: Machine
          name: CNC_01
          properties: {cycle_time: 42s}
      connections:
        - from: Source_01
          to: Buffer_01

Connections are declared by name, separately from the tree, because material
flow is a graph while the tree is ownership — a machine belongs to one line but
can feed several.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import TwinObject
from .objects import REGISTRY, Buffer, Sink, Source, Station

try:  # PyYAML is optional; JSON always works
    import yaml
    _HAVE_YAML = True
except ImportError:  # pragma: no cover
    _HAVE_YAML = False


class TwinFormatError(Exception):
    """Raised when a .twin file cannot be understood."""


def _build(node: dict[str, Any], registry: dict[str, type]) -> TwinObject:
    if "type" not in node:
        raise TwinFormatError(f"object without a 'type': {node!r}")
    kind = node["type"]
    cls = registry.get(kind)
    if cls is None:
        raise TwinFormatError(
            f"unknown object type {kind!r}. Known types: {', '.join(sorted(registry))}"
        )
    props = dict(node.get("properties") or {})
    obj = cls(name=node.get("name"), **props)
    obj.metadata.update(node.get("metadata") or {})
    for child in node.get("children") or []:
        obj.add(_build(child, registry))
    return obj


def _connect(root: TwinObject, links: list[dict[str, str]]) -> None:
    for link in links:
        src_name, dst_name = link.get("from"), link.get("to")
        if not src_name or not dst_name:
            raise TwinFormatError(f"connection needs 'from' and 'to': {link!r}")
        src, dst = root.find(src_name), root.find(dst_name)
        if src is None:
            raise TwinFormatError(f"connection source {src_name!r} not found")
        if dst is None:
            raise TwinFormatError(f"connection target {dst_name!r} not found")

        # Buffer -> Station/Sink  (the consumer pulls)
        if isinstance(src, Buffer) and isinstance(dst, (Station, Sink)):
            dst.fed_by(src)
        # Source/Station -> Buffer  (the producer pushes)
        elif isinstance(src, (Source, Station)) and isinstance(dst, Buffer):
            src.feeds(dst)
        else:
            raise TwinFormatError(
                f"cannot connect {src.type_name} {src.name!r} to "
                f"{dst.type_name} {dst.name!r} — material moves through Buffers"
            )


def loads(text: str, registry: dict[str, type] | None = None) -> TwinObject:
    """Parse a twin from YAML or JSON text."""
    text = text.strip()
    if text.startswith("{"):
        data = json.loads(text)
    elif _HAVE_YAML:
        data = yaml.safe_load(text)
    else:  # pragma: no cover
        raise TwinFormatError(
            "this file looks like YAML but PyYAML is not installed — "
            "run `pip install pyyaml`, or use JSON"
        )
    if not isinstance(data, dict) or "twin" not in data:
        raise TwinFormatError("a .twin file must have a top-level 'twin:' key")

    root = _build(data["twin"], registry or REGISTRY)
    _connect(root, data.get("connections") or [])
    return root


def load(path: str | Path, registry: dict[str, type] | None = None) -> TwinObject:
    """Load a twin from a ``.twin`` file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no such twin: {p}")
    return loads(p.read_text(encoding="utf-8"), registry)


def dumps(root: TwinObject, connections: list[dict[str, str]] | None = None) -> str:
    """Serialise a twin back to YAML (or JSON when PyYAML is absent)."""
    data: dict[str, Any] = {"version": 1, "twin": root.to_dict()}
    if connections:
        data["connections"] = connections
    if _HAVE_YAML:
        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    return json.dumps(data, indent=2)


def save(root: TwinObject, path: str | Path, connections: list[dict[str, str]] | None = None) -> None:
    Path(path).write_text(dumps(root, connections), encoding="utf-8")
