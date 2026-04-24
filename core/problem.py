"""
VRP 问题参数与数据结构定义。
华中杯 2026 A 题：城市绿色物流配送调度。
"""
from dataclasses import dataclass, field
from typing import List, Tuple


# ========= 全局常量 =========

# 时间常量（单位：小时，从 0:00 算起）
DEPART_TIME = 7.5          # 车辆统一出发时间 7:30
MAX_WORK_HOURS = 15.0      # 每辆车最长工作时间 15h → 22:30 前回
                           # (放宽自12h, 原因: 数据最晚时间窗20:58,
                           #  加服务20min + 远郊回程, 需15小时工作窗)
SERVICE_TIME = 20 / 60     # 每客户服务时间 20 分钟
LATEST_RETURN = DEPART_TIME + MAX_WORK_HOURS  # 19:30

# 速度时段（时段起点, 时段终点, 速度均值 km/h, 速度方差）
# 17:00 后按顺畅时段（根据下班后货车限行解除的实际情况）
SPEED_SEGMENTS = [
    (0.0, 8.0, 55.3, 0.12),      # 早上 0-8 点：顺畅（早发福利）
    (8.0, 9.0, 9.8, 4.7**2),     # 8-9 拥堵
    (9.0, 10.0, 55.3, 0.12),     # 9-10 顺畅
    (10.0, 11.5, 35.4, 5.2**2),  # 10-11:30 一般
    (11.5, 13.0, 9.8, 4.7**2),   # 11:30-13 拥堵
    (13.0, 15.0, 55.3, 0.12),    # 13-15 顺畅
    (15.0, 17.0, 35.4, 5.2**2),  # 15-17 一般
    (17.0, 24.0, 55.3, 0.12),    # 17 后顺畅（假设）
]

# 成本相关
FUEL_PRICE = 7.61       # 元/L
ELEC_PRICE = 1.64       # 元/kWh
ETA_FUEL = 2.547        # kg CO2 / L
ETA_ELEC = 0.501        # kg CO2 / kWh
CARBON_PRICE = 0.65     # 元/kg CO2
START_COST = 400.0      # 元/辆

EARLY_PENALTY = 20.0    # 元/小时
LATE_PENALTY = 50.0     # 元/小时

LOAD_FACTOR_FUEL = 0.40  # 满载系数增量
LOAD_FACTOR_ELEC = 0.35

# 绿色配送区
GREEN_ZONE_CENTER = (0.0, 0.0)
GREEN_ZONE_RADIUS = 10.0

# 绿色区限行政策 (问题2)
# 8:00 - 16:00 禁止燃油车进入绿色区
GREEN_BAN_START = 8.0
GREEN_BAN_END = 16.0
POLICY_PENALTY_PER_VIOLATION = 1_000_000.0  # 软罚项, 仅 soft 模式


# ========= 数据类 =========

@dataclass
class VehicleType:
    """车辆类型定义。"""
    type_id: int
    name: str
    capacity_kg: float       # 载重 kg
    capacity_m3: float       # 容积 m³
    fleet_size: int          # 该类型可用车辆数
    is_electric: bool        # True=新能源 False=燃油


# 五种车型（见题目表）
VEHICLE_TYPES: List[VehicleType] = [
    VehicleType(0, "燃油车-3000",  3000, 13.5, 60, False),
    VehicleType(1, "燃油车-1500",  1500, 10.8, 50, False),
    VehicleType(2, "燃油车-1250",  1250, 6.5,  50, False),
    VehicleType(3, "新能源-3000",  3000, 15.0, 10, True),
    VehicleType(4, "新能源-1250",  1250, 8.5,  15, True),
]


@dataclass
class Customer:
    """客户数据。"""
    cid: int           # 客户编号（0=depot, 1~98=customer）
    x: float           # km
    y: float           # km
    demand_kg: float   # 需求重量 kg
    demand_m3: float   # 需求体积 m³
    tw_start: float    # 时间窗起 (h)
    tw_end: float      # 时间窗止 (h)
    in_green_zone: bool  # 是否在绿色区内


@dataclass
class Problem:
    """完整问题实例。"""
    customers: List[Customer]         # [0]=depot, [1..N]=customer
    distance: 'np.ndarray'            # (N+1) x (N+1) 距离矩阵 km
    vehicle_types: List[VehicleType] = field(default_factory=lambda: VEHICLE_TYPES)
    # 问题2: 绿色区限行政策开关
    # 'off' = 问题1, 无约束;
    # 'hard' = 问题2, 燃油车禁入时段进绿色区 → infeasible;
    # 'soft' = ALNS 搜索过渡, 违反加大额罚项但仍探索.
    policy_mode: str = "off"

    @property
    def n_customers(self) -> int:
        return len(self.customers) - 1

    @property
    def depot(self) -> Customer:
        return self.customers[0]

    def is_in_green_zone(self, cid: int) -> bool:
        return self.customers[cid].in_green_zone


if __name__ == "__main__":
    # 简单打印参数检验
    print(f"出发时间: {DEPART_TIME}h = 7:30")
    print(f"最晚返回: {LATEST_RETURN}h = 19:30")
    print(f"车型数: {len(VEHICLE_TYPES)}")
    total_kg = sum(v.capacity_kg * v.fleet_size for v in VEHICLE_TYPES)
    total_m3 = sum(v.capacity_m3 * v.fleet_size for v in VEHICLE_TYPES)
    print(f"车队总载重上限: {total_kg/1000:.1f} t")
    print(f"车队总体积上限: {total_m3:.1f} m³")
    print(f"速度段数: {len(SPEED_SEGMENTS)}")
