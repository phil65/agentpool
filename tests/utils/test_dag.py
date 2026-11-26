"""Tests for minimal DAG implementation."""

import pytest

from agentpool.utils.dag import DAGNode, dag_iterator, dag_to_list


def test_dag_node_creation():
    """Test basic DAGNode creation."""
    node = DAGNode("test")
    assert node.name == "test"
    assert node.parents == []
    assert node.children == []
    assert node.is_root
    assert node.is_leaf


def test_add_parent():
    """Test adding parent nodes."""
    a = DAGNode("a")
    b = DAGNode("b")

    b.add_parent(a)

    assert a in b.parents
    assert b in a.children
    assert a.is_root
    assert not b.is_root
    assert b.is_leaf


def test_add_child():
    """Test adding child nodes."""
    a = DAGNode("a")
    b = DAGNode("b")

    a.add_child(b)

    assert a in b.parents
    assert b in a.children


def test_multiple_parents():
    """Test node with multiple parents."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")

    c.add_parent(a)
    c.add_parent(b)

    assert len(c.parents) == 2
    assert a in c.parents
    assert b in c.parents
    assert c in a.children
    assert c in b.children


def test_cycle_detection():
    """Test that cycles are prevented."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")

    a.add_child(b)
    b.add_child(c)

    # Trying to add a as child of c would create a cycle
    with pytest.raises(ValueError, match="cycle"):
        c.add_child(a)


def test_self_parent_prevention():
    """Test that node cannot be its own parent."""
    a = DAGNode("a")

    with pytest.raises(ValueError, match="own parent"):
        a.add_parent(a)


def test_duplicate_parent():
    """Test that adding same parent twice is idempotent."""
    a = DAGNode("a")
    b = DAGNode("b")

    b.add_parent(a)
    b.add_parent(a)  # Should not error

    assert len(b.parents) == 1
    assert len(a.children) == 1


def test_bitshift_operators():
    """Test >> and << operators for convenience."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")

    # parent >> child
    a >> b
    assert b in a.children

    # child << parent
    c << a
    assert c in a.children


def test_dag_iterator():
    """Test iterating through DAG edges."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")
    d = DAGNode("d")

    # Build: a -> c <- b
    #        c -> d
    c.add_parent(a)
    c.add_parent(b)
    d.add_parent(c)

    edges = list(dag_iterator(a))
    edge_tuples = [(p.name, ch.name) for p, ch in edges]

    # Should have all edges
    assert ("a", "c") in edge_tuples
    assert ("b", "c") in edge_tuples
    assert ("c", "d") in edge_tuples
    assert len(edge_tuples) == 3


def test_dag_to_list():
    """Test converting DAG to list of name tuples."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")
    d = DAGNode("d")
    e = DAGNode("e")

    # Build example from bigtree docs:
    # a -> c <- b
    # a -> d <- c
    #      d -> e
    c.add_parent(a)
    c.add_parent(b)
    d.add_parent(a)
    d.add_parent(c)
    e.add_parent(d)

    result = sorted(dag_to_list(a))
    expected = [("a", "c"), ("a", "d"), ("b", "c"), ("c", "d"), ("d", "e")]

    assert result == expected


def test_simple_chain():
    """Test a simple linear chain."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")

    a >> b >> c

    result = dag_to_list(a)
    assert result == [("a", "b"), ("b", "c")]


def test_diamond_dag():
    """Test a diamond-shaped DAG."""
    #     a
    #    / \
    #   b   c
    #    \ /
    #     d
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")
    d = DAGNode("d")

    b.add_parent(a)
    c.add_parent(a)
    d.add_parent(b)
    d.add_parent(c)

    result = sorted(dag_to_list(a))
    assert ("a", "b") in result
    assert ("a", "c") in result
    assert ("b", "d") in result
    assert ("c", "d") in result
    assert len(result) == 4


def test_empty_dag():
    """Test single node with no edges."""
    a = DAGNode("a")
    result = dag_to_list(a)
    assert result == []


def test_traverse_from_middle():
    """Test that traversing from middle node finds all edges."""
    a = DAGNode("a")
    b = DAGNode("b")
    c = DAGNode("c")
    d = DAGNode("d")

    # a -> b -> c -> d
    a >> b >> c >> d

    # Start from c, should find all edges
    result = sorted(dag_to_list(c))
    expected = [("a", "b"), ("b", "c"), ("c", "d")]
    assert result == expected
