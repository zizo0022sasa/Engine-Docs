"""Constrained Waypoint Graph, Level-3 Safe Corridors, and Reactive Obstacle Rerouter (PR-12 / AUD-37 / T34)."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from osrs_ee.navigation.capabilities import AccountCapabilitiesContext


class NoSafeRouteError(RuntimeError):
    """Raised when no safe path exists under the account's current skill/quest/combat constraints."""


@dataclass(frozen=True)
class WorldPoint:
    x: int
    y: int
    plane: int = 0
    label: str = ""

    def chebyshev_distance(self, other: WorldPoint) -> int:
        if self.plane != other.plane:
            return 999_999
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def euclidean_distance(self, other: WorldPoint) -> float:
        if self.plane != other.plane:
            return 999_999.0
        return math.hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    src: str
    dst: str
    cost_tiles: float
    intermediate_point: WorldPoint | None = None
    min_skills: Mapping[str, int] = field(default_factory=dict)
    requires_member: bool = False
    required_quests: frozenset[str] = field(default_factory=frozenset)
    min_combat_level: int = 3
    toll_coins_gp: int = 0
    quest_waives_toll: str | None = None


@dataclass(frozen=True)
class NavigationPlan:
    origin_node: str
    destination_node: str
    waypoints: tuple[WorldPoint, ...]
    traversed_edges: tuple[str, ...]
    pruned_edges: Mapping[str, str]
    walker_flags: dict[str, Any]
    total_cost_tiles: float


@dataclass
class NavigationProgress:
    plan: NavigationPlan
    target_signature: str = ""
    current_index: int = 0
    last_dispatched_index: int = -1
    last_observed_pos: WorldPoint | None = None
    stalled_ticks_on_segment: int = 0
    completed: bool = False


# ==============================================================================
# Verified OSRS F2P / Early-Game Surface Nodes & Gated Shortcut / Hazard Edges
# ==============================================================================
DEFAULT_WORLD_NODES: dict[str, WorldPoint] = {
    "GRAND_EXCHANGE": WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE"),
    "GE_UNDERWALL_EAST": WorldPoint(3149, 3480, 0, "GE_UNDERWALL_EAST"),  # Field-proven Agility 21 trap!
    "EDGEVILLE_SOUTH_SHORTCUT": WorldPoint(3138, 3516, 0, "EDGEVILLE_SOUTH_SHORTCUT"),
    "VARROCK_WEST_BANK": WorldPoint(3185, 3436, 0, "VARROCK_WEST_BANK"),
    "VARROCK_WEST_GATE": WorldPoint(3176, 3428, 0, "VARROCK_WEST_GATE"),
    "GERTRUDE_SAFE_ROAD": WorldPoint(3148, 3414, 0, "GERTRUDE_SAFE_ROAD"),
    "GUNNARSGRunn_BRIDGE_EAST": WorldPoint(3105, 3420, 0, "GUNNARSGRUNN_BRIDGE_EAST"),
    "BARBARIAN_VILLAGE": WorldPoint(3081, 3421, 0, "BARBARIAN_VILLAGE"),  # Stronghold Entrance!
    "EDGEVILLE_BANK": WorldPoint(3094, 3491, 0, "EDGEVILLE_BANK"),
    "FALADOR_EAST_ROAD": WorldPoint(3007, 3361, 0, "FALADOR_EAST_ROAD"),
    "FALADOR_CRUMBLING_WALL": WorldPoint(2935, 3355, 0, "FALADOR_CRUMBLING_WALL"),
    "FALADOR_WEST_BANK": WorldPoint(2946, 3368, 0, "FALADOR_WEST_BANK"),
    "VARROCK_SOUTH_DARK_WIZARDS": WorldPoint(3226, 3369, 0, "VARROCK_SOUTH_DARK_WIZARDS"),  # Lethal to Lvl 3!
    "CHAMPIONS_SAFE_BYPASS": WorldPoint(3180, 3358, 0, "CHAMPIONS_SAFE_BYPASS"),
    "DRAYNOR_CROSSROAD": WorldPoint(3110, 3295, 0, "DRAYNOR_CROSSROAD"),
    "DRAYNOR_BANK": WorldPoint(3092, 3245, 0, "DRAYNOR_BANK"),
    "LUMBRIDGE_CASTLE_COURTYARD": WorldPoint(3222, 3218, 0, "LUMBRIDGE_CASTLE_COURTYARD"),
    "AL_KHARID_TOLL_GATE": WorldPoint(3268, 3227, 0, "AL_KHARID_TOLL_GATE"),
    "AL_KHARID_NORTH_PASS": WorldPoint(3280, 3315, 0, "AL_KHARID_NORTH_PASS"),
    "AL_KHARID_MINE": WorldPoint(3298, 3284, 0, "AL_KHARID_MINE"),
    "AL_KHARID_BANK": WorldPoint(3269, 3167, 0, "AL_KHARID_BANK"),
    # Stage 0.2 & 0.3 Quest / Skilling Safe Surface Spurs
    "VARROCK_SQUARE": WorldPoint(3211, 3422, 0, "VARROCK_SQUARE"),
    "VARROCK_JULIET_STAIRS": WorldPoint(3157, 3435, 0, "VARROCK_JULIET_STAIRS"),
    "VARROCK_APOTHECARY": WorldPoint(3195, 3405, 0, "VARROCK_APOTHECARY"),
    "VARROCK_EAST_CHURCH": WorldPoint(3254, 3483, 0, "VARROCK_EAST_CHURCH"),
    "LUMBRIDGE_KITCHEN": WorldPoint(3206, 3214, 0, "LUMBRIDGE_KITCHEN"),
    "LUMBRIDGE_WEST_TREES": WorldPoint(3190, 3245, 0, "LUMBRIDGE_WEST_TREES"),
    "LUMBRIDGE_SHEEP_FARM": WorldPoint(3190, 3273, 0, "LUMBRIDGE_SHEEP_FARM"),
    "LUMBRIDGE_SWAMP_FISHING": WorldPoint(3243, 3152, 0, "LUMBRIDGE_SWAMP_FISHING"),
    "BARBARIAN_RIVER_FIRE": WorldPoint(3106, 3432, 0, "BARBARIAN_RIVER_FIRE"),
    "GOBLIN_VILLAGE": WorldPoint(2958, 3512, 0, "GOBLIN_VILLAGE"),
}


