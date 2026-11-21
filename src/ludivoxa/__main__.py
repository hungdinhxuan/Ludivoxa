"""Main entry point for Ludivoxa.

This module is executed when running:
- python -m ludivoxa
- the ludivoxa command

Both routes go through ludivoxa.main so that startup work such as
resolving the install location happens before the GUI is imported.
"""

from . import main

if __name__ == "__main__":
    main()
