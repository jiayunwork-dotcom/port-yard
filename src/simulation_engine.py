import simpy
import random
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
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
        self.container_gen = ContainerGenerator(
            seed=config.get("simulation", {}).get("seed", 42)
        )

        self.env = simpy.Environment()
        self.stats = SimulationStats()

        self.rtgs: Dict[ZoneType, List[RTG]] = {}
        self.rtg_resources: Dict[ZoneType, List[simpy.Resource]] = {}
        self._init_rtgs()

        self.pending_tasks: Dict[ZoneType, List[RTGTask]] = {
            zone: [] for zone in ZoneType
        }

        self._container_lookup: Dict[str, Container] = {}

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

    def run(self, sim_duration_minutes: float = 10080.0):
        self.env.process(self._ship_arrival_process())
        self.env.process(self._export_truck_arrival_process())
        self.env.process(self._utilization_monitor())
        self.env.process(self._rtg_dispatcher())

        self.env.run(until=sim_duration_minutes)
        return self.stats

    def _ship_arrival_process(self):
        sim_config = self.config.get("simulation", {})
        ship_interval_mean = sim_config.get("ship_interval_mean", 1440.0)
        ship_size_mean = sim_config.get("ship_size_mean", 100)
        ship_size_std = sim_config.get("ship_size_std", 20)
        seed = sim_config.get("seed", 42)

        random.seed(seed + 1000)
        ship_idx = 0

        while True:
            inter_arrival = random.expovariate(1.0 / ship_interval_mean)
            yield self.env.timeout(inter_arrival)

            ship_idx += 1
            ship_name = f"SHIP_{ship_idx:03d}"
            num_containers = max(
                10,
                int(np.random.normal(ship_size_mean, ship_size_std)),
            )

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
                sim_duration=self.config.get("simulation", {}).get("duration", 10080),
            )

            for container in containers:
                self._container_lookup[container.container_id] = container
                self.env.process(self._stow_container_process(container, ZoneType.IMPORT))

            export_containers = self.container_gen.generate_export_containers_for_ship(
                ship_name=ship_name,
                num_containers=max(5, num_containers // 2),
                ship_departure_time=self.env.now + random.uniform(600, 1200),
                sim_duration=self.config.get("simulation", {}).get("duration", 10080),
            )

            for container in export_containers:
                self._container_lookup[container.container_id] = container
                truck_arrival_time = container.arrival_time
                if truck_arrival_time > self.env.now:
                    yield self.env.timeout(truck_arrival_time - self.env.now)
                self.env.process(self._stow_container_process(container, ZoneType.EXPORT))

            departure_delay = random.uniform(600, 1200)
            yield self.env.timeout(departure_delay)

            self.env.process(self._ship_departure_process(ship_name, containers))

    def _export_truck_arrival_process(self):
        sim_config = self.config.get("simulation", {})
        truck_interval = sim_config.get("export_truck_interval", 60.0)
        seed = sim_config.get("seed", 42)

        random.seed(seed + 2000)

        while True:
            inter_arrival = random.expovariate(1.0 / truck_interval)
            yield self.env.timeout(inter_arrival)

            container = self.container_gen.generate_random_container(
                is_import=False,
                arrival_time=self.env.now,
                sim_duration=self.config.get("simulation", {}).get("duration", 10080),
            )
            container.pickup_time_window = (
                self.env.now + random.uniform(300, 1000),
                self.env.now + random.uniform(500, 1500),
            )

            self._container_lookup[container.container_id] = container
            self.env.process(self._stow_container_process(container, ZoneType.EXPORT))

    def _stow_container_process(self, container: Container, zone: ZoneType):
        task = RTGTask(
            task_id=f"stow_{container.container_id}",
            task_type=TaskType.STOW,
            zone=zone,
            bay=0,
            container_id=container.container_id,
            arrival_time=self.env.now,
            priority=1,
        )

        self.pending_tasks[zone].append(task)
        yield self.env.timeout(0)

    def _pickup_container_process(self, container: Container):
        slot = self.yard.find_container(container)
        if not slot:
            yield self.env.timeout(0)
            return

        zone = slot.zone
        task = RTGTask(
            task_id=f"pickup_{container.container_id}",
            task_type=TaskType.PICKUP,
            zone=zone,
            bay=slot.bay,
            container_id=container.container_id,
            arrival_time=self.env.now,
            priority=2,
        )

        self.pending_tasks[zone].append(task)
        self.stats.truck_log.append(
            {
                "time": self.env.now,
                "container_id": container.container_id,
                "event": "truck_arrives_for_pickup",
                "zone": zone.value,
            }
        )
        yield self.env.timeout(0)

    def _ship_departure_process(self, ship_name: str, import_containers: List[Container]):
        self.stats.ship_log.append(
            {
                "time": self.env.now,
                "ship_name": ship_name,
                "event": "departure",
            }
        )
        yield self.env.timeout(0)

    def _rtg_dispatcher(self):
        while True:
            for zone in ZoneType:
                if self.pending_tasks[zone]:
                    zone_tasks = sorted(
                        self.pending_tasks[zone],
                        key=lambda t: (-t.priority, t.arrival_time),
                    )

                    rtgs = self.rtgs[zone]
                    resources = self.rtg_resources[zone]

                    num_bays = self.yard.zones[zone]["num_bays"]
                    assignments = self.rtg_scheduler.optimize_task_order(
                        rtgs, zone_tasks, zone, num_bays
                    )

                    for i, rtg in enumerate(rtgs):
                        rtg_id = rtg.rtg_id
                        tasks = assignments.get(rtg_id, [])
                        if tasks and resources[i].count == 0:
                            task = tasks[0]
                            if task in self.pending_tasks[zone]:
                                self.pending_tasks[zone].remove(task)
                                self.env.process(
                                    self._execute_rtg_task(rtg, task, resources[i])
                                )

            yield self.env.timeout(1.0)

    def _execute_rtg_task(self, rtg: RTG, task: RTGTask, resource: simpy.Resource):
        with resource.request() as req:
            yield req

            container = self._container_lookup.get(task.container_id)
            if not container:
                return

            start_time = self.env.now

            if task.task_type == TaskType.STOW:
                position = self.stacking_strategy.find_slot(
                    container, self.yard, task.zone
                )
                if position:
                    bay, row = position
                    task.bay = bay

                    travel_time = abs(rtg.current_bay - bay) * self.rtg_scheduler.travel_time_per_bay
                    if travel_time > 0:
                        yield self.env.timeout(travel_time)
                        rtg.total_distance += abs(bay - rtg.current_bay)
                        rtg.current_bay = bay

                    yield self.env.timeout(
                        self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time
                    )

                    self.yard.place_container(container, task.zone, bay, row)
                    self.stats.total_containers_stowed += 1
                    self.stats.total_stow_operations += 1
                    rtg.total_tasks_completed += 1
                    rtg.total_working_time += (
                        self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time
                    )

                    self.stats.rtg_task_log.append(
                        {
                            "time": self.env.now,
                            "rtg_id": rtg.rtg_id,
                            "task_id": task.task_id,
                            "task_type": task.task_type.value,
                            "container_id": container.container_id,
                            "zone": task.zone.value,
                            "bay": bay,
                            "duration": self.env.now - start_time,
                        }
                    )

                    if not container.is_import:
                        pickup_time = container.pickup_time_window[0]
                        delay = max(0, pickup_time - self.env.now)
                        yield self.env.timeout(delay)
                        self.env.process(self._pickup_container_process(container))

            elif task.task_type == TaskType.PICKUP:
                slot = self.yard.find_container(container)
                if slot:
                    target_bay = slot.bay
                    travel_time = abs(rtg.current_bay - target_bay) * self.rtg_scheduler.travel_time_per_bay
                    if travel_time > 0:
                        yield self.env.timeout(travel_time)
                        rtg.total_distance += abs(target_bay - rtg.current_bay)
                        rtg.current_bay = target_bay

                    relocations_needed = self.relocation_planner.count_relocations_needed(
                        container, self.yard
                    )

                    if relocations_needed > 0:
                        result = self.relocation_planner.plan_retrieval(
                            container, self.yard
                        )
                        if result.success:
                            for move in result.relocations:
                                yield self.env.timeout(
                                    self.rtg_scheduler.lift_time
                                    + self.rtg_scheduler.lower_time
                                    + 15
                                )
                                self.stats.total_relocations += 1
                                rtg.total_tasks_completed += 1
                                rtg.total_working_time += (
                                    self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time
                                )

                    yield self.env.timeout(
                        self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time
                    )

                    self.yard.remove_container(container)
                    self.stats.total_containers_picked += 1
                    self.stats.total_pickup_operations += 1
                    rtg.total_tasks_completed += 1
                    rtg.total_working_time += (
                        self.rtg_scheduler.lift_time + self.rtg_scheduler.lower_time
                    )

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
                            "relocations": relocations_needed if relocations_needed > 0 else 0,
                            "duration": self.env.now - start_time,
                        }
                    )

    def _utilization_monitor(self):
        interval = 60.0
        while True:
            util = self.yard.get_utilization()
            self.stats.utilization_history.append((self.env.now, util))
            self.stats.containers_in_yard.append(
                (self.env.now, len(self.yard.get_all_containers()))
            )
            self.stats.throughput_history.append(
                (self.env.now, self.stats.total_containers_stowed)
            )
            yield self.env.timeout(interval)
