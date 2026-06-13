import streamlit as st
import sys
import os
import plotly.graph_objects as go

sys.path.insert(0, os.path.dirname(__file__))

from src.yard_model import ZoneType, Yard
from src.stacking_strategies import StackingStrategy
from src.simulation_engine import SimulationEngine
from src.kpi import KPICalculator, KPIResult
from src.visualization import Visualizer
from src.parameter_optimization import ParameterOptimizer
from src.replay_analysis import (
    BottleneckDiagnoser,
    ReplayVisualizer,
    DiagnosisReportGenerator,
    BottleneckType,
    _format_time,
    SeverityLevel,
    CustomRule,
    CustomRuleConditionType,
    HistoricalSimulationRecord,
    BottleneckPatternAnalyzer,
    find_nearest_snapshot,
)
import time as _time


st.set_page_config(
    page_title="港口集装箱堆场调度仿真与吞吐量分析 Dashboard",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

visualizer = Visualizer()


def get_default_config():
    return {
        "yard": {
            "import": {
                "num_bays": 20,
                "num_rows": 6,
                "num_tiers": 5,
            },
            "export": {
                "num_bays": 15,
                "num_rows": 6,
                "num_tiers": 5,
            },
            "transit": {
                "num_bays": 10,
                "num_rows": 6,
                "num_tiers": 4,
            },
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


def sidebar_config():
    st.sidebar.title("⚙️ 配置面板")

    config = get_default_config()

    st.sidebar.subheader("📦 堆场结构")

    with st.sidebar.expander("进口区 (Import Zone)", expanded=False):
        imp_bays = st.number_input("贝位数", 5, 50, config["yard"]["import"]["num_bays"], key="imp_bays")
        imp_rows = st.number_input("排数", 2, 12, config["yard"]["import"]["num_rows"], key="imp_rows")
        imp_tiers = st.number_input("层数", 2, 10, config["yard"]["import"]["num_tiers"], key="imp_tiers")

    with st.sidebar.expander("出口区 (Export Zone)", expanded=False):
        exp_bays = st.number_input("贝位数", 5, 50, config["yard"]["export"]["num_bays"], key="exp_bays")
        exp_rows = st.number_input("排数", 2, 12, config["yard"]["export"]["num_rows"], key="exp_rows")
        exp_tiers = st.number_input("层数", 2, 10, config["yard"]["export"]["num_tiers"], key="exp_tiers")

    with st.sidebar.expander("中转区 (Transit Zone)", expanded=False):
        tra_bays = st.number_input("贝位数", 5, 30, config["yard"]["transit"]["num_bays"], key="tra_bays")
        tra_rows = st.number_input("排数", 2, 12, config["yard"]["transit"]["num_rows"], key="tra_rows")
        tra_tiers = st.number_input("层数", 2, 8, config["yard"]["transit"]["num_tiers"], key="tra_tiers")

    config["yard"]["import"] = {"num_bays": imp_bays, "num_rows": imp_rows, "num_tiers": imp_tiers}
    config["yard"]["export"] = {"num_bays": exp_bays, "num_rows": exp_rows, "num_tiers": exp_tiers}
    config["yard"]["transit"] = {"num_bays": tra_bays, "num_rows": tra_rows, "num_tiers": tra_tiers}

    st.sidebar.subheader("🏗️ 场桥配置")

    with st.sidebar.expander("场桥参数", expanded=False):
        lift_time = st.number_input("吊起时间 (秒)", 10.0, 120.0, config["rtg"]["lift_time"], key="lift_time")
        lower_time = st.number_input("放下时间 (秒)", 10.0, 120.0, config["rtg"]["lower_time"], key="lower_time")
        travel_time = st.number_input("每贝位行走时间 (秒)", 5.0, 60.0, config["rtg"]["travel_time_per_bay"], key="travel_time")
        imp_rtgs = st.number_input("进口区场桥数", 1, 5, config["rtg"]["num_rtgs"]["import"], key="imp_rtgs")
        exp_rtgs = st.number_input("出口区场桥数", 1, 5, config["rtg"]["num_rtgs"]["export"], key="exp_rtgs")
        tra_rtgs = st.number_input("中转区场桥数", 1, 3, config["rtg"]["num_rtgs"]["transit"], key="tra_rtgs")

    config["rtg"]["lift_time"] = lift_time
    config["rtg"]["lower_time"] = lower_time
    config["rtg"]["travel_time_per_bay"] = travel_time
    config["rtg"]["num_rtgs"] = {
        "import": imp_rtgs,
        "export": exp_rtgs,
        "transit": tra_rtgs,
    }

    st.sidebar.subheader("⏱️ 仿真参数")

    with st.sidebar.expander("仿真设置", expanded=True):
        sim_days = st.slider("仿真时长 (天)", 1, 30, 7, key="sim_days")
        seed = st.number_input("随机种子", 1, 9999, config["simulation"]["seed"], key="seed")
        ship_interval = st.number_input(
            "船舶平均到港间隔 (分钟)",
            60.0,
            4320.0,
            config["simulation"]["ship_interval_mean"],
            key="ship_interval",
        )
        ship_size = st.number_input(
            "每船平均箱量",
            20,
            500,
            config["simulation"]["ship_size_mean"],
            key="ship_size",
        )

    config["simulation"]["duration"] = sim_days * 1440
    config["simulation"]["seed"] = seed
    config["simulation"]["ship_interval_mean"] = ship_interval
    config["simulation"]["ship_size_mean"] = ship_size

    st.sidebar.subheader("🚛 集卡调度")

    with st.sidebar.expander("闸口与排队参数", expanded=False):
        num_gates = st.number_input("堆场入口闸口数量", 1, 10, config["truck_scheduling"]["num_gates"], key="num_gates")
        gate_process_time = st.number_input("每辆集卡过闸耗时(分钟)", 0.5, 30.0, config["truck_scheduling"]["gate_process_time"], 0.5, key="gate_process_time")
        max_queue_length = st.number_input("集卡最大排队长度", 5, 100, config["truck_scheduling"]["max_queue_length"], key="max_queue_length")
        early_arrival_tolerance = st.number_input("集卡提前到达容忍时间(分钟)", 0.0, 240.0, config["truck_scheduling"]["early_arrival_tolerance"], 5.0, key="early_arrival_tolerance")

    config["truck_scheduling"]["num_gates"] = num_gates
    config["truck_scheduling"]["gate_process_time"] = gate_process_time
    config["truck_scheduling"]["max_queue_length"] = max_queue_length
    config["truck_scheduling"]["early_arrival_tolerance"] = early_arrival_tolerance

    st.sidebar.subheader("🎯 堆垛策略")
    strategy = st.sidebar.selectbox(
        "选择策略",
        [
            ("随机堆放", StackingStrategy.RANDOM),
            ("分类堆放(按船名)", StackingStrategy.CLASSIFIED),
            ("按提箱时序优先", StackingStrategy.TIME_PRIORITY),
            ("重量分层", StackingStrategy.WEIGHT_LAYERED),
            ("综合优化", StackingStrategy.OPTIMIZED),
        ],
        format_func=lambda x: x[0],
        key="strategy_select",
    )

    if strategy[1] == StackingStrategy.OPTIMIZED:
        st.sidebar.subheader("⚖️ 综合优化参数")
        time_weight = st.sidebar.slider("时序权重", 0.1, 0.9, 0.5, 0.05, key="time_weight")
        weight_weight = 1.0 - time_weight
        st.sidebar.info(f"重量权重: {weight_weight:.2f}")
        strategy_params = {"time_weight": time_weight, "weight_weight": weight_weight}
    else:
        strategy_params = {"seed": seed}

    return config, strategy[1], strategy_params


def main():
    st.title("🚢 港口集装箱堆场调度仿真与吞吐量分析 Dashboard")
    st.markdown("---")

    config, strategy, strategy_params = sidebar_config()

    if "simulation_run" not in st.session_state:
        st.session_state.simulation_run = False
    if "engine" not in st.session_state:
        st.session_state.engine = None
    if "kpi" not in st.session_state:
        st.session_state.kpi = None
    if "comparison_results" not in st.session_state:
        st.session_state.comparison_results = None
    if "optimization_result" not in st.session_state:
        st.session_state.optimization_result = None
    if "bottleneck_events" not in st.session_state:
        st.session_state.bottleneck_events = None
    if "replay_current_frame" not in st.session_state:
        st.session_state.replay_current_frame = 0
    if "replay_playing" not in st.session_state:
        st.session_state.replay_playing = False
    if "replay_speed" not in st.session_state:
        st.session_state.replay_speed = 1
    if "replay_zone" not in st.session_state:
        st.session_state.replay_zone = ZoneType.IMPORT
    if "replay_jump_zone" not in st.session_state:
        st.session_state.replay_jump_zone = None
    if "replay_jump_frame" not in st.session_state:
        st.session_state.replay_jump_frame = None
    if "simulation_history" not in st.session_state:
        st.session_state.simulation_history = []
    if "custom_bottleneck_rules" not in st.session_state:
        st.session_state.custom_bottleneck_rules = []
    if "replay_compare_mode" not in st.session_state:
        st.session_state.replay_compare_mode = False
    if "replay_selected_history_id" not in st.session_state:
        st.session_state.replay_selected_history_id = None

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊 仿真运行", "🏗️ 堆场可视化", "📈 KPI分析", "🔄 策略对比", "🎛️ 参数优化",
        "🎬 热力回放与瓶颈诊断"
    ])

    with tab1:
        st.header("仿真运行")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.info(f"**当前策略**: {strategy.value}")
            total_slots = sum(
                config["yard"][z]["num_bays"] * config["yard"][z]["num_rows"] * config["yard"][z]["num_tiers"]
                for z in ["import", "export", "transit"]
            )
            st.metric("总格位数", total_slots)

        with col2:
            sim_duration_days = config["simulation"]["duration"] / 1440
            st.metric("仿真时长", f"{sim_duration_days:.0f} 天")
            total_rtgs = sum(config["rtg"]["num_rtgs"].values())
            st.metric("总场桥数", total_rtgs)

        with col3:
            num_gates = config["truck_scheduling"]["num_gates"]
            st.metric("闸口数量", num_gates)
            st.metric("过闸耗时", f"{config['truck_scheduling']['gate_process_time']:.1f} 分钟")

        st.markdown("---")

        col_run, col_reset = st.columns(2)
        with col_run:
            run_button = st.button("▶️ 开始仿真", type="primary", use_container_width=True)
        with col_reset:
            reset_button = st.button("🔄 重置", use_container_width=True)

        if reset_button:
            st.session_state.simulation_run = False
            st.session_state.engine = None
            st.session_state.kpi = None
            st.rerun()

        if run_button:
            with st.spinner("正在运行仿真..."):
                engine = SimulationEngine(config, strategy, strategy_params=strategy_params)
                replay_cfg = config.get("replay", {})
                engine.set_snapshot_interval(replay_cfg.get("sampling_interval", 10.0))
                engine.run(config["simulation"]["duration"])
                st.session_state.engine = engine

                calc = KPICalculator(config["simulation"]["duration"])
                kpi = calc.calculate(engine)
                st.session_state.kpi = kpi
                st.session_state.simulation_run = True

                snapshots = engine.snapshot_recorder.get_snapshots()
                custom_rules = st.session_state.get("custom_bottleneck_rules", []) or []
                diagnoser = BottleneckDiagnoser(snapshots, config, custom_rules=custom_rules)
                diagnoser.set_parameters(
                    congestion_threshold=replay_cfg.get("congestion_threshold", 0.80),
                    conflict_distance=replay_cfg.get("conflict_distance", 2),
                    gate_saturation_ratio=replay_cfg.get("gate_saturation_ratio", 0.80),
                    custom_rules=custom_rules,
                )
                events = diagnoser.diagnose()
                st.session_state.bottleneck_events = events
                st.session_state.replay_current_frame = 0
                st.session_state.replay_playing = False

                ts_now = _time.time()
                strategy_cn = {
                    StackingStrategy.RANDOM: "随机堆放",
                    StackingStrategy.CLASSIFIED: "分类堆放",
                    StackingStrategy.TIME_PRIORITY: "时序优先",
                    StackingStrategy.WEIGHT_LAYERED: "重量分层",
                    StackingStrategy.OPTIMIZED: "综合优化",
                }
                strat_label = strategy_cn.get(strategy, strategy.value)
                record_label = f"{_time.strftime('%m-%d %H:%M', _time.localtime(ts_now))} | {strat_label}"
                hist_record = HistoricalSimulationRecord(
                    record_id=f"sim_{ts_now}",
                    label=record_label,
                    timestamp=ts_now,
                    strategy_name=strat_label,
                    snapshots=list(snapshots),
                    bottleneck_events=list(events),
                    config_snapshot=dict(config),
                )
                st.session_state.simulation_history.append(hist_record)
                if len(st.session_state.simulation_history) > 5:
                    st.session_state.simulation_history = st.session_state.simulation_history[-5:]

                st.success("✅ 仿真完成！")

        if st.session_state.simulation_run and st.session_state.kpi:
            st.markdown("---")
            st.subheader("📊 核心KPI指标")

            kpi = st.session_state.kpi

            kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
            with kpi_col1:
                st.metric("翻箱率", f"{kpi.relocation_rate:.2%}")
                st.metric("平均利用率", f"{kpi.avg_utilization:.2%}")
            with kpi_col2:
                st.metric("平均提箱耗时", f"{kpi.avg_pickup_time:.1f} 分钟")
                st.metric("峰值利用率", f"{kpi.peak_utilization:.2%}")
            with kpi_col3:
                st.metric("日均吞吐量", f"{kpi.daily_throughput:.0f} TEU")
                st.metric("总翻箱次数", kpi.total_relocations)
            with kpi_col4:
                st.metric("集卡平均等待时间", f"{kpi.avg_truck_wait_time:.1f} 分钟")
                st.metric("集卡拒绝率", f"{kpi.truck_rejection_rate:.2%}")

    with tab2:
        st.header("堆场可视化")

        if not st.session_state.engine:
            st.info("请先运行仿真以查看堆场状态。")
        else:
            engine = st.session_state.engine
            yard = engine.yard

            zone_option = st.selectbox(
                "选择区域",
                [
                    ("进口区", ZoneType.IMPORT),
                    ("出口区", ZoneType.EXPORT),
                    ("中转区", ZoneType.TRANSIT),
                ],
                format_func=lambda x: x[0],
                key="viz_zone",
            )

            col_view1, col_view2 = st.columns(2)

            with col_view1:
                st.subheader("俯视图 (堆高层数)")
                fig_top = visualizer.plot_yard_top_view(yard, zone_option[1])
                st.plotly_chart(fig_top, use_container_width=True)

            with col_view2:
                st.subheader("3D视图")
                fig_3d = visualizer.plot_yard_3d_view(yard, zone_option[1])
                st.plotly_chart(fig_3d, use_container_width=True)

            st.subheader("场地利用率趋势")
            fig_util = visualizer.plot_utilization_trend(engine.stats)
            st.plotly_chart(fig_util, use_container_width=True)

            col_trend1, col_trend2 = st.columns(2)
            with col_trend1:
                st.subheader("累计吞吐量趋势")
                fig_tp = visualizer.plot_throughput_trend(engine.stats)
                st.plotly_chart(fig_tp, use_container_width=True)
            with col_trend2:
                st.subheader("在场箱数趋势")
                fig_cy = visualizer.plot_containers_in_yard_trend(engine.stats)
                st.plotly_chart(fig_cy, use_container_width=True)

    with tab3:
        st.header("KPI分析")

        if not st.session_state.kpi:
            st.info("请先运行仿真以查看KPI分析。")
        else:
            kpi = st.session_state.kpi
            engine = st.session_state.engine

            st.subheader("📋 详细KPI报表")

            kpi_data = {
                "指标": [
                    "翻箱率",
                    "平均场地利用率",
                    "峰值场地利用率",
                    "平均提箱耗时",
                    "平均场桥效率",
                    "日均吞吐量",
                    "总吞吐量",
                    "总翻箱次数",
                    "总提箱作业次数",
                    "总堆存作业次数",
                    "集卡平均等待时间",
                    "闸口平均利用率",
                    "集卡拒绝率",
                    "集卡总到达数",
                    "集卡总拒绝数",
                ],
                "数值": [
                    f"{kpi.relocation_rate:.2%}",
                    f"{kpi.avg_utilization:.2%}",
                    f"{kpi.peak_utilization:.2%}",
                    f"{kpi.avg_pickup_time:.1f} 分钟",
                    f"{kpi.avg_rtg_efficiency:.2f} 箱/小时",
                    f"{kpi.daily_throughput:.0f} TEU",
                    f"{kpi.total_throughput} TEU",
                    str(kpi.total_relocations),
                    str(kpi.total_pickup_operations),
                    str(kpi.total_containers_stowed),
                    f"{kpi.avg_truck_wait_time:.1f} 分钟",
                    f"{kpi.avg_gate_utilization:.2%}",
                    f"{kpi.truck_rejection_rate:.2%}",
                    str(kpi.total_truck_arrivals),
                    str(kpi.total_truck_rejections),
                ],
            }
            st.table(kpi_data)

            st.markdown("---")
            col_gate1, col_gate2 = st.columns(2)
            with col_gate1:
                st.subheader("🚛 各闸口利用率")
                gate_data = {"闸口ID": [], "利用率": []}
                for gate_id, util in kpi.gate_utilization.items():
                    gate_data["闸口ID"].append(gate_id.replace("gate_", "闸口 "))
                    gate_data["利用率"].append(f"{util:.2%}")
                st.table(gate_data)

            with col_gate2:
                st.subheader("🏗️ 各场桥效率")
                rtg_data = {"场桥ID": [], "效率(箱/小时)": [], "完成作业数": []}
                for zone, rtgs in engine.rtgs.items():
                    for rtg in rtgs:
                        rtg_data["场桥ID"].append(rtg.rtg_id)
                        efficiency = kpi.rtg_efficiency.get(rtg.rtg_id, 0)
                        rtg_data["效率(箱/小时)"].append(f"{efficiency:.2f}")
                        rtg_data["完成作业数"].append(rtg.total_tasks_completed)
                st.table(rtg_data)

            st.markdown("---")
            st.subheader("📊 闸口排队长度随时间变化")
            fig_queue = visualizer.plot_gate_queue_trend(engine.stats)
            st.plotly_chart(fig_queue, use_container_width=True)

            st.markdown("---")
            st.subheader("📊 场桥作业甘特图")
            fig_gantt = visualizer.plot_rtg_gantt(engine)
            st.plotly_chart(fig_gantt, use_container_width=True)

    with tab4:
        st.header("策略对比")
        st.markdown("对所有五种堆垛策略运行仿真并对比KPI指标。")

        compare_button = st.button("🔄 运行全部策略对比", type="primary")

        if compare_button:
            with st.spinner("正在运行所有策略对比仿真，这可能需要一些时间..."):
                optimizer = ParameterOptimizer(config)
                strategies = [
                    StackingStrategy.RANDOM,
                    StackingStrategy.CLASSIFIED,
                    StackingStrategy.TIME_PRIORITY,
                    StackingStrategy.WEIGHT_LAYERED,
                    StackingStrategy.OPTIMIZED,
                ]

                progress_bar = st.progress(0)
                status_text = st.empty()

                def progress_cb(pct, strat=""):
                    progress_bar.progress(pct)
                    if strat:
                        status_text.text(f"正在运行: {strat} ({pct:.0%})")

                results = optimizer.run_strategy_comparison(
                    strategies, progress_callback=progress_cb
                )
                st.session_state.comparison_results = results
                progress_bar.progress(1.0)
                status_text.text("✅ 全部策略仿真完成!")

        if st.session_state.comparison_results:
            results = st.session_state.comparison_results

            st.markdown("---")
            st.subheader("📊 策略对比柱状图")

            fig_comp = visualizer.plot_kpi_comparison(results)
            st.plotly_chart(fig_comp, use_container_width=True)

            st.markdown("---")
            st.subheader("📋 对比数据表格")

            strategy_names = {
                "random": "随机堆放",
                "classified": "分类堆放",
                "time_priority": "时序优先",
                "weight_layered": "重量分层",
                "optimized": "综合优化",
            }

            table_data = {
                "策略": [],
                "翻箱率": [],
                "平均利用率": [],
                "平均提箱耗时(min)": [],
                "日均吞吐量(TEU)": [],
                "总翻箱次数": [],
                "集卡平均等待(min)": [],
                "闸口利用率": [],
                "集卡拒绝率": [],
            }

            for strat_key, kpi in results.items():
                table_data["策略"].append(strategy_names.get(strat_key, strat_key))
                table_data["翻箱率"].append(f"{kpi.relocation_rate:.2%}")
                table_data["平均利用率"].append(f"{kpi.avg_utilization:.2%}")
                table_data["平均提箱耗时(min)"].append(f"{kpi.avg_pickup_time:.1f}")
                table_data["日均吞吐量(TEU)"].append(f"{kpi.daily_throughput:.0f}")
                table_data["总翻箱次数"].append(kpi.total_relocations)
                table_data["集卡平均等待(min)"].append(f"{kpi.avg_truck_wait_time:.1f}")
                table_data["闸口利用率"].append(f"{kpi.avg_gate_utilization:.2%}")
                table_data["集卡拒绝率"].append(f"{kpi.truck_rejection_rate:.2%}")

            st.table(table_data)

    with tab5:
        st.header("参数优化")
        st.markdown("调节综合优化策略的参数，找到最优配置。")

        num_points = st.slider("参数扫描点数", 5, 20, 10, key="opt_points")
        objective = st.selectbox(
            "优化目标",
            [
                ("最小翻箱率", "min_relocation"),
                ("最大吞吐量", "max_throughput"),
                ("最小提箱耗时", "min_pickup_time"),
                ("最大利用率", "max_utilization"),
            ],
            format_func=lambda x: x[0],
            key="opt_objective",
        )

        opt_button = st.button("🎯 运行参数优化", type="primary")

        if opt_button:
            with st.spinner("正在运行参数优化仿真..."):
                optimizer = ParameterOptimizer(config)

                progress_bar = st.progress(0)

                def progress_cb(pct):
                    progress_bar.progress(pct)

                result = optimizer.optimize_time_weight(
                    num_points=num_points,
                    objective=objective[1],
                    progress_callback=progress_cb,
                )
                st.session_state.optimization_result = result
                progress_bar.progress(1.0)

                st.success(f"✅ 优化完成！最优时序权重: {result.best_value:.2f}")

        if st.session_state.optimization_result:
            result = st.session_state.optimization_result

            st.markdown("---")
            st.subheader("📈 参数优化曲线")

            fig_opt = visualizer.plot_param_optimization(
                result.param_values,
                result.kpi_results,
                param_name="time_weight (时序权重)",
            )
            st.plotly_chart(fig_opt, use_container_width=True)

            st.markdown("---")
            st.subheader("🏆 最优配置")

            best_kpi = result.kpi_results[result.best_index]
            best_col1, best_col2, best_col3 = st.columns(3)
            with best_col1:
                st.metric("最优时序权重", f"{result.best_value:.2f}")
            with best_col2:
                st.metric("翻箱率", f"{best_kpi.relocation_rate:.2%}")
            with best_col3:
                st.metric("日均吞吐量", f"{best_kpi.daily_throughput:.0f} TEU")

    with tab6:
        st.header("🎬 堆场热力动态回放与瓶颈诊断")

        default_replay = get_default_config()["replay"]

        with st.expander("⚙️ 回放与诊断配置 (点击展开/收起)", expanded=False):
            cfg_col1, cfg_col2 = st.columns(2)
            with cfg_col1:
                sampling_interval = st.slider(
                    "采样间隔 (分钟)",
                    min_value=1,
                    max_value=60,
                    value=int(default_replay["sampling_interval"]),
                    step=1,
                    help="仿真过程中每隔多少分钟录制一次堆场快照",
                    key="replay_sampling_interval",
                )
                congestion_threshold = st.slider(
                    "拥堵阈值 (%)",
                    min_value=50,
                    max_value=95,
                    value=int(default_replay["congestion_threshold"] * 100),
                    step=1,
                    help="分区利用率超过此阈值且持续3个以上快照，标记为拥堵",
                    key="replay_congestion_threshold",
                )
            with cfg_col2:
                conflict_distance = st.slider(
                    "场桥冲突距离阈值 (贝位)",
                    min_value=1,
                    max_value=5,
                    value=default_replay["conflict_distance"],
                    step=1,
                    help="两台场桥贝位间距 ≤ 此值且至少一台等待，标记为冲突",
                    key="replay_conflict_distance",
                )
                gate_saturation_ratio = st.slider(
                    "闸口饱和预警比例 (%)",
                    min_value=50,
                    max_value=95,
                    value=int(default_replay["gate_saturation_ratio"] * 100),
                    step=1,
                    help="排队长度超过最大排队长度的此比例，标记为饱和预警",
                    key="replay_saturation_ratio",
                )

            st.markdown("#### 🎨 自定义瓶颈规则 (最多5条)")
            custom_rules = st.session_state.get("custom_bottleneck_rules", []) or []
            remaining = 5 - len(custom_rules)

            rule_cols = st.columns([2, 2, 2, 2, 1])
            with rule_cols[0]:
                new_rule_name = st.text_input("规则名称", placeholder="例: 高峰拥堵预警", key="new_rule_name")
            with rule_cols[1]:
                condition_options = [
                    ("分区利用率持续超阈值", CustomRuleConditionType.ZONE_UTILIZATION),
                    ("场桥等待次数超阈值", CustomRuleConditionType.RTG_WAIT_COUNT),
                    ("闸口排队波动超阈值", CustomRuleConditionType.GATE_QUEUE_FLUCTUATION),
                ]
                new_condition = st.selectbox(
                    "触发条件",
                    condition_options,
                    format_func=lambda x: x[0],
                    key="new_condition_type",
                )
            with rule_cols[2]:
                new_zone_sel = st.selectbox(
                    "适用分区",
                    [("进口区", ZoneType.IMPORT), ("出口区", ZoneType.EXPORT), ("中转区", ZoneType.TRANSIT), ("不适用", None)],
                    format_func=lambda x: x[0],
                    key="new_rule_zone",
                )
            with rule_cols[3]:
                severity_options = [("高", SeverityLevel.HIGH), ("中", SeverityLevel.MEDIUM), ("低", SeverityLevel.LOW)]
                new_severity = st.selectbox(
                    "严重等级",
                    severity_options,
                    format_func=lambda x: x[0],
                    key="new_rule_severity",
                )
            with rule_cols[4]:
                add_rule = st.button("➕ 添加", disabled=(remaining <= 0), use_container_width=True, key="add_custom_rule_btn")

            param_cols = st.columns(4)
            with param_cols[0]:
                consec_n = st.number_input("连续快照数 N", min_value=2, max_value=50, value=3, key="rule_param_consec")
            with param_cols[1]:
                util_pct = st.slider("利用率阈值 Y (%)", min_value=50, max_value=100, value=85, key="rule_param_util")
            with param_cols[2]:
                wait_k = st.number_input("等待次数阈值 K", min_value=1, max_value=50, value=5, key="rule_param_wait")
            with param_cols[3]:
                fluc_w = st.number_input("波动幅度阈值 W (辆)", min_value=1, max_value=100, value=3, key="rule_param_fluc")

            rtg_id_input = st.text_input("指定场桥ID (仅等待次数规则，留空=不限制)", key="rule_param_rtg_id")

            if add_rule and new_rule_name:
                import uuid as _uuid
                new_rule = CustomRule(
                    rule_id=f"rule_{_uuid.uuid4().hex[:8]}",
                    rule_name=new_rule_name.strip(),
                    condition_type=new_condition[1],
                    zone=new_zone_sel[1],
                    rtg_id=rtg_id_input.strip() if rtg_id_input.strip() else None,
                    consecutive_snapshots=int(consec_n),
                    threshold_pct=util_pct / 100.0,
                    wait_count_threshold=int(wait_k),
                    fluctuation_threshold=int(fluc_w),
                    severity=new_severity[1],
                    description="",
                )
                custom_rules.append(new_rule)
                st.session_state.custom_bottleneck_rules = custom_rules
                st.success(f"✅ 已添加规则: {new_rule_name}（当前{len(custom_rules)}/5）")
                st.rerun()

            if custom_rules:
                st.markdown("**已定义规则:**")
                for ri, rule in enumerate(custom_rules):
                    cond_cn = {
                        CustomRuleConditionType.ZONE_UTILIZATION: f"{rule.zone.value if rule.zone else '指定'}区 连续{rule.consecutive_snapshots}快照 利用率≥{rule.threshold_pct:.0%}",
                        CustomRuleConditionType.RTG_WAIT_COUNT: f"场桥{rule.rtg_id or '指定'} 在{rule.consecutive_snapshots}快照内 等待≥{rule.wait_count_threshold}次",
                        CustomRuleConditionType.GATE_QUEUE_FLUCTUATION: f"闸口排队 {rule.consecutive_snapshots}快照内 波动≥{rule.fluctuation_threshold}辆",
                    }
                    sev_cn = {SeverityLevel.HIGH: "高", SeverityLevel.MEDIUM: "中", SeverityLevel.LOW: "低"}
                    sev_color = {SeverityLevel.HIGH: "🔴", SeverityLevel.MEDIUM: "🟡", SeverityLevel.LOW: "🟢"}
                    del_btn = st.button(
                        f"🗑️ 删除 #{ri+1}",
                        key=f"del_rule_{ri}",
                        help=f"删除规则: {rule.rule_name}",
                    )
                    if del_btn:
                        custom_rules.pop(ri)
                        st.session_state.custom_bottleneck_rules = custom_rules
                        st.rerun()
                    st.markdown(
                        f"{sev_color.get(rule.severity, '⚪')} **{rule.rule_name}** "
                        f"[{sev_cn.get(rule.severity, rule.severity.value)}级] "
                        f"→ {cond_cn.get(rule.condition_type, rule.condition_type.value)}"
                    )
                st.caption("💡 自定义规则检测到的事件将在时间轴和动画中以 🟣 紫色标记显示。")
            else:
                st.info("ℹ️ 暂无自定义规则，可通过上方表单添加（最多5条）。")

            apply_cfg = st.button("🔄 应用新配置并重新诊断", use_container_width=True)
            if apply_cfg and st.session_state.engine:
                with st.spinner("正在重新运行瓶颈诊断..."):
                    engine = st.session_state.engine
                    snapshots = engine.snapshot_recorder.get_snapshots()
                    cur_custom_rules = st.session_state.get("custom_bottleneck_rules", []) or []
                    diagnoser = BottleneckDiagnoser(snapshots, config, custom_rules=cur_custom_rules)
                    diagnoser.set_parameters(
                        congestion_threshold=congestion_threshold / 100.0,
                        conflict_distance=conflict_distance,
                        gate_saturation_ratio=gate_saturation_ratio / 100.0,
                        custom_rules=cur_custom_rules,
                    )
                    events = diagnoser.diagnose()
                    st.session_state.bottleneck_events = events
                    if st.session_state.simulation_history:
                        last_rec = st.session_state.simulation_history[-1]
                        last_rec.bottleneck_events = list(events)
                    st.success("✅ 重新诊断完成！")

        if not st.session_state.engine or not st.session_state.simulation_run:
            st.info("ℹ️ 请先在「📊 仿真运行」标签页中运行仿真，然后才能使用回放与诊断功能。")
        else:
            engine = st.session_state.engine
            snapshots = engine.snapshot_recorder.get_snapshots()
            events = st.session_state.bottleneck_events or []
            replay_vis = ReplayVisualizer()

            st.subheader("📊 诊断概览")
            ev_col1, ev_col2, ev_col3, ev_col4, ev_col5 = st.columns(5)
            with ev_col1:
                num_cong = len([e for e in events if e.event_type == BottleneckType.CONGESTION])
                st.metric("🔴 拥堵事件", num_cong)
            with ev_col2:
                num_conf = len([e for e in events if e.event_type == BottleneckType.RTG_CONFLICT])
                st.metric("🟠 场桥冲突", num_conf)
            with ev_col3:
                num_sat = len([e for e in events if e.event_type == BottleneckType.GATE_SATURATION])
                st.metric("🟡 闸口饱和预警", num_sat)
            with ev_col4:
                num_custom = len([e for e in events if e.event_type == BottleneckType.CUSTOM_RULE])
                st.metric("🟣 自定义规则", num_custom)
            with ev_col5:
                st.metric("📸 快照总数", len(snapshots))

            zone_options = [
                ("进口区", ZoneType.IMPORT),
                ("出口区", ZoneType.EXPORT),
                ("中转区", ZoneType.TRANSIT),
            ]
            top_zone = st.selectbox(
                "📍 当前查看分区 (影响下方热力图与动画)",
                zone_options,
                format_func=lambda x: x[0],
                key="replay_zone_top_select",
                index=0,
            )
            current_zone = top_zone[1]

            st.markdown("---")

            st.subheader("📈 利用率与排队趋势")
            trend1, trend2 = st.columns(2)
            with trend1:
                fig_util = replay_vis.create_zone_utilization_trend(snapshots)
                st.plotly_chart(fig_util, use_container_width=True)
            with trend2:
                fig_queue = replay_vis.create_gate_queue_trend(snapshots)
                st.plotly_chart(fig_queue, use_container_width=True)

            st.markdown("---")

            st.subheader("⏱️ 瓶颈事件时间轴")
            total_duration = config["simulation"]["duration"]
            fig_timeline = replay_vis.create_bottleneck_timeline(events, total_duration)
            st.plotly_chart(fig_timeline, use_container_width=True)

            if events:
                st.caption("💡 点击下方按钮可跳转动画到对应事件起始时刻。")
                event_rows = []
                for idx, e in enumerate(events):
                    icon_map = {
                        BottleneckType.CONGESTION: "🔴",
                        BottleneckType.RTG_CONFLICT: "🟠",
                        BottleneckType.GATE_SATURATION: "🟡",
                        BottleneckType.CUSTOM_RULE: "🟣",
                    }
                    type_map = {
                        BottleneckType.CONGESTION: "拥堵",
                        BottleneckType.RTG_CONFLICT: "冲突",
                        BottleneckType.GATE_SATURATION: "饱和预警",
                        BottleneckType.CUSTOM_RULE: "自定义规则",
                    }
                    type_label = type_map[e.event_type]
                    if e.event_type == BottleneckType.CUSTOM_RULE and e.custom_rule_name:
                        type_label = f"规则:{e.custom_rule_name}"
                    sev_str = ""
                    if e.severity:
                        sev_map = {SeverityLevel.HIGH: "[高]", SeverityLevel.MEDIUM: "[中]", SeverityLevel.LOW: "[低]"}
                        sev_str = sev_map.get(e.severity, "")
                    event_rows.append({
                        "序号": idx + 1,
                        "类型": f"{icon_map[e.event_type]} {type_label}{sev_str}",
                        "起始时间": f"{_format_time(e.start_time)}",
                        "结束时间": f"{_format_time(e.end_time)}",
                        "持续时长": f"{_format_time(e.duration)}",
                        "涉及对象": (
                            ({"import": "进口区", "export": "出口区", "transit": "中转区"}.get(e.zone.value, e.zone.value) if e.zone else "") +
                            (" | " + "、".join(e.rtg_ids) if e.rtg_ids else "") +
                            (" | " + e.gate_id.replace("gate_", "闸口") if e.gate_id else "")
                        ) or "-",
                        "峰值指标": (
                            f"{e.peak_metric:.1%}" if e.event_type == BottleneckType.CONGESTION
                            else f"{e.peak_metric:.0f}贝位" if e.event_type == BottleneckType.RTG_CONFLICT
                            else f"{e.peak_metric:.0f}辆"
                        ),
                    })
                st.table(event_rows)

                max_jump_buttons = min(8, len(events))
                jump_cols = st.columns(max_jump_buttons)
                for i, col in enumerate(jump_cols):
                    if i < len(events):
                        with col:
                            icon_map = {
                                BottleneckType.CONGESTION: "🔴",
                                BottleneckType.RTG_CONFLICT: "🟠",
                                BottleneckType.GATE_SATURATION: "🟡",
                                BottleneckType.CUSTOM_RULE: "🟣",
                            }
                            jump_btn = st.button(
                                f"{icon_map.get(events[i].event_type, '⚪')} 跳至事件{i+1}",
                                key=f"jump_event_{i}",
                                use_container_width=True,
                                help=f"起始: {_format_time(events[i].start_time)} | {events[i].description}",
                            )
                            if jump_btn:
                                target_time = events[i].start_time
                                closest_idx = find_nearest_snapshot(snapshots, target_time)
                                st.session_state.replay_jump_frame = closest_idx
                                st.session_state.replay_jump_zone = events[i].zone if events[i].zone else current_zone
                                st.rerun()

            st.markdown("---")

            history_list = st.session_state.get("simulation_history", []) or []
            compare_mode = st.toggle(
                "🔀 对比回放模式（左右双栏同步回放）",
                value=st.session_state.get("replay_compare_mode", False),
                key="replay_compare_toggle",
                help="打开后左栏显示当前仿真，右栏选择一条历史记录进行同步对比",
            )
            st.session_state.replay_compare_mode = compare_mode

            selected_hist_record = None
            if compare_mode:
                hist_for_compare = history_list[:-1] if len(history_list) > 1 else []
                if not hist_for_compare:
                    st.warning("⚠️ 暂无足够的历史记录用于对比。请先运行至少 2 次不同的仿真后再使用对比回放功能。")
                    st.info("💡 提示：每次仿真完成后会自动保存到历史记录中，最多保留 5 条。")
                    compare_mode = False
                else:
                    st.info(f"📚 可用历史对比记录（共 {len(hist_for_compare)} 条）：")
                    selected_idx = st.selectbox(
                        "选择要对比的历史仿真记录:",
                        options=range(len(hist_for_compare)),
                        format_func=lambda i: f"[{i+1}] {hist_for_compare[i].label}",
                        key="replay_hist_select",
                    )
                    selected_hist_record = hist_for_compare[selected_idx]
                    st.caption(f"📍 已选择: {selected_hist_record.label} | 策略: {selected_hist_record.strategy_name} | 快照数: {len(selected_hist_record.snapshots)}")
                st.markdown("---")

            st.subheader("🎞️ Plotly 动画热力回放器" + (" (双栏对比同步模式)" if compare_mode else ""))

            speed_col1, speed_col2, speed_col3 = st.columns([1, 2, 1])
            with speed_col2:
                speed_options = [1, 2, 5, 10]
                selected_speed = st.select_slider(
                    "🎚️ 播放速度 (1x=正常, 10x=最快)",
                    options=speed_options,
                    value=st.session_state.replay_speed,
                    format_func=lambda x: f"{x}x",
                    key="replay_speed_select",
                )
                st.session_state.replay_speed = selected_speed

            start_frame_idx = 0
            if st.session_state.replay_jump_frame is not None:
                start_frame_idx = max(0, min(st.session_state.replay_jump_frame, len(snapshots) - 1))
                anim_zone = st.session_state.replay_jump_zone if st.session_state.replay_jump_zone else current_zone
                st.session_state.replay_jump_frame = None
                st.session_state.replay_jump_zone = None
            else:
                anim_zone = current_zone

            if "replay_frame_idx" not in st.session_state:
                st.session_state.replay_frame_idx = start_frame_idx
            if "replay_is_playing" not in st.session_state:
                st.session_state.replay_is_playing = False

            cur_frame = st.session_state.replay_frame_idx
            max_frame_cur = max(0, len(snapshots) - 1)
            if selected_hist_record and selected_hist_record.snapshots:
                max_frame_right = max(0, len(selected_hist_record.snapshots) - 1)
            else:
                max_frame_right = 0

            ctl_cols = st.columns([1, 1, 1, 1, 1, 1, 2])
            with ctl_cols[0]:
                if st.button("⏮ 第一帧", use_container_width=True, key="ctl_first"):
                    st.session_state.replay_frame_idx = 0
                    st.session_state.replay_is_playing = False
                    st.rerun()
            with ctl_cols[1]:
                if st.button("◀ 上一帧", use_container_width=True, key="ctl_prev"):
                    st.session_state.replay_frame_idx = max(0, cur_frame - 1)
                    st.session_state.replay_is_playing = False
                    st.rerun()
            with ctl_cols[2]:
                play_btn_label = "⏸ 暂停" if st.session_state.replay_is_playing else "▶ 播放"
                if st.button(play_btn_label, type="primary", use_container_width=True, key="ctl_play"):
                    st.session_state.replay_is_playing = not st.session_state.replay_is_playing
                    st.rerun()
            with ctl_cols[3]:
                if st.button("▶ 下一帧", use_container_width=True, key="ctl_next"):
                    st.session_state.replay_frame_idx = min(max_frame_cur, cur_frame + 1)
                    st.session_state.replay_is_playing = False
                    st.rerun()
            with ctl_cols[4]:
                if st.button("⏭ 最后帧", use_container_width=True, key="ctl_last"):
                    st.session_state.replay_frame_idx = max_frame_cur
                    st.session_state.replay_is_playing = False
                    st.rerun()
            with ctl_cols[5]:
                if st.button("⏹ 停止", use_container_width=True, key="ctl_stop"):
                    st.session_state.replay_frame_idx = 0
                    st.session_state.replay_is_playing = False
                    st.rerun()
            with ctl_cols[6]:
                slider_label = "⏱️ 时间轴 (帧索引)"
                if compare_mode:
                    slider_label = "⏱️ 同步时间轴 (以左栏为准，右栏自动匹配最近时刻)"
                slider_frame = st.slider(
                    slider_label,
                    min_value=0,
                    max_value=max_frame_cur,
                    value=min(cur_frame, max_frame_cur),
                    key="shared_frame_slider",
                    help="拖动时间轴，双栏同步跳转到对应时刻；对比模式下右栏自动匹配最近邻时刻的快照",
                )
                if slider_frame != cur_frame:
                    st.session_state.replay_frame_idx = slider_frame
                    st.rerun()

            left_idx = min(cur_frame, max_frame_cur)
            cur_snap_time = snapshots[left_idx].timestamp if snapshots else 0.0
            if selected_hist_record and selected_hist_record.snapshots:
                right_frame = find_nearest_snapshot(selected_hist_record.snapshots, cur_snap_time)
            else:
                right_frame = 0

            def _auto_play():
                if st.session_state.replay_is_playing:
                    nxt = min(max_frame_cur, st.session_state.replay_frame_idx + 1)
                    if nxt == st.session_state.replay_frame_idx:
                        st.session_state.replay_is_playing = False
                    else:
                        st.session_state.replay_frame_idx = nxt
                        _time.sleep(0.5 / max(1, selected_speed))
                        st.rerun()

            zone_cn = {"import": "进口区", "export": "出口区", "transit": "中转区"}.get(anim_zone.value, anim_zone.value)

            if compare_mode and selected_hist_record:
                replay_vis = ReplayVisualizer()
                col_left, col_right = st.columns(2)
                left_snap = snapshots[left_idx] if snapshots else None
                right_snaps = selected_hist_record.snapshots
                right_idx = find_nearest_snapshot(right_snaps, left_snap.timestamp) if left_snap else 0
                right_snap = right_snaps[right_idx] if right_snaps else None

                with col_left:
                    st.markdown(f"### 🟦 当前仿真")
                    st.caption(f"策略: {history_list[-1].strategy_name if history_list else '-'} | 帧: {left_idx}/{max_frame_cur}")
                    if left_snap:
                        fig_left = replay_vis.create_static_heatmap_frame(left_snap, anim_zone)
                        util_left = left_snap.zone_utilization.get(anim_zone, 0.0)
                        active_evs_left = [e for e in events if e.start_time <= left_snap.timestamp <= e.end_time]
                        badge_left = " | ".join([
                            {BottleneckType.CONGESTION: "🔴", BottleneckType.RTG_CONFLICT: "🟠",
                             BottleneckType.GATE_SATURATION: "🟡", BottleneckType.CUSTOM_RULE: "🟣"}.get(e.event_type, "⚪") +
                            (e.custom_rule_name or {BottleneckType.CONGESTION: "拥堵", BottleneckType.RTG_CONFLICT: "冲突",
                             BottleneckType.GATE_SATURATION: "饱和", BottleneckType.CUSTOM_RULE: "规则"}.get(e.event_type, ""))
                            for e in active_evs_left
                        ]) or "正常"
                        st.info(f"🕒 时刻: {_format_time(left_snap.timestamp)} | 利用率: {util_left:.1%} | 闸口排队: {left_snap.gate_queue_total}辆 | 状态: {badge_left}")
                        st.plotly_chart(fig_left, use_container_width=True)
                        st.caption(f"💡 场桥数: {len(left_snap.rtg_snapshots.get(anim_zone, []))}台")

                with col_right:
                    st.markdown(f"### 🟪 历史对比")
                    st.caption(f"策略: {selected_hist_record.strategy_name} | 帧: {right_idx}/{max(0, len(right_snaps)-1)} (最近邻匹配)")
                    if right_snap:
                        fig_right = replay_vis.create_static_heatmap_frame(right_snap, anim_zone)
                        util_right = right_snap.zone_utilization.get(anim_zone, 0.0)
                        hist_events = selected_hist_record.bottleneck_events
                        active_evs_right = [e for e in hist_events if e.start_time <= right_snap.timestamp <= e.end_time]
                        badge_right = " | ".join([
                            {BottleneckType.CONGESTION: "🔴", BottleneckType.RTG_CONFLICT: "🟠",
                             BottleneckType.GATE_SATURATION: "🟡", BottleneckType.CUSTOM_RULE: "🟣"}.get(e.event_type, "⚪") +
                            (e.custom_rule_name or {BottleneckType.CONGESTION: "拥堵", BottleneckType.RTG_CONFLICT: "冲突",
                             BottleneckType.GATE_SATURATION: "饱和", BottleneckType.CUSTOM_RULE: "规则"}.get(e.event_type, ""))
                            for e in active_evs_right
                        ]) or "正常"
                        delta_util = util_right - util_left
                        delta_q = right_snap.gate_queue_total - left_snap.gate_queue_total
                        st.info(f"🕒 时刻: {_format_time(right_snap.timestamp)} | 利用率: {util_right:.1%} ({'↑' if delta_util>0 else '↓' if delta_util<0 else '→'}{abs(delta_util):.1%}) | 闸口排队: {right_snap.gate_queue_total}辆 ({'↑' if delta_q>0 else '↓' if delta_q<0 else '→'}{abs(delta_q)}) | 状态: {badge_right}")
                        st.plotly_chart(fig_right, use_container_width=True)
                        st.caption(f"💡 场桥数: {len(right_snap.rtg_snapshots.get(anim_zone, []))}台  |  时间差: {abs(right_snap.timestamp - left_snap.timestamp):.0f} 分钟")

                st.markdown("---")
                st.subheader("📊 双栏利用率与排队对比")
                comp_c1, comp_c2 = st.columns(2)
                with comp_c1:
                    fig_comp_util = go.Figure()
                    t_left = [s.timestamp for s in snapshots]
                    u_left = [s.zone_utilization.get(anim_zone, 0.0) * 100 for s in snapshots]
                    fig_comp_util.add_trace(go.Scatter(x=t_left, y=u_left, mode="lines", name="当前仿真", line=dict(color="#3498db", width=2)))
                    if right_snaps:
                        t_right = [s.timestamp for s in right_snaps]
                        u_right = [s.zone_utilization.get(anim_zone, 0.0) * 100 for s in right_snaps]
                        fig_comp_util.add_trace(go.Scatter(x=t_right, y=u_right, mode="lines", name=f"历史:{selected_hist_record.strategy_name}", line=dict(color="#8e44ad", width=2, dash="dash")))
                    fig_comp_util.add_vline(x=left_snap.timestamp, line_width=2, line_dash="dot", line_color="red", annotation_text="当前时刻")
                    fig_comp_util.update_layout(title=f"{zone_cn} 利用率对比", xaxis_title="时间(分钟)", yaxis_title="利用率(%)", yaxis=dict(range=[0, 100]), height=320, hovermode="x unified")
                    st.plotly_chart(fig_comp_util, use_container_width=True)
                with comp_c2:
                    fig_comp_q = go.Figure()
                    q_left = [s.gate_queue_total for s in snapshots]
                    fig_comp_q.add_trace(go.Scatter(x=t_left, y=q_left, mode="lines", name="当前仿真", line=dict(color="#e74c3c", width=2), fill="tozeroy", opacity=0.2))
                    if right_snaps:
                        q_right = [s.gate_queue_total for s in right_snaps]
                        t_right2 = [s.timestamp for s in right_snaps]
                        fig_comp_q.add_trace(go.Scatter(x=t_right2, y=q_right, mode="lines", name=f"历史:{selected_hist_record.strategy_name}", line=dict(color="#9b59b6", width=2, dash="dash"), fill="tozeroy", opacity=0.15))
                    fig_comp_q.add_vline(x=left_snap.timestamp, line_width=2, line_dash="dot", line_color="red", annotation_text="当前时刻")
                    fig_comp_q.update_layout(title="闸口排队长度对比", xaxis_title="时间(分钟)", yaxis_title="排队车辆数", height=320, hovermode="x unified")
                    st.plotly_chart(fig_comp_q, use_container_width=True)

                _auto_play()

            else:
                if not snapshots:
                    st.warning("⚠️ 没有可用的快照数据，请确认采样间隔设置。")
                else:
                    with st.spinner("🎬 正在生成 Plotly 动画帧（帧数较多时请稍候）..."):
                        total_duration = config["simulation"]["duration"]
                        fig_anim = replay_vis.create_heatmap_animation(
                            snapshots=snapshots,
                            zone=anim_zone,
                            bottleneck_events=events,
                            playback_speed=selected_speed,
                            total_duration=total_duration,
                            start_frame=max(0, min(st.session_state.replay_frame_idx, max_frame_cur)),
                        )
                    st.info(f"📍 **当前回放分区**: {zone_cn}  |  **当前帧**: 第{st.session_state.replay_frame_idx}帧 ({_format_time(snapshots[min(st.session_state.replay_frame_idx, max_frame_cur)].timestamp)})")
                    st.plotly_chart(fig_anim, use_container_width=True)
                    _auto_play()

                st.success(
                    "💡 **动画使用说明**:\n"
                    "1. 点击上方按钮组中的 **▶ 播放** 开始连续播放，**⏸ 暂停** 停止；\n"
                    "2. **⏮ 第一帧 / ◀ 上一帧 / ▶ 下一帧 / ⏭ 最后一帧** 可逐帧控制；\n"
                    "3. 拖动 **共享时间轴** 可跳转到任意时刻（对比模式下两栏同步跳转）；\n"
                    "4. 双栏对比模式下，右栏自动取与左栏时刻最近的快照进行显示；\n"
                    "5. 场桥符号：⚪ 空闲 / 💎 作业中 / ❌ 等待让路；\n"
                    "6. 事件标记: 🔴=拥堵  🟠=场桥冲突  🟡=闸口饱和预警  🟣=自定义规则  ⬜=正常"
                )

            detail_col1, detail_col2 = st.columns(2)
            with detail_col1:
                with st.expander(f"📋 {zone_cn} 场桥状态总览", expanded=False):
                    zone_rtgs_all = []
                    for snap in snapshots:
                        rtgs = snap.rtg_snapshots.get(anim_zone, [])
                        for r in rtgs:
                            if r.rtg_id not in [x["rtg_id"] for x in zone_rtgs_all]:
                                zone_rtgs_all.append({
                                    "rtg_id": r.rtg_id,
                                    "statuses": set(),
                                })
                    if zone_rtgs_all:
                        summary_rows = []
                        for r_info in zone_rtgs_all:
                            rid = r_info["rtg_id"]
                            statuses = set()
                            for snap in snapshots:
                                for r in snap.rtg_snapshots.get(anim_zone, []):
                                    if r.rtg_id == rid:
                                        statuses.add(r.status.value)
                            status_cn_map = {"idle": "空闲", "working": "作业中", "waiting": "等待让路"}
                            status_str = "、".join(status_cn_map[s] for s in statuses)
                            summary_rows.append({
                                "场桥ID": rid,
                                "出现过的状态": status_str,
                            })
                        st.table(summary_rows)
                    else:
                        st.info("该区暂无场桥。")

            with detail_col2:
                with st.expander("🚦 各闸口排队详情（首末帧对比）", expanded=False):
                    if snapshots:
                        first = snapshots[0]
                        last = snapshots[-1]
                        gate_rows = []
                        for gid in first.gate_queue_per_gate.keys():
                            q0 = first.gate_queue_per_gate.get(gid, 0)
                            q1 = last.gate_queue_per_gate.get(gid, 0)
                            gate_rows.append({
                                "闸口": gid.replace("gate_", "闸口 "),
                                "首帧排队": q0,
                                "末帧排队": q1,
                                "变化": f"{'↑' if q1 > q0 else '↓' if q1 < q0 else '→'} {abs(q1 - q0)}",
                            })
                        st.table(gate_rows)

            st.markdown("---")

            st.subheader("📋 诊断报告与优化建议")
            report_generator = DiagnosisReportGenerator(events, snapshots, config)
            report_text, suggestions = report_generator.generate_report()
            st.markdown(report_text)
            st.markdown("---")
            st.subheader("💡 单次仿真优化建议")
            for i, sug in enumerate(suggestions, 1):
                st.markdown(f"{i}. {sug}")

            st.markdown("---")

            st.subheader("🔍 瓶颈模式识别（历史仿真聚合分析）")
            min_sims_needed = 3
            if len(history_list) < min_sims_needed:
                st.info(f"ℹ️ 需要积累至少 {min_sims_needed} 条历史仿真记录才能进行结构性瓶颈识别（当前 {len(history_list)} 条）。请多次运行仿真以积累数据。")
            else:
                pattern_analyzer = BottleneckPatternAnalyzer(history_list, time_window_tolerance=30.0, min_occurrences=min_sims_needed)
                structural_bns = pattern_analyzer.analyze()
                if not structural_bns:
                    st.success("✅ 未检测到在多次仿真中反复出现的结构性瓶颈，当前系统运行相对稳定。")
                else:
                    st.warning(f"⚠️ 检测到 {len(structural_bns)} 个结构性瓶颈（在≥{min_sims_needed}次仿真中相近时段反复出现）：")
                    zone_cn_map = {"import": "进口区", "export": "出口区", "transit": "中转区"}
                    type_cn_map = {
                        BottleneckType.CONGESTION: "拥堵",
                        BottleneckType.RTG_CONFLICT: "场桥冲突",
                        BottleneckType.GATE_SATURATION: "闸口饱和预警",
                        BottleneckType.CUSTOM_RULE: "自定义规则",
                    }
                    pattern_rows = []
                    for si, sb in enumerate(structural_bns):
                        zone_str = zone_cn_map.get(sb.zone.value, sb.zone.value) if sb.zone else "-"
                        type_str = type_cn_map.get(sb.event_type, sb.event_type.value)
                        if sb.event_type == BottleneckType.CUSTOM_RULE and sb.custom_rule_name:
                            type_str = f"规则:{sb.custom_rule_name}"
                        sev_str = ""
                        if sb.severity:
                            sev_map = {SeverityLevel.HIGH: "🔴高", SeverityLevel.MEDIUM: "🟡中", SeverityLevel.LOW: "🟢低"}
                            sev_str = sev_map.get(sb.severity, "")
                        pattern_rows.append({
                            "序号": si + 1,
                            "分区": zone_str,
                            "类型": type_str + (f" [{sev_str}]" if sev_str else ""),
                            "典型时段": f"{_format_time(sb.typical_start)} ~ {_format_time(sb.typical_end)}",
                            "出现频次": f"{sb.frequency}/{sb.total_simulations} ({sb.frequency / sb.total_simulations:.0%})",
                            "平均持续时长": _format_time(sb.avg_duration),
                        })
                    st.table(pattern_rows)
                    st.markdown("#### 💡 针对性优化建议")
                    for si, sb in enumerate(structural_bns, 1):
                        st.markdown(f"{si}. {pattern_analyzer.generate_suggestion(sb)}")


if __name__ == "__main__":
    main()
