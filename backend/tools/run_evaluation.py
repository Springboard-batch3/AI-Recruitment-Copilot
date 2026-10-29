import sys
from pathlib import Path

# Add backend directory to sys.path so tests.evaluation_support and app can be resolved
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from tests.evaluation_support.cli import run_evaluation_cli

if __name__ == "__main__":
    sys.exit(run_evaluation_cli())
