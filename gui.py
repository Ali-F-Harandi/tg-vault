#!/usr/bin/env python3
"""
tg-vault GUI — backward-compatibility shim
==========================================
Allows ``python gui.py`` to keep working after the GUI code was moved
into :mod:`gui.app`.
"""

import os
import sys

# Make sure both the project root and the gui/ directory are importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from gui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
