from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from .yard_model import Yard, Container, Slot, ZoneType, ContainerSize
from .stacking_strategies import BaseStackingStrategy, create_strategy, StackingStrategy


@dataclass
class RelocationMove:
    container: Container
    from_slot: Slot
    to_slot: Optional[Slot]
    cost: float


@dataclass
class RelocationResult:
    target_slot: Slot
    relocations: List[RelocationMove] = field(default_factory=list)
    total_cost: float = 0.0
    success: bool = True

    @property
    def count(self) -> int:
        return len(self.relocations)


class RelocationPlanner:
    def __init__(
        self,
        stacking_strategy: Optional[BaseStackingStrategy] = None,
        same_bay_cost: float = 1.0,
        adjacent_bay_cost: float = 2.0,
        cross_zone_cost: float = 5.0,
    ):
        self.stacking_strategy = stacking_strategy or create_strategy(
            StackingStrategy.RANDOM
        )
        self.same_bay_cost = same_bay_cost
        self.adjacent_bay_cost = adjacent_bay_cost
        self.cross_zone_cost = cross_zone_cost

    def plan_retrieval(
        self, container: Container, yard: Yard
    ) -> RelocationResult:
        target_slot = yard.find_container(container)
        if not target_slot:
            result = RelocationResult(target_slot=Slot(ZoneType.IMPORT, -1, -1, -1))
            result.success = False
            return result

        result = RelocationResult(target_slot=target_slot)

        containers_above = yard.get_containers_above(target_slot)

        for cont in reversed(containers_above):
            cont_slot = yard.find_container(cont)
            if not cont_slot:
                continue

            new_position = self._find_relocation_slot(cont, yard, cont_slot)
            if new_position is None:
                result.success = False
                return result

            zone, bay, row = new_position
            cost = self._calculate_cost(cont_slot, zone, bay)

            yard.remove_container(cont)
            placed_slots = yard.place_container(cont, zone, bay, row)

            if placed_slots:
                result.relocations.append(
                    RelocationMove(
                        container=cont,
                        from_slot=cont_slot,
                        to_slot=placed_slots[0],
                        cost=cost,
                    )
                )
                result.total_cost += cost
            else:
                yard.place_container(cont, cont_slot.zone, cont_slot.bay, cont_slot.row)
                result.success = False
                return result

        return result

    def _find_relocation_slot(
        self, container: Container, yard: Yard, from_slot: Slot
    ) -> Optional[Tuple[ZoneType, int, int]]:
        zone = from_slot.zone
        bay = from_slot.bay

        zone_data = yard.zones.get(zone)
        if not zone_data:
            return None

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]

        same_bay_options = []
        for row in range(num_rows):
            if row == from_slot.row:
                continue
            if container.size == ContainerSize.SIZE_40FT and row + 1 >= num_rows:
                continue
            if yard.can_place_container(container, zone, bay, row):
                same_bay_options.append((zone, bay, row))

        if same_bay_options:
            return same_bay_options[0]

        for offset in range(1, num_bays):
            for delta in [-offset, offset]:
                adj_bay = bay + delta
                if 0 <= adj_bay < num_bays:
                    for row in range(num_rows):
                        if container.size == ContainerSize.SIZE_40FT and row + 1 >= num_rows:
                            continue
                        if yard.can_place_container(container, zone, adj_bay, row):
                            return (zone, adj_bay, row)

        for other_zone in ZoneType:
            if other_zone == zone:
                continue
            other_zone_data = yard.zones.get(other_zone)
            if not other_zone_data:
                continue
            for b in range(other_zone_data["num_bays"]):
                for r in range(other_zone_data["num_rows"]):
                    if container.size == ContainerSize.SIZE_40FT and r + 1 >= other_zone_data["num_rows"]:
                        continue
                    if yard.can_place_container(container, other_zone, b, r):
                        return (other_zone, b, r)

        return None

    def _calculate_cost(
        self, from_slot: Slot, to_zone: ZoneType, to_bay: int
    ) -> float:
        if from_slot.zone == to_zone:
            bay_distance = abs(from_slot.bay - to_bay)
            if bay_distance == 0:
                return self.same_bay_cost
            elif bay_distance == 1:
                return self.adjacent_bay_cost
            else:
                return self.adjacent_bay_cost + bay_distance * 0.5
        else:
            return self.cross_zone_cost

    def count_relocations_needed(
        self, container: Container, yard: Yard
    ) -> int:
        target_slot = yard.find_container(container)
        if not target_slot:
            return 0

        containers_above = yard.get_containers_above(target_slot)
        return len(containers_above)
