"""Minimal DAG (Directed Acyclic Graph) implementation.

This module provides a lightweight DAG node class for tracking message flows
and generating mermaid diagrams. It replaces the bigtree dependency with
only the functionality actually used.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterable


@dataclass
class DAGNode:
    """A node in a Directed Acyclic Graph.

    Nodes can have multiple parents and multiple children, representing
    a DAG structure suitable for tracking message flows between agents.

    Example:
        >>> a = DAGNode("a")
        >>> b = DAGNode("b")
        >>> c = DAGNode("c")
        >>> c.add_parent(a)
        >>> c.add_parent(b)
        >>> a.children
        [DAGNode(name='c')]
    """

    name: str
    """Name/identifier of this node."""

    _parents: list[DAGNode] = field(default_factory=list, repr=False)
    _children: list[DAGNode] = field(default_factory=list, repr=False)

    @property
    def parents(self) -> list[DAGNode]:
        """Get parent nodes."""
        return list(self._parents)

    @property
    def children(self) -> list[DAGNode]:
        """Get child nodes."""
        return list(self._children)

    @property
    def is_root(self) -> bool:
        """Check if this node has no parents."""
        return len(self._parents) == 0

    @property
    def is_leaf(self) -> bool:
        """Check if this node has no children."""
        return len(self._children) == 0

    def add_parent(self, parent: DAGNode) -> None:
        """Add a parent node, also adding self as child of parent.

        Args:
            parent: Node to add as parent

        Raises:
            ValueError: If adding would create a cycle
        """
        if parent is self:
            raise ValueError("Node cannot be its own parent")
        if parent in self._parents:
            return  # Already a parent
        if self._is_ancestor_of(parent):
            raise ValueError("Adding this parent would create a cycle")

        self._parents.append(parent)
        parent._children.append(self)

    def add_child(self, child: DAGNode) -> None:
        """Add a child node, also adding self as parent of child.

        Args:
            child: Node to add as child

        Raises:
            ValueError: If adding would create a cycle
        """
        child.add_parent(self)

    def _is_ancestor_of(self, node: DAGNode) -> bool:
        """Check if self is an ancestor of the given node."""
        visited: set[str] = set()

        def _check(current: DAGNode) -> bool:
            if current.name in visited:
                return False
            visited.add(current.name)
            if current is self:
                return True
            return any(_check(p) for p in current._parents)

        return _check(node)

    def __rshift__(self, other: DAGNode) -> DAGNode:
        """Set child using >> operator: parent >> child."""
        other.add_parent(self)
        return other

    def __lshift__(self, other: DAGNode) -> DAGNode:
        """Set parent using << operator: child << parent."""
        self.add_parent(other)
        return self


def dag_iterator(root: DAGNode) -> Iterable[tuple[DAGNode, DAGNode]]:
    """Iterate through all edges in a DAG starting from a node.

    Traverses both upward (to parents) and downward (to children) to
    discover all edges reachable from the starting node.

    Args:
        root: Starting node for iteration

    Yields:
        Tuples of (parent, child) for each edge in the DAG
    """
    visited_nodes: set[str] = set()
    visited_edges: set[tuple[str, str]] = set()

    def _iterate(node: DAGNode) -> Iterable[tuple[DAGNode, DAGNode]]:
        node_name = node.name
        if node_name in visited_nodes:
            return
        visited_nodes.add(node_name)

        # Yield edges to parents (upward)
        for parent in node._parents:
            edge = (parent.name, node_name)
            if edge not in visited_edges:
                visited_edges.add(edge)
                yield parent, node

        # Yield edges to children (downward)
        for child in node._children:
            edge = (node_name, child.name)
            if edge not in visited_edges:
                visited_edges.add(edge)
                yield node, child

        # Recursively visit parents
        for parent in node._parents:
            yield from _iterate(parent)

        # Recursively visit children
        for child in node._children:
            yield from _iterate(child)

    yield from _iterate(root)


def dag_to_list(dag: DAGNode) -> list[tuple[str, str]]:
    """Export DAG edges as list of (parent_name, child_name) tuples.

    Example:
        >>> a = DAGNode("a")
        >>> b = DAGNode("b")
        >>> c = DAGNode("c")
        >>> c.add_parent(a)
        >>> c.add_parent(b)
        >>> d = DAGNode("d")
        >>> d.add_parent(c)
        >>> sorted(dag_to_list(a))
        [('a', 'c'), ('b', 'c'), ('c', 'd')]

    Args:
        dag: Any node in the DAG (will traverse to find all edges)

    Returns:
        List of (parent_name, child_name) tuples for all edges
    """
    return [(parent.name, child.name) for parent, child in dag_iterator(dag)]