def _build_default_edges() -> tuple[GraphEdge, ...]:
    bidirectional_specs: list[dict[str, Any]] = [
        {
            "edge_id": "safe_bridge_east_to_barbarian_river_fire",
            "src": "GUNNARSGRunn_BRIDGE_EAST",
            "dst": "BARBARIAN_RIVER_FIRE",
            "cost_tiles": 14.0,
        },
        {
            "edge_id": "safe_barbarian_river_fire_to_stronghold_entrance",
            "src": "BARBARIAN_RIVER_FIRE",
            "dst": "BARBARIAN_VILLAGE",
            "cost_tiles": 26.0,
        },
        # --- GATED SHORTCUT: GE Underwall Tunnel (3149, 3480) requires Agility 21 + Members! ---
        {
            "edge_id": "shortcut_ge_underwall_agility_21",
            "src": "GRAND_EXCHANGE",
            "dst": "EDGEVILLE_SOUTH_SHORTCUT",
            "cost_tiles": 28.0,  # Looks tempting geographically, which trapped naive pathfinders!
            "intermediate_point": DEFAULT_WORLD_NODES["GE_UNDERWALL_EAST"],
            "min_skills": {"agility": 21},
            "requires_member": True,
        },
        {
            "edge_id": "road_edgeville_shortcut_to_bank",
            "src": "EDGEVILLE_SOUTH_SHORTCUT",
            "dst": "EDGEVILLE_BANK",
            "cost_tiles": 48.0,
        },
        {
            "edge_id": "road_edgeville_to_barbarian",
            "src": "EDGEVILLE_BANK",
            "dst": "BARBARIAN_VILLAGE",
            "cost_tiles": 72.0,
        },
        # --- LEVEL-3 SAFE CORRIDOR: GE -> Varrock West -> Safe South-West Road -> Barbarian Village ---
        {
            "edge_id": "safe_ge_to_varrock_west_bank",
            "src": "GRAND_EXCHANGE",
            "dst": "VARROCK_WEST_BANK",
            "cost_tiles": 56.0,
        },
        {
            "edge_id": "safe_varrock_west_bank_to_gate",
            "src": "VARROCK_WEST_BANK",
            "dst": "VARROCK_WEST_GATE",
            "cost_tiles": 14.0,
        },
        {
            "edge_id": "safe_varrock_gate_to_gertrude_road",
            "src": "VARROCK_WEST_GATE",
            "dst": "GERTRUDE_SAFE_ROAD",
            "cost_tiles": 32.0,
        },
        {
            "edge_id": "safe_gertrude_to_bridge_east",
            "src": "GERTRUDE_SAFE_ROAD",
            "dst": "GUNNARSGRunn_BRIDGE_EAST",
            "cost_tiles": 44.0,
        },
        {
            "edge_id": "safe_bridge_to_barbarian_village",
            "src": "GUNNARSGRunn_BRIDGE_EAST",
            "dst": "BARBARIAN_VILLAGE",
            "cost_tiles": 25.0,
        },
        # --- HAZARD EDGE vs LEVEL-3 SAFE CORRIDOR TO LUMBRIDGE / DRAYNOR ---
        {
            "edge_id": "hazard_varrock_south_dark_wizards",
            "src": "VARROCK_WEST_GATE",
            "dst": "LUMBRIDGE_CASTLE_COURTYARD",
            "cost_tiles": 165.0,  # Geographically shorter, but passes lvl-20 Dark Wizards at (3226, 3369)!
            "intermediate_point": DEFAULT_WORLD_NODES["VARROCK_SOUTH_DARK_WIZARDS"],
            "min_combat_level": 26,
        },
        {
            "edge_id": "safe_varrock_gate_to_champions_west_bypass",
            "src": "VARROCK_WEST_GATE",
            "dst": "CHAMPIONS_SAFE_BYPASS",
            "cost_tiles": 72.0,
        },
        {
            "edge_id": "safe_champions_bypass_to_draynor_crossroad",
            "src": "CHAMPIONS_SAFE_BYPASS",
            "dst": "DRAYNOR_CROSSROAD",
            "cost_tiles": 94.0,
        },
        {
            "edge_id": "safe_draynor_crossroad_to_draynor_bank",
            "src": "DRAYNOR_CROSSROAD",
            "dst": "DRAYNOR_BANK",
            "cost_tiles": 54.0,
        },
        {
            "edge_id": "safe_draynor_crossroad_to_lumbridge",
            "src": "DRAYNOR_CROSSROAD",
            "dst": "LUMBRIDGE_CASTLE_COURTYARD",
            "cost_tiles": 135.0,
        },
        # --- FALADOR CRUMBLING WALL SHORTCUT vs SAFE EAST GATE ---
        {
            "edge_id": "safe_barbarian_to_falador_east",
            "src": "BARBARIAN_VILLAGE",
            "dst": "FALADOR_EAST_ROAD",
            "cost_tiles": 96.0,
        },
        {
            "edge_id": "safe_falador_east_to_west_bank",
            "src": "FALADOR_EAST_ROAD",
            "dst": "FALADOR_WEST_BANK",
            "cost_tiles": 70.0,
        },
        {
            "edge_id": "shortcut_falador_crumbling_wall_agility_5",
            "src": "FALADOR_EAST_ROAD",
            "dst": "FALADOR_WEST_BANK",
            "cost_tiles": 35.0,
            "intermediate_point": DEFAULT_WORLD_NODES["FALADOR_CRUMBLING_WALL"],
            "min_skills": {"agility": 5},
            "requires_member": True,
        },
        # --- AL KHARID TOLL GATE vs SAFE FREE NORTHERN PASS ---
        {
            "edge_id": "gated_lumbridge_alkharid_toll_gate",
            "src": "LUMBRIDGE_CASTLE_COURTYARD",
            "dst": "AL_KHARID_BANK",
            "cost_tiles": 68.0,
            "intermediate_point": DEFAULT_WORLD_NODES["AL_KHARID_TOLL_GATE"],
            "toll_coins_gp": 10,
            "quest_waives_toll": "prince_ali_rescue",
        },
        {
            "edge_id": "safe_free_northern_alkharid_pass",
            "src": "VARROCK_WEST_GATE",
            "dst": "AL_KHARID_NORTH_PASS",
            "cost_tiles": 155.0,
        },
        {
            "edge_id": "safe_alkharid_north_to_bank",
            "src": "AL_KHARID_NORTH_PASS",
            "dst": "AL_KHARID_BANK",
            "cost_tiles": 148.0,
        },
        {
            "edge_id": "safe_alkharid_bank_to_mine",
            "src": "AL_KHARID_BANK",
            "dst": "AL_KHARID_MINE",
            "cost_tiles": 120.0,
        },
        # --- STAGE 0.2 & 0.3 SAFE QUEST / SKILLING LEAF SPURS ---
        {
            "edge_id": "safe_varrock_west_to_juliet_stairs",
            "src": "VARROCK_WEST_BANK",
            "dst": "VARROCK_JULIET_STAIRS",
            "cost_tiles": 28.0,
        },
        {
            "edge_id": "safe_varrock_west_to_square",
            "src": "VARROCK_WEST_BANK",
            "dst": "VARROCK_SQUARE",
            "cost_tiles": 30.0,
        },
        {
            "edge_id": "safe_varrock_square_to_apothecary",
            "src": "VARROCK_SQUARE",
            "dst": "VARROCK_APOTHECARY",
            "cost_tiles": 24.0,
        },
        {
            "edge_id": "safe_varrock_square_to_east_church",
            "src": "VARROCK_SQUARE",
            "dst": "VARROCK_EAST_CHURCH",
            "cost_tiles": 74.0,
        },
        {
            "edge_id": "safe_lumbridge_courtyard_to_kitchen",
            "src": "LUMBRIDGE_CASTLE_COURTYARD",
            "dst": "LUMBRIDGE_KITCHEN",
            "cost_tiles": 18.0,
        },
        {
            "edge_id": "safe_lumbridge_courtyard_to_west_trees",
            "src": "LUMBRIDGE_CASTLE_COURTYARD",
            "dst": "LUMBRIDGE_WEST_TREES",
            "cost_tiles": 42.0,
        },
        {
            "edge_id": "safe_lumbridge_courtyard_to_sheep_farm",
            "src": "LUMBRIDGE_CASTLE_COURTYARD",
            "dst": "LUMBRIDGE_SHEEP_FARM",
            "cost_tiles": 64.0,
        },
        {
            "edge_id": "safe_lumbridge_courtyard_to_swamp_fishing",
            "src": "LUMBRIDGE_CASTLE_COURTYARD",
            "dst": "LUMBRIDGE_SWAMP_FISHING",
            "cost_tiles": 70.0,
        },
        {
            "edge_id": "safe_falador_east_to_goblin_village",
            "src": "FALADOR_EAST_ROAD",
            "dst": "GOBLIN_VILLAGE",
            "cost_tiles": 158.0,
        },
    ]

    edges: list[GraphEdge] = []
    for spec in bidirectional_specs:
        edges.append(
            GraphEdge(
                edge_id=spec["edge_id"],
                src=spec["src"],
                dst=spec["dst"],
                cost_tiles=spec["cost_tiles"],
                intermediate_point=spec.get("intermediate_point"),
                min_skills=spec.get("min_skills", {}),
                requires_member=spec.get("requires_member", False),
                required_quests=frozenset(spec.get("required_quests", ())),
                min_combat_level=spec.get("min_combat_level", 3),
                toll_coins_gp=spec.get("toll_coins_gp", 0),
                quest_waives_toll=spec.get("quest_waives_toll"),
            )
        )
        edges.append(
            GraphEdge(
                edge_id=f"{spec['edge_id']}__rev",
                src=spec["dst"],
                dst=spec["src"],
                cost_tiles=spec["cost_tiles"],
                intermediate_point=spec.get("intermediate_point"),
                min_skills=spec.get("min_skills", {}),
                requires_member=spec.get("requires_member", False),
                required_quests=frozenset(spec.get("required_quests", ())),
                min_combat_level=spec.get("min_combat_level", 3),
                toll_coins_gp=spec.get("toll_coins_gp", 0),
                quest_waives_toll=spec.get("quest_waives_toll"),
            )
        )
    return tuple(edges)


