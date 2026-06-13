from dataclasses import dataclass, field
from typing import List, Dict, Tuple
import statistics

from .simulation_engine import SimulationStats, SimulationEngine
from .yard_model import ZoneType


@dataclass
class KPIResult:
    relocation_rate: float = 0.0
    avg_utilization: float = 0.0
    peak_utilization: float = 0.0
    avg_pickup_time: float = 0.0
    rtg_efficiency: Dict[str, float] = field(default_factory=dict)
    daily_throughput: float = 0.0
    total_throughput: int = 0
    total_containers_stowed: int = 0
    total_containers_picked: int = 0
    total_relocations: int = 0
    total_pickup_operations: int = 0
    avg_rtg_efficiency: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "翻箱率": f"{self.relocation_rate:.2%}",
            "平均场地利用率": f"{self.avg_utilization:.2%}",
            "峰值场地利用率": f"{self.peak_utilization:.2%}",
            "平均提箱耗时(分钟)": f"{self.avg_pickup_time:.1f}",
            "平均场桥效率(箱/小时)": f"{self.avg_rtg_efficiency:.1f}",
            "日均吞吐量(TEU)": f"{self.daily_throughput:.0f}",
            "总吞吐量(TEU)": self.total_throughput,
            "总翻箱次数": self.total_relocations,
            "总提箱次数": self.total_pickup_operations,
        }


class KPICalculator:
    def __init__(self, sim_duration_minutes: float = 10080.0):
        self.sim_duration = sim_duration_minutes

    def calculate(self, engine: SimulationEngine) -> KPIResult:
        stats = engine.stats
        kpi = KPIResult()

        if stats.total_pickup_operations > 0:
            kpi.relocation_rate = stats.total_relocations / stats.total_pickup_operations
        kpi.total_relocations = stats.total_relocations
        kpi.total_pickup_operations = stats.total_pickup_operations

        if stats.utilization_history:
            utils = [u for _, u in stats.utilization_history]
            kpi.avg_utilization = statistics.mean(utils) if utils else 0
            kpi.peak_utilization = max(utils) if utils else 0

        if stats.pickup_wait_times:
            kpi.avg_pickup_time = statistics.mean(stats.pickup_wait_times)

        kpi.rtg_efficiency = {}
        total_rtg_efficiency = 0.0
        rtg_count = 0
        for zone, rtgs in engine.rtgs.items():
            for rtg in rtgs:
                if self.sim_duration > 0:
                    efficiency = (rtg.total_tasks_completed / (self.sim_duration / 60.0))
                else:
                    efficiency = 0
                kpi.rtg_efficiency[rtg.rtg_id] = efficiency
                total_rtg_efficiency += efficiency
                rtg_count += 1

        kpi.avg_rtg_efficiency = total_rtg_efficiency / rtg_count if rtg_count > 0 else 0

        days = self.sim_duration / 1440.0
        kpi.total_throughput = stats.total_containers_stowed
        kpi.daily_throughput = kpi.total_throughput / days if days > 0 else 0
        kpi.total_containers_stowed = stats.total_containers_stowed
        kpi.total_containers_picked = stats.total_containers_picked

        return kpi

    def get_utilization_trend(self, stats: SimulationStats) -> List[Tuple[float, float]]:
        return stats.utilization_history

    def get_throughput_trend(self, stats: SimulationStats) -> List[Tuple[float, int]]:
        return stats.throughput_history

    def get_containers_in_yard_trend(self, stats: SimulationStats) -> List[Tuple[float, int]]:
        return stats.containers_in_yard
