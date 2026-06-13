"""Quick test script for replay analysis features."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from src.yard_model import ZoneType
from src.stacking_strategies import StackingStrategy
from src.simulation_engine import SimulationEngine
from src.replay_analysis import (
    BottleneckDiagnoser,
    BottleneckType,
    SeverityLevel,
    CustomRule,
    CustomRuleConditionType,
    HistoricalSimulationRecord,
    BottleneckPatternAnalyzer,
    find_nearest_snapshot,
    _format_time,
    StructuralBottleneck,
)


def run_simulation(strategy_name, strategy, seed=42, duration=1440):
    """Run a quick simulation and return engine and events."""
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
            "num_rtgs": {"import": 2, "export": 2, "transit": 1},
        },
        "simulation": {
            "duration": duration,
            "seed": seed,
            "ship_interval_mean": 720.0,
            "ship_size_mean": 80,
            "ship_size_std": 20,
            "export_truck_interval": 30.0,
        },
        "truck_scheduling": {
            "num_gates": 3,
            "gate_process_time": 2.0,
            "max_queue_length": 20,
            "early_arrival_tolerance": 30.0,
        },
        "replay": {
            "sampling_interval": 10.0,
            "congestion_threshold": 0.80,
            "conflict_distance": 2,
            "gate_saturation_ratio": 0.80,
        },
    }

    engine = SimulationEngine(config, strategy, strategy_params={"seed": seed})
    engine.set_snapshot_interval(10.0)
    engine.run(duration)

    snapshots = engine.snapshot_recorder.get_snapshots()

    custom_rules = [
        CustomRule(
            rule_id="rule_test1",
            rule_name="高利用率预警",
            condition_type=CustomRuleConditionType.ZONE_UTILIZATION,
            zone=ZoneType.IMPORT,
            consecutive_snapshots=3,
            threshold_pct=0.70,
            severity=SeverityLevel.HIGH,
        ),
    ]

    diagnoser = BottleneckDiagnoser(snapshots, config, custom_rules=custom_rules)
    diagnoser.set_parameters(
        congestion_threshold=0.80,
        conflict_distance=2,
        gate_saturation_ratio=0.80,
        custom_rules=custom_rules,
    )
    events = diagnoser.diagnose()

    return engine, snapshots, events, config


def test_find_nearest_snapshot():
    """Test find_nearest_snapshot function."""
    print("\n=== Testing find_nearest_snapshot ===")
    from src.replay_analysis import YardSnapshot

    snaps = [
        YardSnapshot(timestamp=0.0),
        YardSnapshot(timestamp=10.0),
        YardSnapshot(timestamp=20.0),
        YardSnapshot(timestamp=30.0),
        YardSnapshot(timestamp=40.0),
    ]

    assert find_nearest_snapshot(snaps, 0.0) == 0
    assert find_nearest_snapshot(snaps, 5.0) == 0
    assert find_nearest_snapshot(snaps, 12.0) == 1
    assert find_nearest_snapshot(snaps, 25.0) == 2
    assert find_nearest_snapshot(snaps, 100.0) == 4
    assert find_nearest_snapshot([], 10.0) == 0
    print("✅ find_nearest_snapshot tests passed")


def test_custom_rules():
    """Test custom bottleneck rules detection."""
    print("\n=== Testing Custom Rules ===")

    engine, snapshots, events, config = run_simulation(
        "随机堆放", StackingStrategy.RANDOM, seed=42, duration=1440
    )

    custom_events = [e for e in events if e.event_type == BottleneckType.CUSTOM_RULE]
    print(f"Total events: {len(events)}")
    print(f"Custom rule events: {len(custom_events)}")
    for e in custom_events:
        print(f"  - {e.custom_rule_name}: {_format_time(e.start_time)} - {_format_time(e.end_time)} (severity: {e.severity.value})")

    print("✅ Custom rules test completed")
    return engine, snapshots, events, config


def test_historical_records_and_pattern_analysis():
    """Test historical records and bottleneck pattern analysis."""
    print("\n=== Testing Historical Records & Pattern Analysis ===")

    strategies = [
        ("随机堆放", StackingStrategy.RANDOM, 42),
        ("分类堆放", StackingStrategy.CLASSIFIED, 43),
        ("时序优先", StackingStrategy.TIME_PRIORITY, 44),
        ("重量分层", StackingStrategy.WEIGHT_LAYERED, 45),
    ]

    history = []
    for i, (name, strat, seed) in enumerate(strategies):
        print(f"  Running simulation {i+1}/{len(strategies)}: {name}...")
        engine, snapshots, events, config = run_simulation(name, strat, seed=seed, duration=2880)

        record = HistoricalSimulationRecord(
            record_id=f"sim_{i}",
            label=f"Record {i} - {name}",
            timestamp=1000000 + i * 1000,
            strategy_name=name,
            snapshots=list(snapshots),
            bottleneck_events=list(events),
            config_snapshot=dict(config),
        )
        history.append(record)
        print(f"    -> {len(snapshots)} snapshots, {len(events)} events")

    print(f"\nTotal history records: {len(history)}")

    print("\n--- Running BottleneckPatternAnalyzer (min_occurrences=3) ---")
    analyzer = BottleneckPatternAnalyzer(history, time_window_tolerance=30.0, min_occurrences=3)
    structural = analyzer.analyze()

    print(f"Structural bottlenecks found: {len(structural)}")
    for i, sb in enumerate(structural):
        zone_str = sb.zone.value if sb.zone else "N/A"
        type_str = sb.event_type.value
        if sb.event_type == BottleneckType.CUSTOM_RULE and sb.custom_rule_name:
            type_str = f"rule:{sb.custom_rule_name}"
        print(f"  [{i+1}] Zone: {zone_str}, Type: {type_str}")
        print(f"      Typical time: {_format_time(sb.typical_start)} - {_format_time(sb.typical_end)}")
        print(f"      Frequency: {sb.frequency}/{sb.total_simulations}")
        print(f"      Avg duration: {_format_time(sb.avg_duration)}")
        print(f"      Suggestion: {analyzer.generate_suggestion(sb)}")

    print("✅ Pattern analysis test completed")
    return structural


def main():
    print("=" * 60)
    print("Testing Replay Analysis Features")
    print("=" * 60)

    test_find_nearest_snapshot()
    test_custom_rules()
    test_historical_records_and_pattern_analysis()

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
