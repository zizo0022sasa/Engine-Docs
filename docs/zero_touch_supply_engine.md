# Zero-Touch Quest Supply Engine (`PR-19`)

> **Constitutional rule (`Zero-Touch Invariant — صفر نقرات ماوس من المشغل`)**
> The operator never touches the mouse, never pre-buys, never pre-banks and never pre-carries a
> single quest supply. **One command line → 0 QP → 12 QP**, fully autonomous.

---

## 1. Why this exists

`PR-18 Lite` (`25d232c`) could *craft* the Goblin Diplomacy mails (`1769`/`1767` onto `288`) but
assumed the dyes and the 3 Goblin mails were already sitting in the bank. That assumption required an
operator purchase — categorically rejected by Zizo & Bolla:

> `"⛔ نرفض الخيار (ب) والتدخل اليدوي رفضاً قاطعاً!"` — *"ما فائدة البوت إذن؟!"*

`PR-19` removes the assumption: the engine obtains **every** supply itself from the world.

## 2. Supply cascade (priority order, per item)

For every required item the module runs `_procure()` in a strict, cheap-first order:

| # | Stage | Cost | Wire command |
|---|-------|------|--------------|
| 1 | **Inventory** | 0 | *(already have it → skip)* |
| 2 | **Bank** (audited once, live, at the GE anchor) | 0 GP, short walk | `BANK_OPEN` → `BANK_WITHDRAW_ITEM` |
| 3 | **World gather / in-world craft** | GP-free, long walk | `WALK_TO_WORLD_POINT` → `INTERACT_OBJECT` / `INTERACT_NPC` / `TAKE_GROUND_ITEM` / `USE_ITEM_ON_ITEM` |
| 4 | **Escalate** with a precise machine reason | — | `ESCALATE_HUMAN` + Telegram P0 |

The bank is audited **exactly once** per session (`_audit_bank_once`) so a level-3 account never
ping-pongs GE ↔ quest site. Everything after that is derived from live `inventory_items`, which makes
the engine **stateless-by-rederivation**: a crash, a relog or a partial tick resumes exactly where it
stopped.

## 3. `Grand Exchange` note (honest status)

Automated `GE_BUY` is **`PLANNED`, not implemented**. The pinned AgentServer exposes **no**
`/grand-exchange` handler, so a purchase would require guessing `Widget 465` child IDs — forbidden by
`EXECUTION MANDATE §Zero Guessing`. Because of that, `PR-19` deliberately builds the
**GP-free world-gather path**, which needs **0 GP** and therefore cannot be blocked by any GE state,
trade limit, or price movement.

## 4.Verified supply graph (every coordinate from OSRS Wiki map pins)

| Item (id) | Source | Node | Coordinates | Wire action |
|---|---|---|---|---|
| Egg `1944` | Groats' farm coop | `LUMBRIDGE_CHICKEN_COOP` | 3169–3185, 3291–3305 | `TAKE_GROUND_ITEM "Egg"` |
| Pot `1931` | Castle kitchen | `LUMBRIDGE_KITCHEN` | 3209,3214 | `TAKE_GROUND_ITEM "Pot"` |
| Bucket `1925` | Castle basement | `LUMBRIDGE_CASTLE_BASEMENT` | 3216,9625 | `TAKE_GROUND_ITEM "Bucket"` |
| Bucket of milk `1927` | Dairy cow | `LUMBRIDGE_COW_FIELD` | 3172,3317 | `INTERACT_NPC "Dairy cow" Milk` |
| Grain `1947` | Mill Lane field | `MILL_LANE_WHEAT_FIELD` | 3303–3311, 3157–3171 | `INTERACT_OBJECT "Wheat" Pick` |
| Pot of flour `1933` | Mill hopper | `MILL_LANE_MILL` | — | `Hopper Fill` → `Hopper controls Operate` → `Flour bin Empty` |
| Shears `1735` | Sheep pen | `LUMBRIDGE_SHEEP_FARM` | 3192,3272 spawn | `TAKE_GROUND_ITEM "Shears"` |
| Wool `1737` ×20 | Sheep | `LUMBRIDGE_SHEEP_FARM` | 3190,3273 | `INTERACT_NPC "Sheep" Shear` |
| Ball of wool `1759` ×20 | Spinning wheel | `LUMBRIDGE_SPINNING_WHEEL` | 3209,3212 **plane 1** | `INTERACT_OBJECT "Spinning wheel" Spin` |
| Cadava berries `753` | Cadava bushes | `VARROCK_CADAVA_BUSHES` | 3264–3277, 3366–3374 | `INTERACT_OBJECT "Cadava bush" Pick` |
| Woad leaves `1766` ×2 | Wyson | `FALADOR_PARK` | Falador park | `INTERACT_NPC "Wyson the gardener"` + 2 dialogue options |
| Onions `1957` ×2 | Fred's garden | `FRED_ONION_GARDEN` | 3188–3191, 3266–3268 | `INTERACT_OBJECT "Onion" Pick` |
| Redberries `1951` ×3 | Wild bushes | `VARROCK_REDBERRY_BUSHES` | south-east Varrock, **east** of the dark wizards | `INTERACT_OBJECT "Redberry bush" Pick from` |
| Blue dye `1767` | Aggie | `DRAYNOR_AGGIE` | Draynor Village | 3 Wiki-verbatim dialogue options |
| Red dye `1763` / Yellow `1765` | Aggie | `DRAYNOR_AGGIE` | Draynor Village | 3 dialogue options each |
| Orange dye `1769` | Craft | inventory | — | `USE_ITEM_ON_ITEM 1763 → 1765` |
| Goblin mail `288` ×3 | Crates | `GOBLIN_VILLAGE` | Goblin Village huts | `INTERACT_OBJECT "Crate" Search` |
| Blue/Orange mail `287`/`286` | Craft | inventory | — | `USE_ITEM_ON_ITEM 1767→288`, `1769→288` |

