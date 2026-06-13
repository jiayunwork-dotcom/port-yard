from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from enum import Enum

from .yard_model import ZoneType


class TaskType(Enum):
    PICKUP = "pickup"
    STOW = "stow"


@dataclass
class RTGTask:
    task_id: str
    task_type: TaskType
    zone: ZoneType
    bay: int
    container_id: str
    arrival_time: float = 0.0
    priority: int = 0


@dataclass
class RTG:
    rtg_id: str
    zone: ZoneType
    current_bay: int = 0
    is_busy: bool = False
    current_task: Optional[RTGTask] = None
    total_tasks_completed: int = 0
    total_working_time: float = 0.0
    total_idle_time: float = 0.0
    total_distance: float = 0.0
    schedule: List[Dict] = field(default_factory=list)


@dataclass
class RTGScheduleResult:
    total_distance: float
    total_time: float
    task_order: List[RTGTask]


class RTGScheduler:
    def __init__(
        self,
        lift_time: float = 30.0,
        lower_time: float = 30.0,
        travel_time_per_bay: float = 15.0,
        collision_avoidance: bool = True,
    ):
        self.lift_time = lift_time
        self.lower_time = lower_time
        self.travel_time_per_bay = travel_time_per_bay
        self.collision_avoidance = collision_avoidance

    def calculate_travel_time(self, from_bay: int, to_bay: int) -> float:
        return abs(from_bay - to_bay) * self.travel_time_per_bay

    def calculate_operation_time(self, from_bay: int, to_bay: int) -> float:
        return (
            self.lift_time
            + self.calculate_travel_time(from_bay, to_bay)
            + self.lower_time
        )

    def optimize_task_order(
        self,
        rtgs: List[RTG],
        tasks: List[RTGTask],
        zone: ZoneType,
        num_bays: int,
    ) -> Dict[str, List[RTGTask]]:
        if not tasks:
            return {rtg.rtg_id: [] for rtg in rtgs}

        zone_tasks = [t for t in tasks if t.zone == zone]
        if not zone_tasks:
            return {rtg.rtg_id: [] for rtg in rtgs}

        assignments = {rtg.rtg_id: [] for rtg in rtgs}

        if len(rtgs) == 1:
            ordered = self._nearest_neighbor_single(
                rtgs[0].current_bay, zone_tasks
            )
            assignments[rtgs[0].rtg_id] = ordered
            return assignments

        if self.collision_avoidance and len(rtgs) >= 2:
            left_rtg = min(rtgs, key=lambda r: r.current_bay)
            right_rtg = max(rtgs, key=lambda r: r.current_bay)

            mid_bay = num_bays // 2

            left_tasks = [t for t in zone_tasks if t.bay < mid_bay]
            right_tasks = [t for t in zone_tasks if t.bay >= mid_bay]

            left_ordered = self._nearest_neighbor_single(
                left_rtg.current_bay, left_tasks
            )
            right_ordered = self._nearest_neighbor_single(
                right_rtg.current_bay, right_tasks
            )

            assignments[left_rtg.rtg_id] = left_ordered
            assignments[right_rtg.rtg_id] = right_ordered

            return assignments
        else:
            for i, task in enumerate(zone_tasks):
                rtg_idx = i % len(rtgs)
                assignments[rtgs[rtg_idx].rtg_id].append(task)

            for rtg_id, assigned_tasks in assignments.items():
                rtg = next((r for r in rtgs if r.rtg_id == rtg_id), None)
                if rtg and assigned_tasks:
                    assignments[rtg_id] = self._nearest_neighbor_single(
                        rtg.current_bay, assigned_tasks
                    )

            return assignments

    def _nearest_neighbor_single(
        self, start_bay: int, tasks: List[RTGTask]
    ) -> List[RTGTask]:
        if not tasks:
            return []

        remaining = list(tasks)
        ordered = []
        current_bay = start_bay

        while remaining:
            nearest_idx = 0
            nearest_dist = abs(remaining[0].bay - current_bay)

            for i in range(1, len(remaining)):
                dist = abs(remaining[i].bay - current_bay)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_idx = i

            task = remaining.pop(nearest_idx)
            ordered.append(task)
            current_bay = task.bay

        return ordered

    def calculate_schedule_metrics(
        self, start_bay: int, tasks: List[RTGTask]
    ) -> RTGScheduleResult:
        if not tasks:
            return RTGScheduleResult(
                total_distance=0, total_time=0, task_order=[]
            )

        total_distance = 0
        total_time = 0
        current_bay = start_bay

        for task in tasks:
            distance = abs(task.bay - current_bay)
            travel_time = distance * self.travel_time_per_bay
            op_time = self.lift_time + travel_time + self.lower_time

            total_distance += distance
            total_time += op_time
            current_bay = task.bay

        return RTGScheduleResult(
            total_distance=total_distance,
            total_time=total_time,
            task_order=tasks,
        )

    def simulate_task_execution(
        self,
        rtg: RTG,
        tasks: List[RTGTask],
        start_time: float,
    ) -> List[Dict]:
        schedule = []
        current_time = start_time
        current_bay = rtg.current_bay

        for task in tasks:
            travel_time = abs(task.bay - current_bay) * self.travel_time_per_bay

            if travel_time > 0:
                schedule.append(
                    {
                        "type": "travel",
                        "start_time": current_time,
                        "end_time": current_time + travel_time,
                        "from_bay": current_bay,
                        "to_bay": task.bay,
                        "task_id": task.task_id,
                    }
                )
                current_time += travel_time
                rtg.total_distance += abs(task.bay - current_bay)

            op_time = self.lift_time + self.lower_time
            schedule.append(
                {
                    "type": "operate",
                    "start_time": current_time,
                    "end_time": current_time + op_time,
                    "bay": task.bay,
                    "task_type": task.task_type.value,
                    "container_id": task.container_id,
                    "task_id": task.task_id,
                }
            )
            current_time += op_time
            rtg.total_working_time += op_time

            current_bay = task.bay
            rtg.total_tasks_completed += 1

        rtg.current_bay = current_bay
        rtg.schedule.extend(schedule)

        return schedule

    def create_rtg(self, rtg_id: str, zone: ZoneType, start_bay: int = 0) -> RTG:
        return RTG(
            rtg_id=rtg_id,
            zone=zone,
            current_bay=start_bay,
        )
