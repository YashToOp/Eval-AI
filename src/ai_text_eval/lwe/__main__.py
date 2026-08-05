"""`python -m ai_text_eval.lwe` — start the local writing application."""

import sys

from ai_text_eval.lwe.server import main

if __name__ == "__main__":
    sys.exit(main())