class ConstrainedWaypointGraph:
    """Computes capability-filtered safe routes, pruning skill/member shortcuts and low-lvl aggro traps."""

    def __init__(
        self,
        *,
        nodes: Mapping[str, WorldPoint] | None = None,
        edges: tuple[GraphEdge, ...] | None = None,
    ) -> None:
        self.nodes: dict[str, WorldPoint] = dict(nodes or DEFAULT_WORLD_NODES)
        self.edges: tuple[GraphEdge, ...] = edges if edges is not None else _build_default_edges()

    def nearest_node(self, point: WorldPoint) -> str:
        best_id = ""
        best_dist = float("inf")
        for node_id, wp in self.nodes.items():
            # Never snap onto an internal shortcut/hazard pseudo-node as an origin/destination
            if node_id in ("GE_UNDERWALL_EAST", "VARROCK_SOUTH_DARK_WIZARDS", "FALADOR_CRUMBLING_WALL", "AL_KHARID_TOLL_GATE"):
                continue
            d = point.euclidean_distance(wp)
            if d < best_dist:
                best_dist = d
                best_id = node_id
        if not best_id:
            raise NoSafeRouteError(f"No reachable surface node near {point}")
        return best_id

    def plan_safe_route(
        self,
        *,
        origin: str | WorldPoint,
        destination: str | WorldPoint,
        capabilities: AccountCapabilitiesContext,
        blacklisted_edges: frozenset[str] = frozenset(),
    ) -> NavigationPlan:
        start_id = origin if isinstance(origin, str) else self.nearest_node(origin)
        goal_id = destination if isinstance(destination, str) else self.nearest_node(destination)

        if start_id not in self.nodes or goal_id not in self.nodes:
            raise NoSafeRouteError(f"Unknown route endpoints: {start_id!r} -> {goal_id!r}")

        adj: dict[str, list[GraphEdge]] = {nid: [] for nid in self.nodes}
        pruned: dict[str, str] = {}

        for edge in self.edges:
            base_id = edge.edge_id.removesuffix("__rev")
            if edge.edge_id in blacklisted_edges or base_id in blacklisted_edges:
                pruned[base_id] = "RUNTIME_STALL_BLACKLISTED"
                continue
            ok, reason = capabilities.meets_requirements(
                min_skills=edge.min_skills,
                requires_member=edge.requires_member,
                required_quests=edge.required_quests,
                min_combat_level=edge.min_combat_level,
                toll_coins_gp=edge.toll_coins_gp,
                quest_waives_toll=edge.quest_waives_toll,
            )
            if not ok:
                pruned[base_id] = reason
                continue
            adj[edge.src].append(edge)

        # Dijkstra shortest path over capability-verified edges ONLY
        dist: dict[str, float] = {start_id: 0.0}
        prev: dict[str, tuple[str, GraphEdge]] = {}
        pq: list[tuple[float, str]] = [(0.0, start_id)]

        while pq:
            cost_u, u = heapq.heappop(pq)
            if u == goal_id:
                break
            if cost_u > dist.get(u, float("inf")):
                continue
            for edge in adj.get(u, ()):
                cand = cost_u + edge.cost_tiles
                if cand < dist.get(edge.dst, float("inf")):
                    dist[edge.dst] = cand
                    prev[edge.dst] = (u, edge)
                    heapq.heappush(pq, (cand, edge.dst))

        if goal_id not in dist:
            raise NoSafeRouteError(
                f"No safe route from {start_id} to {goal_id} for capabilities (agility={capabilities.agility_level}, combat={capabilities.combat_level}, member={capabilities.is_member})"
            )

        # Reconstruct waypoints (including exact destination WorldPoint if caller passed coordinates)
        rev_edges: list[GraphEdge] = []
        cur = goal_id
        while cur != start_id:
            pred_node, used_edge = prev[cur]
            rev_edges.append(used_edge)
            cur = pred_node
        rev_edges.reverse()

        waypoints: list[WorldPoint] = [self.nodes[start_id]]
        edge_ids: list[str] = []
        for e in rev_edges:
            edge_ids.append(e.edge_id.removesuffix("__rev"))
            if e.intermediate_point is not None:
                waypoints.append(e.intermediate_point)
            waypoints.append(self.nodes[e.dst])

        if isinstance(destination, WorldPoint) and waypoints[-1].chebyshev_distance(destination) > 1:
            waypoints.append(destination)

        return NavigationPlan(
            origin_node=start_id,
            destination_node=goal_id,
            waypoints=tuple(waypoints),
            traversed_edges=tuple(edge_ids),
            pruned_edges=pruned,
            walker_flags=capabilities.to_walker_constraint_flags(),
            total_cost_tiles=round(dist[goal_id], 2),
        )


