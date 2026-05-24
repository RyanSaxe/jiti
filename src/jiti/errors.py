"""Exceptions raised by jiti."""


class JitiError(Exception):
    """Base class for all jiti errors."""


class RealBodyError(JitiError):
    """`@jiti` was applied to a function that already has a real implementation.

    `@jiti` means exactly one thing: it generates. A function with real statements in
    its body is a normal function — remove `@jiti`, or empty the body to a stub
    (docstring/comments and `...`) to have jiti write it.
    """


class GenerationError(JitiError):
    """The repair loop failed to produce an implementation that passes validation."""


class ConflictError(JitiError):
    """A hand-edited implementation conflicts with a changed declaration.

    The declaration is canonical, but the implementation was edited by hand, so jiti
    refuses to silently clobber it or run a stale-signature implementation.
    """
