import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum

from .yard_model import Yard, ZoneType, ContainerSize, WeightClass, Slot
from .rtg_scheduler import RTG


class RTGStatus(Enum):
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"


@dataclass
class SlotSnapshot:
    zone: ZoneType
    bay: int
    row: int
    tier: int
    is_empty: bool
    container_id: Optional[str] = None
    weight_class: Optional[str] = None
    stack_height: int = 0


@dataclass
class RTGSnapshot:
    rtg_id: str
    zone: ZoneType
    current_bay: int
    status: RTGStatus


@dataclass
class YardSnapshot:
    timestamp: float
    slot_snapshots: Dict[ZoneType, List[SlotSnapshot]] = field(default_factory=dict)
    rtg_snapshots: Dict[ZoneType, List[RTGSnapshot]] = field(default_factory=dict)
    zone_utilization: Dict[ZoneType, float] = field(default_factory=dict)
    gate_queue_total: int = 0
    gate_queue_per_gate: Dict[str, int] = field(default_factory=dict)


class BottleneckType(Enum):
    CONGESTION = "congestion"
    RTG_CONFLICT = "rtg_conflict"
    GATE_SATURATION = "gate_saturation"


@dataclass
class BottleneckEvent:
    event_type: BottleneckType
    start_time: float
    end_time: float
    duration: float
    zone: Optional[ZoneType] = None
    rtg_ids: List[str] = field(default_factory=list)
    gate_id: Optional[str] = None
    peak_metric: float = 0.0
    description: str = ""


class SnapshotRecorder:
    def __init__(self, config: Dict):
        self.config = config
        self.snapshots: List[YardSnapshot] = []
        self.sampling_interval = 10.0

    def set_sampling_interval(self, interval: float):
        self.sampling_interval = max(1.0, min(60.0, interval))

    def record(self, timestamp: float, yard: Yard, rtgs: Dict[ZoneType, List[RTG]],
               rtg_resources: Dict[ZoneType, List], gate_resources: List,
               rtg_pending_tasks: Dict[ZoneType, List]):
        snapshot = YardSnapshot(timestamp=timestamp)

        for zone in ZoneType:
            zone_data = yard.zones.get(zone)
            if not zone_data:
                continue

            slots = []
            num_bays = zone_data["num_bays"]
            num_rows = zone_data["num_rows"]
            num_tiers = zone_data["num_tiers"]

            total_slots = num_bays * num_rows * num_tiers
            occupied = 0

            for bay in range(num_bays):
                for row in range(num_rows):
                    stack_height = 0
                    for tier in range(num_tiers):
                        slot = yard.get_slot(zone, bay, row, tier)
                        if slot and slot.container:
                            occupied += 1
                            stack_height = tier + 1
                            slots.append(SlotSnapshot(
                                zone=zone,
                                bay=bay,
                                row=row,
                                tier=tier,
                                is_empty=False,
                                container_id=slot.container.container_id,
                                weight_class=slot.container.weight_class.value,
                                stack_height=stack_height,
                            ))
                        else:
                            slots.append(SlotSnapshot(
                                zone=zone,
                                bay=bay,
                                row=row,
                                tier=tier,
                                is_empty=True,
                                stack_height=stack_height,
                            ))

            snapshot.slot_snapshots[zone] = slots
            snapshot.zone_utilization[zone] = occupied / total_slots if total_slots > 0 else 0.0

            zone_rtgs = rtgs.get(zone, [])
            zone_resources = rtg_resources.get(zone, [])
            zone_rtg_snapshots = []

            for i, rtg in enumerate(zone_rtgs):
                status = RTGStatus.IDLE
                resource = zone_resources[i] if i < len(zone_resources) else None

                if resource and resource.count > 0:
                    status = RTGStatus.WORKING
                else:
                    pending = rtg_pending_tasks.get(zone, [])
                    if len(pending) > 0 and len(zone_rtgs) > 1:
                        for other_i, other_rtg in enumerate(zone_rtgs):
                            if other_i != i:
                                dist = abs(rtg.current_bay - other_rtg.current_bay)
                                if dist <= 2:
                                    other_res = zone_resources[other_i] if other_i < len(zone_resources) else None
                                    if other_res and other_res.count > 0:
                                        status = RTGStatus.WAITING
                                        break

                zone_rtg_snapshots.append(RTGSnapshot(
                    rtg_id=rtg.rtg_id,
                    zone=zone,
                    current_bay=rtg.current_bay,
                    status=status,
                ))

            snapshot.rtg_snapshots[zone] = zone_rtg_snapshots

        gate_queue_per_gate = {}
        total_queue = 0
        for i, resource in enumerate(gate_resources):
            gate_id = f"gate_{i}"
            qlen = len(resource.queue)
            gate_queue_per_gate[gate_id] = qlen
            total_queue += qlen
        snapshot.gate_queue_total = total_queue
        snapshot.gate_queue_per_gate = gate_queue_per_gate

        self.snapshots.append(snapshot)

    def get_snapshots(self) -> List[YardSnapshot]:
        return self.snapshots


