# CHANGELOG

<!-- version list -->

## v1.1.0 (2026-05-26)

### Bug Fixes

- Preserve user signature on splice, merge imports, dedupe promoted defs
  ([`2bbc224`](https://github.com/RyanSaxe/jiti/commit/2bbc22478695bc439810b259aec406c53657a79a))

- Resolve the mirror root before walking it
  ([`01dbc62`](https://github.com/RyanSaxe/jiti/commit/01dbc62d2686e0814752cde0aa1b002eed8df1ca))

### Features

- Jiti CLI entrypoint and docs
  ([`d535cea`](https://github.com/RyanSaxe/jiti/commit/d535ceabb40bfbff5ddd18cbf39c536fef94e1c9))

- Jiti merge
  ([`6380ebd`](https://github.com/RyanSaxe/jiti/commit/6380ebdaead992d9710ab3471bd593c728772731))

- Jiti status
  ([`579b118`](https://github.com/RyanSaxe/jiti/commit/579b118eb36825417309e320215554e156299c2d))

- Jiti test prune/keep and jiti clear
  ([`dbea2fb`](https://github.com/RyanSaxe/jiti/commit/dbea2fb0078c6c6840117570c22db2c93c4bfd07))

- Merge folds tests into source, with --prune to drop agent scratch
  ([`e1a17dd`](https://github.com/RyanSaxe/jiti/commit/e1a17dd06e1f6caa9b8737eab8d064eb7a206294))

- Merge methods inside their class
  ([`6733cfa`](https://github.com/RyanSaxe/jiti/commit/6733cfa1ffcb85a72e368635b9cb3422e7c6fde7))

- Required_for on methods
  ([`053c47d`](https://github.com/RyanSaxe/jiti/commit/053c47dcd195d10b09e77aff0c590f223152e373))

- Section inventory and merge-target resolution
  ([`4efd566`](https://github.com/RyanSaxe/jiti/commit/4efd566049b77ff417967dbecd06ff3422b21e59))

- Source rewriter for jiti merge
  ([`c10d904`](https://github.com/RyanSaxe/jiti/commit/c10d904216bb89d269ea8af28858bbf5d8d7c000))

### Performance Improvements

- Batch test-file reads in status; skip no-op writes in prune
  ([`665faba`](https://github.com/RyanSaxe/jiti/commit/665fabaff14ccb699ab9199045686e6c6a04ac91))

### Refactoring

- Flatten cli.keep and dispatch via argparse set_defaults
  ([`ba91a8d`](https://github.com/RyanSaxe/jiti/commit/ba91a8d30d782520de81f0a9bbec3bc40a1a1fd0))

- Promote atomic_write and test_path_for_module to public API
  ([`dcd5205`](https://github.com/RyanSaxe/jiti/commit/dcd5205725c6d5d29fd6393563ab1ec5e8f2ec00))

- Promote internal APIs for the CLI
  ([`3c375d8`](https://github.com/RyanSaxe/jiti/commit/3c375d8dbdb2ff8f058c465451b70f58cbf2154a))

- Simplify pass over the method-merge and test-merge work
  ([`6621283`](https://github.com/RyanSaxe/jiti/commit/662128383fc69a1fce926a473df4915750822a2a))


## v1.0.0 (2026-05-25)

- Initial Release
