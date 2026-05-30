"""CLI for the semver example. Each command calls the @jiti functions, generating them on
first use. Set ANTHROPIC_API_KEY (and JITI_LOG=info to watch generation)."""

import argparse

from examples.semver.core import Version, compare, latest, parse, satisfies, sort_versions

_COMMANDS = {
    "parse": ["version"],
    "compare": ["a", "b"],
    "bump": ["version", "part"],
    "satisfies": ["version", "spec"],
    "sort": ["versions"],
    "latest": ["versions"],
    "stable": ["version"],
    "wellformed": ["text"],
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="semver", description="A jiti-generated semver toolkit.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fields in _COMMANDS.items():
        command = sub.add_parser(name)
        for field in fields:
            command.add_argument(field, nargs="+" if field == "versions" else None)
    sub.add_parser("zero", help="print Version.zero()")
    sub.add_parser("demo", help="run every command on sample input")

    args = parser.parse_args(argv)
    if args.command == "demo":
        _demo()
    elif args.command == "zero":
        print(Version.zero())
    else:
        print(_run(args))


def _run(args: argparse.Namespace) -> str:
    match args.command:
        case "parse":
            return str(parse(args.version))
        case "compare":
            return str(compare(args.a, args.b))
        case "bump":
            return str(parse(args.version).bump(args.part))
        case "satisfies":
            return str(satisfies(args.version, args.spec))
        case "sort":
            return " ".join(sort_versions(args.versions))
        case "stable":
            return str(parse(args.version).is_stable)
        case "wellformed":
            return str(Version.is_well_formed(args.text))
        case _:
            return latest(args.versions)


def _demo() -> None:
    print("parse      1.2.3-rc.1     ->", parse("1.2.3-rc.1"))
    print("compare    1.2.0 1.10.0   ->", compare("1.2.0", "1.10.0"))
    print("bump       1.2.3 minor    ->", parse("1.2.3").bump("minor"))
    print('satisfies  1.4.0 ">=1.2"  ->', satisfies("1.4.0", ">=1.2.0"))
    print("sort       1.2 1.10 1.1   ->", " ".join(sort_versions(["1.2.0", "1.10.0", "1.1.0"])))
    print("latest     1.2 1.10 1.1   ->", latest(["1.2.0", "1.10.0", "1.1.0"]))
    print("stable     1.0.0          ->", parse("1.0.0").is_stable)
    print("stable     0.9.0          ->", parse("0.9.0").is_stable)
    print("stable     1.0.0-rc.1     ->", parse("1.0.0-rc.1").is_stable)
    print("zero                       ->", Version.zero())
    print("wellformed 1.2.3          ->", Version.is_well_formed("1.2.3"))
    print("wellformed 01.2.3         ->", Version.is_well_formed("01.2.3"))