class BottleneckDiagnoser:
    def __init__(self, snapshots: List[YardSnapshot], config: Dict):
        self.snapshots = snapshots
        self.config = config
        self.events: List[BottleneckEvent] = []

        self.congestion_threshold = 0.80
        self.conflict_distance_threshold = 2
        self.gate_saturation_ratio = 0.80

    def set_parameters(self, congestion_threshold: float = 0.80,
                       conflict_distance: int = 2,
                       gate_saturation_ratio: float = 0.80):
        self.congestion_threshold = max(0.50, min(0.95, congestion_threshold))
        self.conflict_distance_threshold = max(1, min(10, conflict_distance))
        self.gate_saturation_ratio = max(0.50, min(0.95, gate_saturation_ratio))

    def diagnose(self) -> List[BottleneckEvent]:
        self.events = []
        self._detect_congestion()
        self._detect_rtg_conflicts()
        self._detect_gate_saturation()
        self.events.sort(key=lambda e: e.start_time)
        return self.events

    def _detect_congestion(self):
        min_consecutive = 3
        for zone in ZoneType:
            zone_snaps = []
            for snap in self.snapshots:
                util = snap.zone_utilization.get(zone, 0.0)
                zone_snaps.append((snap.timestamp, util))

            i = 0
            while i < len(zone_snaps):
                if zone_snaps[i][1] >= self.congestion_threshold:
                    start_idx = i
                    peak_util = zone_snaps[i][1]
                    while i < len(zone_snaps) and zone_snaps[i][1] >= self.congestion_threshold:
                        peak_util = max(peak_util, zone_snaps[i][1])
                        i += 1
                    count = i - start_idx
                    if count >= min_consecutive:
                        start_time = zone_snaps[start_idx][0]
                        end_time = zone_snaps[i - 1][0] if i - 1 >= 0 else start_time
                        duration = end_time - start_time
                        self.events.append(BottleneckEvent(
                            event_type=BottleneckType.CONGESTION,
                            start_time=start_time,
                            end_time=end_time,
                            duration=duration,
                            zone=zone,
                            peak_metric=peak_util,
                            description=f"{zone.value}区利用率持续超过{self.congestion_threshold:.0%}",
                        ))
                else:
                    i += 1

    def _detect_rtg_conflicts(self):
        min_consecutive = 2
        rtg_id_to_snaps: Dict[str, List[Tuple[float, int, RTGStatus]]] = {}

        for snap in self.snapshots:
            for zone, rtg_snaps in snap.rtg_snapshots.items():
                for rtg_snap in rtg_snaps:
                    if rtg_snap.rtg_id not in rtg_id_to_snaps:
                        rtg_id_to_snaps[rtg_snap.rtg_id] = []
                    rtg_id_to_snaps[rtg_snap.rtg_id].append(
                        (snap.timestamp, rtg_snap.current_bay, rtg_snap.status)
                    )

        rtg_ids = list(rtg_id_to_snaps.keys())
        for i in range(len(rtg_ids)):
            for j in range(i + 1, len(rtg_ids)):
                id1, id2 = rtg_ids[i], rtg_ids[j]
                snaps1 = rtg_id_to_snaps[id1]
                snaps2 = rtg_id_to_snaps[id2]

                n = min(len(snaps1), len(snaps2))
                k = 0
                while k < n:
                    bay1 = snaps1[k][1]
                    bay2 = snaps2[k][1]
                    status1 = snaps1[k][2]
                    status2 = snaps2[k][2]
                    dist = abs(bay1 - bay2)

                    if dist <= self.conflict_distance_threshold and (
                            status1 == RTGStatus.WAITING or status2 == RTGStatus.WAITING):
                        start_idx = k
                        peak_dist = dist
                        while k < n:
                            b1 = snaps1[k][1]
                            b2 = snaps2[k][1]
                            s1 = snaps1[k][2]
                            s2 = snaps2[k][2]
                            d = abs(b1 - b2)
                            if d <= self.conflict_distance_threshold and (
                                    s1 == RTGStatus.WAITING or s2 == RTGStatus.WAITING):
                                peak_dist = min(peak_dist, d)
                                k += 1
                            else:
                                break
                        count = k - start_idx
                        if count >= min_consecutive:
                            start_time = snaps1[start_idx][0]
                            end_time = snaps1[k - 1][0] if k - 1 >= 0 else start_time
                            duration = end_time - start_time
                            zone1 = ZoneType(id1.split("_")[0]) if "_" in id1 else None
                            self.events.append(BottleneckEvent(
                                event_type=BottleneckType.RTG_CONFLICT,
                                start_time=start_time,
                                end_time=end_time,
                                duration=duration,
                                zone=zone1,
                                rtg_ids=[id1, id2],
                                peak_metric=float(peak_dist),
                                description=f"场桥冲突: {id1} 与 {id2} 相邻作业",
                            ))
                    else:
                        k += 1

    def _detect_gate_saturation(self):
        min_consecutive = 3
        max_queue = self.config.get("truck_scheduling", {}).get("max_queue_length", 20)
        threshold = max_queue * self.gate_saturation_ratio

        gate_histories: Dict[str, List[Tuple[float, int]]] = {}
        for snap in self.snapshots:
            for gate_id, qlen in snap.gate_queue_per_gate.items():
                if gate_id not in gate_histories:
                    gate_histories[gate_id] = []
                gate_histories[gate_id].append((snap.timestamp, qlen))

        for gate_id, history in gate_histories.items():
            i = 0
            while i < len(history):
                if history[i][1] >= threshold:
                    start_idx = i
                    peak_q = history[i][1]
                    while i < len(history) and history[i][1] >= threshold:
                        peak_q = max(peak_q, history[i][1])
                        i += 1
                    count = i - start_idx
                    if count >= min_consecutive:
                        start_time = history[start_idx][0]
                        end_time = history[i - 1][0] if i - 1 >= 0 else start_time
                        duration = end_time - start_time
                        self.events.append(BottleneckEvent(
                            event_type=BottleneckType.GATE_SATURATION,
                            start_time=start_time,
                            end_time=end_time,
                            duration=duration,
                            gate_id=gate_id,
                            peak_metric=float(peak_q),
                            description=f"{gate_id.replace('gate_', '闸口')}排队饱和预警",
                        ))
                else:
                    i += 1


