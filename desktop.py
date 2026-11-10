"""Arranque recomendado del contenedor de escritorio de Delivery Manager Toolbox."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from delivery_manager_toolbox.desktop import run_desktop


if __name__ == "__main__":
    run_desktop(app_path=ROOT / "app.py")
