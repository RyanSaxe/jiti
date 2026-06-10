"""Generate one Internals doc page per module under src/jiti (runs inside mkdocs-gen-files).

Every module gets a virtual page at internals/<module-path>/ containing a mkdocstrings
directive, so every type that appears in a public signature has an anchor and
`signature_crossrefs` can link it. Pages are generated at build time — nothing under
docs/internals/ is committed.
"""

from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()
root = Path(__file__).resolve().parent.parent
src = root / "src"

BANNER = (
    '!!! note "Internal API"\n'
    "    Documented for transparency — the supported public surface is the top-level "
    "`jiti` package. Internals can change in any release.\n\n"
)

for path in sorted(src.rglob("*.py")):
    module_path = path.relative_to(src).with_suffix("")
    doc_path = path.relative_to(src).with_suffix(".md")
    full_doc_path = Path("internals", doc_path)
    parts = tuple(module_path.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")
    elif parts[-1] == "__main__":
        continue
    identifier = ".".join(parts)
    nav[parts] = doc_path.as_posix()
    with mkdocs_gen_files.open(full_doc_path, "w") as page:
        page.write(f"# `{identifier}`\n\n{BANNER}::: {identifier}\n")
    mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(root))

with mkdocs_gen_files.open("internals/SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
