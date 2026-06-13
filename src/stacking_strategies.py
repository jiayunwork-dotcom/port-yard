import random
from typing import Optional, Tuple, Dict, List
from enum import Enum

from .yard_model import Yard, Container, ZoneType, ContainerSize, WeightClass, WEIGHT_ORDER


class StackingStrategy(Enum):
    RANDOM = "random"
    CLASSIFIED = "classified"
    TIME_PRIORITY = "time_priority"
    WEIGHT_LAYERED = "weight_layered"
    OPTIMIZED = "optimized"


class BaseStackingStrategy:
    def find_slot(
        self, container: Container, yard: Yard, zone: ZoneType
    ) -> Optional[Tuple[int, int]]:
        raise NotImplementedError

    def find_best_zone(self, container: Container, yard: Yard) -> ZoneType:
        if container.is_import:
            return ZoneType.IMPORT
        else:
            return ZoneType.EXPORT


class RandomStacking(BaseStackingStrategy):
    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)

    def find_slot(
        self, container: Container, yard: Yard, zone: ZoneType
    ) -> Optional[Tuple[int, int]]:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return None

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]

        valid_positions = []
        for bay in range(num_bays):
            for row in range(num_rows):
                if yard.can_place_container(container, zone, bay, row):
                    valid_positions.append((bay, row))

        if not valid_positions:
            return None

        return random.choice(valid_positions)


class ClassifiedStacking(BaseStackingStrategy):
    def __init__(self, seed: Optional[int] = None):
        self.ship_bay_assignment: Dict[str, Dict[ZoneType, int]] = {}
        self.next_bay_idx: Dict[ZoneType, int] = {}
        if seed is not None:
            random.seed(seed)

    def _get_ship_bay(self, ship_name: str, zone: ZoneType, yard: Yard) -> int:
        if zone not in self.next_bay_idx:
            self.next_bay_idx[zone] = 0

        if ship_name not in self.ship_bay_assignment:
            self.ship_bay_assignment[ship_name] = {}

        if zone not in self.ship_bay_assignment[ship_name]:
            zone_data = yard.zones.get(zone)
            if not zone_data:
                return 0
            num_bays = zone_data["num_bays"]
            bay = self.next_bay_idx[zone] % num_bays
            self.ship_bay_assignment[ship_name][zone] = bay
            self.next_bay_idx[zone] += 1

        return self.ship_bay_assignment[ship_name][zone]

    def find_slot(
        self, container: Container, yard: Yard, zone: ZoneType
    ) -> Optional[Tuple[int, int]]:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return None

        preferred_bay = self._get_ship_bay(container.ship_name, zone, yard)
        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]

        for row in range(num_rows):
            if yard.can_place_container(container, zone, preferred_bay, row):
                return (preferred_bay, row)

        for offset in range(1, num_bays):
            for delta in [-offset, offset]:
                bay = preferred_bay + delta
                if 0 <= bay < num_bays:
                    for row in range(num_rows):
                        if yard.can_place_container(container, zone, bay, row):
                            return (bay, row)

        valid_positions = []
        for bay in range(num_bays):
            for row in range(num_rows):
                if yard.can_place_container(container, zone, bay, row):
                    valid_positions.append((bay, row))
        if valid_positions:
            return random.choice(valid_positions)

        return None


class TimePriorityStacking(BaseStackingStrategy):
    def find_slot(
        self, container: Container, yard: Yard, zone: ZoneType
    ) -> Optional[Tuple[int, int]]:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return None

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]
        num_tiers = zone_data["num_tiers"]

        container_pickup_start = container.pickup_time_window[0]

        best_score = float("inf")
        best_position = None

        for bay in range(num_bays):
            for row in range(num_rows):
                if not yard.can_place_container(container, zone, bay, row):
                    continue

                stack_height = yard.get_stack_height(zone, bay, row)
                if container.size == ContainerSize.SIZE_40FT and row + 1 < num_rows:
                    h2 = yard.get_stack_height(zone, bay, row + 1)
                    stack_height = min(stack_height, h2)

                top_container = yard.get_top_container(zone, bay, row)
                if top_container:
                    top_pickup = top_container.pickup_time_window[0]
                    if container_pickup_start > top_pickup:
                        continue

                score = stack_height * 100 + abs(container_pickup_start - (stack_height * 100))

                if score < best_score:
                    best_score = score
                    best_position = (bay, row)

        if best_position:
            return best_position

        valid_positions = []
        for bay in range(num_bays):
            for row in range(num_rows):
                if yard.can_place_container(container, zone, bay, row):
                    valid_positions.append((bay, row))
        if valid_positions:
            return random.choice(valid_positions)

        return None