**Level-3 safety:** all 13 supply nodes are Dijkstra-reachable from `GRAND_EXCHANGE` for a
`combat_level=3`, `agility=1` F2P account. The GE Agility-21 underwall trap and the
Dark-Wizard hazard edge (`min_combat_level=26`) are provably pruned on every route — asserted by
`T74`.

## 5. New wire primitive: `TAKE_GROUND_ITEM`

Backed by the upstream Microbot `GroundItemHandler`:

```
POST /ground-items/pickup  {"name": "Egg"}
→ {"success": true, "item": {"id": 1944, "name": "Egg", "quantity": 1}}
```

It resolves the **nearest** matching ground item, so no coordinate guessing is involved. Full
plumbing: `DirectiveType.TAKE_GROUND_ITEM` → `schemas/command_intent.schema.json` → `GameAdapterGate`
allow-list → `MicrobotHttpAdapter` → `FakeGameAdapter` (world-script hook).

## 6. Self-healing & diagnostics

* **Progress-aware stall reset** — any inventory gain on a step's product resets its attempt budget,
  so `20× Sheer Sheep` or `20× Spin` never trips the guard.
* **Alias rotation** — display names rotate every 6 attempts (`Cadava bush` → `Cadava berry bush` →
  `Cadava bush (wild)`) so one Wiki spelling drift cannot burn the budget.
* **Produce-less steps fire exactly once** (hopper `Fill` is never re-fired into an already-primed
  hopper).
* **Precise escalations** (never a silent loop):
  `SUPPLY_STEP_STALLED:<recipe>:<step>`, `SUPPLY_AREA_UNREACHABLE:<node>`,
  `SUPPLY_CHAIN_EXHAUSTED:<recipe>`, `SUPPLY_RECURSION_LIMIT:<item>`, `SUPPLY_UNSOURCED:<label>`.

## 7. Verification matrix

| Test | Proves |
|---|---|
| `T70` | Engine semantics: recursion, 20× repeat-gather, single-fire produce-less step, unreachable-area escalation |
| `T71` | `TAKE_GROUND_ITEM` → `POST /ground-items/pickup` wire contract + schema allow-list |
| `T72` | **0 → 12 QP from an EMPTY bank and EMPTY inventory**, zero operator input, ends `COMPLETE_TASK` at the GE |
| `T73` | Bank-first economy: with supplies banked, the bot never walks to a gather site |
| `T74` / `T74b` | Every recipe area is level-3 routing-reachable and every node label exists |

Status: **`HERMETIC_VERIFIED`** (81 tests green, full offline world simulation).
`LIVE_WIRE_VERIFIED` requires one attended run on `acc_02` @ `8081`.

## 8. Single launch command

