import simpy
import random
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set
from enum import Enum

from .yard_model import Yard, Container, ZoneType, ContainerSize
from .stacking_strategies import StackingStrategy, create_strategy, BaseStackingStrategy
from .container_generator import ContainerGenerator
from .relocation import RelocationPlanner
from .rtg_scheduler import RTGScheduler, RTG, RTGTask, TaskType


class EventType(Enum):
    SHIP_ARRIVAL = "ship_arrival"
    SHIP_DEPARTURE = "ship_departure"
    TRUCK_ARRIVAL_PICKUP = "truck_arrival_pickup"
    TRUCK_ARRIVAL_STOW = "truck_arrival_stow"
    RTG_COMPLETE = "rtg_complete"


@dataclass
class SimulationStats:
    total_containers_stowed: int = 0
    total_containers_picked: int = 0
    total_relocations: int = 0
    total_pickup_operations: int = 0
    total_stow_operations: int = 0
    utilization_history: List[Tuple[float, float]] = field(default_factory=list)
    throughput_history: List[Tuple[float, int]] = field(default_factory=list)
    pickup_wait_times: List[float] = field(default_factory=list)
    rtg_task_log: List[Dict] = field(default_factory=list)
    ship_log: List[Dict] = field(default_factory=list)
    truck_log: List[Dict] = field(default_factory=list)
    containers_in_yard: List[Tuple[float, int]] = field(default_factory=list)
    import_containers_arrived: int = 0
    export_containers_arrived: int = 0
    daily_throughput: List[Tuple[float, int]] = field(default_factory=list)


