import sys
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.cli.bootstrap_admin import run_bootstrap_cli

if __name__ == "__main__":
    sys.exit(run_bootstrap_cli())