```powershell
python -m osrs_ee launch-slot --account-id acc_02 --adapter-port 8081 `
  --client-jar C:\Microbot\microbot-2.6.34.jar `
  --recipe-id stage1a_f2p_quests_10qp --slot-id 0
```

That is the **entire** operator surface: one command, then walk away. The bot audits the GE bank
once, withdraws anything already banked, gathers everything else in-world, crafts the dyes and the
three Goblin mails, delivers all four quests, deposits leftovers at the GE, and fires a Smart
Telegram alert on `COMPLETE_TASK`.

### Field-observe checklist (read-only, no clicks)
1. `Acc_02` must start **logged in, standing at the GE, bank closed**.
2. Watch the first 40 ticks: `BANK_TURNAROUND(open_only)` → `WAIT_NEXT_TICK` (audit) → `NAVIGATE_TO`.
3. Any `ESCALATE_HUMAN` carries an exact reason string — copy it verbatim into the field log.

---

## 9. `PR-19.1` — Field remediation from the live `acc_02` run (tick 337)

### What the field proved
The first live run took the bot from the GE all the way to `(3134, 3401)` — 120 healthy walk ticks,
10/10 HP, zero operator input — and then **wrongly self-halted**:

```
SUPPLY_AREA_UNREACHABLE:LUMBRIDGE_CHICKEN_COOP
```

Root cause: `max_walk_attempts = 120` counted **ticks**, not **progress**. A 210-tile GE → Lumbridge
trek legitimately needs ~180 ticks once the run bar is empty, so the guard fired on a perfectly
healthy route. The guard was measuring *duration* when the question was *blocked or not*.

### The three surgical fixes

| # | Fix | Before | After |
|---|-----|--------|-------|
| 1 | **Progress-aware walk guard** | escalate after `120` walk ticks | `_walk_progressed()` keeps a per-step **best distance**; every new best **resets** the counter. Only ticks with **no** distance reduction accumulate, against a `350` no-progress backstop |
| 2 | **Run-aware travel** | run handled only globally by the watchdog (`>= 15%`) | while routing, the engine itself returns `SET_RUN_MODE {enabled: true}` as soon as `run_energy_pct >= run_min_energy_pct` (**20%**), and stays silent when run is already on (`POST /run`) |
| 3 | **Plane-safe climbing** *(defect found while verifying Sheep Shearer)* | the `Lumbridge Castle` spinning wheel sits on **plane 1**; the bot would have walked forever at its ground-floor twin | new `CLIMB` step kind: climb the north staircase, then `Spin` on plane 1 — with `SUPPLY_CLIMB_FAILED` as the precise diagnostic |

Both fixes are **strictly anti-false-positive**: a genuinely blocked route still escalates
(`T75b`, `T76` — including distance *oscillation*, which does not count as progress), so the safety
property is preserved, not weakened.

### New verification (`87 passed`)

| Test | Proves |
|---|---|
| `T75` | the exact field trek (GE → coop) survives **181 walk ticks** with zero escalation |
| `T75b` | a frozen bot at the field position `(3134, 3401)` still stops, with the exact old reason string |
| `T76` | rubber-banding between two tiles is **not** progress → still escalates |
| `T77` | run re-enabled at `>= 20%`, silent at `19%`, silent when already running, silent when the adapter reports `None` |
| `T78` | plane-1 supply uses `CLIMB`; `Spin` is never emitted from the ground floor; climb failure escalates precisely |
| `T79` | full kernel run with a drained bar + `2 tiles/tick` realistic client speed: long trek, **2×** `SET_RUN_MODE` (boot + recovery), egg collected, no `SUPPLY_AREA_UNREACHABLE` |

Test-facility addition: `FakeGameAdapter(walk_speed_tiles_per_tick=N)` now models a real client that
keeps walking between ticks (default `None` = legacy instant teleport, so all 81 previous tests are
byte-identical), plus faithful Jagex behaviour where an empty run bar auto-disables run.

---

## 10. `PR-19.2` — Movement-first invariant (field incident #2)

### What the field proved (live `acc_02`, ~992 ticks, stalled at Lumbridge `(3190, 3273)`)
`PR-19.1` let `_travel()` return `SET_RUN_MODE` **instead of** `NAVIGATE_TO`. The client lagged in
reporting `runEnabled=True`, so the engine re-issued the toggle every tick, the character never moved,
and the (correct) progress guard finally declared the route unreachable. **The bot was healthy — the
directive layer was wrong.**

