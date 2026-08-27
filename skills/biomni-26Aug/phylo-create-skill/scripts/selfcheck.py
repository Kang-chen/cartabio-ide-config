#!/usr/bin/env python3
"""Prove this gate is a superset of the platform's own packaging validator.

A local checker that drifts from the real validator is worse than no local checker: authors trust it,
then a package fails late with a confusing error. This asserts the property that matters —

    every package the real validator REJECTS, check_skill.py must also reject

— and reports the reverse delta, which is the inventory of things this gate catches and the platform
does not. That delta is the point of the gate; it should be non-empty.

This is a maintainer tool, run against a checkout. It reads a whole directory of packages, so do not
run it inside a live skill session — pass an explicit local path.

    selfcheck.py --skills <dir-of-packages> --validator <path-to-platform-validator.py>
    selfcheck.py --skills <dir-of-packages>            # superset check skipped, delta still reported

Exit 0 property holds · 1 property violated · 2 could not evaluate (never a pass).
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CHECK = HERE / "check_skill.py"


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def platform_rejects(validator: pathlib.Path, skills: pathlib.Path,
                     pkg: pathlib.Path) -> bool | None:
    """Run the real validator over a temp tree holding only this package."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "skills"
        root.mkdir(parents=True)
        try:
            shutil.copytree(pkg, root / pkg.name)
        except OSError:
            return None
        code, _ = run([sys.executable, str(validator), "--skills-root", str(root)])
        return code != 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skills", required=True, help="directory containing skill packages")
    ap.add_argument("--validator", help="path to the platform's validate script")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if the superset property is violated")
    args = ap.parse_args()

    skills = pathlib.Path(args.skills).expanduser().resolve()
    if not skills.is_dir():
        print(f"RESULT: not evaluable — {skills} is not a directory")
        return 2

    pkgs = sorted(p for p in skills.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
    if not pkgs:
        print(f"RESULT: not evaluable — no packages under {skills}")
        return 2

    validator = pathlib.Path(args.validator).expanduser().resolve() if args.validator else None
    if validator and not validator.exists():
        print(f"RESULT: not evaluable — validator not found at {validator}")
        return 2

    ours_reject: set[str] = set()
    theirs_reject: set[str] = set()
    findings: dict[str, list[str]] = {}

    for pkg in pkgs:
        code, out = run([sys.executable, str(CHECK), str(pkg), "--contract", "A"])
        if code == 1:
            ours_reject.add(pkg.name)
            findings[pkg.name] = sorted({ln.split()[1] for ln in out.splitlines()
                                         if ln.strip().startswith("FAIL")})
        if validator:
            r = platform_rejects(validator, skills, pkg)
            if r is None:
                print(f"RESULT: not evaluable — could not stage {pkg.name}")
                return 2
            if r:
                theirs_reject.add(pkg.name)

    print(f"packages: {len(pkgs)}")
    print(f"this gate rejects: {len(ours_reject)}")

    violated: set[str] = set()
    if validator:
        print(f"platform validator rejects: {len(theirs_reject)}")
        violated = theirs_reject - ours_reject
        print()
        if violated:
            print("SUPERSET PROPERTY VIOLATED — the platform rejects these and this gate does not:")
            for n in sorted(violated):
                print(f"  {n}")
        else:
            print("superset property holds: nothing the platform rejects passes this gate")
    else:
        print("\nsuperset property NOT CHECKED (no --validator given). That is not a pass.")

    extra = ours_reject - theirs_reject if validator else ours_reject
    print(f"\ncaught here and not by the platform: {len(extra)}")
    for n in sorted(extra):
        print(f"  {n:44} {', '.join(findings.get(n, []))}")

    if validator and violated:
        return 1 if args.strict else 0
    if not validator:
        return 2
    return 0


if __name__ == "__main__":
    if not CHECK.exists():
        print(f"RESULT: not evaluable — check_skill.py not found beside {HERE}")
        sys.exit(2)
    sys.exit(main())
