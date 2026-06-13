import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
from typing import List, Dict, Tuple, Optional

from .yard_model import Yard, ZoneType, ContainerSize, WeightClass, Slot
from .simulation_engine import SimulationStats, SimulationEngine
from .kpi import KPIResult
from .stacking_strategies import StackingStrategy


class Visualizer:
    def __init__(self):
        self.color_palette = {
            "import": "#3498db",
            "export": "#2ecc71",
            "transit": "#e74c3c",
            "light": "#85c1e9",
            "medium": "#3498db",
            "heavy": "#1a5276",
            "empty": "#ecf0f1",
        }

    def plot_yard_top_view(self, yard: Yard, zone: ZoneType, bay: Optional[int] = None) -> go.Figure:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return go.Figure()

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]
        num_tiers = zone_data["num_tiers"]

        if bay is not None:
            bays_to_show = [bay]
            title = f"{zone.value.capitalize()} Zone - Bay {bay}"
        else:
            bays_to_show = list(range(num_bays))
            title = f"{zone.value.capitalize()} Zone - All Bays"

        heatmap_data = []
        hover_texts = []
        x_labels = []
        y_labels = []

        for bay_idx in bays_to_show:
            x_labels.append(f"Bay {bay_idx}")

        for row_idx in range(num_rows):
            y_labels.append(f"Row {row_idx}")
            row_data = []
            row_hovers = []
            for bay_idx in bays_to_show:
                height = yard.get_stack_height(zone, bay_idx, row_idx)
                row_data.append(height)

                top_cont = yard.get_top_container(zone, bay_idx, row_idx)
                if top_cont:
                    hover = (
                        f"Bay: {bay_idx}<br>"
                        f"Row: {row_idx}<br>"
                        f"Height: {height}/{num_tiers}<br>"
                        f"Top: {top_cont.container_id}<br>"
                        f"Size: {top_cont.size.value}ft<br>"
                        f"Weight: {top_cont.weight_class.value}<br>"
                        f"Ship: {top_cont.ship_name}"
                    )
                else:
                    hover = f"Bay: {bay_idx}<br>Row: {row_idx}<br>Empty"
                row_hovers.append(hover)
            heatmap_data.append(row_data)
            hover_texts.append(row_hovers)

        fig = go.Figure(
            data=go.Heatmap(
                z=heatmap_data,
                x=x_labels,
                y=y_labels,
                text=hover_texts,
                hoverinfo="text",
                colorscale="Blues",
                zmin=0,
                zmax=num_tiers,
                colorbar=dict(title="Stack Height"),
            )
        )

        fig.update_layout(
            title=title,
            xaxis_title="Bay",
            yaxis_title="Row",
            height=400,
        )

        return fig

    def plot_yard_3d_view(self, yard: Yard, zone: ZoneType) -> go.Figure:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return go.Figure()

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]
        num_tiers = zone_data["num_tiers"]

        x, y, z = [], [], []
        colors = []
        hover_texts = []

        for bay in range(num_bays):
            for row in range(num_rows):
                for tier in range(num_tiers):
                    slot = yard.get_slot(zone, bay, row, tier)
                    if slot and slot.container:
                        x.append(bay)
                        y.append(row)
                        z.append(tier)

                        weight = slot.container.weight_class.value
                        colors.append(self.color_palette.get(weight, "#3498db"))

                        hover = (
                            f"Container: {slot.container.container_id}<br>"
                            f"Size: {slot.container.size.value}ft<br>"
                            f"Weight: {slot.container.weight_class.value}<br>"
                            f"Ship: {slot.container.ship_name}<br>"
                            f"Bay: {bay}, Row: {row}, Tier: {tier}"
                        )
                        hover_texts.append(hover)

        fig = go.Figure(
            data=[
                go.Scatter3d(
                    x=x,
                    y=y,
                    z=z,
                    mode="markers",
                    marker=dict(
                        size=8,
                        color=colors,
                        opacity=0.8,
                    ),
                    text=hover_texts,
                    hoverinfo="text",
                )
            ]
        )

        fig.update_layout(
            title=f"{zone.value.capitalize()} Zone - 3D View",
            scene=dict(
                xaxis_title="Bay",
                yaxis_title="Row",
                zaxis_title="Tier",
            ),
            height=500,
        )

        return fig

    def plot_utilization_trend(self, stats: SimulationStats) -> go.Figure:
        times = [t / 60.0 for t, _ in stats.utilization_history]
        utils = [u * 100 for _, u in stats.utilization_history]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=times,
                y=utils,
                mode="lines",
                name="场地利用率",
                line=dict(color="#3498db", width=2),
                fill="tozeroy",
                opacity=0.3,
            )
        )

        fig.update_layout(
            title="场地利用率趋势",
            xaxis_title="时间 (小时)",
            yaxis_title="利用率 (%)",
            yaxis=dict(range=[0, 100]),
            height=350,
            hovermode="x unified",
        )

        return fig

    def plot_throughput_trend(self, stats: SimulationStats) -> go.Figure:
        times = [t / 60.0 for t, _ in stats.throughput_history]
        throughputs = [c for _, c in stats.throughput_history]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=times,
                y=throughputs,
                mode="lines",
                name="累计吞吐量",
                line=dict(color="#2ecc71", width=2),
                fill="tozeroy",
                opacity=0.3,
            )
        )

        fig.update_layout(
            title="累计吞吐量趋势",
            xaxis_title="时间 (小时)",
            yaxis_title="TEU",
            height=350,
            hovermode="x unified",
        )

        return fig

    def plot_containers_in_yard_trend(self, stats: SimulationStats) -> go.Figure:
        times = [t / 60.0 for t, _ in stats.containers_in_yard]
        counts = [c for _, c in stats.containers_in_yard]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=times,
                y=counts,
                mode="lines",
                name="在场箱数",
                line=dict(color="#e74c3c", width=2),
                fill="tozeroy",
                opacity=0.3,
            )
        )

        fig.update_layout(
            title="在场集装箱数量趋势",
            xaxis_title="时间 (小时)",
            yaxis_title="集装箱数",
            height=350,
            hovermode="x unified",
        )

        return fig

    def plot_kpi_comparison(self, kpi_results: Dict[str, KPIResult]) -> go.Figure:
        strategies = list(kpi_results.keys())
        metrics = ["翻箱率", "平均利用率", "平均提箱耗时", "场桥效率", "日均吞吐量"]

        normalized_values = {}
        for metric in metrics:
            values = []
            for strat in strategies:
                kpi = kpi_results[strat]
                if metric == "翻箱率":
                    values.append(kpi.relocation_rate * 100)
                elif metric == "平均利用率":
                    values.append(kpi.avg_utilization * 100)
                elif metric == "平均提箱耗时":
                    values.append(kpi.avg_pickup_time)
                elif metric == "场桥效率":
                    values.append(kpi.avg_rtg_efficiency)
                elif metric == "日均吞吐量":
                    values.append(kpi.daily_throughput)
            normalized_values[metric] = values

        fig = make_subplots(
            rows=2, cols=3,
            subplot_titles=metrics,
            specs=[[{"type": "bar"}, {"type": "bar"}, {"type": "bar"}],
                   [{"type": "bar"}, {"type": "bar"}, {"type": "bar"}]],
        )

        positions = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2)]

        for i, (metric, values) in enumerate(normalized_values.items()):
            row, col = positions[i]
            colors = [self._get_strategy_color(s) for s in strategies]
            fig.add_trace(
                go.Bar(
                    x=strategies,
                    y=values,
                    name=metric,
                    marker_color=colors,
                    showlegend=False,
                ),
                row=row, col=col,
            )

        fig.update_layout(
            title="各策略KPI对比",
            height=600,
            showlegend=False,
        )

        return fig

    def _get_strategy_color(self, strategy: str) -> str:
        colors = {
            "random": "#95a5a6",
            "classified": "#3498db",
            "time_priority": "#2ecc71",
            "weight_layered": "#e67e22",
            "optimized": "#9b59b6",
        }
        return colors.get(strategy, "#34495e")

    def plot_rtg_gantt(self, engine: SimulationEngine) -> go.Figure:
        fig = go.Figure()

        colors = {
            "stow": "#3498db",
            "pickup": "#2ecc71",
            "travel": "#f39c12",
            "idle": "#ecf0f1",
        }

        rtg_ids = []
        for zone, rtgs in engine.rtgs.items():
            for rtg in rtgs:
                rtg_ids.append(rtg.rtg_id)

                for task_log in rtg.schedule:
                    task_type = task_log.get("type", "operate")
                    if task_type == "operate":
                        sub_type = task_log.get("task_type", "stow")
                        color = colors.get(sub_type, "#95a5a6")
                        label = f"{sub_type}: {task_log.get('container_id', '')}"
                    else:
                        color = colors.get("travel", "#f39c12")
                        label = f"travel: Bay{task_log.get('from_bay', 0)}→{task_log.get('to_bay', 0)}"

                    fig.add_trace(
                        go.Bar(
                            x=[task_log["end_time"] - task_log["start_time"]],
                            y=[rtg.rtg_id],
                            base=[task_log["start_time"] / 60.0],
                            orientation="h",
                            marker_color=color,
                            name=label,
                            hovertext=label,
                            showlegend=False,
                        )
                    )

        if not rtg_ids:
            for zone in ZoneType:
                for i in range(2):
                    rtg_ids.append(f"{zone.value}_rtg_{i}")

        fig.update_layout(
            title="场桥作业甘特图",
            xaxis_title="时间 (分钟)",
            yaxis_title="场桥",
            height=400,
            barmode="overlay",
        )

        return fig

    def plot_kpi_cards(self, kpi: KPIResult) -> go.Figure:
        fig = go.Figure()

        cards = [
            {"label": "翻箱率", "value": f"{kpi.relocation_rate:.2%}", "color": "#e74c3c"},
            {"label": "平均利用率", "value": f"{kpi.avg_utilization:.2%}", "color": "#3498db"},
            {"label": "平均提箱耗时", "value": f"{kpi.avg_pickup_time:.1f} min", "color": "#f39c12"},
            {"label": "场桥效率", "value": f"{kpi.avg_rtg_efficiency:.1f}/h", "color": "#2ecc71"},
            {"label": "日均吞吐量", "value": f"{kpi.daily_throughput:.0f} TEU", "color": "#9b59b6"},
            {"label": "总翻箱", "value": str(kpi.total_relocations), "color": "#1abc9c"},
        ]

        fig = make_subplots(
            rows=2, cols=3,
            subplot_titles=[c["label"] for c in cards],
            specs=[[{"type": "indicator"}] * 3, [{"type": "indicator"}] * 3],
        )

        positions = [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3)]

        for i, card in enumerate(cards):
            row, col = positions[i]
            fig.add_trace(
                go.Indicator(
                    mode="number",
                    value=float(card["value"].replace("%", "").replace(" min", "").replace("/h", "").replace(" TEU", "")) if isinstance(card["value"], str) and card["value"].replace(".", "").replace("%", "").replace(" min", "").replace("/h", "").replace(" TEU", "").isdigit() else 0,
                    number={"prefix": "", "suffix": ""},
                    title={"text": card["label"]},
                ),
                row=row, col=col,
            )

        fig.update_layout(height=400)
        return fig

    def plot_param_optimization(
        self,
        param_values: List[float],
        kpi_list: List[KPIResult],
        param_name: str = "time_weight",
    ) -> go.Figure:
        fig = make_subplots(
            rows=2, cols=2,
            subplot_titles=["翻箱率", "平均利用率", "平均提箱耗时", "日均吞吐量"],
        )

        relocation_rates = [k.relocation_rate * 100 for k in kpi_list]
        avg_utils = [k.avg_utilization * 100 for k in kpi_list]
        pickup_times = [k.avg_pickup_time for k in kpi_list]
        throughputs = [k.daily_throughput for k in kpi_list]

        fig.add_trace(
            go.Scatter(x=param_values, y=relocation_rates, mode="lines+markers", name="翻箱率(%)", line=dict(color="#e74c3c")),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=param_values, y=avg_utils, mode="lines+markers", name="利用率(%)", line=dict(color="#3498db")),
            row=1, col=2,
        )
        fig.add_trace(
            go.Scatter(x=param_values, y=pickup_times, mode="lines+markers", name="提箱耗时(min)", line=dict(color="#f39c12")),
            row=2, col=1,
        )
        fig.add_trace(
            go.Scatter(x=param_values, y=throughputs, mode="lines+markers", name="吞吐量(TEU)", line=dict(color="#2ecc71")),
            row=2, col=2,
        )

        fig.update_layout(
            title=f"参数优化曲线 - {param_name}",
            height=600,
            showlegend=False,
        )

        fig.update_xaxes(title_text=param_name)

        return fig
