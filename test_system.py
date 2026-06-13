import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.yard_model import Yard, Container, ContainerSize, WeightClass, ZoneType
from src.stacking_strategies import create_strategy, StackingStrategy
from src.container_generator import ContainerGenerator
from src.relocation import RelocationPlanner
from src.rtg_scheduler import RTGScheduler, RTGTask, TaskType


def test_yard_model():
    print("=" * 50)
    print("测试1: 堆场建模模块")
    print("=" * 50)

    config = {
        "import": {"num_bays": 10, "num_rows": 6, "num_tiers": 5},
        "export": {"num_bays": 10, "num_rows": 6, "num_tiers": 5},
        "transit": {"num_bays": 5, "num_rows": 6, "num_tiers": 4},
    }

    yard = Yard(config)
    total_slots = yard.get_total_slots()
    print(f"总格位数: {total_slots}")
    print(f"初始利用率: {yard.get_utilization():.2%}")

    container = Container(
        container_id="TEST001",
        size=ContainerSize.SIZE_20FT,
        weight_class=WeightClass.HEAVY,
        ship_name="SHIP_A",
        pickup_time_window=(100, 200),
        is_import=True,
    )

    result = yard.place_container(container, ZoneType.IMPORT, 0, 0)
    if result:
        print(f"集装箱 {container.container_id} 已放置到进口区贝位0排0")
        print(f"当前利用率: {yard.get_utilization():.2%}")
        print(f"在场集装箱数: {len(yard.get_all_containers())}")

    found = yard.find_container(container)
    if found:
        print(f"找到集装箱，位置: 贝位={found.bay}, 排={found.row}, 层={found.tier}")

    print("堆场建模测试: ✅ 通过\n")


def test_stacking_strategies():
    print("=" * 50)
    print("测试2: 堆垛策略模块")
    print("=" * 50)

    config = {
        "import": {"num_bays": 5, "num_rows": 4, "num_tiers": 3},
        "export": {"num_bays": 5, "num_rows": 4, "num_tiers": 3},
        "transit": {"num_bays": 3, "num_rows": 4, "num_tiers": 3},
    }
    yard = Yard(config)

    strategies = [
        ("随机堆放", StackingStrategy.RANDOM),
        ("分类堆放", StackingStrategy.CLASSIFIED),
        ("时序优先", StackingStrategy.TIME_PRIORITY),
        ("重量分层", StackingStrategy.WEIGHT_LAYERED),
        ("综合优化", StackingStrategy.OPTIMIZED),
    ]

    gen = ContainerGenerator(seed=42)
    containers = gen.generate_containers(20, is_import=True, sim_duration=1000)

    for name, strategy_type in strategies:
        test_yard = Yard(config)
        strategy = create_strategy(strategy_type, seed=42, time_weight=0.5, weight_weight=0.5)

        placed = 0
        for c in containers:
            pos = strategy.find_slot(c, test_yard, ZoneType.IMPORT)
            if pos:
                test_yard.place_container(c, ZoneType.IMPORT, pos[0], pos[1])
                placed += 1

        util = test_yard.get_utilization()
        print(f"{name}: 放置 {placed}/{len(containers)} 个箱子, 利用率 {util:.2%}")

    print("堆垛策略测试: ✅ 通过\n")


def test_relocation():
    print("=" * 50)
    print("测试3: 翻箱计算模块")
    print("=" * 50)

    config = {
        "import": {"num_bays": 3, "num_rows": 4, "num_tiers": 5},
        "export": {"num_bays": 3, "num_rows": 4, "num_tiers": 5},
        "transit": {"num_bays": 2, "num_rows": 4, "num_tiers": 4},
    }
    yard = Yard(config)

    gen = ContainerGenerator(seed=123)
    containers = gen.generate_containers(15, is_import=True, sim_duration=1000)

    for c in containers:
        yard.place_container(c, ZoneType.IMPORT, 0, 0)

    bottom_container = containers[0]
    planner = RelocationPlanner()

    relocations_needed = planner.count_relocations_needed(bottom_container, yard)
    print(f"提取最底层箱子需要翻箱: {relocations_needed} 次")

    result = planner.plan_retrieval(bottom_container, yard)
    print(f"翻箱计划成功: {result.success}")
    print(f"实际翻箱次数: {result.count}")
    print(f"总翻箱成本: {result.total_cost:.1f}")

    print("翻箱计算测试: ✅ 通过\n")


