#!/usr/bin/env python3
"""Fail when production dependency or container references become floating.

This is a lightweight repository guard. It verifies exact direct Python
requirements, exact OpenKB direct dependencies, patch-level container tags, and
offline wheel installation in the final runtime image. It does not replace a
CVE scanner or a resolver-generated, hash-locked transitive dependency file.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PIN = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;.*)?$")
PATCH_TAG = re.compile(r"^[^:@\s]+:\d+\.\d+\.\d+(?:[-._][A-Za-z0-9._-]+)?$")
POSTGRES_TAG = re.compile(r"^postgres:\d+\.\d+-alpine\d+\.\d+$")


def fail(message: str) -> None:
    raise ValueError(message)


def requirement_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def verify_python_requirements() -> None:
    for line in requirement_lines(ROOT / "requirements.txt"):
        if not VERSION_PIN.fullmatch(line):
            fail(f"requirements.txt contains a non-exact direct dependency: {line}")

    project = tomllib.loads((ROOT / "OpenKB-main" / "pyproject.toml").read_text(encoding="utf-8"))
    for dependency in project.get("project", {}).get("dependencies", []):
        if not VERSION_PIN.fullmatch(dependency):
            fail(f"OpenKB pyproject.toml contains a non-exact direct dependency: {dependency}")
    for dependency in project.get("build-system", {}).get("requires", []):
        if not VERSION_PIN.fullmatch(dependency):
            fail(f"OpenKB build-system contains a non-exact dependency: {dependency}")


def verify_container_references() -> None:
    references: list[str] = []
    for dockerfile_name in ("Dockerfile", "Dockerfile.postgres-vault"):
        for line in (ROOT / dockerfile_name).read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*FROM\s+([^\s]+)", line, flags=re.IGNORECASE)
            if match:
                references.append(match.group(1))

    for line in (ROOT / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*image:\s*([^\s#]+)", line)
        if match:
            references.append(match.group(1))

    for reference in references:
        # An immutable digest is acceptable. Otherwise require an explicit
        # patch-level semantic version rather than latest/major/minor aliases.
        if "@sha256:" in reference:
            continue
        if not (PATCH_TAG.fullmatch(reference) or POSTGRES_TAG.fullmatch(reference)):
            fail(f"Container image reference is not patch-pinned: {reference}")

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "--no-index --find-links=/wheels" not in dockerfile:
        fail("The final Python runtime image must install only from the builder wheelhouse.")
    for tool_pin in ("pip==", "setuptools==", "wheel=="):
        if tool_pin not in dockerfile:
            fail(f"Docker build tool is not exactly pinned: {tool_pin[:-2]}")


def main() -> int:
    try:
        verify_python_requirements()
        verify_container_references()
    except (OSError, KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"Supply-chain pin verification failed: {error}", file=sys.stderr)
        return 1
    print("Supply-chain direct dependency and container tag checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
