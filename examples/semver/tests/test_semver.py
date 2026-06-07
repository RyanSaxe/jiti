"""A TDD spec for the semver toolkit — these tests ARE the definition of done.

Run `pytest examples/semver/tests` (with ANTHROPIC_API_KEY set) and jiti generates the library
to satisfy them, red -> green. A real-bodied test is your own gate; an empty-bodied stub is a
jiti-test that jiti writes from the target's interface (so it can't couple to the impl).
"""

from examples.semver.core import Version, compare, parse
from jiti import jiti


@jiti.required_for(parse)
def test_parse_reads_core_and_prerelease():
    assert parse("1.2.3") == Version(1, 2, 3)
    assert parse("1.2.3-rc.1") == Version(1, 2, 3, "rc.1")


@jiti.required_for(parse)
def test_parse_rejects_malformed() -> None:
    """parse raises ValueError on '1.2' (missing patch), '' (empty), and '01.2.3' (leading zero)."""
    ...


@jiti.required_for(compare)
def test_compare_orders_by_precedence() -> None:
    """compare(a, b) returns -1/0/1 by semver precedence: compare('1.2.0', '1.10.0') == -1,
    equal versions give 0, and a prerelease is below its release (1.0.0-rc.1 < 1.0.0)."""
    ...


@jiti.required_for(Version.bump)
def test_bump_increments_and_resets_lower_parts():
    assert Version(1, 2, 3).bump("major") == Version(2, 0, 0)
    assert Version(1, 2, 3).bump("minor") == Version(1, 3, 0)
    assert Version(1, 2, 3).bump("patch") == Version(1, 2, 4)
    assert Version(1, 2, 3, "rc.1").bump("patch") == Version(1, 2, 4)  # prerelease dropped


# `Version.is_stable`, `Version.zero`, and `Version.is_well_formed` stack a descriptor
# decorator above `@jiti`. They're exercised by the demo path — first call triggers
# generation and caches, then these post-generation tests verify the cached behavior.


def test_is_stable_post_generation():
    """Runs after the demo has cached `is_stable`; verifies the cached impl works correctly."""
    assert parse("1.0.0").is_stable is True
    assert parse("0.9.0").is_stable is False
    assert parse("1.0.0-rc.1").is_stable is False


def test_zero_post_generation():
    assert Version.zero() == Version(0, 0, 0)


def test_is_well_formed_post_generation():
    assert Version.is_well_formed("1.2.3") is True
    assert Version.is_well_formed("1.2.3-rc.1") is True
    assert Version.is_well_formed("01.2.3") is False
    assert Version.is_well_formed("1.2") is False
    assert Version.is_well_formed("") is False