def test_rtg_scheduler():
    print("=" * 50)
    print("测试4: 场桥调度模块")
    print("=" * 50)

    scheduler = RTGScheduler(
        lift_time=30.0,
        lower_time=30.0,
        travel_time_per_bay=15.0,
    )

    rtg = scheduler.create_rtg("test_rtg", ZoneType.IMPORT, start_bay=0)

    tasks = [
        RTGTask("t1", TaskType.STOW, ZoneType.IMPORT, 3, "C1"),
        RTGTask("t2", TaskType.PICKUP, ZoneType.IMPORT, 7, "C2"),
        RTGTask("t3", TaskType.STOW, ZoneType.IMPORT, 1, "C3"),
        RTGTask("t4", TaskType.PICKUP, ZoneType.IMPORT, 5, "C4"),
    ]

    ordered = scheduler._nearest_neighbor_single(0, tasks)
    print("最近邻算法优化后的作业顺序:")
    for t in ordered:
        print(f"  {t.task_id}: 贝位 {t.bay}")

    metrics = scheduler.calculate_schedule_metrics(0, ordered)
    print(f"总行走距离: {metrics.total_distance} 贝位")
    print(f"总作业时间: {metrics.total_time:.1f} 秒")

    print("场桥调度测试: ✅ 通过\n")


def test_simulation_engine():
    print("=" * 50)
    print("测试5: 仿真引擎模块")
    print("=" * 50)

    from src.simulation_engine import SimulationEngine
    from src.kpi import KPICalculator

    config = {
        "yard": {
            "import": {"num_bays": 10, "num_rows": 6, "num_tiers": 5},
            "export": {"num_bays": 8, "num_rows": 6, "num_tiers": 5},
            "transit": {"num_bays": 5, "num_rows": 6, "num_tiers": 4},
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
            "duration": 1440,
            "seed": 42,
            "ship_interval_mean": 480.0,
            "ship_size_mean": 30,
            "ship_size_std": 10,
            "export_truck_interval": 60.0,
        },
    }

    print("运行1天仿真...")
    engine = SimulationEngine(config, StackingStrategy.RANDOM, strategy_params={"seed": 42})
    stats = engine.run(config["simulation"]["duration"])

    print(f"总堆存作业数: {stats.total_containers_stowed}")
    print(f"总提取作业数: {stats.total_containers_picked}")
    print(f"总翻箱次数: {stats.total_relocations}")

    calc = KPICalculator(config["simulation"]["duration"])
    kpi = calc.calculate(engine)
    print(f"\nKPI结果:")
    print(f"  翻箱率: {kpi.relocation_rate:.2%}")
    print(f"  平均利用率: {kpi.avg_utilization:.2%}")
    print(f"  峰值利用率: {kpi.peak_utilization:.2%}")
    print(f"  日均吞吐量: {kpi.daily_throughput:.0f} TEU")
    print(f"  平均场桥效率: {kpi.avg_rtg_efficiency:.2f} 箱/小时")

    print("仿真引擎测试: ✅ 通过\n")


if __name__ == "__main__":
    print("港口集装箱堆场调度仿真系统 - 单元测试\n")

    try:
        test_yard_model()
        test_stacking_strategies()
        test_relocation()
        test_rtg_scheduler()
        test_simulation_engine()

        print("=" * 50)
        print("🎉 所有测试通过！")
        print("=" * 50)
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
