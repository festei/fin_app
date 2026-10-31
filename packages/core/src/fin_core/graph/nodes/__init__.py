"""Philosophy nodes. Register new ones in ``PHILOSOPHY_NODES``; the graph wires them in parallel."""

from collections.abc import Callable
from typing import Any

from fin_core.graph.nodes.graham import NAME as GRAHAM
from fin_core.graph.nodes.graham import graham_node

PHILOSOPHY_NODES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    GRAHAM: graham_node,
}

__all__ = ["PHILOSOPHY_NODES"]
