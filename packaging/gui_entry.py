"""PyInstaller entry point for the packaged setup app: always opens the web
dashboard, no command-line flags needed, since a double-clicked executable
can't be handed arguments. The pip-installed `eyes-of-aziz-setup` console
script (eyes_of_aziz/setup.py:main) stays dual-mode (terminal or --web) for
people running it from source.
"""

from eyes_of_aziz.webapp import run_web


def main() -> None:
    run_web(open_browser=True)


if __name__ == "__main__":
    main()
