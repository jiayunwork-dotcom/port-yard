from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict
from enum import Enum


class ZoneType(Enum):
    IMPORT = "import"
    EXPORT = "export"
    TRANSIT = "transit"


class ContainerSize(Enum):
    SIZE_20FT = 20
    SIZE_40FT = 40


class WeightClass(Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


WEIGHT_ORDER = {WeightClass.LIGHT: 0, WeightClass.MEDIUM: 1, WeightClass.HEAVY: 2}


@dataclass
class Container:
    container_id: str
    size: ContainerSize
    weight_class: WeightClass
    ship_name: str
    pickup_time_window: Tuple[float, float]
    destination_port: Optional[str] = None
    is_import: bool = True
    arrival_time: float = 0.0

    def is_heavier_than(self, other: "Container") -> bool:
        return WEIGHT_ORDER[self.weight_class] > WEIGHT_ORDER[other.weight_class]

    def __hash__(self):
        return hash(self.container_id)

    def __eq__(self, other):
        if not isinstance(other, Container):
            return False
        return self.container_id == other.container_id


@dataclass
class Slot:
    zone: ZoneType
    bay: int
    row: int
    tier: int
    container: Optional[Container] = None

    @property
    def is_empty(self) -> bool:
        return self.container is None

    @property
    def position_key(self) -> Tuple[ZoneType, int, int, int]:
        return (self.zone, self.bay, self.row, self.tier)


class Yard:
    def __init__(self, config: Dict):
        self.config = config
        self.zones: Dict[ZoneType, Dict] = {}
        self._initialize_yard()

    def _initialize_yard(self):
        for zone_type in ZoneType:
            zone_config = self.config.get(zone_type.value, {})
            num_bays = zone_config.get("num_bays", 10)
            num_rows = zone_config.get("num_rows", 6)
            num_tiers = zone_config.get("num_tiers", 5)

            bays = {}
            for bay_idx in range(num_bays):
                rows = {}
                for row_idx in range(num_rows):
                    tiers = {}
                    for tier_idx in range(num_tiers):
                        tiers[tier_idx] = Slot(
                            zone=zone_type,
                            bay=bay_idx,
                            row=row_idx,
                            tier=tier_idx,
                        )
                    rows[row_idx] = tiers
                bays[bay_idx] = rows

            self.zones[zone_type] = {
                "num_bays": num_bays,
                "num_rows": num_rows,
                "num_tiers": num_tiers,
                "bays": bays,
            }

    def get_slot(self, zone: ZoneType, bay: int, row: int, tier: int) -> Optional[Slot]:
        if zone not in self.zones:
            return None
        zone_data = self.zones[zone]
        if bay < 0 or bay >= zone_data["num_bays"]:
            return None
        if row < 0 or row >= zone_data["num_rows"]:
            return None
        if tier < 0 or tier >= zone_data["num_tiers"]:
            return None
        return zone_data["bays"][bay][row][tier]

    def can_place_container(
        self, container: Container, zone: ZoneType, bay: int, row: int
    ) -> bool:
        zone_data = self.zones.get(zone)
        if not zone_data:
            return False

        num_tiers = zone_data["num_tiers"]
        num_rows = zone_data["num_rows"]

        if container.size == ContainerSize.SIZE_40FT:
            if row + 1 >= num_rows:
                return False
            bottom_tier = self._find_bottom_tier(zone, bay, row, num_tiers)
            bottom_tier2 = self._find_bottom_tier(zone, bay, row + 1, num_tiers)
            if bottom_tier is None or bottom_tier2 is None:
                return False
            bottom_tier = min(bottom_tier, bottom_tier2)
            if bottom_tier >= num_tiers:
                return False
            if bottom_tier > 0:
                slot1 = self.get_slot(zone, bay, row, bottom_tier - 1)
                slot2 = self.get_slot(zone, bay, row + 1, bottom_tier - 1)
                if slot1 and slot1.container and container.is_heavier_than(slot1.container):
                    return False
                if slot2 and slot2.container and container.is_heavier_than(slot2.container):
                    return False
            return True
        else:
            bottom_tier = self._find_bottom_tier(zone, bay, row, num_tiers)
            if bottom_tier is None or bottom_tier >= num_tiers:
                return False
            if bottom_tier > 0:
                below_slot = self.get_slot(zone, bay, row, bottom_tier - 1)
                if below_slot and below_slot.container and container.is_heavier_than(below_slot.container):
                    return False
            return True

    def _find_bottom_tier(self, zone: ZoneType, bay: int, row: int, num_tiers: int) -> Optional[int]:
        for tier in range(num_tiers):
            slot = self.get_slot(zone, bay, row, tier)
            if slot and slot.is_empty:
                return tier
        return None

    def place_container(
        self, container: Container, zone: ZoneType, bay: int, row: int
    ) -> Optional[List[Slot]]:
        if not self.can_place_container(container, zone, bay, row):
            return None

        zone_data = self.zones[zone]
        num_tiers = zone_data["num_tiers"]

        if container.size == ContainerSize.SIZE_40FT:
            bottom_tier1 = self._find_bottom_tier(zone, bay, row, num_tiers)
            bottom_tier2 = self._find_bottom_tier(zone, bay, row + 1, num_tiers)
            bottom_tier = min(bottom_tier1, bottom_tier2)
            slot1 = self.get_slot(zone, bay, row, bottom_tier)
            slot2 = self.get_slot(zone, bay, row + 1, bottom_tier)
            if slot1:
                slot1.container = container
            if slot2:
                slot2.container = container
            return [slot1, slot2] if slot1 and slot2 else None
        else:
            bottom_tier = self._find_bottom_tier(zone, bay, row, num_tiers)
            slot = self.get_slot(zone, bay, row, bottom_tier)
            if slot:
                slot.container = container
            return [slot] if slot else None

    def remove_container(self, container: Container) -> Optional[List[Slot]]:
        slot = self.find_container(container)
        if not slot:
            return None

        if container.size == ContainerSize.SIZE_40FT:
            slot2 = self.get_slot(slot.zone, slot.bay, slot.row + 1, slot.tier)
            slot.container = None
            if slot2:
                slot2.container = None
            return [slot, slot2] if slot2 else [slot]
        else:
            slot.container = None
            return [slot]

    def find_container(self, container: Container) -> Optional[Slot]:
        for zone_type in ZoneType:
            zone_data = self.zones[zone_type]
            for bay_idx in range(zone_data["num_bays"]):
                for row_idx in range(zone_data["num_rows"]):
                    for tier_idx in range(zone_data["num_tiers"]):
                        slot = zone_data["bays"][bay_idx][row_idx][tier_idx]
                        if slot.container and slot.container.container_id == container.container_id:
                            return slot
        return None

    def get_containers_above(self, slot: Slot) -> List[Container]:
        containers = []
        zone_data = self.zones[slot.zone]
        num_tiers = zone_data["num_tiers"]

        if slot.container and slot.container.size == ContainerSize.SIZE_40FT:
            for tier in range(slot.tier + 1, num_tiers):
                s1 = self.get_slot(slot.zone, slot.bay, slot.row, tier)
                s2 = self.get_slot(slot.zone, slot.bay, slot.row + 1, tier)
                if s1 and s1.container:
                    containers.append(s1.container)
                elif s2 and s2.container:
                    containers.append(s2.container)
        else:
            for tier in range(slot.tier + 1, num_tiers):
                s = self.get_slot(slot.zone, slot.bay, slot.row, tier)
                if s and s.container:
                    containers.append(s.container)

        return containers

    def get_top_container(self, zone: ZoneType, bay: int, row: int) -> Optional[Container]:
        zone_data = self.zones.get(zone)
        if not zone_data:
            return None
        num_tiers = zone_data["num_tiers"]

        for tier in range(num_tiers - 1, -1, -1):
            slot = self.get_slot(zone, bay, row, tier)
            if slot and slot.container:
                return slot.container
        return None

    def get_stack_height(self, zone: ZoneType, bay: int, row: int) -> int:
        zone_data = self.zones.get(zone)
        if not zone_data:
            return 0
        num_tiers = zone_data["num_tiers"]

        height = 0
        for tier in range(num_tiers):
            slot = self.get_slot(zone, bay, row, tier)
            if slot and slot.container:
                height = tier + 1
            else:
                break
        return height

    def get_bay_max_height(self, zone: ZoneType, bay: int) -> int:
        zone_data = self.zones.get(zone)
        if not zone_data:
            return 0
        num_rows = zone_data["num_rows"]

        max_height = 0
        for row in range(num_rows):
            height = self.get_stack_height(zone, bay, row)
            if height > max_height:
                max_height = height
        return max_height

    def get_total_slots(self) -> int:
        total = 0
        for zone_type in ZoneType:
            zone_data = self.zones[zone_type]
            total += zone_data["num_bays"] * zone_data["num_rows"] * zone_data["num_tiers"]
        return total

    def get_occupied_slots(self) -> int:
        count = 0
        for zone_type in ZoneType:
            zone_data = self.zones[zone_type]
            for bay_idx in range(zone_data["num_bays"]):
                for row_idx in range(zone_data["num_rows"]):
                    for tier_idx in range(zone_data["num_tiers"]):
                        slot = zone_data["bays"][bay_idx][row_idx][tier_idx]
                        if slot.container:
                            count += 1
        return count

    def get_utilization(self) -> float:
        total = self.get_total_slots()
        if total == 0:
            return 0.0
        return self.get_occupied_slots() / total

    def get_all_containers(self) -> List[Container]:
        containers = set()
        for zone_type in ZoneType:
            zone_data = self.zones[zone_type]
            for bay_idx in range(zone_data["num_bays"]):
                for row_idx in range(zone_data["num_rows"]):
                    for tier_idx in range(zone_data["num_tiers"]):
                        slot = zone_data["bays"][bay_idx][row_idx][tier_idx]
                        if slot.container:
                            containers.add(slot.container)
        return list(containers)
