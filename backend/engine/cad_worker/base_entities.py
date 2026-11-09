"""兼容入口：基础实体算子已迁移到 ``cad_worker.operators.base_entities``。"""

import sys

from .operators import base_entities as _implementation

sys.modules[__name__] = _implementation
