"""A safe boolean expression evaluator for rule ``when`` clauses.

Why not ``eval()``
-------------------
A rule's ``when`` string comes from ``config/risk_policy.yaml`` — a file this
project explicitly designs to be editable by a risk manager with no code
deploy (requirement 4c). That is exactly the profile of an untrusted-enough
input: it may pass through a config-management UI, a pull request from
someone who is not a Python developer, or in a future iteration a web form.
``eval("sanctions_screen_hits > 0")`` also evaluates
``eval("__import__('os').system('rm -rf /')")``. There is no safe subset of
``eval`` — the only safe subset of Python's grammar is the one you build
yourself and enumerate explicitly.

This module parses the expression with :mod:`ast`, then walks the tree and
rejects anything outside a small whitelist: boolean combinators, comparisons,
``in``/``not in``, literals, and bare names (which resolve against the
customer record, nothing else). No calls, no attribute access, no
subscripting beyond the ``in`` operator, no comprehensions, no imports. There
is no code path from a rule string to arbitrary execution.

Missing data
------------
A rule may reference a field the caller did not send (an optional input the
CRM integration does not populate). The evaluator treats a comparison against
a missing (``None``) value as **False** — the rule does not fire — rather than
raising. This is a deliberate, documented choice: the deterministic rule layer
is for *known, certain* red flags, and a rule silently firing on absent data
would turn "we don't know" into "this is true," which is the wrong direction
for a system whose rules can only ever raise risk. Missingness itself is
already represented to the ML model via the phase-2 ``*_is_missing``
indicator features, so it is not lost — it just is not what trips a
deterministic AML rule.

The rule applies with no exception for ``==``: ``a == b`` with both ``a`` and
``b`` missing evaluates to ``False``, not ``True``. Treating "two unknowns" as
equal would not be a meaningful claim about the customer, and a single
uniform rule ("any operand missing means indeterminate") is easier to reason
about than one with a carve-out for equality.
"""

from __future__ import annotations

import ast
from typing import Any

#: AST node types a `when` expression may use. Anything else raises at parse
#: time, before the rule is ever stored as "valid" — so a bad rule fails at
#: policy load, not silently at scoring time.
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.List,
    ast.Tuple,
)


class ExpressionError(ValueError):
    """Raised when a rule expression is unparseable or uses a disallowed construct."""


def _validate(node: ast.AST, source: str) -> None:
    if not isinstance(node, _ALLOWED_NODES):
        raise ExpressionError(
            f"disallowed syntax {type(node).__name__!r} in rule expression {source!r}; "
            "only and/or/not, comparisons (== != < <= > >= in not-in), names, "
            "and literal constants/lists are permitted"
        )
    for child in ast.iter_child_nodes(node):
        _validate(child, source)


def compile_expression(source: str) -> ast.Expression:
    """Parse and validate a ``when`` string. Raises :class:`ExpressionError` on
    anything outside the whitelist. Call this at policy-load time so an unsafe
    or malformed rule is rejected before it ever reaches a scoring request."""
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"could not parse rule expression {source!r}: {exc}") from exc
    _validate(tree, source)
    return tree


class _Evaluator(ast.NodeVisitor):
    """Walks a pre-validated AST and evaluates it against a record.

    Only ever called on a tree that already passed :func:`compile_expression`,
    so every branch here corresponds to a node type on the whitelist — there is
    no default/fallback branch that executes anything.
    """

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record

    def visit_Expression(self, node: ast.Expression) -> bool:
        return bool(self.visit(node.body))

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:
        values = [self.visit(v) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> bool:
        return not self.visit(node.operand)

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self.visit(comparator)
            if not self._compare_one(left, op, right):
                return False
            left = right
        return True

    def _compare_one(self, left: Any, op: ast.cmpop, right: Any) -> bool:
        # A comparison against missing data does not fire the rule — see the
        # module docstring for why "unknown" resolves to False, not True.
        if left is None or (right is None and not isinstance(op, (ast.In, ast.NotIn))):
            return False
        if isinstance(op, ast.Eq):
            return bool(left == right)
        if isinstance(op, ast.NotEq):
            return bool(left != right)
        if isinstance(op, ast.Lt):
            return bool(_comparable(left, right) and left < right)
        if isinstance(op, ast.LtE):
            return bool(_comparable(left, right) and left <= right)
        if isinstance(op, ast.Gt):
            return bool(_comparable(left, right) and left > right)
        if isinstance(op, ast.GtE):
            return bool(_comparable(left, right) and left >= right)
        if isinstance(op, ast.In):
            return left in right if right is not None else False
        if isinstance(op, ast.NotIn):
            return left not in right if right is not None else True
        raise ExpressionError(f"unsupported comparison operator {op!r}")  # pragma: no cover — filtered at compile

    def visit_Name(self, node: ast.Name) -> Any:
        # Unknown/absent field -> None, which every comparison above treats as
        # "cannot determine" rather than raising KeyError mid-scoring.
        return self.record.get(node.id)

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_List(self, node: ast.List) -> list[Any]:
        return [self.visit(elt) for elt in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> tuple[Any, ...]:
        return tuple(self.visit(elt) for elt in node.elts)

    def generic_visit(self, node: ast.AST) -> Any:  # pragma: no cover — unreachable post-validation
        raise ExpressionError(f"unsupported node {type(node).__name__!r}")


def _comparable(left: Any, right: Any) -> bool:
    """Guard ordered comparisons against a type mismatch (e.g. a string vs int
    from a malformed payload) raising ``TypeError`` mid-scoring. Treated the
    same as missing data: the rule does not fire rather than the request
    failing."""
    if isinstance(left, bool) or isinstance(right, bool):
        # bool is a subclass of int in Python; only allow bool<->bool ordering
        # so `pep_flag > 0` behaves as an int comparison (pep_flag is 0/1),
        # not a bool/bool ordering surprise.
        return isinstance(left, (int, float)) and isinstance(right, (int, float))
    return isinstance(left, (int, float)) and isinstance(right, (int, float)) or (
        isinstance(left, str) and isinstance(right, str)
    )


def evaluate(compiled: ast.Expression, record: dict[str, Any]) -> bool:
    """Evaluate a pre-compiled expression against a flat record of field values."""
    return bool(_Evaluator(record).visit(compiled))


def evaluate_source(source: str, record: dict[str, Any]) -> bool:
    """Compile-then-evaluate in one call. Prefer :func:`compile_expression` once
    at policy load and :func:`evaluate` per request when evaluating the same
    expression repeatedly — this is for tests and one-off checks."""
    return evaluate(compile_expression(source), record)
