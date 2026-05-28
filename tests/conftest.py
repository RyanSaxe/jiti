"""Shared fixtures for CLI/merge tests: a throwaway importable project with a `.jiti/` mirror."""

import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from jiti.core.discovery import import_file
from jiti.core.store import Section, content_hash, module_relpath, parse_file, render_file


@dataclass
class Project:
    """A unique, importable package under a tmp root, plus helpers to seed its `.jiti/` mirror."""

    root: Path
    pkg: str

    @property
    def mirror(self) -> Path:
        return self.root / ".jiti"

    def module(self, name: str, source: str) -> str:
        (self.root / self.pkg / f"{name}.py").write_text(source)
        return f"{self.pkg}.{name}"

    def source_of(self, module: str) -> Path:
        return self.root / module_relpath(module)

    def jiti_impl(self, module: str) -> Path:
        return self.mirror / module_relpath(module)

    def jiti_test(self, module: str) -> Path:
        relpath = module_relpath(module)
        return self.mirror / "tests" / relpath.with_name(f"test_{relpath.name}")

    def spec_hash(self, module: str, name: str) -> str:
        import_file(self.source_of(module))
        target: object = sys.modules[module]
        for part in name.split("."):
            target = getattr(target, part)
        return target.declaration().spec_hash

    def generate(
        self,
        module: str,
        name: str,
        body: str,
        *,
        imports: str = "",
        spec_hash: str | None = None,
        edited: bool = False,
    ) -> None:
        spec = spec_hash if spec_hash is not None else self.spec_hash(module, name)
        gen = "stale" if edited else content_hash(body)
        path = self.jiti_impl(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_imports, sections = parse_file(path.read_text()) if path.exists() else ("", {})
        combined = "\n".join(part for part in (existing_imports, imports) if part)
        key = f"{module}.{name}"
        sections[key] = Section(key, spec, gen, body)
        path.write_text(render_file(combined, sections))

    def generate_test(self, module: str, name: str, body: str) -> None:
        path = self.jiti_test(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = f"{module}.{name}"
        path.write_text(render_file("", {key: Section(key, "s", content_hash(body), body)}))

    def write_test_file(self, relpath: str, source: str) -> Path:
        """Write a user test file at `relpath` and import it so its gates register immediately."""
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        import_file(path)
        return path


@pytest.fixture
def proj(tmp_path):
    pkg = f"proj_{uuid4().hex[:8]}"
    (tmp_path / pkg).mkdir()
    (tmp_path / pkg / "__init__.py").write_text("")
    yield Project(tmp_path, pkg)
    for name in [n for n in sys.modules if n == pkg or n.startswith(f"{pkg}.")]:
        del sys.modules[name]
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))