### The invariant
> **A tick that can walk MUST walk. Side actions ride alongside; they never displace movement.**

| Component | Before (`PR-19.1`) | After (`PR-19.2`) |
|---|---|---|
| `_travel()` | could return `SET_RUN_MODE` **instead of** `NAVIGATE_TO` | **always** returns `NAVIGATE_TO` (zero run logic in the engine — statically asserted) |
| Run enabling | engine directive (blocking the walk) | kernel **pipelined side command** in the SAME tick as `WALK_TO_WORLD_POINT` |
| Spam protection | none | shared `travel_run_assist_cooldown_ticks` (10) across assist **and** watchdog/boot path; never two toggles in one tick; danger pulses still immediate |

### New verification (`90 passed`)

| Test | Proves |
|---|---|
| `T77` *(rewritten)* | `_travel()` returns `NAVIGATE_TO` for every run state (`25% off`, `60% on`, `19%`, `None`, `100%`) |
| `T80` | with `runEnabled` frozen to `False` forever (the exact field scenario), the bot still walks 150+ tiles, reaches the coop and collects the egg — **no escalation** |
| `T81` | the tick that enables run also contains `WALK_TO_WORLD_POINT` (side channel, not substitution) |
| `T82` | ≤ 1 toggle per tick and total toggles bounded by the cooldown budget (no per-tick spam) |

`T33` (pre-existing) still passes byte-identically: the low-HP danger tick keeps the exact dispatch
order `("CONSUME_FOOD", "SET_RUN_MODE", "WALK_TO_WORLD_POINT")` — the assist detects the toggle and
stays silent instead of duplicating it.

---

## 11. `PR-19.3` — Collision map / non-walkable destination (field incident #3)

### The evidence (verbatim, `client.log`, 2026-10-29 15:39:43 EEST)
```
[Walker] walk rejected: target WorldPoint(x=3172, y=3317, plane=0) has no walkable tile within 0
    in the collision map (nearest walkable WorldPoint(x=3171, y=3316, plane=0)); check the destination
[WalkerTelemetry] UNREACHABLE cause=target-not-walkable ... totalUnreachable=462
```
`LUMBRIDGE_COW_FIELD` pointed at the tile the **dairy cow entity** occupies. `Rs2Walker` refuses a
destination with `threshold=0`, so **every** `/walk` was rejected and the bot stood still from tick
~1230 until the progress guard (correctly) fired. The route was also forced through that node:
`SHEEP_FARM -> COW_FIELD -> CHICKEN_COOP`.

### Two-layer fix

**Layer 1 — the specific, field-proven correction**
* `LUMBRIDGE_COW_FIELD` → `(3171, 3316)` — the tile the client itself reported as walkable.
* New direct spur `supply_sheep_farm_to_chicken_coop` (34.0) so the coop is reachable **without**
  passing through the cow field at all.

