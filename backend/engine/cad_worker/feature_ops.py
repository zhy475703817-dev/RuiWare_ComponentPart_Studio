"""兼容入口：加工特征算子已迁移到 ``cad_worker.operators.feature_ops``。"""

import sys

from .operators import feature_ops as _implementation

sys.modules[__name__] = _implementation
