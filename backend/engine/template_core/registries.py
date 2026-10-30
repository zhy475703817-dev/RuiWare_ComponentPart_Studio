from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RegistryOption(BaseModel):
    id: str
    label: str
    description: str = ""
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class GeometryPrototypeOption(RegistryOption):
    constructionMode: str
    previewStrategy: Literal["generic", "plate", "openProfile", "closedProfile", "revolved", "path"] = "generic"
    operator: str
    drivingParameters: list[str] = Field(default_factory=list)
    implementationStatus: Literal["available", "configurable", "planned"] = "configurable"


class TemplateAuthoringRegistry(BaseModel):
    version: str
    templateKind: Literal["monolithicPart"]
    origins: list[RegistryOption]
    primaryProcesses: list[RegistryOption]
    secondaryProcesses: list[RegistryOption]
    geometryPrototypes: list[GeometryPrototypeOption]


TEMPLATE_AUTHORING_REGISTRY = TemplateAuthoringRegistry(
    version="2.0.0",
    templateKind="monolithicPart",
    origins=[
        RegistryOption(id="inHouse", label="自制件", description="由企业内部制造资源完成。"),
        RegistryOption(id="outsourced", label="外协件", description="由外部供应商按图制造。"),
        RegistryOption(id="purchasedStandard", label="采购标准件", description="按标准或目录型号采购。"),
        RegistryOption(id="purchasedCustom", label="采购非标件", description="按企业规格采购的非标零件。"),
        RegistryOption(id="externalDerived", label="外部模型派生件", description="以受控外部 CAD 模型为几何来源。"),
    ],
    primaryProcesses=[
        RegistryOption(id="coldRollForming", label="冷弯辊压", tags=["coil", "openProfile", "closedProfile"]),
        RegistryOption(id="cutting", label="激光／冲裁下料", tags=["sheet", "plate"]),
        RegistryOption(id="bending", label="折弯成形", tags=["sheetMetal"]),
        RegistryOption(id="extrusion", label="挤压成形", tags=["openProfile", "closedProfile"]),
        RegistryOption(id="machining", label="机加工", tags=["bar", "tube", "stock"]),
        RegistryOption(id="casting", label="铸造成形", tags=["solid"]),
        RegistryOption(id="injectionMolding", label="注塑成形", tags=["plastic"]),
        RegistryOption(id="wireForming", label="线材成形", tags=["wire"]),
        RegistryOption(id="additive", label="增材制造", tags=["solid"]),
        RegistryOption(id="purchased", label="直接采购", tags=["standardPart"]),
    ],
    secondaryProcesses=[
        RegistryOption(id="cutting", label="切断／切边"), RegistryOption(id="punching", label="冲孔／冲槽"),
        RegistryOption(id="drilling", label="钻孔"), RegistryOption(id="tapping", label="攻丝"),
        RegistryOption(id="bending", label="折弯"),
        RegistryOption(id="surfaceTreatment", label="表面处理"), RegistryOption(id="heatTreatment", label="热处理"),
        RegistryOption(id="deburring", label="去毛刺"),
    ],
    geometryPrototypes=[
        GeometryPrototypeOption(id="prototype.customRecipe", label="通用二维参数化截面", description="从可编辑图元、尺寸、约束和截面区域开始，不限定零部件形状。", constructionMode="extrude", operator="profile.open_profile_tube_extrude", drivingParameters=["length", "sectionWidth", "sectionHeight"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.plate", label="板类／平面轮廓", description="用通用二维草图建立实心或带孔平面轮廓。", constructionMode="extrude", previewStrategy="plate", operator="profile.open_profile_tube_extrude", drivingParameters=["length", "sectionWidth", "sectionHeight"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.openThinWallProfile", label="开口薄壁截面", description="以草图中心线偏移生成薄壁轮廓，再区域拉伸成实体。", constructionMode="coldRollForming", previewStrategy="openProfile", operator="profile.open_profile_tube_extrude", drivingParameters=["length", "sectionWidth", "sectionHeight", "thickness"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.closedProfile", label="闭口／管类截面", description="使用外环加内环减材表达矩形管和任意多腔截面。", constructionMode="extrude", previewStrategy="closedProfile", operator="profile.open_profile_tube_extrude", drivingParameters=["length", "sectionWidth", "sectionHeight", "thickness"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.revolvedBody", label="回转体", description="由受约束截面绕草图平面内参数化轴旋转形成。", constructionMode="revolve", previewStrategy="revolved", operator="solid.revolve", drivingParameters=["sectionWidth", "sectionHeight"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.pathSweep", label="路径扫掠体", description="由受约束截面沿参数化三维折线路径扫掠。", constructionMode="sweep", previewStrategy="path", operator="solid.sweep", drivingParameters=["sectionWidth", "sectionHeight", "length"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.loftedBody", label="多截面放样体", description="由同拓扑截面按参数化位置和缩放站放样。", constructionMode="loft", operator="solid.loft", drivingParameters=["sectionWidth", "sectionHeight", "length"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.sheetBend", label="钣金单折弯件", description="由板长、板宽、厚度、折弯位置和角度生成单折弯实体。", constructionMode="sheetMetal", operator="sheet.bend", drivingParameters=["length", "sectionWidth", "thickness"], implementationStatus="available"),
        GeometryPrototypeOption(id="prototype.solidStock", label="实体毛坯机加工", description="从板、棒、块或锻件毛坯逐步去除材料。", constructionMode="machinedStock", operator="stock.solid", drivingParameters=["length", "width", "depth"], implementationStatus="configurable"),
        GeometryPrototypeOption(id="prototype.wirePath", label="线材路径体", description="由圆形或异形线材截面沿路径成形。", constructionMode="sweep", previewStrategy="path", operator="wire.path_sweep", implementationStatus="configurable"),
        GeometryPrototypeOption(id="prototype.externalDerived", label="外部模型派生", description="引用受控 STEP 等外部几何并补充语义。", constructionMode="externalDerived", operator="solid.import", implementationStatus="configurable"),
    ],
)


def registry_option_exists(collection: str, option_id: str) -> bool:
    options = getattr(TEMPLATE_AUTHORING_REGISTRY, collection)
    return any(item.id == option_id and item.enabled for item in options)
