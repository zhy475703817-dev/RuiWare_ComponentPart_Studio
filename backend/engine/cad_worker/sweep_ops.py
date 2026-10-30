"""兼容入口：扫掠算子已迁移到 ``cad_worker.operators.sweep_ops``。"""

import sys

from .operators import sweep_ops as _implementation

sys.modules[__name__] = _implementation
