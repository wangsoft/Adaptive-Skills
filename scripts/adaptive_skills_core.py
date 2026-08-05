"""PyInstaller entry point for the self-contained desktop core."""

from adaptive_skills.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