def _format_time(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} 分钟"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    days = hours // 24
    rem_hours = hours % 24
    if days > 0:
        return f"{days}天{rem_hours}时{mins}分"
    return f"{hours}时{mins}分"


class ReplayVisualizer:
    def __init__(self):
        self.zone_colors = {
            ZoneType.IMPORT: "#3498db",
            ZoneType.EXPORT: "#2ecc71",
            ZoneType.TRANSIT: "#e74c3c",
        }
        self.weight_colors = {
            "light": "#85c1e9",
            "medium": "#3498db",
            "heavy": "#1a5276",
        }
        self.rtg_colors = [
            "#e74c3c", "#f39c12", "#9b59b6", "#1abc9c", "#34495e"
        ]

    def create_heatmap_animation(self, snapshots: List[YardSnapshot], zone: ZoneType,
                                 bottleneck_events: List[BottleneckEvent] = None) -> go.Figure:
        if not snapshots:
            return go.Figure()

        zone_data_ref = None
        for snap in snapshots:
            if zone in snap.slot_snapshots and snap.slot_snapshots[zone]:
                zone_data_ref = snap.slot_snapshots[zone]
                break

        if not zone_data_ref:
            return go.Figure()

        num_bays = max(s.bay for s in zone_data_ref) + 1
        num_rows = max(s.row for s in zone_data_ref) + 1
        num_tiers = max(s.tier for s in zone_data_ref) + 1

        frames = []
        for snap in snapshots:
            zone_slots = snap.slot_snapshots.get(zone, [])
            heatmap_z = [[0] * num_bays for _ in range(num_rows)]
            hover_texts = [[""] * num_bays for _ in range(num_rows)]
            zone_rtgs = snap.rtg_snapshots.get(zone, [])

            for slot in zone_slots:
                if slot.stack_height > heatmap_z[slot.row][slot.bay]:
                    heatmap_z[slot.row][slot.bay] = slot.stack_height

            for slot in zone_slots:
                if slot.tier == 0 or slot.stack_height > 0:
                    existing = hover_texts[slot.row][slot.bay]
                    if slot.tier == slot.stack_height - 1 and not slot.is_empty:
                        info = (f"Bay: {slot.bay}<br>Row: {slot.row}<br>"
                                f"Height: {slot.stack_height}/{num_tiers}<br>"
                                f"Top: {slot.container_id or '空'}<br>"
                                f"Weight: {slot.weight_class or 'N/A'}")
                        if not existing:
                            hover_texts[slot.row][slot.bay] = info

            x_labels = [f"Bay {b}" for b in range(num_bays)]
            y_labels = [f"Row {r}" for r in range(num_rows)]

            traces = [
                go.Heatmap(
                    z=heatmap_z,
                    x=x_labels,
                    y=y_labels,
                    text=hover_texts,
                    hoverinfo="text",
                    colorscale="Blues",
                    zmin=0,
                    zmax=num_tiers,
                    colorbar=dict(title="堆高层数", x=1.02),
                    showscale=True,
                )
            ]

            for idx, rtg in enumerate(zone_rtgs):
                color = self.rtg_colors[idx % len(self.rtg_colors)]
                status_symbol = "circle"
                if rtg.status == RTGStatus.WORKING:
                    status_symbol = "diamond"
                elif rtg.status == RTGStatus.WAITING:
                    status_symbol = "x"

                status_label = {
                    RTGStatus.IDLE: "空闲",
                    RTGStatus.WORKING: "作业中",
                    RTGStatus.WAITING: "等待让路",
                }[rtg.status]

                traces.append(go.Scatter(
                    x=[f"Bay {rtg.current_bay}"],
                    y=[f"Row {num_rows // 2}"],
                    mode="markers+text",
                    marker=dict(
                        symbol=status_symbol,
                        size=18,
                        color=color,
                        line=dict(width=2, color="black"),
                    ),
                    text=[f"🏗️{idx + 1}"],
                    textposition="top center",
                    name=f"场桥{idx + 1}",
                    hovertext=f"{rtg.rtg_id}<br>位置: Bay {rtg.current_bay}<br>状态: {status_label}",
                    hoverinfo="text",
                    showlegend=True,
                    legendgroup="rtgs",
                ))

            frames.append(go.Frame(
                data=traces,
                name=f"{snap.timestamp:.0f}",
            ))

        if not frames:
            return go.Figure()

        fig = go.Figure(
            data=frames[0].data,
            layout=go.Layout(
                title=f"{zone.value.capitalize()}区 热力动态回放",
                xaxis=dict(title="贝位 (Bay)"),
                yaxis=dict(title="排 (Row)"),
                height=550,
                updatemenus=[
                    dict(
                        type="buttons",
                        showactive=False,
                        y=-0.25,
                        x=-0.05,
                        xanchor="right",
                        yanchor="top",
                        pad=dict(t=0, r=10),
                        buttons=[
                            dict(
                                label="▶ 播放",
                                method="animate",
                                args=[
                                    None,
                                    dict(
                                        frame=dict(duration=500, redraw=True),
                                        fromcurrent=True,
                                        transition=dict(duration=0),
                                    ),
                                ],
                            ),
                            dict(
                                label="⏸ 暂停",
                                method="animate",
                                args=[
                                    [None],
                                    dict(
                                        frame=dict(duration=0, redraw=False),
                                        mode="immediate",
                                        transition=dict(duration=0),
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
                sliders=[
                    dict(
                        active=0,
                        yanchor="top",
                        xanchor="left",
                        currentvalue=dict(
                            font=dict(size=14),
                            prefix="时间: ",
                            suffix=" 分钟",
                            visible=True,
                            xanchor="right",
                        ),
                        transition=dict(duration=300, easing="cubic-in-out"),
                        pad=dict(b=10, t=30),
                        len=0.9,
                        x=0.05,
                        y=-0.15,
                        steps=[
                            dict(
                                method="animate",
                                label=f"{snap.timestamp:.0f}",
                                args=[
                                    [f"{snap.timestamp:.0f}"],
                                    dict(
                                        frame=dict(duration=300, redraw=True),
                                        mode="immediate",
                                        transition=dict(duration=0),
                                    ),
                                ],
                            )
                            for snap in snapshots
                        ],
                    )
                ],
            ),
            frames=frames,
        )

        return fig

    def create_static_heatmap_frame(self, snapshot: YardSnapshot, zone: ZoneType) -> go.Figure:
        zone_slots = snapshot.slot_snapshots.get(zone, [])
        if not zone_slots:
            return go.Figure()

        num_bays = max(s.bay for s in zone_slots) + 1
        num_rows = max(s.row for s in zone_slots) + 1
        num_tiers = max(s.tier for s in zone_slots) + 1

        heatmap_z = [[0] * num_bays for _ in range(num_rows)]
        hover_texts = [[""] * num_bays for _ in range(num_rows)]

        for slot in zone_slots:
            if slot.stack_height > heatmap_z[slot.row][slot.bay]:
                heatmap_z[slot.row][slot.bay] = slot.stack_height

        for slot in zone_slots:
            if slot.tier == slot.stack_height - 1 and not slot.is_empty:
                info = (f"Bay: {slot.bay}<br>Row: {slot.row}<br>"
                        f"Height: {slot.stack_height}/{num_tiers}<br>"
                        f"Top: {slot.container_id or '空'}<br>"
                        f"Weight: {slot.weight_class or 'N/A'}")
                hover_texts[slot.row][slot.bay] = info

        x_labels = [f"Bay {b}" for b in range(num_bays)]
        y_labels = [f"Row {r}" for r in range(num_rows)]

        fig = go.Figure(
            data=go.Heatmap(
                z=heatmap_z,
                x=x_labels,
                y=y_labels,
                text=hover_texts,
                hoverinfo="text",
                colorscale="Blues",
                zmin=0,
                zmax=num_tiers,
                colorbar=dict(title="堆高层数"),
            )
        )

        zone_rtgs = snapshot.rtg_snapshots.get(zone, [])
        for idx, rtg in enumerate(zone_rtgs):
            color = self.rtg_colors[idx % len(self.rtg_colors)]
            status_symbol = "circle"
            if rtg.status == RTGStatus.WORKING:
                status_symbol = "diamond"
            elif rtg.status == RTGStatus.WAITING:
                status_symbol = "x"

            status_label = {
                RTGStatus.IDLE: "空闲",
                RTGStatus.WORKING: "作业中",
                RTGStatus.WAITING: "等待让路",
            }[rtg.status]

            fig.add_trace(go.Scatter(
                x=[f"Bay {rtg.current_bay}"],
                y=[f"Row {num_rows // 2}"],
                mode="markers+text",
                marker=dict(
                    symbol=status_symbol,
                    size=18,
                    color=color,
                    line=dict(width=2, color="black"),
                ),
                text=[f"🏗️{idx + 1}"],
                textposition="top center",
                name=f"{rtg.rtg_id} [{status_label}]",
                hovertext=f"{rtg.rtg_id}<br>位置: Bay {rtg.current_bay}<br>状态: {status_label}",
                hoverinfo="text",
            ))

        util = snapshot.zone_utilization.get(zone, 0.0)
        fig.update_layout(
            title=f"{zone.value.capitalize()}区 时刻 {_format_time(snapshot.timestamp)} | 利用率: {util:.1%} | 闸口排队: {snapshot.gate_queue_total}辆",
            xaxis_title="贝位 (Bay)",
            yaxis_title="排 (Row)",
            height=500,
        )

        return fig

    def create_bottleneck_timeline(self, events: List[BottleneckEvent],
                                   total_duration: float) -> go.Figure:
        if not events:
            fig = go.Figure()
            fig.update_layout(
                title="瓶颈事件时间轴",
                xaxis_title="时间 (分钟)",
                height=200,
                annotations=[
                    dict(
                        x=0.5, y=0.5,
                        xref="paper", yref="paper",
                        text="✅ 未检测到瓶颈事件",
                        showarrow=False,
                        font=dict(size=16, color="#2ecc71"),
                    )
                ],
            )
            return fig

        color_map = {
            BottleneckType.CONGESTION: "#e74c3c",
            BottleneckType.RTG_CONFLICT: "#f39c12",
            BottleneckType.GATE_SATURATION: "#f1c40f",
        }
        label_map = {
            BottleneckType.CONGESTION: "拥堵",
            BottleneckType.RTG_CONFLICT: "场桥冲突",
            BottleneckType.GATE_SATURATION: "闸口饱和预警",
        }

        fig = go.Figure()

        y_positions = {
            BottleneckType.CONGESTION: 2,
            BottleneckType.RTG_CONFLICT: 1,
            BottleneckType.GATE_SATURATION: 0,
        }

        for event in events:
            y = y_positions.get(event.event_type, 1)
            color = color_map.get(event.event_type, "#95a5a6")
            label = label_map.get(event.event_type, event.event_type.value)

            hover = f"{event.description}<br>起止: {_format_time(event.start_time)} ~ {_format_time(event.end_time)}<br>时长: {_format_time(event.duration)}<br>峰值: {event.peak_metric:.2f}"

            fig.add_trace(go.Scatter(
                x=[event.start_time, event.end_time, event.end_time, event.start_time],
                y=[y - 0.3, y - 0.3, y + 0.3, y + 0.3],
                fill="toself",
                fillcolor=color,
                mode="lines",
                line=dict(color=color, width=2),
                name=label,
                text=hover,
                hoverinfo="text",
                showlegend=False,
                customdata=[event.start_time],
            ))

        fig.update_layout(
            title="瓶颈事件时间轴 (红=拥堵, 橙=冲突, 黄=饱和预警)",
            xaxis=dict(
                title="时间 (分钟)",
                range=[0, total_duration],
            ),
            yaxis=dict(
                tickmode="array",
                tickvals=[2, 1, 0],
                ticktext=["堆场拥堵", "场桥冲突", "闸口饱和"],
                range=[-0.8, 2.8],
            ),
            height=250,
            showlegend=False,
        )

        return fig

    def create_zone_utilization_trend(self, snapshots: List[YardSnapshot]) -> go.Figure:
        if not snapshots:
            return go.Figure()

        fig = go.Figure()

        colors = [self.zone_colors[z] for z in ZoneType]
        for i, zone in enumerate(ZoneType):
            times = [s.timestamp for s in snapshots]
            utils = [s.zone_utilization.get(zone, 0.0) * 100 for s in snapshots]

            fig.add_trace(go.Scatter(
                x=times,
                y=utils,
                mode="lines",
                name=f"{zone.value.capitalize()}区",
                line=dict(color=colors[i], width=2),
                fill="tozeroy",
                opacity=0.2,
            ))

        fig.update_layout(
            title="各分区利用率趋势",
            xaxis_title="时间 (分钟)",
            yaxis_title="利用率 (%)",
            yaxis=dict(range=[0, 100]),
            height=300,
            hovermode="x unified",
        )

        return fig

    def create_gate_queue_trend(self, snapshots: List[YardSnapshot]) -> go.Figure:
        if not snapshots:
            return go.Figure()

        times = [s.timestamp for s in snapshots]
        queues = [s.gate_queue_total for s in snapshots]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=times,
            y=queues,
            mode="lines",
            name="总排队车辆",
            line=dict(color="#e74c3c", width=2),
            fill="tozeroy",
            opacity=0.3,
        ))

        fig.update_layout(
            title="闸口总排队长度趋势",
            xaxis_title="时间 (分钟)",
            yaxis_title="排队车辆数",
            height=300,
            hovermode="x unified",
        )

        return fig


class DiagnosisReportGenerator:
    def __init__(self, events: List[BottleneckEvent], snapshots: List[YardSnapshot], config: Dict):
        self.events = events
        self.snapshots = snapshots
        self.config = config

    def generate_report(self) -> Tuple[str, List[str]]:
        congestion_events = [e for e in self.events if e.event_type == BottleneckType.CONGESTION]
        conflict_events = [e for e in self.events if e.event_type == BottleneckType.RTG_CONFLICT]
        saturation_events = [e for e in self.events if e.event_type == BottleneckType.GATE_SATURATION]

        lines = []
        lines.append("## 📋 瓶颈诊断报告")
        lines.append("")
        lines.append(f"本次仿真共检测到 **{len(self.events)}** 个瓶颈事件：")
        lines.append(f"- 🔴 堆场拥堵事件: **{len(congestion_events)}** 个")
        lines.append(f"- 🟠 场桥冲突事件: **{len(conflict_events)}** 个")
        lines.append(f"- 🟡 闸口饱和预警: **{len(saturation_events)}** 个")
        lines.append("")

        if not self.events:
            lines.append("### ✅ 仿真运行良好")
            lines.append("本次仿真未检测到显著的瓶颈事件，当前配置下堆场运行较为顺畅。")
            return "\n".join(lines), self._generate_suggestions(congestion_events, conflict_events, saturation_events)

        lines.append("---")
        lines.append("### 🕒 按时间排序的瓶颈事件详情")
        lines.append("")

        for idx, event in enumerate(self.events, 1):
            icon_map = {
                BottleneckType.CONGESTION: "🔴",
                BottleneckType.RTG_CONFLICT: "🟠",
                BottleneckType.GATE_SATURATION: "🟡",
            }
            type_map = {
                BottleneckType.CONGESTION: "堆场拥堵",
                BottleneckType.RTG_CONFLICT: "场桥冲突",
                BottleneckType.GATE_SATURATION: "闸口饱和预警",
            }
            icon = icon_map.get(event.event_type, "⚪")
            type_name = type_map.get(event.event_type, event.event_type.value)

            lines.append(f"#### {idx}. {icon} {type_name}")
            lines.append(f"- **起止时间**: 第{_format_time(event.start_time)} ~ 第{_format_time(event.end_time)}")
            lines.append(f"- **持续时长**: {_format_time(event.duration)}")

            if event.zone:
                zone_cn = {"import": "进口区", "export": "出口区", "transit": "中转区"}
                lines.append(f"- **涉及分区**: {zone_cn.get(event.zone.value, event.zone.value)}")
            if event.rtg_ids:
                lines.append(f"- **涉及场桥**: {', '.join(event.rtg_ids)}")
            if event.gate_id:
                lines.append(f"- **涉及闸口**: {event.gate_id.replace('gate_', '闸口')}")

            if event.event_type == BottleneckType.CONGESTION:
                lines.append(f"- **峰值利用率**: {event.peak_metric:.1%}")
            elif event.event_type == BottleneckType.RTG_CONFLICT:
                lines.append(f"- **最小贝位间距**: {event.peak_metric:.0f} 贝位")
            elif event.event_type == BottleneckType.GATE_SATURATION:
                max_q = self.config.get("truck_scheduling", {}).get("max_queue_length", 20)
                lines.append(f"- **峰值排队长度**: {event.peak_metric:.0f} / {max_q} ({event.peak_metric / max_q:.0%})")

            lines.append(f"- **描述**: {event.description}")
            lines.append("")

        suggestions = self._generate_suggestions(congestion_events, conflict_events, saturation_events)
        return "\n".join(lines), suggestions

    def _generate_suggestions(self, congestion_events, conflict_events, saturation_events) -> List[str]:
        suggestions = []

        total_congestion_dur = sum(e.duration for e in congestion_events)
        total_conflict_dur = sum(e.duration for e in conflict_events)
        total_saturation_dur = sum(e.duration for e in saturation_events)

        if total_congestion_dur > 0:
            affected_zones = set(e.zone for e in congestion_events if e.zone)
            zone_names = {
                ZoneType.IMPORT: "进口区",
                ZoneType.EXPORT: "出口区",
                ZoneType.TRANSIT: "中转区",
            }
            zones_str = "、".join(zone_names.get(z, z.value) for z in affected_zones)
            suggestions.append(
                f"📦 **建议扩容堆场容量**: 检测到{zones_str}存在持续拥堵（累计{_format_time(total_congestion_dur)}）。"
                f"建议增加贝位数或优化堆垛策略以提高空间利用率。"
            )

            current_bays = {}
            for zone in affected_zones:
                current_bays[zone] = self.config.get("yard", {}).get(zone.value, {}).get("num_bays", 20)
            bay_suggestions = [f"{zone_names.get(z, z.value)}从{current_bays[z]}贝位增至{current_bays[z] + 5}贝位" for z in current_bays]
            if bay_suggestions:
                suggestions.append(f"   具体建议: {'；'.join(bay_suggestions)}。")

        if total_conflict_dur > 0:
            num_rtgs_by_zone = self.config.get("rtg", {}).get("num_rtgs", {})
            affected_zones = set(e.zone for e in conflict_events if e.zone)
            zones_str = "、".join(
                {ZoneType.IMPORT: "进口区", ZoneType.EXPORT: "出口区", ZoneType.TRANSIT: "中转区"}.get(z, z.value)
                for z in affected_zones
            )
            suggestions.append(
                f"🏗️ **建议优化场桥调度**: 检测到{zones_str}场桥频繁冲突（累计{_format_time(total_conflict_dur)}）。"
            )

            rtg_adjust_suggestions = []
            for zone in affected_zones:
                current_count = num_rtgs_by_zone.get(zone.value, 2)
                if current_count <= 1:
                    rtg_adjust_suggestions.append(
                        f"{zone.value}区可考虑将场桥工作范围做更明确的划分（左半/右半区）"
                    )
                else:
                    rtg_adjust_suggestions.append(
                        f"{zone.value}区当前{current_count}台场桥，可调整为{current_count - 1}台（降低冲突）或优化工作范围划分"
                    )
            if rtg_adjust_suggestions:
                suggestions.append(f"   具体建议: {'；'.join(rtg_adjust_suggestions)}。")

        if total_saturation_dur > 0:
            current_gates = self.config.get("truck_scheduling", {}).get("num_gates", 3)
            suggestions.append(
                f"🚛 **建议增加闸口数量**: 检测到闸口频繁饱和（累计{_format_time(total_saturation_dur)}）。"
                f"建议将闸口数量从{current_gates}个增至{current_gates + 1}~{current_gates + 2}个。"
            )

        if not suggestions:
            suggestions.append("✅ 当前配置下仿真运行状况良好，无需特别优化。")
            suggestions.append("💡 如需进一步提升，可考虑：微调堆垛策略、在高峰时段动态增开场桥作业。")

        return suggestions
