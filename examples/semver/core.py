"""A small semver toolkit, declared as a spec and implemented by jiti on first use.

Every function below is a `@jiti` stub: the signature and docstring are the contract, and
jiti writes the implementation the first time it's called. The functions build on each other
(`satisfies` → `compare` → `parse`), so calling one cascades generation through the rest.

Run `python -m examples.semver demo` with `ANTHROPIC_API_KEY` set (and `JITI_LOG=info` to
watch the calls) to generate the whole library in one go.
"""

from dataclasses import dataclass

from jiti import jiti


@dataclass(frozen=True)
class Version:
    """A parsed semantic version."""

    major: int
    minor: int
    patch: int
    prerelease: str | None = None

    def __str__(self) -> str:
        core = f"{self.major}.{self.minor}.{self.patch}"
        return f"{core}-{self.prerelease}" if self.prerelease else core

    @jiti
    def bump(self, part: str) -> "Version":
        """Return a new Version with `part` ('major', 'minor', or 'patch') incremented and
        every lower part reset to 0, dropping any prerelease. Raise ValueError for an
        unknown part."""
        ...


@jiti
def parse(text: str) -> Version:
    """Parse 'MAJOR.MINOR.PATCH' with an optional '-prerelease' suffix (e.g. '1.2.3-rc.1')
    into a Version. Raise ValueError on malformed input."""
    ...


@jiti
def compare(a: str, b: str) -> int:
    """Compare two version strings by semver precedence, returning -1, 0, or 1. A release
    outranks a prerelease of the same major.minor.patch."""
    ...


@jiti
def satisfies(version: str, spec: str) -> bool:
    """Return True if `version` satisfies `spec`. A spec is an exact version or one prefixed
    with '>=', '>', '<=', '<', '~' (allow patch-level changes), or '^' (allow changes that do
    not modify the left-most non-zero part)."""
    ...


@jiti
def sort_versions(versions: list[str]) -> list[str]:
    """Return the version strings sorted ascending by semver precedence."""
    ...


@jiti
def latest(versions: list[str]) -> str:
    """Return the highest-precedence version string. Raise ValueError if the list is empty."""
    ...