class NavigationEngine:
    """Non-blocking lookahead waypoint streamer + obstacle stall detector & automatic rerouter."""

    def __init__(
        self,
        graph: ConstrainedWaypointGraph | None = None,
        *,
        lookahead_tiles: int = 4,
        max_stalled_ticks_before_reroute: int = 20,
        walk_redispatch_cooldown_ticks: int = 5,
    ) -> None:
        self.graph = graph or ConstrainedWaypointGraph()
        self.lookahead_tiles = lookahead_tiles
        self.max_stalled_ticks_before_reroute = max(2, int(max_stalled_ticks_before_reroute))
        self.walk_redispatch_cooldown_ticks = max(3, int(walk_redispatch_cooldown_ticks))
        self._blacklisted_edges_by_account: dict[str, set[str]] = {}
        self._active_progress: dict[str, NavigationProgress] = {}

    @staticmethod
    def _dest_sig(destination: str | WorldPoint) -> str:
        if isinstance(destination, str):
            return f"NODE:{destination}"
        return f"WP:{destination.x}:{destination.y}:{destination.plane}"

    def start_route(
        self,
        *,
        origin: str | WorldPoint,
        destination: str | WorldPoint,
        capabilities: AccountCapabilitiesContext,
    ) -> NavigationPlan:
        sig = self._dest_sig(destination)
        # Direct local micro-route for underground dungeons (y >= 5000), upper floors (plane != 0),
        # or short local room steps so dungeon coordinates NEVER snap to overworld surface road nodes!
        if isinstance(destination, WorldPoint) and isinstance(origin, WorldPoint):
            is_dungeon_or_upper = (
                origin.y >= 5000
                or destination.y >= 5000
                or origin.plane != 0
                or destination.plane != 0
                or origin.chebyshev_distance(destination) <= 20
            )
            if is_dungeon_or_upper:
                plan = NavigationPlan(
                    origin_node=origin.label or "LOCAL_ORIGIN",
                    destination_node=destination.label or "LOCAL_TARGET",
                    waypoints=(origin, destination),
                    traversed_edges=(),
                    pruned_edges={},
                    walker_flags=capabilities.to_walker_constraint_flags(),
                    total_cost_tiles=float(origin.chebyshev_distance(destination)),
                )
                self._active_progress[capabilities.account_id] = NavigationProgress(
                    plan=plan,
                    target_signature=sig,
                    current_index=1,
                )
                return plan

        blocked = frozenset(self._blacklisted_edges_by_account.get(capabilities.account_id, set()))
        plan = self.graph.plan_safe_route(
            origin=origin,
            destination=destination,
            capabilities=capabilities,
            blacklisted_edges=blocked,
        )
        # Skip index 0 if already standing at origin node
        start_idx = 1 if len(plan.waypoints) > 1 else 0
        self._active_progress[capabilities.account_id] = NavigationProgress(
            plan=plan,
            target_signature=sig,
            current_index=start_idx,
        )
        return plan

    def step_navigation_on_tick(
        self,
        *,
        current_pos: WorldPoint,
        tick_advanced: bool,
        capabilities: AccountCapabilitiesContext,
        destination: str | WorldPoint,
    ) -> dict[str, Any] | None:
        """Pure non-blocking per-tick navigation controller (ZERO sleep!).

        - Streams the next waypoint smoothly BEFORE the character stops (`dist <= lookahead_tiles`),
          preventing stutter-stepping on dangerous tiles!
        - Automatically replans when `destination` changes across multi-leg missions!
        - If `tick_advanced` is True for `>= 3` consecutive ticks with zero movement toward the active
          waypoint (e.g. stuck on an unmapped wall/obstacle), blacklists the jammed edge and reroutes!
        - Returns a `/walk` payload dict ONLY when a new waypoint needs to be dispatched, else `None`.
        """
        sig = self._dest_sig(destination)
        prog = self._active_progress.get(capabilities.account_id)
        if prog is None or prog.completed or (prog.target_signature and prog.target_signature != sig):
            self.start_route(origin=current_pos, destination=destination, capabilities=capabilities)
            prog = self._active_progress[capabilities.account_id]

        final_wp = prog.plan.waypoints[-1]
        if current_pos.chebyshev_distance(final_wp) <= 1:
            prog.completed = True
            return None

        target_wp = prog.plan.waypoints[prog.current_index]
        # Advance smoothly via lookahead before stopping!
        while (
            prog.current_index < len(prog.plan.waypoints) - 1
            and current_pos.chebyshev_distance(target_wp) <= self.lookahead_tiles
        ):
            prog.current_index += 1
            prog.stalled_ticks_on_segment = 0
            target_wp = prog.plan.waypoints[prog.current_index]

        # Detect stalled ticks when stationary on the exact same tile
        if tick_advanced and prog.last_observed_pos is not None:
            stationary = (
                current_pos.x == prog.last_observed_pos.x
                and current_pos.y == prog.last_observed_pos.y
                and current_pos.plane == prog.last_observed_pos.plane
            )
            if stationary and prog.last_dispatched_index == prog.current_index:
                prog.stalled_ticks_on_segment += 1
            else:
                prog.stalled_ticks_on_segment = 0
        prog.last_observed_pos = current_pos

        force_redispatch = False
        effective_cooldown = (
            min(3, self.walk_redispatch_cooldown_ticks)
            if current_pos.y >= 5000
            else self.walk_redispatch_cooldown_ticks
        )
        effective_reroute_ticks = (
            min(5, self.max_stalled_ticks_before_reroute)
            if current_pos.y >= 5000
            else self.max_stalled_ticks_before_reroute
        )
        stall_gate = (
            min(effective_reroute_ticks, effective_cooldown)
            if not prog.plan.traversed_edges
            else effective_reroute_ticks
        )

        if prog.stalled_ticks_on_segment >= stall_gate:
            if prog.plan.traversed_edges:
                edge_idx = max(0, min(prog.current_index - 1, len(prog.plan.traversed_edges) - 1))
                jammed_edge = prog.plan.traversed_edges[edge_idx]
                acct_blocked = self._blacklisted_edges_by_account.setdefault(capabilities.account_id, set())
                acct_blocked.add(jammed_edge)
                try:
                    self.start_route(origin=current_pos, destination=destination, capabilities=capabilities)
                    prog = self._active_progress[capabilities.account_id]
                    target_wp = prog.plan.waypoints[prog.current_index]
                except NoSafeRouteError:
                    # Single safe corridor (or bridge crossing): keep edge, reset stall counter to 0,
                    # and re-dispatch `/walk` ONLY after full debounce window (>= 5 surface ticks = 3.0s)
                    # so WebWalker is NEVER interrupted every 0.6s!
                    acct_blocked.discard(jammed_edge)
                    if prog.stalled_ticks_on_segment >= effective_cooldown:
                        prog.stalled_ticks_on_segment = 0
                        force_redispatch = True
            elif prog.stalled_ticks_on_segment >= effective_cooldown:
                # Direct 2-point micro-route: debounce `/walk` re-dispatch so WebWalker finishes computing!
                prog.stalled_ticks_on_segment = 0
                force_redispatch = True

        if prog.last_dispatched_index != prog.current_index or force_redispatch:
            prog.last_dispatched_index = prog.current_index
            return {
                "x": target_wp.x,
                "y": target_wp.y,
                "plane": target_wp.plane,
                "wait": False,
                **prog.plan.walker_flags,
            }
        return None

    def get_progress(self, account_id: str) -> NavigationProgress | None:
        return self._active_progress.get(account_id)