**Layer 2 — the systemic cure (matters more than the coordinate)**
Any node may one day sit under an NPC/object. Edge blacklisting cannot fix that, because the problem
is the **destination itself**. `NavigationEngine` now carries `FINAL_TARGET_SNAP_OFFSETS` — a
deterministic 17-tile ring (origin tile → cardinals → diagonals → radius 2 → radius 3, every offset
within the supply engine's 3-tile tolerance):

* Fires only when the **final waypoint** is what we are commanding (the exact `walk rejected`
  signature), only **on the surface** (`y < 5000`), and only after the **same 20-tick surface budget**
  as the generic reroute — so no existing behaviour fires earlier (`T62` unchanged).
* An independent `stationary_ticks` counter (never reset by the micro-route re-dispatch debounce)
  makes sure the ring can actually reach its threshold, so micro-routes heal too.
* Mid-route jams stay with the edge-blacklist machinery: the ring is never spent on a geometry
  problem it cannot fix.

### New verification (`94 passed`)

| Test | Proves |
|---|---|
| `T83` | cow-field coordinate **is** the walker-verified `(3171, 3316)`; the new direct spur wins the route |
| `T84` | ring starts at the origin tile, is duplicate-free, stays within 3 tiles, covers all 4 directions |
| `T85` | replaying `walk rejected`: single-hop leg **and** micro-route both rotate to an accepted tile |
| `T86` | full kernel replay from the exact field position with the cow tile **and** the coop tile blocked: the bot still collects the egg, zero escalation, `walk_rejections > 0` (proves the ring carried it) |

`FakeGameAdapter` can now model the client faithfully: `unwalkable_tiles` refuses a walk exactly like
`Rs2Walker` (command rejected, character does not move, `walk_rejections` counted).

### Honest limits
* The snap ring explores ±3 tiles; a destination blocked by an object larger than 7×7 will exhaust the
  ring and the engine escalates with the precise `SUPPLY_AREA_UNREACHABLE:<node>` reason.
* Coordinates for the 5 unverified nodes (Aggie, Wyson, redberries, Goblin crates, mill) are still
  approximate; if one of them lands on an object, the ring will now recover it automatically instead of
  halting the session.

---

## 12. `PR-19.4` — Underground classification & authorized exits (field incident #4)

### Field progress before the halt
| Tick | Event |
|---|---|
| 894 | `TAKE_GROUND_ITEM "Egg"` — **Egg (1944) collected** ✅ |
| 1076 | `TAKE_GROUND_ITEM "Bucket"` — **Bucket (1925) collected** in the Lumbridge cellar ✅ |
| 1094 | `UNEXPECTED_DISPLACEMENT_HALT` — climbing the kitchen staircase was misread as a death respawn ❌ |

### Root cause
`y >= 5000` conflated **genuine dungeons** (Stronghold of Security `5100..5400`) with an ordinary
stair-connected **cellar** (Lumbridge basement `y = 9625`). Climbing out is normal play; the guard
treated it as teleport/death.

### Two layers (same philosophy as `PR-19.3`: fix the instance, then fix the class)

**Layer 1 — one shared registry** (`osrs_ee/navigation/underground.py`, new):

| Region | Rect | Authorized exit required? |
|---|---|---|
| `STRONGHOLD_OF_SECURITY` | x 2290–2410, y 5100–5400 | **Yes** (protection preserved) |
| `LUMBRIDGE_CASTLE_BASEMENT` | x 3195–3240, y 9595–9650 | **No** (shallow cellar, own staircase) |
| `UNCLASSIFIED_UNDERGROUND` (fallback) | any other `y >= 5000` | **Yes** — fail-closed for unknown caves |

The watchdog and the supply engine import the *same* table, so they can never drift apart — the
coordinate list is not duplicated.

**Layer 2 — exits are authorized by intent**: `UNDERGROUND_EXITS` maps a region to its stair route, and
`EXIT_UNDERGROUND` steps (plus an implicit guard at the top of `_procure`) emit
`AUTHORIZE_SURFACE_EXIT` — the established Stronghold pattern — before anything else. The engine
refuses to walk from a cave to a surface tile without an exit (`SUPPLY_EXIT_REQUIRED`), so a 40-minute
cross-map `/walk` can never happen.

### New verification (`99 passed`)

| Test | Proves |
|---|---|
| `T87` | the exact tick-1094 transition (cellar → kitchen) is **NOMINAL** |
| `T88` | the Stronghold death-respawn guard **still fires**, and explicit authorization clears it |
| `T89` | unknown caves stay **fail-closed**; a 260-tile in-dungeon jump still halts |
| `T90` | the engine emits `AUTHORIZE_SURFACE_EXIT` from the cellar, walks normally on the surface, and escalates `SUPPLY_EXIT_REQUIRED` only for **unregistered** regions |
| `T91` | full kernel replay: egg → bucket → cellar exit → milk, zero escalation, ends above ground |

---

## 13. `PR-19.5` — Six field-proven corrections (live `acc_02`)

### Confirmed field wins before this patch
| Tick | Result |
|---|---|
| 894 | `TAKE_GROUND_ITEM "Egg"` → **Egg (1944)** ✅ |
| 1076 | `TAKE_GROUND_ITEM "Bucket"` → **Bucket (1925)** ✅ |
| ~811 | `Bucket of milk` → `SUPPLY_STEP_STALLED` ❌ |
| 135 | `IN_DANGER` freeze inside a Highwayman's reach ❌ |
| re-attach | 200-tile walk back to the GE just to re-audit the bank ❌ |

### 1. The dairy cow is **scenery**, not an NPC (the decisive fix)
Live probe evidence:
* `GET /npcs` → only `Cow` (2790) and `Cow calf` (2794) — **neither can be milked**.
* `GET /objects?name=Dairy cow` → `{id: 8689, name: "Dairy cow", position: {3172, 3317}}`.
* `INTERACT_OBJECT {id: 8689, action: "Milk"}` → success, milk appears in the bag.

`Milk` sent to `/npcs/interact` returns `ok:true` but **produces nothing**, so the engine burned its
whole attempt budget. The recipe now uses `SupplyStepKind.OBJECT` with `object_id=8689`, and
`SupplyStep.object_id` is emitted in the payload so the client targets the exact scenery instead of
matching a display name.

### 2. Anti-Safety-Freeze (watchdog)
`EMERGENCY_NO_FOOD_EVACUATE` used to **halt in place**, parking a level-3 account inside an aggressor's
attack range — the freeze itself became the danger. Now:

| Situation | Verdict | Movement |
|---|---|---|
| Surface / shallow cellar, no food | `EMERGENCY_NO_FOOD_FLEE` | **keeps moving**, `enable_run=True` |
| Real dungeon (`requires_authorized_exit`) | `EMERGENCY_NO_FOOD_EVACUATE` | hard halt (unchanged) |

### 3. Log-message bug
The detail string printed the **base** threshold (`HP=8 <= 7` — nonsense) instead of the effective one.
It now always quotes `effective_eat_threshold`, in both the no-food and unconfirmed-eat branches.

### 4. Bank stock survives a restart
`_bank_audit_done` was in-memory, so a re-attach walked 200+ tiles to the GE purely to re-open the
bank. Now the kernel injects the persisted `AccountProfileSnapshot.bank_summary` from `state.db`
(cached once per session) into `TickContext.bank_summary`, and the audit resolves as:

1. live open bank → authoritative;
2. **persisted snapshot → trusted, zero walking**;
3. nothing known → exactly one live audit at the GE.

Bonus: an item **missing** from the persisted bank no longer triggers a GE trip at all — the bot goes
straight out to gather it.

### 5. `VARROCK_EAST_CHURCH` tile
`(3254, 3483)` sits on the church's outer wall → field-measured `(3253, 3482)`.

### 6. Test world-scripts
`Dairy cow` moved from the `INTERACT_NPC` branch to `INTERACT_OBJECT` (asserting `id == 8689`) so the
kitchen-sink world simulation matches the live client.

### New verification (`105 passed`)
| Test | Proves |
|---|---|
| `T92` | cow = OBJECT `8689`, `Milk` action, id present in the emitted payload |
| `T93` | surface/cellar → flee (no halt, run on); dungeon → hard halt preserved |
| `T94` | freeze message quotes the effective threshold, never the base one |
| `T95` | persisted bank → **no GE walk**; empty snapshot → one live audit; missing item → gather, no GE |
| `T96` | church tile is `(3253, 3482)` |
| `T97` | kernel run with `state.db` snapshot: audit restored from disk, flour recognised as banked |

---

## 14. `PR-19.6` — Lumbridge windmill chain & coordinate sanity (field incident #5)

### The funniest bug so far 😅
Tick 221: `SUPPLY_STEP_STALLED:Grain:OBJECT:Wheat`. The recorded wheat-field tile `(3307, 3163)` is
inside the **Al Kharid Palace throne room** — the bot was standing next to `Throne (1108)` and
`Vase (583)` trying to pick wheat from the Emir's carpet.

Root cause: the wiki's raw pages carry several `{{Map}}` pin sets, and I read the wrong one. **The field
probe is the authority; the wiki is a hint.**

### Live-verified Lumbridge windmill ground truth (now in the graph)
| Element | id | Tile |
|---|---|---|
| `Wheat` (`Pick`) | 15506 | (3162, 3296) |
| `Large door` | 1521 / 1524 | (3166, 3302) |
| `Flour bin` (`Empty`) | 1781 | (3166, 3306) plane 0 |
| `Ladder` | 12964 | (3164, 3307) → hopper floor plane 2 |

`supply_cow_field_to_wheat_field` cost corrected `205.0 → 25.0` (they are ~20 tiles apart, not a
trans-desert trek), and a new `supply_mill_to_hopper_floor` edge was added.

### The six-step mill chain (with two real bugs found while validating it)
1. `Large door (1521)` — **Open**, gated on `consumes=(Grain, Pot)` so the bot can never strand itself
   upstairs without ingredients;
2. `Ladder (12964)` — **Climb-up** to plane 2;
3. `Hopper` — **Fill** (grain);
4. `Hopper controls` — **Operate**;
5. `Ladder` — **Climb-down** to plane 0;
6. `Flour bin (1781)` — **Empty** (consumes `Pot 1931` → `Pot of flour 1933`).

**Bug found:** without a latch, the *climb-down* re-armed the *climb-up* step → infinite stair
ping-pong. Fixed with `_done` latch on arrival.

### New verification (`110 passed`)
| Test | Proves |
|---|---|
| `T98` | corrected coordinates; hopper-floor node exists on plane 2; cow→mill < 60 tiles |
| `T99` | exact 6-step order, verified ids, and **exactly one** `Climb-up` (no ping-pong) |
| `T100` | the `Pot` is procured before the bin is emptied |
| `T101` | **regional sanity guard** — every supply node must sit inside its documented rectangle; nothing outside Al Kharid may fall into Al Kharid |
| `T102` | end-to-end kernel run produces `Pot of flour (1933)` with zero escalation |

`T101` is the antidote to this bug class: a transposed digit now fails the suite, not a live run.

---

## 15. `PR-20` — Hopper consumption latch, the 12-QP cascade, and the coin economy (field incident #6)

### 15.1 The incident: dispatch is not completion (tick 289 → 349)

The field run reached the windmill and executed nearly everything perfectly — then halted:

```text
[tick=0289] INTERACT_OBJECT | Fill  Pot of flour source (top floor hopper: Fill with grain)
[tick=0290] INTERACT_OBJECT | Operate Pot of flour source (operate the controls ...)
[tick=0291] INTERACT_OBJECT | climbing to plane 0 for Pot of flour
[tick=0323] INTERACT_OBJECT | Empty Pot of flour source (ground-floor flour bin 1781 (3166,3306))
[tick=0349] ESCALATE_HUMAN | SUPPLY_STEP_STALLED:Pot of flour:OBJECT:Flour bin
```

Post-run inventory scan: `Grain (1947)` was **still in the bag**. The hopper never received it, so the
bin was empty and every `Empty` click was a no-op.

The engine treated *dispatch* as *completion* for every step without a `produces` clause:

```python
# before (PR-19.6) — the defect
if not step.produces and directive.directive_type != DirectiveType.ESCALATE_HUMAN:
    self._done.add(key)          # "done" the instant it was clicked
```

So `Fill` completed on the tick it was issued, `Operate` followed 600 ms later and `Climb-down` 600 ms
after that — while the character was still walking to the hopper. In the real client each interaction
needs 2–3 ticks to path to the object and play the animation.

### 15.2 The fix: a consumption latch + an action-grace window

Two new declarative fields on `SupplyStep`:

| field | meaning |
|---|---|
| `uses_up` | items this step **destroys** in the world. A produce-less step with `uses_up` is *not* done on dispatch — it completes only when the live inventory actually shrinks below the dispatch-time snapshot. |
| `settle_ticks` | ticks the client is left alone after the step before the next step of the chain may be dispatched (`None` = engine default `3`, `0` disables). |

```python
# after (PR-20)
if step.uses_up and not step.produces:
    self._pending_consumption[key] = tuple(
        (iid, _inv_qty(ctx.inventory_items, iid)) for iid, _qty in step.uses_up
    )
    ...
```

The hopper now declares `uses_up=((GRAIN_ID, 1),)`; the mill door keeps `consumes=((Grain,1),(Pot,1))`
with **no** `uses_up` (it needs the items, it does not destroy them) and `settle_ticks=0`.

Anti-false-positive rules:
* while the grain has not been consumed the engine **re-issues `Fill`** (idempotent — "the hopper is
  already full"), walking back to the object first if needed;
* after `max_consume_wait = 16` attempted ticks it escalates with the exact field string
  `SUPPLY_STEP_STALLED:Pot of flour:OBJECT:Hopper` — a genuinely broken chain still stops;
* the settle window is hard-capped (anti-deadlock) in case a client stops reporting ticks.

### 15.3 Finding #2: the spinning wheel stranding (found while building T109/T110)

`Ball of wool` climbed to plane 1 **before** the wool existed. Once upstairs the engine discovered the
wool was missing, went back down to shear, and could never climb again — the stair latch had already
fired. Fix: the climb step now carries `consumes=((WOOL_ID, 20),)`. **Rule: never place a
required-acquisition step above a `CLIMB`.**

### 15.4 Finding #3: no way *into* a cellar (found by the 12-QP pipeline)

`Bucket (1925)` spawns at `(3216, 9625)` — a different map region, not a walk away. The engine only had
`EXIT_UNDERGROUND`. Added `SupplyStepKind.ENTER_UNDERGROUND` (authorized descent, stateless: the live
classification *is* the latch) plus a **recipe-aware** surface guard:

```python
# quests_module._procure — PR-20
if not self.supply.recipe_is_underground(item_id):     # recursive over the ingredient tree
    underground_exit = self._authorize_exit_if_underground(ctx, label)
```

Without the recursion the bot descended for the bucket, and the next request (`Bucket of milk`) dragged
it straight back out — `SUPPLY_ENTER_FAILED:LUMBRIDGE_KITCHEN` at tick 84.

### 15.5 Finding #4: the dyes are not free (the coin economy)

`Goblin Diplomacy` cannot be finished with wishes: **Wyson charges 20 GP** for 2 woad leaves and
**Aggie charges 5 GP per dye**. The engine now declares that cost on the steps themselves
(`consumes=((COINS_ID, 20),)`, `consumes=(..., (COINS_ID, 5))`) and the module withdraws **one float**
(`COIN_FLOAT_GP = 512`) from the bot's **own** Stage-2 earnings while it is still parked at the anchor
bank:

* no operator hand-out, no manual GE purchase — `Zero-Touch` holds;
* the float is a *single* withdrawal, not a trek per dye;
* a genuinely coinless account escalates precisely: `SUPPLY_UNSOURCED:Coins`.

### 15.6 Hardening around the cascade

* **Plane-safe descent** (`_descend_to_ground`): any `_walk_and_talk` destination on plane 0 is now
  reached by climbing down first, with `STUCK_UPSTAIRS:plane_N:<label>` after 12 attempts instead of an
  infinite orbit. The goal latch and the bank anchor use it too.
* **QP latch baseline**: the bundle latches on `all_four_done`, or on `quest_points >= target` only if
  *this run* moved the QP counter. An account that already carried 10+ QP can no longer "complete" the
  bundle without doing it (`T120`).
* **Cadava berries** are re-checked up to the Apothecary hand-off (`state < 50`), so a death/relog no
  longer dead-ends Romeo & Juliet.
* **`_needed_wool(Varp 179)`**: a single source of truth for "20 at the start, `21 - state` after a
  partial hand-in".

### 15.7 Verification (`T103..T121`, 19 new tests)

| test | guarantee |
|---|---|
| `T103` | `Fill` is never done while the grain is in the bag; nothing downstream is dispatched |
| `T104` | `Operate` → `Climb-down` respects the settle window (no mid-animation interruption) |
| `T105` | a hopper that never eats escalates with the exact field string (no infinite loop) |
| `T106` | a step that only *requires* items (the door) is never latch-gated |
| `T107` | kernel run: wheat → mill → flour → `Cook` → `Varp 29 = 2`, `+1 QP`, `COMPLETE_TASK` |
| `T108` | Cook's options selected by verbatim text; unknown option escalates |
| `T109` | shears → 20 shear actions → climb → 20 spins, all self-served |
| `T110` | kernel run to `Varp 179 = 21` and `COMPLETE_TASK` |
| `T111` | Fred's dialogue + live wool demand |
| `T112` | every Romeo stage targets the right NPC on the right plane |
| `T113` | lost berries are re-acquired before the Apothecary |
| `T114` | kernel run to `Varp 144 = 100` (+5 QP), Juliet visited upstairs |
| `T115` | the dye economy: withdrawal from own GP, precise `SUPPLY_UNSOURCED:Coins` |
| `T116` | combine order: red+yellow → orange dye → orange mail → blue mail |
| `T117` | three mails, three searches |
| `T118` | kernel run to `Varp 62 = 6` (+5 QP) with the verified combine wire order |
| `T119` | **full `0 → 12 QP`** from an empty bag and an empty supplies bank, zero escalations |
| `T120` | the QP latch never fires on pre-existing QP |
| `T121` | Zero-Touch audit: the only bank withdrawal is the bot's own coins |

Full suite: **`129 passed`** (`T01..T121`), `0 failed`.
