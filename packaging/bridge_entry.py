"""PyInstaller entry point for the packaged bridge executable -- identical
to the `eyes-of-aziz-bridge` console script, just as a plain script file
since PyInstaller needs one to build from.
"""

import sys

from eyes_of_aziz.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
