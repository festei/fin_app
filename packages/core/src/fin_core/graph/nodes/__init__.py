"""Philosophy nodes. Register new ones in ``PHILOSOPHY_NODES``; the graph wires them in parallel."""

from collections.abc import Callable
from typing import Any

from fin_core.graph.nodes.buffett import NAME as BUFFETT
from fin_core.graph.nodes.buffett import buffett_node
from fin_core.graph.nodes.fisher import NAME as FISHER
from fin_core.graph.nodes.fisher import fisher_node
from fin_core.graph.nodes.graham import NAME as GRAHAM
from fin_core.graph.nodes.graham import graham_node
from fin_core.graph.nodes.greenblatt import NAME as GREENBLATT
from fin_core.graph.nodes.greenblatt import greenblatt_node
from fin_core.graph.nodes.lynch import NAME as LYNCH
from fin_core.graph.nodes.lynch import lynch_node

PHILOSOPHY_NODES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    GRAHAM: graham_node,
    BUFFETT: buffett_node,
    LYNCH: lynch_node,
    GREENBLATT: greenblatt_node,
    FISHER: fisher_node,
}

__all__ = ["PHILOSOPHY_NODES"]
