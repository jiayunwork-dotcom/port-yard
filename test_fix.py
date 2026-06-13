import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.yard_model import ZoneType
from src.stacking_strategies import StackingStrategy
from src.simulation_engine import SimulationEngine
from src.kpi import KPICalculator


def test_full_simulation():
    config = {
        "yard": {
            "import": {"num_bays": 20, "num_rows": 6, "num_tiers": 5},
            "export": {"num_bays": 15, "num_rows": 6, "num_tiers": 5},
            "transit": {"num_bays": 10, "num_rows": 6, "num_tiers": 4},
        },
        "rtg": {
            "lift_time": 30.0,
            "lower_time": 30.0,
            "travel_time_per_bay": 15.0,
            "num_rtgs": {
                "import": 2,
                "export": 2,
                "transit": 1,
            },
        },
        "simulation": {
            "duration": 10080,
            "seed": 42,
            "ship_interval_mean": 720.0,
            "ship_size_mean": 80,
            "ship_size_std": 20,
            "export_truck_interval": 120.0,
        },
    }

    sim_duration = 7 * 1440
    strategies = [
        ("随机堆放", StackingStrategy.RANDOM),
        ("分类堆放", StackingStrategy.CLASSIFIED),
        ("时序优先", StackingStrategy.TIME_PRIORITY),
        ("重量分层", StackingStrategy.WEIGHT_LAYERED),
        ("综合优化", StackingStrategy.OPTIMIZED),
    ]

    for name, strategy in strategies:
        print(f"\n{'='*60}")
        print(f"策略: {name}")
        print(f"{'='*60}")

        engine = SimulationEngine(config, strategy, strategy_params={"seed": 42})
        stats = engine.run(sim_duration)

        calc = KPICalculator(sim_duration)
        kpi = calc.calculate(engine)

        print(f"船舶到港数: {len([s for s in stats.ship_log if s['event'] == 'arrival'])}")
        print(f"进口箱到达: {stats.import_containers_arrived}")
        print(f"出口箱到达: {stats.export_containers_arrived}")
        print(f"总堆存作业: {stats.total_containers_stowed}")
        print(f"总提箱作业: {stats.total_containers_picked}")
        print(f"总翻箱次数: {stats.total_relocations}")
        print(f"翻箱率: {kpi.relocation_rate:.2%}")
        print(f"平均利用率: {kpi.avg_utilization:.2%}")
        print(f"峰值利用率: {kpi.peak_utilization:.2%}")
        print(f"日均吞吐量: {kpi.daily_throughput:.0f} TEU")
        print(f"平均提箱耗时: {kpi.avg_pickup_time:.1f} 分钟")
        print(f"平均场桥效率: {kpi.avg_rtg_efficiency:.2f} 箱/小时")

        for zone, rtgs in engine.rtgs.items():
            for rtg in rtgs:
                eff = kpi.rtg_efficiency.get(rtg.rtg_id, 0)
                print(f"  {rtg.rtg_id}: 作业{rtg.total_tasks_completed}次, 效率{eff:.2f}箱/h")

    print(f"\n{'='*60}")
    print("验证关键指标:")
    print(f"{'='*60}")

    engine = SimulationEngine(config, StackingStrategy.RANDOM, strategy_params={"seed": 42})
    stats = engine.run(sim_duration)
    calc = KPICalculator(sim_duration)
    kpi = calc.calculate(engine)

    ships_arrived = len([s for s in stats.ship_log if s['event'] == 'arrival'])
    expected_min = int(sim_duration / 720) - 2

    assert ships_arrived >= expected_min, f"到港船只数{ships_arrived}远低于预期{expected_min}"
    print(f"✅ 到港船只数: {ships_arrived} (预期约{expected_min}+)")

    assert stats.total_containers_stowed >= 200, f"堆存作业{stats.total_containers_stowed}远低于预期"
    print(f"✅ 堆存作业: {stats.total_containers_stowed}")

    assert stats.total_containers_picked >= 50, f"提箱作业{stats.total_containers_picked}远低于预期"
    print(f"✅ 提箱作业: {stats.total_containers_picked}")

    assert kpi.daily_throughput >= 50, f"日均吞吐量{kpi.daily_throughput}远低于预期"
    print(f"✅ 日均吞吐量: {kpi.daily_throughput:.0f} TEU")

    print("\n🎉 所有验证通过！")


if __name__ == "__main__":
    test_full_simulation()
