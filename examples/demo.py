"""A tiny live demo. Run it with your Anthropic key set:

    ANTHROPIC_API_KEY=sk-ant-... uv run python examples/demo.py

The first run generates real implementations into ./.jiti/ (and tests under .jiti/tests/),
then runs them. Open the generated files to see the code jiti wrote.
"""

from jiti import jiti


@jiti
def slugify(text: str) -> str:
    """Convert text to a URL-safe slug: lowercase, alphanumeric, hyphen-separated."""
    ...


@jiti
def word_frequencies(text: str) -> dict[str, int]:
    """Count how often each word appears, case-insensitively, ignoring punctuation."""
    ...


def main() -> None:
    print(slugify("Hello, World! This is jiti"))
    print(word_frequencies("the cat sat on the mat the cat"))


if __name__ == "__main__":
    main()
