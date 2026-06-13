from dataclasses import dataclass, field
from typing import List, Dict, Callable, Optional, Tuple
import numpy as np

from .simulation_engine import SimulationEngine
from .stacking_strategies import StackingStrategy
from .kpi import KPICalculator, KPIResult


@dataclass
class OptimizationResult:
    param_name: str
    param_values: List[float]
    kpi_results: List[KPIResult]
    best_value: float = 0.0
    best_index: int = 0
    objective: str = "min_relocation"


class ParameterOptimizer:
    def __init__(self, base_config: Dict):
        self.base_config = base_config

    def optimize_time_weight(
        self,
        num_points: int = 10,
        objective: str = "min_relocation",
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> OptimizationResult:
        param_values = np.linspace(0.1, 0.9, num_points).tolist()
        kpi_results = []

        for i, time_weight in enumerate(param_values):
            weight_weight = 1.0 - time_weight

            engine = SimulationEngine(
                self.base_config,
                StackingStrategy.OPTIMIZED,
                strategy_params={
                    "time_weight": time_weight,
                    "weight_weight": weight_weight,
                },
            )

            sim_duration = self.base_config.get("simulation", {}).get("duration", 10080)
            engine.run(sim_duration)

            calc = KPICalculator(sim_duration)
            kpi = calc.calculate(engine)
            kpi_results.append(kpi)

            if progress_callback:
                progress_callback((i + 1) / num_points)

        best_idx = self._find_best(kpi_results, objective)

        return OptimizationResult(
            param_name="time_weight",
            param_values=param_values,
            kpi_results=kpi_results,
            best_value=param_values[best_idx],
            best_index=best_idx,
            objective=objective,
        )

    def _find_best(self, kpi_results: List[KPIResult], objective: str) -> int:
        if objective == "min_relocation":
            values = [k.relocation_rate for k in kpi_results]
            return int(np.argmin(values))
        elif objective == "max_throughput":
            values = [k.daily_throughput for k in kpi_results]
            return int(np.argmax(values))
        elif objective == "min_pickup_time":
            values = [k.avg_pickup_time for k in kpi_results]
            return int(np.argmin(values))
        elif objective == "max_utilization":
            values = [k.avg_utilization for k in kpi_results]
            return int(np.argmax(values))
        else:
            return 0

    def run_strategy_comparison(
        self,
        strategies: List[StackingStrategy],
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> Dict[str, KPIResult]:
        results = {}
        sim_duration = self.base_config.get("simulation", {}).get("duration", 10080)

        for i, strategy in enumerate(strategies):
            engine = SimulationEngine(
                self.base_config,
                strategy,
                strategy_params={
                    "time_weight": 0.5,
                    "weight_weight": 0.5,
                    "seed": 42,
                },
            )
            engine.run(sim_duration)

            calc = KPICalculator(sim_duration)
            kpi = calc.calculate(engine)
            results[strategy.value] = kpi

            if progress_callback:
                progress_callback((i + 1) / len(strategies), strategy.value)

        return results
