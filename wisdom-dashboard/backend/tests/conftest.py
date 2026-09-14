"""Put the backend package root on sys.path so tests import the modules the
same way the app does (`import aggregation`, not `import backend.aggregation`)
-- app.py/orchestrator.py run with backend/ as the working directory, and the
tests should exercise exactly that import shape rather than a parallel one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
