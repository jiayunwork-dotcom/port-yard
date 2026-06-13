import random
import csv
from typing import List, Dict, Optional, Tuple
from .yard_model import Container, ContainerSize, WeightClass, ZoneType


SHIP_NAMES = [
    "COSCO SHIPPING", "MAERSK", "MSC", "CMA CGM", "HAPAG-LLOYD",
    "EVERGREEN", "ONE", "YANG MING", "ZIM", "HYUNDAI"
]

DESTINATION_PORTS = [
    "Shanghai", "Singapore", "Rotterdam", "Los Angeles", "Dubai",
    "Hong Kong", "Qingdao", "Busan", "Hamburg", "Antwerp"
]


class ContainerGenerator:
    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)
        self._id_counter = 0

    def _generate_container_id(self, prefix: str = "CONT") -> str:
        self._id_counter += 1
        return f"{prefix}{self._id_counter:06d}"

    def generate_random_container(
        self,
        is_import: bool = True,
        arrival_time: float = 0.0,
        pickup_time_window: Optional[Tuple[float, float]] = None,
        sim_duration: float = 10080.0,
    ) -> Container:
        size = random.choice([ContainerSize.SIZE_20FT, ContainerSize.SIZE_40FT])
        weight_class = random.choice([WeightClass.LIGHT, WeightClass.MEDIUM, WeightClass.HEAVY])
        ship_name = random.choice(SHIP_NAMES)
        destination_port = random.choice(DESTINATION_PORTS) if not is_import else None

        if pickup_time_window is None:
            start = arrival_time + random.uniform(60, 240)
            end = start + random.uniform(60, 480)
            end = min(end, sim_duration)
            pickup_time_window = (start, end)

        return Container(
            container_id=self._generate_container_id(),
            size=size,
            weight_class=weight_class,
            ship_name=ship_name,
            pickup_time_window=pickup_time_window,
            destination_port=destination_port,
            is_import=is_import,
            arrival_time=arrival_time,
        )

    def generate_containers(
        self,
        num_containers: int,
        is_import: bool = True,
        arrival_time_start: float = 0.0,
        arrival_time_end: float = 60.0,
        sim_duration: float = 10080.0,
    ) -> List[Container]:
        containers = []
        for _ in range(num_containers):
            arrival_time = random.uniform(arrival_time_start, arrival_time_end)
            pickup_start = arrival_time + random.uniform(120, 1440)
            pickup_end = pickup_start + random.uniform(60, 480)
            pickup_end = min(pickup_end, sim_duration)
            pickup_window = (pickup_start, pickup_end)

            container = self.generate_random_container(
                is_import=is_import,
                arrival_time=arrival_time,
                pickup_time_window=pickup_window,
                sim_duration=sim_duration,
            )
            containers.append(container)
        return containers

    def generate_ship_containers(
        self,
        ship_name: str,
        num_containers: int,
        arrival_time: float = 0.0,
        is_import: bool = True,
        sim_duration: float = 10080.0,
    ) -> List[Container]:
        containers = []
        for _ in range(num_containers):
            size = random.choice([ContainerSize.SIZE_20FT, ContainerSize.SIZE_40FT])
            weight_class = random.choice([WeightClass.LIGHT, WeightClass.MEDIUM, WeightClass.HEAVY])
            destination_port = random.choice(DESTINATION_PORTS) if not is_import else None

            pickup_start = arrival_time + random.uniform(120, 1440)
            pickup_end = pickup_start + random.uniform(60, 480)
            pickup_end = min(pickup_end, sim_duration)
            pickup_window = (pickup_start, pickup_end)

            container = Container(
                container_id=self._generate_container_id(),
                size=size,
                weight_class=weight_class,
                ship_name=ship_name,
                pickup_time_window=pickup_window,
                destination_port=destination_port,
                is_import=is_import,
                arrival_time=arrival_time,
            )
            containers.append(container)
        return containers

    def generate_export_containers_for_ship(
        self,
        ship_name: str,
        num_containers: int,
        ship_departure_time: float,
        sim_duration: float = 10080.0,
    ) -> List[Container]:
        containers = []
        for _ in range(num_containers):
            size = random.choice([ContainerSize.SIZE_20FT, ContainerSize.SIZE_40FT])
            weight_class = random.choice([WeightClass.LIGHT, WeightClass.MEDIUM, WeightClass.HEAVY])
            destination_port = random.choice(DESTINATION_PORTS)

            arrival_start = max(0, ship_departure_time - 1440)
            arrival_end = ship_departure_time - 120
            arrival_time = random.uniform(arrival_start, arrival_end)

            pickup_start = ship_departure_time - 60
            pickup_end = ship_departure_time
            pickup_window = (pickup_start, pickup_end)

            container = Container(
                container_id=self._generate_container_id(),
                size=size,
                weight_class=weight_class,
                ship_name=ship_name,
                pickup_time_window=pickup_window,
                destination_port=destination_port,
                is_import=False,
                arrival_time=arrival_time,
            )
            containers.append(container)
        return containers

    def export_to_csv(self, containers: List[Container], filepath: str):
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "container_id", "size_ft", "weight_class", "ship_name",
                "pickup_start", "pickup_end", "destination_port",
                "is_import", "arrival_time"
            ])
            for c in containers:
                writer.writerow([
                    c.container_id,
                    c.size.value,
                    c.weight_class.value,
                    c.ship_name,
                    c.pickup_time_window[0],
                    c.pickup_time_window[1],
                    c.destination_port or "",
                    "1" if c.is_import else "0",
                    c.arrival_time,
                ])

    def import_from_csv(self, filepath: str) -> List[Container]:
        containers = []
        with open(filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                size = ContainerSize.SIZE_20FT if int(row["size_ft"]) == 20 else ContainerSize.SIZE_40FT
                weight_class = WeightClass(row["weight_class"])
                is_import = row["is_import"] == "1"
                pickup_start = float(row["pickup_start"])
                pickup_end = float(row["pickup_end"])
                destination = row.get("destination_port") or None
                if destination == "":
                    destination = None

                container = Container(
                    container_id=row["container_id"],
                    size=size,
                    weight_class=weight_class,
                    ship_name=row["ship_name"],
                    pickup_time_window=(pickup_start, pickup_end),
                    destination_port=destination,
                    is_import=is_import,
                    arrival_time=float(row.get("arrival_time", 0)),
                )
                containers.append(container)
        return containers

    def reset(self):
        self._id_counter = 0
