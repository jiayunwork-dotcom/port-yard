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
    CUSTOM_RULE = "custom_rule"


class SeverityLevel(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CustomRuleConditionType(Enum):
    ZONE_UTILIZATION = "zone_utilization"
    RTG_WAIT_COUNT = "rtg_wait_count"
    GATE_QUEUE_FLUCTUATION = "gate_queue_fluctuation"


@dataclass
class CustomRule:
    rule_id: str
    rule_name: str
    condition_type: CustomRuleConditionType
    zone: Optional[ZoneType] = None
    rtg_id: Optional[str] = None
    consecutive_snapshots: int = 3
    threshold_pct: float = 0.85
    wait_count_threshold: int = 5
    fluctuation_threshold: int = 3
    severity: SeverityLevel = SeverityLevel.MEDIUM
    description: str = ""


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
    custom_rule_id: Optional[str] = None
    custom_rule_name: Optional[str] = None
    severity: Optional[SeverityLevel] = None


@dataclass
class HistoricalSimulationRecord:
    record_id: str
    label: str
    timestamp: float
    strategy_name: str
    snapshots: List[YardSnapshot]
    bottleneck_events: List[BottleneckEvent]
    config_snapshot: Dict = field(default_factory=dict)


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
    def __init__(self, snapshots: List[YardSnapshot], config: Dict,
                 custom_rules: Optional[List[CustomRule]] = None):
        self.snapshots = snapshots
        self.config = config
        self.events: List[BottleneckEvent] = []
        self.custom_rules = custom_rules or []

        self.congestion_threshold = 0.80
        self.conflict_distance_threshold = 2
        self.gate_saturation_ratio = 0.80

    def set_parameters(self, congestion_threshold: float = 0.80,
                       conflict_distance: int = 2,
                       gate_saturation_ratio: float = 0.80,
                       custom_rules: Optional[List[CustomRule]] = None):
        self.congestion_threshold = max(0.50, min(0.95, congestion_threshold))
        self.conflict_distance_threshold = max(1, min(10, conflict_distance))
        self.gate_saturation_ratio = max(0.50, min(0.95, gate_saturation_ratio))
        if custom_rules is not None:
            self.custom_rules = custom_rules

    def diagnose(self) -> List[BottleneckEvent]:
        self.events = []
        self._detect_congestion()
        self._detect_rtg_conflicts()
        self._detect_gate_saturation()
        self._detect_custom_rules()
        self.events.sort(key=lambda e: e.start_time)
        return self.events

    def _detect_custom_rules(self):
        if not self.custom_rules:
            return
        for rule in self.custom_rules:
            if rule.condition_type == CustomRuleConditionType.ZONE_UTILIZATION:
                self._detect_rule_zone_utilization(rule)
            elif rule.condition_type == CustomRuleConditionType.RTG_WAIT_COUNT:
                self._detect_rule_rtg_wait(rule)
            elif rule.condition_type == CustomRuleConditionType.GATE_QUEUE_FLUCTUATION:
                self._detect_rule_gate_fluctuation(rule)

    def _detect_rule_zone_utilization(self, rule: CustomRule):
        zone = rule.zone
        if not zone:
            return
        min_consecutive = max(1, rule.consecutive_snapshots)
        zone_snaps = [(s.timestamp, s.zone_utilization.get(zone, 0.0)) for s in self.snapshots]
        i = 0
        while i < len(zone_snaps):
            if zone_snaps[i][1] >= rule.threshold_pct:
                start_idx = i
                peak_util = zone_snaps[i][1]
                while i < len(zone_snaps) and zone_snaps[i][1] >= rule.threshold_pct:
                    peak_util = max(peak_util, zone_snaps[i][1])
                    i += 1
                count = i - start_idx
                if count >= min_consecutive:
                    start_time = zone_snaps[start_idx][0]
                    end_time = zone_snaps[i - 1][0] if i - 1 >= 0 else start_time
                    duration = end_time - start_time
                    self.events.append(BottleneckEvent(
                        event_type=BottleneckType.CUSTOM_RULE,
                        start_time=start_time,
                        end_time=end_time,
                        duration=duration,
                        zone=zone,
                        peak_metric=peak_util,
                        description=f"[自定义规则] {rule.rule_name}: {zone.value}区利用率连续{count}个快照超过{rule.threshold_pct:.0%}",
                        custom_rule_id=rule.rule_id,
                        custom_rule_name=rule.rule_name,
                        severity=rule.severity,
                    ))
            else:
                i += 1

    def _detect_rule_rtg_wait(self, rule: CustomRule):
        rtg_id = rule.rtg_id
        min_snapshots = max(1, rule.consecutive_snapshots)
        threshold = max(1, rule.wait_count_threshold)
        rtg_snaps: List[Tuple[float, int, RTGStatus]] = []
        for snap in self.snapshots:
            found = False
            for z, rtg_list in snap.rtg_snapshots.items():
                for r in rtg_list:
                    if r.rtg_id == rtg_id:
                        wait_count = 1 if r.status == RTGStatus.WAITING else 0
                        rtg_snaps.append((snap.timestamp, wait_count, r.status))
                        found = True
                        break
                if found:
                    break
            if not found:
                rtg_snaps.append((snap.timestamp, 0, RTGStatus.IDLE))
        i = 0
        while i < len(rtg_snaps):
            window = rtg_snaps[i:i + min_snapshots]
            total_wait = sum(w for _, w, _ in window)
            if total_wait >= threshold and len(window) >= min_snapshots:
                start_idx = i
                peak_wait = total_wait
                zone_info = None
                while i < len(rtg_snaps):
                    end_i = min(i + min_snapshots, len(rtg_snaps))
                    cur_window = rtg_snaps[start_idx:end_i]
                    cur_wait = sum(w for _, w, _ in cur_window)
                    if cur_wait >= threshold:
                        peak_wait = max(peak_wait, cur_wait)
                        i += 1
                    else:
                        break
                start_time = rtg_snaps[start_idx][0]
                end_time = rtg_snaps[i - 1][0] if i - 1 >= 0 else start_time
                duration = end_time - start_time
                self.events.append(BottleneckEvent(
                    event_type=BottleneckType.CUSTOM_RULE,
                    start_time=start_time,
                    end_time=end_time,
                    duration=duration,
                    rtg_ids=[rtg_id] if rtg_id else [],
                    peak_metric=float(peak_wait),
                    description=f"[自定义规则] {rule.rule_name}: 场桥{rtg_id}在窗口内等待次数达到{total_wait}次",
                    custom_rule_id=rule.rule_id,
                    custom_rule_name=rule.rule_name,
                    severity=rule.severity,
                ))
            else:
                i += 1

    def _detect_rule_gate_fluctuation(self, rule: CustomRule):
        threshold = max(1, rule.fluctuation_threshold)
        gate_queues: List[Tuple[float, int]] = [(s.timestamp, s.gate_queue_total) for s in self.snapshots]
        i = 0
        min_snapshots = max(2, rule.consecutive_snapshots)
        while i < len(gate_queues):
            if i + min_snapshots <= len(gate_queues):
                window = gate_queues[i:i + min_snapshots]
                q_values = [q for _, q in window]
                fluctuation = max(q_values) - min(q_values)
                if fluctuation >= threshold:
                    start_idx = i
                    end_idx = i + min_snapshots - 1
                    peak_fluc = fluctuation
                    while i + min_snapshots <= len(gate_queues):
                        cur_window = gate_queues[start_idx:i + min_snapshots]
                        cur_q = [q for _, q in cur_window]
                        cur_fluc = max(cur_q) - min(cur_q)
                        if cur_fluc >= threshold:
                            peak_fluc = max(peak_fluc, cur_fluc)
                            end_idx = i + min_snapshots - 1
                            i += 1
                        else:
                            break
                    start_time = gate_queues[start_idx][0]
                    end_time = gate_queues[end_idx][0]
                    duration = end_time - start_time
                    self.events.append(BottleneckEvent(
                        event_type=BottleneckType.CUSTOM_RULE,
                        start_time=start_time,
                        end_time=end_time,
                        duration=duration,
                        peak_metric=float(peak_fluc),
                        description=f"[自定义规则] {rule.rule_name}: 闸口总排队长度波动幅度达到{peak_fluc}辆",
                        custom_rule_id=rule.rule_id,
                        custom_rule_name=rule.rule_name,
                        severity=rule.severity,
                    ))
                else:
                    i += 1
            else:
                i += 1

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

    def _get_bottleneck_prefix(self, timestamp: float, events: List[BottleneckEvent]) -> str:
        for e in events:
            if e.start_time <= timestamp <= e.end_time:
                if e.event_type == BottleneckType.CONGESTION:
                    return "🔴"
                elif e.event_type == BottleneckType.RTG_CONFLICT:
                    return "🟠"
                elif e.event_type == BottleneckType.GATE_SATURATION:
                    return "🟡"
                elif e.event_type == BottleneckType.CUSTOM_RULE:
                    return "🟣"
        return "  "

    def create_heatmap_animation(self, snapshots: List[YardSnapshot], zone: ZoneType,
                                 bottleneck_events: List[BottleneckEvent] = None,
                                 playback_speed: int = 1,
                                 total_duration: float = None,
                                 start_frame: int = 0) -> go.Figure:
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

        if total_duration is None:
            total_duration = snapshots[-1].timestamp if snapshots else 10080.0

        bottleneck_events = bottleneck_events or []

        frame_duration_ms = int(500 / playback_speed)
        transition_ms = int(200 / playback_speed)

        start_frame = max(0, min(start_frame, len(snapshots) - 1))
        first_snap = snapshots[start_frame]

        color_map = {
            BottleneckType.CONGESTION: "#e74c3c",
            BottleneckType.RTG_CONFLICT: "#f39c12",
            BottleneckType.GATE_SATURATION: "#f1c40f",
            BottleneckType.CUSTOM_RULE: "#8e44ad",
        }
        label_map = {
            BottleneckType.CONGESTION: "拥堵",
            BottleneckType.RTG_CONFLICT: "场桥冲突",
            BottleneckType.GATE_SATURATION: "闸口饱和",
            BottleneckType.CUSTOM_RULE: "自定义规则",
        }

        fig = go.Figure()

        x_labels = [f"Bay {b}" for b in range(num_bays)]
        y_labels = [f"Row {r}" for r in range(num_rows)]

        zone_slots_0 = first_snap.slot_snapshots.get(zone, [])
        heatmap_z_0 = [[0] * num_bays for _ in range(num_rows)]
        hover_texts_0 = [[""] * num_bays for _ in range(num_rows)]

        for slot in zone_slots_0:
            if slot.stack_height > heatmap_z_0[slot.row][slot.bay]:
                heatmap_z_0[slot.row][slot.bay] = slot.stack_height

        for slot in zone_slots_0:
            if slot.tier == slot.stack_height - 1 and not slot.is_empty:
                info = (f"Bay: {slot.bay}<br>Row: {slot.row}<br>"
                        f"Height: {slot.stack_height}/{num_tiers}<br>"
                        f"Top: {slot.container_id or '空'}<br>"
                        f"Weight: {slot.weight_class or 'N/A'}")
                hover_texts_0[slot.row][slot.bay] = info

        fig.add_trace(
            go.Heatmap(
                z=heatmap_z_0,
                x=x_labels,
                y=y_labels,
                text=hover_texts_0,
                hoverinfo="text",
                colorscale="Blues",
                zmin=0,
                zmax=num_tiers,
                colorbar=dict(
                    title="堆高层数",
                    x=1.02,
                    y=0.55,
                    len=0.65,
                ),
                showscale=True,
            ),
        )

        zone_rtgs_0 = first_snap.rtg_snapshots.get(zone, [])
        for idx, rtg in enumerate(zone_rtgs_0):
            color = self.rtg_colors[idx % len(self.rtg_colors)]
            status_symbol, status_label = self._rtg_marker_info(rtg.status)
            fig.add_trace(
                go.Scatter(
                    x=[f"Bay {rtg.current_bay}"],
                    y=[f"Row {num_rows // 2}"],
                    mode="markers+text",
                    marker=dict(
                        symbol=status_symbol,
                        size=22,
                        color=color,
                        line=dict(width=2, color="black"),
                    ),
                    text=[f"🏗{idx + 1}"],
                    textposition="top center",
                    name=f"场桥{idx + 1}",
                    hovertext=f"{rtg.rtg_id}<br>位置: Bay {rtg.current_bay}<br>状态: {status_label}",
                    hoverinfo="text",
                    showlegend=True,
                    legendgroup="rtgs",
                ),
            )

        max_num_rtgs = max(len(s.rtg_snapshots.get(zone, [])) for s in snapshots)

        frames = []
        for snap_idx, snap in enumerate(snapshots):
            zone_slots = snap.slot_snapshots.get(zone, [])
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

            frame_traces = [
                go.Heatmap(
                    z=heatmap_z,
                    x=x_labels,
                    y=y_labels,
                    text=hover_texts,
                    hoverinfo="text",
                    colorscale="Blues",
                    zmin=0,
                    zmax=num_tiers,
                ),
            ]

            zone_rtgs = snap.rtg_snapshots.get(zone, [])
            for idx in range(max_num_rtgs):
                if idx < len(zone_rtgs):
                    rtg = zone_rtgs[idx]
                    color = self.rtg_colors[idx % len(self.rtg_colors)]
                    status_symbol, status_label = self._rtg_marker_info(rtg.status)
                    frame_traces.append(go.Scatter(
                        x=[f"Bay {rtg.current_bay}"],
                        y=[f"Row {num_rows // 2}"],
                        mode="markers+text",
                        marker=dict(
                            symbol=status_symbol,
                            size=22,
                            color=color,
                            line=dict(width=2, color="black"),
                        ),
                        text=[f"🏗{idx + 1}"],
                        textposition="top center",
                        hovertext=f"{rtg.rtg_id}<br>位置: Bay {rtg.current_bay}<br>状态: {status_label}",
                        hoverinfo="text",
                    ))
                else:
                    frame_traces.append(go.Scatter(
                        x=[None], y=[None], mode="markers",
                        hoverinfo="none", showlegend=False,
                    ))

            frames.append(go.Frame(
                data=frame_traces,
                name=f"frame_{snap_idx}",
                layout=go.Layout(
                    title=(f"{zone.value.capitalize()}区 热力动态回放 — "
                           f"时刻: {_format_time(snap.timestamp)} | "
                           f"利用率: {snap.zone_utilization.get(zone, 0):.1%} | "
                           f"闸口排队: {snap.gate_queue_total}辆 | "
                           f"场桥作业: {len([r for r in snap.rtg_snapshots.get(zone, []) if r.status == RTGStatus.WORKING])}"),
                    annotations=self._build_bottleneck_annotations(bottleneck_events, snap.timestamp),
                ),
            ))

        fig.frames = frames

        slider_steps = []
        for snap_idx, snap in enumerate(snapshots):
            prefix = self._get_bottleneck_prefix(snap.timestamp, bottleneck_events)
            time_str = _format_time(snap.timestamp)
            slider_steps.append(
                dict(
                    method="animate",
                    label=f"{prefix} {time_str}",
                    args=[
                        [f"frame_{snap_idx}"],
                        dict(
                            frame=dict(duration=0, redraw=True),
                            mode="immediate",
                            transition=dict(duration=0),
                        ),
                    ],
                )
            )

        fig.update_layout(
            height=620,
            title=f"{zone.value.capitalize()}区 热力动态回放  "
                  f"(🔴=拥堵  🟠=场桥冲突  🟡=闸口饱和预警  🟣=自定义规则  ⬜=正常)",
            xaxis_title="贝位 (Bay)",
            yaxis_title="排 (Row)",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
            ),
            annotations=self._build_bottleneck_annotations(bottleneck_events, first_snap.timestamp),
            updatemenus=[
                dict(
                    type="buttons",
                    showactive=True,
                    active=0 if start_frame else -1,
                    y=-0.18,
                    x=0.0,
                    xanchor="left",
                    yanchor="top",
                    direction="left",
                    pad=dict(t=0, r=10, l=0, b=0),
                    buttons=[
                        dict(
                            label="⏮ 第一帧",
                            method="animate",
                            args=[
                                [f"frame_0"],
                                dict(
                                    frame=dict(duration=0, redraw=True),
                                    mode="immediate",
                                    transition=dict(duration=0),
                                ),
                            ],
                        ),
                        dict(
                            label="◀ 上一帧",
                            method="animate",
                            args=[
                                [f"frame_{max(0, start_frame - 1)}"],
                                dict(
                                    frame=dict(duration=0, redraw=True),
                                    mode="immediate",
                                    transition=dict(duration=0),
                                ),
                            ],
                        ),
                        dict(
                            label="▶ 播放",
                            method="animate",
                            args=[
                                None,
                                dict(
                                    frame=dict(duration=frame_duration_ms, redraw=True),
                                    fromcurrent=True,
                                    transition=dict(duration=transition_ms, easing="cubic-in-out"),
                                    mode="immediate",
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
                        dict(
                            label="▶ 下一帧",
                            method="animate",
                            args=[
                                [f"frame_{min(len(snapshots) - 1, start_frame + 1)}"],
                                dict(
                                    frame=dict(duration=0, redraw=True),
                                    mode="immediate",
                                    transition=dict(duration=0),
                                ),
                            ],
                        ),
                        dict(
                            label="⏭ 最后一帧",
                            method="animate",
                            args=[
                                [f"frame_{len(snapshots) - 1}"],
                                dict(
                                    frame=dict(duration=0, redraw=True),
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
                    active=start_frame,
                    yanchor="top",
                    xanchor="left",
                    currentvalue=dict(
                        font=dict(size=14, color="#2c3e50"),
                        prefix="⏱ 当前时刻: ",
                        suffix="",
                        visible=True,
                        xanchor="right",
                    ),
                    transition=dict(duration=transition_ms, easing="cubic-in-out"),
                    pad=dict(b=10, t=40, l=0, r=0),
                    len=1.0,
                    x=0.0,
                    y=-0.05,
                    steps=slider_steps,
                ),
            ],
        )

        return fig

    def _build_bottleneck_annotations(self, events: List[BottleneckEvent],
                                       current_time: float) -> List[Dict]:
        if not events:
            return []
        color_map = {
            BottleneckType.CONGESTION: "#e74c3c",
            BottleneckType.RTG_CONFLICT: "#f39c12",
            BottleneckType.GATE_SATURATION: "#f1c40f",
            BottleneckType.CUSTOM_RULE: "#8e44ad",
        }
        anns = []
        y_positions = {
            BottleneckType.CONGESTION: 0.02,
            BottleneckType.RTG_CONFLICT: 0.06,
            BottleneckType.GATE_SATURATION: 0.10,
            BottleneckType.CUSTOM_RULE: 0.14,
        }
        active_labels = []
        for e in events:
            if e.start_time <= current_time <= e.end_time:
                c = color_map.get(e.event_type, "#95a5a6")
                if e.event_type == BottleneckType.CONGESTION:
                    active_labels.append(f"🔴 拥堵")
                elif e.event_type == BottleneckType.RTG_CONFLICT:
                    active_labels.append(f"🟠 场桥冲突")
                elif e.event_type == BottleneckType.GATE_SATURATION:
                    active_labels.append(f"🟡 闸口饱和")
                elif e.event_type == BottleneckType.CUSTOM_RULE:
                    rn = e.custom_rule_name or "自定义规则"
                    active_labels.append(f"🟣 {rn}")
        if active_labels:
            anns.append(dict(
                x=0.01, y=0.98,
                xref="paper", yref="paper",
                text=" | ".join(active_labels),
                showarrow=False,
                font=dict(size=13, color="white", family="Arial Black"),
                bgcolor="#2c3e50",
                bordercolor="#34495e",
                borderwidth=1,
                borderpad=4,
                xanchor="left",
                yanchor="top",
            ))
        return anns

    def _rtg_marker_info(self, status: RTGStatus) -> Tuple[str, str]:
        if status == RTGStatus.WORKING:
            return "diamond", "作业中"
        elif status == RTGStatus.WAITING:
            return "x", "等待让路"
        else:
            return "circle", "空闲"

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
            BottleneckType.CUSTOM_RULE: "#8e44ad",
        }
        label_map = {
            BottleneckType.CONGESTION: "拥堵",
            BottleneckType.RTG_CONFLICT: "场桥冲突",
            BottleneckType.GATE_SATURATION: "闸口饱和预警",
            BottleneckType.CUSTOM_RULE: "自定义规则",
        }

        fig = go.Figure()

        y_positions = {
            BottleneckType.CONGESTION: 3,
            BottleneckType.RTG_CONFLICT: 2,
            BottleneckType.GATE_SATURATION: 1,
            BottleneckType.CUSTOM_RULE: 0,
        }

        for event in events:
            y = y_positions.get(event.event_type, 1)
            color = color_map.get(event.event_type, "#95a5a6")
            label = label_map.get(event.event_type, event.event_type.value)
            if event.event_type == BottleneckType.CUSTOM_RULE and event.custom_rule_name:
                label = event.custom_rule_name

            hover = f"{event.description}<br>起止: {_format_time(event.start_time)} ~ {_format_time(event.end_time)}<br>时长: {_format_time(event.duration)}<br>峰值: {event.peak_metric:.2f}"
            if event.severity:
                sev_map = {SeverityLevel.HIGH: "高", SeverityLevel.MEDIUM: "中", SeverityLevel.LOW: "低"}
                hover += f"<br>严重等级: {sev_map.get(event.severity, event.severity.value)}"

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
            title="瓶颈事件时间轴 (红=拥堵, 橙=冲突, 黄=饱和预警, 紫=自定义规则)",
            xaxis=dict(
                title="时间 (分钟)",
                range=[0, total_duration],
            ),
            yaxis=dict(
                tickmode="array",
                tickvals=[3, 2, 1, 0],
                ticktext=["堆场拥堵", "场桥冲突", "闸口饱和", "自定义规则"],
                range=[-0.8, 3.8],
            ),
            height=280,
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
        custom_events = [e for e in self.events if e.event_type == BottleneckType.CUSTOM_RULE]

        lines = []
        lines.append("## 📋 瓶颈诊断报告")
        lines.append("")
        lines.append(f"本次仿真共检测到 **{len(self.events)}** 个瓶颈事件：")
        lines.append(f"- 🔴 堆场拥堵事件: **{len(congestion_events)}** 个")
        lines.append(f"- 🟠 场桥冲突事件: **{len(conflict_events)}** 个")
        lines.append(f"- 🟡 闸口饱和预警: **{len(saturation_events)}** 个")
        lines.append(f"- 🟣 自定义规则触发: **{len(custom_events)}** 个")
        lines.append("")

        if not self.events:
            lines.append("### ✅ 仿真运行良好")
            lines.append("本次仿真未检测到显著的瓶颈事件，当前配置下堆场运行较为顺畅。")
            return "\n".join(lines), self._generate_suggestions(congestion_events, conflict_events, saturation_events, custom_events)

        lines.append("---")
        lines.append("### 🕒 按时间排序的瓶颈事件详情")
        lines.append("")

        for idx, event in enumerate(self.events, 1):
            icon_map = {
                BottleneckType.CONGESTION: "🔴",
                BottleneckType.RTG_CONFLICT: "🟠",
                BottleneckType.GATE_SATURATION: "🟡",
                BottleneckType.CUSTOM_RULE: "🟣",
            }
            type_map = {
                BottleneckType.CONGESTION: "堆场拥堵",
                BottleneckType.RTG_CONFLICT: "场桥冲突",
                BottleneckType.GATE_SATURATION: "闸口饱和预警",
                BottleneckType.CUSTOM_RULE: "自定义规则",
            }
            icon = icon_map.get(event.event_type, "⚪")
            type_name = type_map.get(event.event_type, event.event_type.value)
            if event.event_type == BottleneckType.CUSTOM_RULE and event.custom_rule_name:
                type_name = f"自定义规则: {event.custom_rule_name}"

            lines.append(f"#### {idx}. {icon} {type_name}")
            lines.append(f"- **起止时间**: 第{_format_time(event.start_time)} ~ 第{_format_time(event.end_time)}")
            lines.append(f"- **持续时长**: {_format_time(event.duration)}")

            if event.severity:
                sev_map = {SeverityLevel.HIGH: "高", SeverityLevel.MEDIUM: "中", SeverityLevel.LOW: "低"}
                lines.append(f"- **严重等级**: {sev_map.get(event.severity, event.severity.value)}")

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

        suggestions = self._generate_suggestions(congestion_events, conflict_events, saturation_events, custom_events)
        return "\n".join(lines), suggestions

    def _generate_suggestions(self, congestion_events, conflict_events, saturation_events, custom_events=None) -> List[str]:
        suggestions = []
        custom_events = custom_events or []

        total_congestion_dur = sum(e.duration for e in congestion_events)
        total_conflict_dur = sum(e.duration for e in conflict_events)
        total_saturation_dur = sum(e.duration for e in saturation_events)
        total_custom_dur = sum(e.duration for e in custom_events)

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

        if total_custom_dur > 0:
            high_sev = [e for e in custom_events if e.severity == SeverityLevel.HIGH]
            med_sev = [e for e in custom_events if e.severity == SeverityLevel.MEDIUM]
            suggestions.append(
                f"🟣 **自定义规则告警**: 共触发{len(custom_events)}次自定义规则（高优先级{len(high_sev)}次，中优先级{len(med_sev)}次）。"
                f"请根据规则定义逐一核查并制定改进措施。"
            )
            rule_names = list(dict.fromkeys([e.custom_rule_name or e.custom_rule_id for e in custom_events]))
            if rule_names:
                suggestions.append(f"   涉及规则: {'；'.join(rule_names)}。")

        if not suggestions:
            suggestions.append("✅ 当前配置下仿真运行状况良好，无需特别优化。")
            suggestions.append("💡 如需进一步提升，可考虑：微调堆垛策略、在高峰时段动态增开场桥作业。")

        return suggestions


@dataclass
class StructuralBottleneck:
    zone: Optional[ZoneType]
    event_type: BottleneckType
    custom_rule_id: Optional[str]
    custom_rule_name: Optional[str]
    typical_start: float
    typical_end: float
    frequency: int
    total_simulations: int
    avg_duration: float
    severity: Optional[SeverityLevel] = None


class BottleneckPatternAnalyzer:
    def __init__(self, history: List[HistoricalSimulationRecord], time_window_tolerance: float = 30.0,
                 min_occurrences: int = 3):
        self.history = history
        self.time_window_tolerance = time_window_tolerance
        self.min_occurrences = max(2, min_occurrences)
        self.zone_cn = {"import": "进口区", "export": "出口区", "transit": "中转区"}

    def _event_key(self, event: BottleneckEvent) -> Tuple:
        zone_val = event.zone.value if event.zone else None
        if event.event_type == BottleneckType.CUSTOM_RULE:
            return (zone_val, BottleneckType.CUSTOM_RULE.value, event.custom_rule_id or event.custom_rule_name)
        return (zone_val, event.event_type.value, None)

    def analyze(self) -> List[StructuralBottleneck]:
        if len(self.history) < self.min_occurrences:
            return []

        all_occurrences: Dict[Tuple, List[Tuple[int, BottleneckEvent]]] = {}
        for sim_idx, record in enumerate(self.history):
            for event in record.bottleneck_events:
                key = self._event_key(event)
                if key not in all_occurrences:
                    all_occurrences[key] = []
                all_occurrences[key].append((sim_idx, event))

        structural: List[StructuralBottleneck] = []
        total_sims = len(self.history)

        for key, occurrences in all_occurrences.items():
            if len(occurrences) < self.min_occurrences:
                continue

            unique_sims = list(set(sim_idx for sim_idx, _ in occurrences))
            if len(unique_sims) < self.min_occurrences:
                continue

            occurrences.sort(key=lambda x: x[1].start_time)

            clusters: List[List[BottleneckEvent]] = []
            for sim_idx, event in occurrences:
                placed = False
                for cluster in clusters:
                    cluster_centers = [e.start_time for e in cluster]
                    cluster_center = sum(cluster_centers) / len(cluster_centers)
                    if abs(event.start_time - cluster_center) <= self.time_window_tolerance:
                        cluster.append(event)
                        placed = True
                        break
                if not placed:
                    clusters.append([event])

            for cluster in clusters:
                cluster_sims = set()
                sim_to_event = {}
                for ev in cluster:
                    for sim_idx, occ_ev in occurrences:
                        if occ_ev is ev:
                            cluster_sims.add(sim_idx)
                            sim_to_event[sim_idx] = ev
                            break

                if len(cluster_sims) < self.min_occurrences:
                    continue

                freq = len(cluster_sims)
                avg_start = sum(e.start_time for e in cluster) / len(cluster)
                avg_end = sum(e.end_time for e in cluster) / len(cluster)
                avg_duration = sum(e.duration for e in cluster) / len(cluster)
                zone = cluster[0].zone
                ev_type = cluster[0].event_type
                severity = cluster[0].severity
                custom_rule_id = cluster[0].custom_rule_id
                custom_rule_name = cluster[0].custom_rule_name

                structural.append(StructuralBottleneck(
                    zone=zone,
                    event_type=ev_type,
                    custom_rule_id=custom_rule_id,
                    custom_rule_name=custom_rule_name,
                    typical_start=avg_start,
                    typical_end=avg_end,
                    frequency=freq,
                    total_simulations=total_sims,
                    avg_duration=avg_duration,
                    severity=severity,
                ))

        structural.sort(key=lambda x: (-x.frequency, x.typical_start))
        return structural

    def generate_suggestion(self, sb: StructuralBottleneck) -> str:
        zone_name = self.zone_cn.get(sb.zone.value, sb.zone.value) if sb.zone else "堆场"
        start_str = _format_time(sb.typical_start)
        end_str = _format_time(sb.typical_end)
        freq_ratio = f"{sb.frequency}/{sb.total_simulations}"

        type_cn = {
            BottleneckType.CONGESTION: "拥堵",
            BottleneckType.RTG_CONFLICT: "场桥冲突",
            BottleneckType.GATE_SATURATION: "闸口饱和预警",
            BottleneckType.CUSTOM_RULE: f"自定义规则[{sb.custom_rule_name or sb.custom_rule_id}]",
        }

        type_name = type_cn.get(sb.event_type, sb.event_type.value)

        base = f"{zone_name}在仿真第{start_str}-{end_str}时段反复出现{type_name}（{freq_ratio}次仿真均出现），属于结构性瓶颈。"

        if sb.event_type == BottleneckType.CONGESTION:
            return (f"📦 {base}"
                    f"建议在该时段增派临时场桥或提前疏散库存，或考虑增加贝位数扩容。")
        elif sb.event_type == BottleneckType.RTG_CONFLICT:
            return (f"🏗️ {base}"
                    f"建议优化场桥工作范围划分，避免在高峰时段让多台场桥集中于同一区域作业。")
        elif sb.event_type == BottleneckType.GATE_SATURATION:
            return (f"🚛 {base}"
                    f"建议在该时段临时增开闸口车道，或通过预约系统分散集卡到达时间。")
        elif sb.event_type == BottleneckType.CUSTOM_RULE:
            sev_str = ""
            if sb.severity:
                sev_map = {SeverityLevel.HIGH: "高优先级", SeverityLevel.MEDIUM: "中优先级", SeverityLevel.LOW: "低优先级"}
                sev_str = f"[{sev_map.get(sb.severity, sb.severity.value)}]"
            return (f"🟣 {sev_str} {base}"
                    f"请根据该自定义规则的业务含义，制定针对性优化措施。")
        else:
            return f"⚠️ {base}请根据实际情况评估优化方案。"


def find_nearest_snapshot(snapshots: List[YardSnapshot], target_time: float) -> int:
    if not snapshots:
        return 0
    best_idx = 0
    best_diff = abs(snapshots[0].timestamp - target_time)
    for i, s in enumerate(snapshots):
        diff = abs(s.timestamp - target_time)
        if diff < best_diff:
            best_diff = diff
            best_idx = i
    return best_idx
