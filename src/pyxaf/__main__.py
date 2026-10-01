"""Command-line entry point (``python -m pyxaf`` / ``pyxaf``)."""

from __future__ import annotations

import sys


def main() -> None:
    """Run the CLI (needs the ``cli`` extra)."""
    try:
        from .cli import app  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        if "typer" not in str(exc) and "click" not in str(exc):
            raise
        sys.stderr.write("The pyxaf command line needs the 'cli' extra: pip install 'pyxaf[cli]'\n")
        raise SystemExit(3) from None
    import typer  # noqa: PLC0415 - installed with the cli extra

    try:
        code = app(standalone_mode=False)
    except typer.Abort:
        raise SystemExit(130) from None
    except typer.TyperException as exc:  # usage errors must not look like "errors found" (2)
        show = getattr(exc, "show", None)
        if show is not None:
            show()
        else:
            sys.stderr.write(f"pyxaf: {exc}\n")
        raise SystemExit(3) from None
    raise SystemExit(code if isinstance(code, int) else 0)


if __name__ == "__main__":
    main()
