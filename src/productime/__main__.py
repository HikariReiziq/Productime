"""Entry point: `python3 -m productime`."""

import sys


def main() -> int:
    from .app import run

    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
