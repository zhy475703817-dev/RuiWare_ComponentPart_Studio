"""CAD 几何算子实现。

按职责集中管理基础实体、基体、加工特征和扫掠算子；legacy 仅用于兼容
历史上的单文件算子入口，不作为新的执行入口。
"""

from .feature_ops import FEATURE_OPERATORS, apply_operation
from .body_ops import FaceMap, FaceSupport, build_body, build_body_with_face_map, resolve_face_support
from .sweep_ops import SweepPathConstructionError, _build_sweep_path_wire, _sketch_sweep

__all__ = [
    "FEATURE_OPERATORS",
    "FaceMap",
    "FaceSupport",
    "SweepPathConstructionError",
    "apply_operation",
    "build_body",
    "build_body_with_face_map",
    "resolve_face_support",
]