class WeightLayeredStacking(BaseStackingStrategy):
    def find_slot(
        self, container: Container, yard: Yard, zone: ZoneType
    ) -> Optional[Tuple[int, int]]:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return None

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]
        num_tiers = zone_data["num_tiers"]

        container_weight = WEIGHT_ORDER[container.weight_class]

        target_tier_ranges = {
            2: (0, num_tiers // 3),
            1: (num_tiers // 3, 2 * num_tiers // 3),
            0: (2 * num_tiers // 3, num_tiers),
        }

        min_tier, max_tier = target_tier_ranges.get(
            container_weight, (0, num_tiers)
        )

        best_score = float("inf")
        best_position = None

        for bay in range(num_bays):
            for row in range(num_rows):
                if not yard.can_place_container(container, zone, bay, row):
                    continue

                stack_height = yard.get_stack_height(zone, bay, row)
                if container.size == ContainerSize.SIZE_40FT and row + 1 < num_rows:
                    h2 = yard.get_stack_height(zone, bay, row + 1)
                    stack_height = min(stack_height, h2)

                if min_tier <= stack_height < max_tier:
                    tier_score = 0
                else:
                    tier_score = abs(stack_height - (min_tier + max_tier) // 2) * 10

                score = tier_score + bay * 0.1

                if score < best_score:
                    best_score = score
                    best_position = (bay, row)

        if best_position:
            return best_position

        valid_positions = []
        for bay in range(num_bays):
            for row in range(num_rows):
                if yard.can_place_container(container, zone, bay, row):
                    valid_positions.append((bay, row))
        if valid_positions:
            return random.choice(valid_positions)

        return None


class OptimizedStacking(BaseStackingStrategy):
    def __init__(self, time_weight: float = 0.5, weight_weight: float = 0.5):
        self.time_weight = time_weight
        self.weight_weight = weight_weight

    def find_slot(
        self, container: Container, yard: Yard, zone: ZoneType
    ) -> Optional[Tuple[int, int]]:
        zone_data = yard.zones.get(zone)
        if not zone_data:
            return None

        num_bays = zone_data["num_bays"]
        num_rows = zone_data["num_rows"]
        num_tiers = zone_data["num_tiers"]

        container_pickup = container.pickup_time_window[0]
        container_weight = WEIGHT_ORDER[container.weight_class]

        best_score = float("inf")
        best_position = None

        for bay in range(num_bays):
            for row in range(num_rows):
                if not yard.can_place_container(container, zone, bay, row):
                    continue

                stack_height = yard.get_stack_height(zone, bay, row)
                if container.size == ContainerSize.SIZE_40FT and row + 1 < num_rows:
                    h2 = yard.get_stack_height(zone, bay, row + 1)
                    stack_height = min(stack_height, h2)

                time_score = 0
                top_container = yard.get_top_container(zone, bay, row)
                if top_container:
                    top_pickup = top_container.pickup_time_window[0]
                    if container_pickup > top_pickup:
                        time_score = (container_pickup - top_pickup) * 2
                    else:
                        time_score = (top_pickup - container_pickup) * 0.5
                else:
                    time_score = stack_height * 50

                ideal_tier = (num_tiers - 1) * (1 - container_weight / 2)
                weight_score = abs(stack_height - ideal_tier) * 10

                height_penalty = stack_height * 2

                total_score = (
                    self.time_weight * time_score
                    + self.weight_weight * weight_score
                    + height_penalty
                    + bay * 0.01
                )

                if total_score < best_score:
                    best_score = total_score
                    best_position = (bay, row)

        if best_position:
            return best_position

        valid_positions = []
        for bay in range(num_bays):
            for row in range(num_rows):
                if yard.can_place_container(container, zone, bay, row):
                    valid_positions.append((bay, row))
        if valid_positions:
            return random.choice(valid_positions)

        return None


def create_strategy(strategy_type: StackingStrategy, **kwargs) -> BaseStackingStrategy:
    if strategy_type == StackingStrategy.RANDOM:
        return RandomStacking(seed=kwargs.get("seed"))
    elif strategy_type == StackingStrategy.CLASSIFIED:
        return ClassifiedStacking(seed=kwargs.get("seed"))
    elif strategy_type == StackingStrategy.TIME_PRIORITY:
        return TimePriorityStacking()
    elif strategy_type == StackingStrategy.WEIGHT_LAYERED:
        return WeightLayeredStacking()
    elif strategy_type == StackingStrategy.OPTIMIZED:
        return OptimizedStacking(
            time_weight=kwargs.get("time_weight", 0.5),
            weight_weight=kwargs.get("weight_weight", 0.5),
        )
    else:
        return RandomStacking()
