import sys
from pathlib import Path

_test_dir = Path(__file__).resolve().parent
_service_root = _test_dir.parent
_project_root = _service_root.parent.parent
for p in (_service_root, _project_root):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