class SimulationEngine:
    def __init__(self, config: Dict, strategy: StackingStrategy, strategy_params: Optional[Dict] = None):
        self.config = config
        self.strategy_type = strategy
        self.strategy_params = strategy_params or {}

        self.yard = Yard(config["yard"])
        self.stacking_strategy: BaseStackingStrategy = create_strategy(
            strategy, **self.strategy_params
        )
        self.relocation_planner = RelocationPlanner(
            stacking_strategy=self.stacking_strategy
        )
        self.rtg_scheduler = RTGScheduler(
            lift_time=config.get("rtg", {}).get("lift_time", 30.0),
            lower_time=config.get("rtg", {}).get("lower_time", 30.0),
            travel_time_per_bay=config.get("rtg", {}).get("travel_time_per_bay", 15.0),
        )
        seed = config.get("simulation", {}).get("seed", 42)
        self.container_gen = ContainerGenerator(seed=seed)

        self.env = simpy.Environment()
        self.stats = SimulationStats()
        self.sim_duration = config.get("simulation", {}).get("duration", 10080)

        self.rtgs: Dict[ZoneType, List[RTG]] = {}
        self.rtg_resources: Dict[ZoneType, List[simpy.Resource]] = {}
        self._init_rtgs()

        self.pending_tasks: Dict[ZoneType, List[RTGTask]] = {
            zone: [] for zone in ZoneType
        }

        self._container_lookup: Dict[str, Container] = {}
        self._task_container_map: Dict[str, Container] = {}

        self._stowed_containers: Set[str] = set()
        self._picked_containers: Set[str] = set()
        self._pending_pickup: Set[str] = set()

        self._ship_rng = random.Random(seed + 1000)
        self._export_rng = random.Random(seed + 3000)
        self._np_rng = np.random.RandomState(seed + 2000)

    def _init_rtgs(self):
        rtg_config = self.config.get("rtg", {})
        for zone in ZoneType:
            count = rtg_config.get("num_rtgs", {}).get(zone.value, 1)
            zone_data = self.yard.zones.get(zone)
            num_bays = zone_data["num_bays"] if zone_data else 10

            rtgs = []
            resources = []
            for i in range(count):
                start_bay = (num_bays // (count + 1)) * (i + 1) if count > 0 else 0
                rtg = self.rtg_scheduler.create_rtg(
                    rtg_id=f"{zone.value}_rtg_{i}",
                    zone=zone,
                    start_bay=start_bay,
                )
                rtgs.append(rtg)
                resources.append(simpy.Resource(self.env, capacity=1))

            self.rtgs[zone] = rtgs
            self.rtg_resources[zone] = resources

    def _rtg_time(self, seconds: float) -> float:
        return seconds / 60.0

    def run(self, sim_duration_minutes: float = 10080.0):
        self.sim_duration = sim_duration_minutes

        self.env.process(self._ship_arrival_process())
        self.env.process(self._import_truck_pickup_process())
        self.env.process(self._export_truck_arrival_process())
        self.env.process(self._utilization_monitor())
        self.env.process(self._rtg_dispatcher())

        self.env.run(until=sim_duration_minutes)
        return self.stats

    def _ship_arrival_process(self):
        sim_config = self.config.get("simulation", {})
        ship_interval_mean = sim_config.get("ship_interval_mean", 720.0)
        ship_size_mean = sim_config.get("ship_size_mean", 80)
        ship_size_std = sim_config.get("ship_size_std", 20)

        ship_idx = 0

        while True:
            inter_arrival = self._ship_rng.expovariate(1.0 / ship_interval_mean)
            yield self.env.timeout(inter_arrival)

            if self.env.now >= self.sim_duration:
                break

            ship_idx += 1
            ship_name = f"SHIP_{ship_idx:03d}"
            num_containers = max(
                10,
                int(self._np_rng.normal(ship_size_mean, ship_size_std)),
            )

            ship_berth_hours = self._ship_rng.uniform(10, 24)
            ship_departure_time = self.env.now + ship_berth_hours * 60.0

            self.stats.ship_log.append(
                {
                    "time": self.env.now,
                    "ship_name": ship_name,
                    "num_containers": num_containers,
                    "event": "arrival",
                }
            )

            containers = self.container_gen.generate_ship_containers(
                ship_name=ship_name,
                num_containers=num_containers,
                arrival_time=self.env.now,
                is_import=True,
                sim_duration=self.sim_duration,
            )

            self.stats.import_containers_arrived += len(containers)

            transit_ratio = 0.15
            for container in containers:
                self._container_lookup[container.container_id] = container
                if self._ship_rng.random() < transit_ratio:
                    self._add_stow_task(container, ZoneType.TRANSIT)
                else:
                    self._add_stow_task(container, ZoneType.IMPORT)

            num_export = max(5, int(num_containers * 0.6))
            export_containers = self.container_gen.generate_export_containers_for_ship(
                ship_name=ship_name,
                num_containers=num_export,
                ship_departure_time=ship_departure_time,
                sim_duration=self.sim_duration,
            )

            self.stats.export_containers_arrived += len(export_containers)

            for container in export_containers:
                self._container_lookup[container.container_id] = container
                self.env.process(
                    self._export_truck_delivery_process(container)
                )

            self.env.process(
                self._ship_departure_process(ship_name, ship_departure_time)
            )

    def _ship_departure_process(self, ship_name: str, departure_time: float):
        delay = departure_time - self.env.now
        if delay > 0:
            yield self.env.timeout(delay)

        self.stats.ship_log.append(
            {
                "time": self.env.now,
                "ship_name": ship_name,
                "event": "departure",
            }
        )

    def _export_truck_delivery_process(self, container: Container):
        truck_arrival_delay = max(0, container.arrival_time - self.env.now)
        if truck_arrival_delay > 0:
            yield self.env.timeout(truck_arrival_delay)

        if self.env.now >= self.sim_duration:
            return

        self._add_stow_task(container, ZoneType.EXPORT)

        self.stats.truck_log.append(
            {
                "time": self.env.now,
                "container_id": container.container_id,
                "event": "truck_arrives_with_export",
                "zone": "export",
            }
        )

    def _import_truck_pickup_process(self):
        check_interval = 5.0

        while True:
            yield self.env.timeout(check_interval)

            if self.env.now >= self.sim_duration:
                break

            for container_id in list(self._stowed_containers):
                if container_id in self._picked_containers:
                    continue
                if container_id in self._pending_pickup:
                    continue

                container = self._container_lookup.get(container_id)
                if not container:
                    continue

                pickup_start = container.pickup_time_window[0]
                pickup_end = container.pickup_time_window[1]

                if self.env.now < pickup_start:
                    continue
                if self.env.now > pickup_end + 120:
                    self._pending_pickup.add(container_id)
                    continue

                slot = self.yard.find_container(container)
                if not slot:
                    continue

                task_id = f"pickup_{container.container_id}"
                already_pending = any(
                    t.task_id == task_id
                    for zone_tasks in self.pending_tasks.values()
                    for t in zone_tasks
                )

                if already_pending:
                    continue

                self._pending_pickup.add(container_id)

                pickup_task = RTGTask(
                    task_id=task_id,
                    task_type=TaskType.PICKUP,
                    zone=slot.zone,
                    bay=slot.bay,
                    container_id=container.container_id,
                    arrival_time=self.env.now,
                    priority=2,
                )
                self._task_container_map[task_id] = container
                self.pending_tasks[slot.zone].append(pickup_task)

                self.stats.truck_log.append(
                    {
                        "time": self.env.now,
                        "container_id": container.container_id,
                        "event": "truck_arrives_for_pickup",
                        "zone": slot.zone.value,
                    }
                )

    def _export_truck_arrival_process(self):
        sim_config = self.config.get("simulation", {})
        truck_interval = sim_config.get("export_truck_interval", 120.0)

        while True:
            inter_arrival = self._export_rng.expovariate(1.0 / truck_interval)
            yield self.env.timeout(inter_arrival)

            if self.env.now >= self.sim_duration:
                break

            container = self.container_gen.generate_random_container(
                is_import=False,
                arrival_time=self.env.now,
                sim_duration=self.sim_duration,
            )
            container.pickup_time_window = (
                self.env.now + self._export_rng.uniform(300, 1440),
                self.env.now + self._export_rng.uniform(600, 2160),
            )

            self._container_lookup[container.container_id] = container
            self.stats.export_containers_arrived += 1
            self._add_stow_task(container, ZoneType.EXPORT)

            self.stats.truck_log.append(
                {
                    "time": self.env.now,
                    "container_id": container.container_id,
                    "event": "truck_arrives_with_export",
                    "zone": "export",
                }
            )

    def _add_stow_task(self, container: Container, zone: ZoneType):
        task_id = f"stow_{container.container_id}"
        task = RTGTask(
            task_id=task_id,
            task_type=TaskType.STOW,
            zone=zone,
            bay=0,
            container_id=container.container_id,
            arrival_time=self.env.now,
            priority=1,
        )
        self._task_container_map[task_id] = container
        self.pending_tasks[zone].append(task)

    def _rtg_dispatcher(self):
        dispatch_interval = 0.5

        while True:
            any_dispatched = False

            for zone in ZoneType:
                if not self.pending_tasks[zone]:
                    continue

                rtgs = self.rtgs[zone]
                resources = self.rtg_resources[zone]

                for i, rtg in enumerate(rtgs):
                    if resources[i].count > 0:
                        continue

                    if not self.pending_tasks[zone]:
                        break

                    stow_tasks = [t for t in self.pending_tasks[zone] if t.task_type == TaskType.STOW]
                    pickup_tasks = [t for t in self.pending_tasks[zone] if t.task_type == TaskType.PICKUP]

                    pickup_tasks.sort(key=lambda t: t.arrival_time)
                    stow_tasks.sort(key=lambda t: t.arrival_time)

                    best_task = None
                    if pickup_tasks and stow_tasks:
                        current_bay = rtg.current_bay
                        pickup_dist = min(abs(t.bay - current_bay) for t in pickup_tasks)
                        stow_dist = min(abs(t.bay - current_bay) for t in stow_tasks) if stow_tasks else float('inf')

                        if pickup_dist <= stow_dist:
                            best_task = pickup_tasks[0]
                        else:
                            best_task = stow_tasks[0]
                    elif pickup_tasks:
                        best_task = pickup_tasks[0]
                    elif stow_tasks:
                        best_task = stow_tasks[0]

                    if best_task and best_task in self.pending_tasks[zone]:
                        self.pending_tasks[zone].remove(best_task)
                        self.env.process(
                            self._execute_rtg_task(rtg, best_task, resources[i])
                        )
                        any_dispatched = True

            if not any_dispatched:
                yield self.env.timeout(dispatch_interval)
            else:
                yield self.env.timeout(0.01)

    def _execute_rtg_task(self, rtg: RTG, task: RTGTask, resource: simpy.Resource):
        with resource.request() as req:
            yield req

            container = self._task_container_map.get(task.task_id)
            if not container:
                container = self._container_lookup.get(task.container_id)
            if not container:
                return

            start_time = self.env.now

            if task.task_type == TaskType.STOW:
                yield from self._execute_stow(rtg, task, container, start_time)

            elif task.task_type == TaskType.PICKUP:
                yield from self._execute_pickup(rtg, task, container, start_time)

    def _execute_stow(self, rtg: RTG, task: RTGTask, container: Container, start_time: float):
        position = self.stacking_strategy.find_slot(
            container, self.yard, task.zone
        )
        if not position:
            return

        bay, row = position
        task.bay = bay

        travel_min = self._rtg_time(abs(rtg.current_bay - bay) * self.rtg_scheduler.travel_time_per_bay)
        if travel_min > 0:
            yield self.env.timeout(travel_min)
            rtg.total_distance += abs(bay - rtg.current_bay)
            rtg.current_bay = bay

        op_min = self._rtg_time(self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time)
        yield self.env.timeout(op_min)

        result = self.yard.place_container(container, task.zone, bay, row)
        if result:
            self.stats.total_containers_stowed += 1
            self.stats.total_stow_operations += 1
            rtg.total_tasks_completed += 1
            rtg.total_working_time += op_min + travel_min
            self._stowed_containers.add(container.container_id)

            self.stats.rtg_task_log.append(
                {
                    "time": self.env.now,
                    "rtg_id": rtg.rtg_id,
                    "task_id": task.task_id,
                    "task_type": task.task_type.value,
                    "container_id": container.container_id,
                    "zone": task.zone.value,
                    "bay": bay,
                    "row": row,
                    "duration": self.env.now - start_time,
                }
            )

    def _execute_pickup(self, rtg: RTG, task: RTGTask, container: Container, start_time: float):
        slot = self.yard.find_container(container)
        if not slot:
            return

        target_bay = slot.bay

        travel_min = self._rtg_time(abs(rtg.current_bay - target_bay) * self.rtg_scheduler.travel_time_per_bay)
        if travel_min > 0:
            yield self.env.timeout(travel_min)
            rtg.total_distance += abs(target_bay - rtg.current_bay)
            rtg.current_bay = target_bay

        relocations_needed = self.relocation_planner.count_relocations_needed(
            container, self.yard
        )
        actual_relocations = 0

        if relocations_needed > 0:
            result = self.relocation_planner.plan_retrieval(container, self.yard)
            if result.success:
                for move in result.relocations:
                    move_time_min = self._rtg_time(
                        self.rtg_scheduler.lift_time
                        + self.rtg_scheduler.lower_time
                        + 15
                    )
                    yield self.env.timeout(move_time_min)
                    actual_relocations += 1
                    self.stats.total_relocations += 1
                    rtg.total_tasks_completed += 1
                    rtg.total_working_time += move_time_min

        op_min = self._rtg_time(self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time)
        yield self.env.timeout(op_min)

        self.yard.remove_container(container)
        self.stats.total_containers_picked += 1
        self.stats.total_pickup_operations += 1
        rtg.total_tasks_completed += 1
        rtg.total_working_time += op_min + travel_min

        self._picked_containers.add(container.container_id)

        wait_time = self.env.now - container.pickup_time_window[0]
        self.stats.pickup_wait_times.append(max(0, wait_time))

        self.stats.rtg_task_log.append(
            {
                "time": self.env.now,
                "rtg_id": rtg.rtg_id,
                "task_id": task.task_id,
                "task_type": task.task_type.value,
                "container_id": container.container_id,
                "zone": task.zone.value,
                "bay": target_bay,
                "relocations": actual_relocations,
                "duration": self.env.now - start_time,
            }
        )

        if container.container_id in self._stowed_containers:
            self._stowed_containers.discard(container.container_id)
        if container.container_id in self._pending_pickup:
            self._pending_pickup.discard(container.container_id)

    def _utilization_monitor(self):
        interval = 60.0
        while True:
            util = self.yard.get_utilization()
            self.stats.utilization_history.append((self.env.now, util))
            self.stats.containers_in_yard.append(
                (self.env.now, len(self.yard.get_all_containers()))
            )
            self.stats.throughput_history.append(
                (self.env.now, self.stats.total_containers_stowed + self.stats.total_containers_picked)
            )
            yield self.env.timeout(interval)
