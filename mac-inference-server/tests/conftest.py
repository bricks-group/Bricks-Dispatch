import sys
from pathlib import Path

# Make `import app` (this directory's ../app.py) resolvable regardless of
# where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
