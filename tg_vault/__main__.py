"""
Entry point for ``python -m tg_vault``.
"""

from .interactive import install_signal_handlers
from .cli import main

if __name__ == "__main__":
    install_signal_handlers()
    main()
