# CHANGELOG

<!-- version list -->

## v1.3.0 (2026-06-07)

### Chores

- Add uv dependency cooldown
  ([`3bcd3d4`](https://github.com/RyanSaxe/jiti/commit/3bcd3d4bffa03ed8879b8fdcfc5f86e2e8978084))

### Features

- Route generation through litellm
  ([`b0e851c`](https://github.com/RyanSaxe/jiti/commit/b0e851ca32c5d0324d13bca21f683d6a87160857))


## v1.2.0 (2026-06-07)

### Bug Fixes

- Default generation to sonnet
  ([`80a2f65`](https://github.com/RyanSaxe/jiti/commit/80a2f65213ba113f0fe7202cac640cc40171bd3c))

- **store**: Isolate `.jiti` compile from caller's `__future__` flags
  ([`ab61db7`](https://github.com/RyanSaxe/jiti/commit/ab61db760d272b7857036a599cf776bc10c02a9d))

### Continuous Integration

- Also install the ast-grep companion binary
  ([`ce9cc0a`](https://github.com/RyanSaxe/jiti/commit/ce9cc0a83d9bb615d228bccacc6910617150cdbf))

- Install ripgrep and ast-grep so jiti's grep/sg tools work
  ([`f0bfb50`](https://github.com/RyanSaxe/jiti/commit/f0bfb50d1bf9daa8b897ec331603126d6b06790f))

- Install search tools before release tests
  ([`ac3de0f`](https://github.com/RyanSaxe/jiti/commit/ac3de0f224a33e5d76f4d959ae994a76be834e4e))

### Documentation

- Add interface-first walkthrough for examples/semver
  ([`8d7096b`](https://github.com/RyanSaxe/jiti/commit/8d7096be1ff0b34451c7ce72b592b2a5ece34a2b))

- Add reference doc covering engine, env, CLI, lifecycle, logging
  ([`c8c2eb9`](https://github.com/RyanSaxe/jiti/commit/c8c2eb9f26fd17bcdc3bab40af2244f0e16b9707))

- Frame the graph as human-written orchestration over jiti leaves
  ([`e352128`](https://github.com/RyanSaxe/jiti/commit/e3521287b53bca5a11eea12997a764e686258f04))

- Rewrite README around the interface-first story
  ([`1cf0d18`](https://github.com/RyanSaxe/jiti/commit/1cf0d18b85fe310eea42fb7c8b96fdd4e97a38ec))

- **merge**: Note that the pydantic runtime contract is intentionally stripped
  ([`4bca537`](https://github.com/RyanSaxe/jiti/commit/4bca537a91670222fb731bfd5375394e1a4b79b4))

### Features

- Support async jiti targets
  ([`2cbfd82`](https://github.com/RyanSaxe/jiti/commit/2cbfd82956b5457027f506b282ee3d357cd8b19f))

- **decorator**: Validate runtime types with pydantic on every call
  ([`b67e607`](https://github.com/RyanSaxe/jiti/commit/b67e607f5b8578332bf5578b84ebfe3c4530960d))

- **logging**: Surface tool I/O and per-session cost summary in debug logs
  ([`093273d`](https://github.com/RyanSaxe/jiti/commit/093273d3186a75e3d6465d41ea49d6d0a0b6ade1))

- **merge**: Preserve non-@jiti decorators stacked above @jiti
  ([`688e238`](https://github.com/RyanSaxe/jiti/commit/688e2388c2e7bfb3c8ac670c942ee60ae8cae127))

- **models**: Add Model enum and JITI_MODEL env var to swap the default
  ([`4c60c84`](https://github.com/RyanSaxe/jiti/commit/4c60c845d2a77a2500ce2652084ecb4ccd6d9a0f))

- **prompts**: Tighten code-gen and test-gen guidance
  ([`eb0df0f`](https://github.com/RyanSaxe/jiti/commit/eb0df0f99a949c5044bf70cdeafc3ab7f46cfc4a))

- **store**: Fail loudly when a `.jiti` impl's signature drifts from the spec
  ([`a12bcb1`](https://github.com/RyanSaxe/jiti/commit/a12bcb161c37cf014b8f97d7f8737ff521613f7d))

- **store**: Surface user decorators in .jiti section headers
  ([`d3f0da0`](https://github.com/RyanSaxe/jiti/commit/d3f0da0750585e9bf62a2e2b14f11c33c56d0711))

- **tools**: Add sg tool, require rg/sg as system CLIs, sharpen quality rubric
  ([`9767bb6`](https://github.com/RyanSaxe/jiti/commit/9767bb6286413fb358a582dbe9d9b68ec03fff65))

- **transcript**: Persist per-generation agent transcripts as JSONL
  ([`212df50`](https://github.com/RyanSaxe/jiti/commit/212df50c58023b200c9de699949d4a02b053c046))

- **validate**: Apply runtime contract to candidate during in-process tests
  ([`bbaba8f`](https://github.com/RyanSaxe/jiti/commit/bbaba8f1046dab3f5ba543fc3200121bcb688918))

- **validate**: Type-check the test code too, not just the impl candidate
  ([`5fd1b8f`](https://github.com/RyanSaxe/jiti/commit/5fd1b8fe177a56b025b501a2978b476a0f0e0c86))

### Refactoring

- Route validation through JitiCallable._impl, not binding swaps
  ([`69b3025`](https://github.com/RyanSaxe/jiti/commit/69b302508cbfe7784fae7b95ead690e6ad8e4268))

- Simplify Phase 2 internals from review
  ([`b3c41f0`](https://github.com/RyanSaxe/jiti/commit/b3c41f0000ea8baf7e1355583cb8c23a3bcf4304))

- Unify class detection at introspect; fix merge for stacked decorators
  ([`95e30a3`](https://github.com/RyanSaxe/jiti/commit/95e30a3437b8d9c660380b84ea4cf4bd7b74d283))

- **agent**: Body-only contract — jiti splices the stub's def line
  ([`ca27745`](https://github.com/RyanSaxe/jiti/commit/ca2774530084f5b3e0f5e4801e26c3edaa10d217))

- **agent**: Create jiti.agent package for the generation loop
  ([`cf66576`](https://github.com/RyanSaxe/jiti/commit/cf665762476c42aadcac7efc3d89d70ba4a382a4))

- **cli**: Create jiti.cli package and split merge.py
  ([`15bf7cd`](https://github.com/RyanSaxe/jiti/commit/15bf7cd2b388d929db88b79b00679b2e6fd924fa))

- **core**: Create jiti.core package for hubs and shared utilities
  ([`b657d08`](https://github.com/RyanSaxe/jiti/commit/b657d087e9a223fbda3ffa70d6218ad886429138))

- **tests**: Trim dead weight from the type-safety branch
  ([`59004de`](https://github.com/RyanSaxe/jiti/commit/59004de1c03b9a79570fcf248bb66323645ebca2))

- **validate**: Explicit candidate imports in ty-on-tests
  ([`8eaada5`](https://github.com/RyanSaxe/jiti/commit/8eaada5ba3bc513e24e8b59b7587a17846854c35))

### Testing

- Pin decorator composition with jiti
  ([`bd115c7`](https://github.com/RyanSaxe/jiti/commit/bd115c7c7b6bb09229533a6f12ccac82e370aa97))

- Pin merge regressions for stacked decorators and aliased self-imports
  ([`3c5b846`](https://github.com/RyanSaxe/jiti/commit/3c5b846bb48615c10013ddf1b30ef57292464117))

- **tools**: Cover grep, sg, and missing-CLI install hints
  ([`2028f83`](https://github.com/RyanSaxe/jiti/commit/2028f83384de8548869862de3163682f8200902f))


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
