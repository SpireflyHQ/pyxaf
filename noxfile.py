#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["nox>=2025.10.16"]
# ///
"""Developer tasks. Run ``uvx nox -l`` to list them, ``uvx nox -s tests`` to run one.

Every session uses the locked environment from ``uv.lock`` (``uv sync --locked``), so results
match CI. The plain ``uv run …`` equivalents are listed in CONTRIBUTING.md.
"""

from __future__ import annotations

import nox

nox.needs_version = ">=2025.10.16"
nox.options.default_venv_backend = "uv"


def _sync(session: nox.Session, *args: str) -> None:
    session.run_install(
        "uv",
        "sync",
        "--locked",
        "--no-default-groups",
        *args,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )


@nox.session
def lint(session: nox.Session) -> None:
    """Run all pre-commit hooks (ruff, typos, zizmor, uv-lock, …) with prek."""
    session.install("prek")
    session.run("prek", "run", "--all-files", *session.posargs)


@nox.session
def typing(session: nox.Session) -> None:
    """Type-check with mypy (strict) and check public API completeness with pyright."""
    _sync(session, "--all-extras", "--group", "typing")
    session.run("mypy")
    session.run("pyright", "--verifytypes", "pyxaf", "--ignoreexternal", success_codes=[0, 1])


@nox.session
def tests(session: nox.Session) -> None:
    """Run the test suite with all extras. Pass pytest options after ``--``."""
    _sync(session, "--all-extras", "--group", "test")
    session.run("pytest", *session.posargs)


@nox.session(name="tests-core", default=False)
def tests_core(session: nox.Session) -> None:
    """Run the tests that need no optional extra, without any extra installed."""
    _sync(session, "--group", "test")
    session.run("pytest", "-m", "not extras", *session.posargs)


@nox.session(default=False)
def generate(session: nox.Session) -> None:
    """Regenerate the field catalogues, the generated docs pages and the sample files."""
    _sync(session, "--group", "gen", "--group", "docs")
    session.run("python", "scripts/gen_catalogues.py")
    session.run("python", "scripts/gen_docs.py")
    session.run("python", "scripts/gen_examples.py")


@nox.session(default=False)
def docs(session: nox.Session) -> None:
    """Build the documentation site (``-- serve`` for a live preview)."""
    _sync(session, "--group", "docs")
    if session.posargs and session.posargs[0] == "serve":
        session.run("zensical", "serve")
    else:
        session.run("python", "scripts/gen_docs.py", "--check")
        session.run("zensical", "build", "--strict", "--clean")


@nox.session(default=False)
def bench(session: nox.Session) -> None:
    """Throughput and memory benchmark (``-- --lines 1000000`` for a bigger file)."""
    _sync(session, "--all-extras")
    session.run("python", "benchmarks/run.py", *(session.posargs or ["--lines", "200000"]))


if __name__ == "__main__":
    nox.main()
