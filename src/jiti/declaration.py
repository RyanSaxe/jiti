"""Introspect a decorated stub into a `Declaration` — jiti's canonical spec.

A `Declaration` captures everything the generator needs and everything the lifecycle
hashes: the qualified name, the typed signature, the docstring, the stub body (generate
vs. hint), the module-level symbols the implementation may import back, and — for a
method — the enclosing class's shape.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import textwrap
import tokenize
import types
from dataclasses import dataclass
from enum import Enum

from jiti.errors import JitiError, RealBodyError


class BodyMode(Enum):
    """How the stub body directs generation."""

    GENERATE = "generate"
    """Body is only a docstring and/or `...`/`pass` — generate from scratch."""

    HINT = "hint"
    """Body adds comments/pseudocode — generate, using the comments as guidance."""


@dataclass(frozen=True)
class ClassContext:
    """The enclosing class's shape, so a method's body can use `self`/`cls` correctly."""

    name: str
    attributes: tuple[tuple[str, str], ...]
    """`(attribute_name, type_string)` pairs from annotations and `__init__`."""

    methods: tuple[tuple[str, str], ...]
    """`(method_name, signature_string)` pairs for sibling methods."""


@dataclass(frozen=True)
class Declaration:
    """The canonical interface jiti generates against."""

    module: str
    qualname: str
    name: str
    signature: inspect.Signature
    docstring: str | None
    hint: str | None
    available_symbols: tuple[str, ...]
    class_context: ClassContext | None

    @property
    def body_mode(self) -> BodyMode:
        return BodyMode.HINT if self.hint else BodyMode.GENERATE

    @property
    def key(self) -> str:
        """Stable identity used to key sections and state, e.g. `app.text.Parser.parse`."""
        return f"{self.module}.{self.qualname}"

    @property
    def spec_hash(self) -> str:
        """Hash of everything that should trigger regeneration when changed."""
        return _spec_hash(self)


def introspect(func: types.FunctionType, owner: type | None = None) -> Declaration:
    """Build a `Declaration` from a stub function and (for methods) its owner class.

    Raises `RealBodyError` if the stub already has a real implementation.
    """
    class_context = class_context_of(owner, exclude=func.__name__) if owner else None
    return Declaration(
        module=func.__module__,
        qualname=func.__qualname__,
        name=func.__name__,
        signature=inspect.signature(func),
        docstring=inspect.getdoc(func),
        hint=analyze_body(func),
        available_symbols=_module_symbols(func),
        class_context=class_context,
    )


def analyze_body(func: types.FunctionType) -> str | None:
    """Return the stub's comment hint (or None), rejecting a body with real statements."""
    source = textwrap.dedent(inspect.getsource(func))
    node = _function_node(source, func.__name__)
    body = _body_without_docstring(node)
    if any(not _is_placeholder(statement) for statement in body):
        raise RealBodyError(
            f"{func.__qualname__} has an implementation; remove @jiti or reduce its "
            "body to a stub (docstring/comments and `...`)."
        )
    return _extract_comments(source)


def class_context_of(owner: type, exclude: str) -> ClassContext:
    """Capture the attributes and sibling method signatures of a method's owner class."""
    methods = tuple(
        (name, str(inspect.signature(member)))
        for name, member in inspect.getmembers(owner, inspect.isfunction)
        if name != exclude and (name == "__init__" or not name.startswith("__"))
    )
    return ClassContext(owner.__name__, _class_attributes(owner), methods)


def _function_node(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise JitiError(f"Could not locate the definition of {name} in its own source.")


def _body_without_docstring(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = node.body
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


def _is_placeholder(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr):
        return isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis
    if isinstance(statement, ast.Raise):
        return _raises_not_implemented(statement)
    return False


def _raises_not_implemented(node: ast.Raise) -> bool:
    exception = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(exception, ast.Name) and exception.id == "NotImplementedError"


def _extract_comments(source: str) -> str | None:
    """Collect own-line comments inside the stub — these are the generation hints.

    Trailing comments (e.g. `def f():  # note`) are ignored; only comments that are the
    first non-whitespace on their line count, which excludes signature/decorator notes.
    """
    lines = source.splitlines()
    comments = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line = lines[token.start[0] - 1]
        if line.lstrip().startswith("#"):
            comments.append(token.string.lstrip("#").strip())
    text = "\n".join(comment for comment in comments if comment)
    return text or None


def _module_symbols(func: types.FunctionType) -> tuple[str, ...]:
    """Public module-level names the generated implementation may import back."""
    return tuple(sorted(name for name in func.__globals__ if not name.startswith("_")))


def _class_attributes(owner: type) -> tuple[tuple[str, str], ...]:
    attributes = {name: _type_str(value) for name, value in inspect.get_annotations(owner).items()}
    initializer = owner.__dict__.get("__init__")
    if isinstance(initializer, types.FunctionType):
        attributes.update(_self_assignments(initializer))
    return tuple(sorted(attributes.items()))


def _self_assignments(initializer: types.FunctionType) -> dict[str, str]:
    """Find `self.x` (and `self.x: T`) assignments in an `__init__` to learn attributes."""
    try:
        source = inspect.getsource(initializer)
    except (OSError, TypeError):
        return {}  # synthetic __init__ (e.g. a dataclass) has no source — annotations suffice
    tree = ast.parse(textwrap.dedent(source))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Attribute) and _is_self(target):
                found[target.attr] = ast.unparse(node.annotation)
        elif isinstance(node, ast.Assign):
            for assigned in node.targets:
                if isinstance(assigned, ast.Attribute) and _is_self(assigned):
                    found.setdefault(assigned.attr, "")
    return found


def _is_self(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _type_str(annotation: object) -> str:
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", None) or str(annotation)


def short_hash(text: str) -> str:
    """16 hex chars of SHA-256 — the shared primitive for spec and section hashing."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _spec_hash(declaration: Declaration) -> str:
    parts = [
        declaration.key,
        str(declaration.signature),
        declaration.docstring or "",
        declaration.hint or "",
    ]
    if declaration.class_context:
        parts.append(declaration.class_context.name)
        parts.extend(f"{name}:{type_}" for name, type_ in declaration.class_context.attributes)
        parts.extend(f"{name}{sig}" for name, sig in declaration.class_context.methods)
    return short_hash("\x00".join(parts))
