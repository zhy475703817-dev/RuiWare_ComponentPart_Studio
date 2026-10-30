"""兼容入口：基体算子已迁移到 ``cad_worker.operators.body_ops``。"""

import sys

from .operators import body_ops as _implementation

sys.modules[__name__] = _implementation
