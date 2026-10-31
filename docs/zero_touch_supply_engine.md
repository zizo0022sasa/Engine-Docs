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

---

## 16. `PR-20.1` — Sheep-pen tether ping-pong & livestock budget (field incident #7)

### 16.1 The incident (live tick 431)

`PR-20` delivered **Cook's Assistant live** (`Varp 29 = 2`, QP `1 → 2`, Cooking `1 → 4`, total level
`35 → 38`) and then transitioned on its own into `Sheep Shearer`: took `Shears (1735)`, sheared, and
had **`1 Wool (1737)`** in the bag — then halted:

```text
[tick=0330] INTERACT_NPC      | Zero-Touch supply: Shear for Wool
[tick=0335] state=IN_DANGER   | Zero-Touch supply: walking to LUMBRIDGE_SHEEP_FARM for Wool
[tick=0370] NAVIGATE_TO       | Zero-Touch supply: walking to LUMBRIDGE_SHEEP_FARM for Wool
[tick=0420] NAVIGATE_TO       | Zero-Touch supply: walking to LUMBRIDGE_SHEEP_FARM for Wool
[tick=0431] ESCALATE_HUMAN    | SUPPLY_STEP_STALLED:Wool:NPC:Sheep
```

### 16.2 Root cause: the pen is an AREA, the engine treated it as a TILE

`LUMBRIDGE_SHEEP_FARM` is a single node `(3190, 3273)` at the corner of Fred's ~20×20 pen, and
`_at_area()` demanded `chebyshev_distance <= walk_tolerance (3)`. Reproduced hermetically before the
fix — a sheep 6 tiles into the pen was already "outside":

| bot position | distance to anchor | pre-fix directive | post-fix directive |
|---|---|---|---|
| (3190, 3273) | 0 | `INTERACT_NPC Sheep` | `INTERACT_NPC Sheep` |
| (3196, 3274) | 6 | **`NAVIGATE_TO` (dragged back)** | `INTERACT_NPC Sheep` |
| (3204, 3264) | 14 | **`NAVIGATE_TO`** | `INTERACT_NPC Sheep` |
| (3209, 3280) | 19 | **`NAVIGATE_TO`** | `INTERACT_NPC Sheep` |

So the loop was: `Shear` → the client walks toward the sheep → out of the 3-tile radius → the engine
marches the bot back to the corner tile → `Shear` → … Every failed shear burned an attempt, and
`MAX_STEP_ATTEMPTS (26)` ran out on a pen that was simply bigger than the tolerance.

> Note: the `IN_DANGER` line at tick 335 is incidental (a `Ram` in the pen puts `under_attack=True`,
> which only raises the eat threshold to 8 and sets `enable_run`). It did **not** replace the task
> directive — `preserve_movement=True` by design (`PR-19.5`).

### 16.3 The fix: the work site is declarative

```python
@dataclass(frozen=True)
class SupplyStep:
    ...
    area_radius: int | None = None     # the WORK SITE is a radius around the node
    max_attempts: int | None = None    # explicit budget for wandering livestock
```

`_at_area()` uses `step.area_radius` when present, else the global `walk_tolerance`; `_emit()` uses
`step.max_attempts` when present, else `max_step_attempts`. Routing is untouched: outside the radius
the engine still issues `NAVIGATE_TO`.

The sheep step now declares:

```python
area_radius=22,    # chebyshev(anchor (3190,3273) -> far pen corner (3212,3258)) = 22
max_attempts=80,   # 20 wandering sheep + "It's not in the mood" refusals
```

22 is not a guess: `T125` asserts the **whole documented pen rectangle** `(3190..3212, 3258..3282)`
sits inside the declared radius, and that the wheat field (25+ tiles away) stays outside.

### 16.4 Anti-false-positive guarantees

* `T123`: a bot in Lumbridge is still routed to the farm, and a tile 23 tiles from the anchor (one
  outside the radius) still walks — the radius never becomes "shear from anywhere".
* `T124`: 30 consecutive failed shears do **not** escalate, but a pen that never yields still halts
  with the exact same string `SUPPLY_STEP_STALLED:Wool:NPC:Sheep` at attempt 81.
* `T127`: **surgical scope** — exactly one step in the entire recipe table carries `area_radius` or
  `max_attempts` (the `Sheep` `Shear`); the wheat-field control proves the default 3-tile gate is
  still enforced elsewhere.

### 16.5 Verification

| test | guarantee |
|---|---|
| `T122` | every documented sheep tile shears; a full loop around the pen never pulls the bot back |
| `T123` | outside the pen the engine still navigates (Anti-false-positive) |
| `T124` | livestock budget: no early escalation, still escalates at its own limit |
| `T125` | the whole pen rectangle fits inside `area_radius` (the `T101` rule, applied to a work site) |
| `T126` | kernel run against **moving** sheep (they hop between documented tiles and refuse 1 in 5) → 20 wool → plane-1 spin → Fred → `Varp 179 = 21` |
| `T127` | surgical scope: only the livestock step is widened |

Full suite: **`135 passed`** (`T01..T127`), `0 failed`.

### 16.6 Known next risks on this leg (pre-declared, not yet fixed)

1. **Spinning wheel object id is still name-matched.** `Ball of wool` spins at `(3209,3212, plane 1)`
   via `INTERACT_OBJECT {"name": "Spinning wheel", "action": "Spin"}`. No live probe has confirmed
   the object id or the action spelling yet. Watch for
   `SUPPLY_STEP_STALLED:Ball of wool:OBJECT:Spinning wheel`; the fix is a live `GET /objects?name=Spinning`
   probe → pin `object_id`.
2. **The castle north staircase** `(3205,3227)` is wiki-derived; if the climb escalates, expect
   `SUPPLY_CLIMB_FAILED:LUMBRIDGE_CASTLE_NORTH_STAIRS` and probe the real stair tile.
3. **Goblin crates are positional in name only.** All three steps search `{"name": "Crate"}` and the
   client picks the *nearest* crate, which may already have been looted. Expect a repeat-search dead
   end on the 2nd/3rd mail.

---

## 17. `PR-20.2` — The Make-X interface & the live spinning-wheel truth (honest post-mortem)

### 17.1 What the live run proved

The `PR-20.1` field run finished **Sheep Shearer live** (`Varp 179 = 21`, QP `2 → 3`, Crafting `1 → 3`,
total level `38 → 40`, 60 GP reward) and rolled straight into `Romeo & Juliet`. Two new ground-truth
facts came back from the probe:

| Element | Live value |
|---|---|
| `Spinning wheel` | object id **`14889`** at `(3209, 3212)` plane 1 |
| Make-X production interface | `groupId 270`, `childId 15` = `Ball of wool` |
| 20 wool → 20 balls | ≈ 36 s of server-side production (make-all) |

### 17.2 The uncomfortable part (stated plainly)

**`Spin` produces nothing.** It opens the Make-X interface; the balls only appear after clicking
`270:15`. My code assumed the object interaction itself yielded all 20 balls — and in the live run
**the field operator clicked that widget by hand**.

So: the quest point is genuinely earned, but that leg was **not** Zero-Touch. `PR-20.2` removes the
need for the human. Any future report of a "successful" run should be read with this question: *which
directives did the engine actually emit, and which did a person emit?*

### 17.3 The fix

A new step kind, plus two declarative fields:

```python
class SupplyStepKind(StrEnum):
    ...
    WIDGET = "WIDGET"          # Make-X / production interface

@dataclass(frozen=True)
class SupplyStep:
    ...
    widget_group: int | None = None     # live: 270
    widget_child: int | None = None     # live: 15
    redispatch_hold: int | None = None  # min ticks between two dispatches of the SAME step
```

`WIDGET` emits `CLICK_WIDGET {groupId, childId, text}` — the kernel already routes that to
`POST /widgets/click`. The ball-of-wool chain is now:

```
Climb-up (needs 20 wool in the bag)
  -> INTERACT_OBJECT {name: Spinning wheel, action: Spin, id: 14889}   # opens Make-X
  -> CLICK_WIDGET {groupId: 270, childId: 15, text: "Ball of wool"}    # make-all, produces 20
```

Three behaviours make this robust:

* **`redispatch_hold = 8`** — make-all takes ~36 s server-side; the click is re-issued at most every
  8 ticks instead of every tick.
* **progress-aware budget** — each ball produced resets the attempt counter, so a 60-tick production
  can never look like a stall (`max_attempts = 120`).
* **self-heal** — after half the budget with no production the engine discards the "interface is
  open" latch and re-issues `Spin`. A window closed by a death or a region hop is reopened instead of
  dead-ending in front of a closed interface.

Also pinned in this release: `SPINNING_WHEEL_OBJECT_ID = 14889`.

### 17.4 Pre-empting incident #8 (arithmetic, not prophecy)

`T127` (surgical scope) now allow-lists **exactly three** widened work sites, and the two new ones are
justified by geometry alone — the same class as the sheep pen:

| step | documented extent | anchor | worst corner | needed radius |
|---|---|---|---|---|
| `Cadava bush` | `(3264,3366)-(3277,3374)` | `(3268,3368)` | `(3277,3374)` → **9 tiles** | `10` |
| `Redberry bush` | two separate wild bushes | `(3240,3358)` | — | `8` |

With the global 3-tile tolerance both would have tethered the bot exactly like the pen, minutes into
the `Romeo & Juliet` leg. `T133` asserts the documented rectangle corners are inside the radius **and**
that a distant bot is still routed (the radius is a work site, not a licence to pick from anywhere).

### 17.5 Verification

| test | guarantee |
|---|---|
| `T128` | the wheel is pinned to live id `14889` on `(3209,3212)` plane 1 |
| `T129` | chain = `Climb-up` → `Spin (id 14889)` → `CLICK_WIDGET {270,15,"Ball of wool"}` |
| `T130` | make-all: ≤ 12 clicks for 20 balls, no escalation, no spam |
| `T131` | a closed interface is reopened (self-heal) instead of dead-ending |
| `T132` | kernel run against a world that **refuses** to produce on `Spin` alone → `Varp 179 = 21` |
| `T133` | cadava/redberry work sites bounded by their documented rectangles |

Harness note: `FakeGameAdapter` now forwards `WIDGET_CLICK` to the world script — a production
interface *applies an effect* on the server, so the hermetic world must see it like any interaction.

Full suite: **`141 passed`** (`T01..T133`), `0 failed`.

### 17.6 Next risks on the running leg (`Romeo & Juliet`)

1. **Cadava bush object id is still name-matched** (`Cadava bush` / `Cadava berry bush` aliases exist).
   Watch for `SUPPLY_STEP_STALLED:Cadava berries:OBJECT:Cadava bush` → probe `GET /objects?name=Cadava`.
2. **Dark wizards** sit immediately west of the cadava cluster: an `under_attack` pulse plus no food
   yields `EMERGENCY_NO_FOOD_FLEE` (surface behaviour, `PR-19.5`). Expected, not a bug — but the bot
   will run rather than fight.
3. **Apothecary dialogue**: the potion requires the berries to still be held; `T113` re-acquires them,
   the live text still needs confirming.

---

## 18. `PR-20.3` — Redberry bushes: the coordinate the world actually has (field incident #8)

### 18.1 The live probe

`GET /objects?name=Redberry bush` on `8081`:

```json
{ "id": 23628, "name": "Redberry bush", "position": { "x": 3272, "y": 3375, "plane": 0 } }
{ "id": 23628, "name": "Redberry bush", "position": { "x": 3277, "y": 3371, "plane": 0 } }
```

The graph node said `VARROCK_REDBERRY_BUSHES = (3240, 3358)` — **~35 tiles south-west, next to the
level-20 Dark Wizard circle**. The live loop (ticks 35..70) was the familiar one:

```text
INTERACT_OBJECT  Pick from Redberries    -> the client starts walking east to the real bush
NAVIGATE_TO      walking to VARROCK_REDBERRY_BUSHES   -> outside the work site, dragged back
INTERACT_OBJECT  Pick from Redberries    -> ...
```

Third member of the same family: incident #5 (throne room), #7 (sheep pen), #8 (redberries). The
common root is always the same — **a coordinate that is not the world's**, harvested from the wiki.

### 18.2 The fix

| item | before | after |
|---|---|---|
| `VARROCK_REDBERRY_BUSHES` | `(3240, 3358)` | **`(3274, 3373)`** — the midpoint of the two live bushes |
| `supply_cadava_to_redberry_bushes` | `30.0` | **`8.0`** (chebyshev is 6; 30 came from the wrong tile) |
| bush identity | name match only | **`REDBERRY_BUSH_OBJECT_ID = 23628`** pinned |
| work site | `area_radius=8` around the wrong tile | **`area_radius=6`** around the midpoint (both bushes ≤ 3 tiles) |
| `T101` regional rectangle | `(3230..3255, 3345..3370)` | `(3260..3290, 3360..3385)` — the old box described the wrong place, so it *accepted* the bug |

Safety side-effect worth stating: the work site moved from **14 tiles** away from the Dark Wizard
circle to **48**. A combat-3 account was being parked inside a level-20 aggressor's neighbourhood.

### 18.3 Verification

| test | guarantee |
|---|---|
| `T134` | both probed bush tiles are inside `area_radius`, the node IS their midpoint, and standing on either bush yields `Pick from` (never a walk) |
| `T135` | live id `23628` pinned, `Pick from` preserved, cadava→redberry edge honest, the node stays routable from the anchor bank |
| `T136` | the work site is ≥ 40 tiles from the Dark Wizards, and the hazard edge is still combat-gated |

Full suite: **`144 passed`** (`T01..T136`), `0 failed`.

### 18.4 The pattern, stated once, for the rest of Stage 1A

Every remaining unknown in `Goblin Diplomacy` is the same shape: **a name-matched object or a
wiki-derived tile**. Before the next field run, these are the suspects, in order of risk:

1. **Goblin crates** — three steps, all `{"name": "Crate"}` at the single node `GOBLIN_VILLAGE
   (2958,3512)`. The client picks the *nearest* crate, which may be the one already looted; and the
   village huts are far wider than the 3-tile tolerance. **Highest risk of incident #9.**
2. **Aggie** `(3087,3232)` — wiki-derived, never probed.
3. **Wyson the gardener** — wanders Falador Park; the node `(3000,3375)` is a single tile in a large
   park (pen geometry again).
4. **General Bentnoze / Wartface** — dialogue options are wiki transcripts, not live text.

---

## 19. `PR-20.4` — Aggie: the house tile, the dialogue text, and the coin float (field incident #9)

### 19.1 Two defects, neither of them a crash

The `PR-20.3` run crafted **`Red dye (1763)` live** (3 redberries + 5 GP) with 55 GP of change left in
the bag — but the probe exposed two things the code had wrong:

| # | Defect | Live truth |
|---|---|---|
| 1 | `DRAYNOR_AGGIE = (3087, 3232)` | Aggie is NPC **`120`** at `(3088, 3259)`; doorstep `(3086, 3258)` — the node was **26 tiles south, at the fishing pier** |
| 2 | dialogue opener `"Can you make dyes for me please?"` | the on-screen option is **`"What could you make for me?"`** |

Both are silent failures by design: the engine happily walked to the pier and talked to empty air, and
its dialogue matcher only selects options it can *see*, so a wrong string just escalates
(`UNRECOGNIZED_QUEST_DIALOGUE_OPTION`) after burning the attempt budget.

### 19.2 Why the `id` was recorded but not used

Aggie is also `NPC 120`, and `INTERACT_OBJECT` pins object ids everywhere. **`INTERACT_NPC` does not**,
and it must not start guessing: `http_adapter` posts the payload verbatim to `POST /npcs/interact`,
and there is **no live proof** that endpoint accepts an `id` field (unlike `/objects/interact`, whose
id handling *is* proven). `AGGIE_NPC_ID = 120` is therefore recorded for the next probe round, and the
interaction stays name-based. **Zero guessing** outranks symmetry.

### 19.3 The coin float was over-eager

`COIN_FLOAT_GP` was `512`, requested at the **top of the bundle** — so a bot that had done nothing yet
trekked to the bank for money it would not spend for another hour. Now:

```python
COIN_FLOAT_GP = GOBLIN_DYE_BUDGET_GP = 20 + 5 * 3   # exactly what the dyes cost
```

* requested **only** when the Goblin stage starts (`_prepare_goblin_diplomacy_supplies`);
* only for the **missing** amount (`budget - held`), so Fred's 60 GP reward covers it entirely;
* a bot that already holds enough GP never touches the bank.

### 19.4 The regression this release caused — and fixed

Making the `PR-19.4` surface guard skip the whole *dependency tree* of an underground recipe was too
coarse. A bot standing in the Lumbridge cellar holding a `Bucket` tried to `/walk` to the Grand
Exchange for a `Bucket of milk` it could produce ten tiles away (`T91` caught it).

The rule is now **situational**, not structural:

```python
supply.needs_descent_now(ctx, item_id)   # some ACTIONABLE step of the (recursive) recipe is underground
```

"Actionable" = its products are not already satisfied **and** its ingredients are obtainable. So:

* at the GE needing a `Bucket` → descent required → guard skipped, the bot goes down;
* in the cellar holding a `Bucket` needing `Bucket of milk` → no descent required → the authorized
  exit still fires (`T91` green again).

### 19.5 Harness truth worth recording

The bot **keeps its change**: after the dyes, `Coins (995)` legitimately stays in the bag (live run
ended with 55 GP). `T69` asserted an exact inventory of `[286, 287, 288]` and now excludes coins —
the `Zero-Touch` invariant is about quest *supplies*, not about the bot's own money.

### 19.6 Verification

| test | guarantee |
|---|---|
| `T137` | Aggie's node is 2 tiles from her NPC, inside the documented area; standing on it talks instead of walking; Draynor bank edge honest (13) |
| `T138` | the live opener is selected for all three dyes; the legacy string survives as an alias; an unknown menu still escalates |
| `T139` | the float is 35 GP, only at the Goblin stage, only for the missing amount, never for a bot that already has change |

Full suite: **`147 passed`** (`T01..T139`), `0 failed`.

### 19.7 Remaining Goblin Diplomacy risk, updated

The two live-proven suspects are now behind us (Aggie, Wyson's dialogue is next). What is left:

1. **Goblin crates** — still three `{"name": "Crate"}` steps at one node `(2958,3512)`; the client
   picks the nearest crate, which may be the looted one. **Highest risk of incident #10.**
2. **Wyson** — wanders Falador Park; the node `(3000,3375)` is a single tile in a large park
   (pen geometry). Also his live dialogue has not been captured yet.
3. **Generals' dialogue** — wiki transcripts, not live text.

---

## 20. `PR-20.5` — The gate that meant "not started" instead of "not finished" (field incident #10)

### 20.1 What happened on the live account

Immediately after `PR-20.4` was pulled, the seventy-tick probe produced this:

```text
[tick=0001] dir=NAVIGATE_TO | Goblin Diplomacy (QP=8): routing safely to General Bentnoze
[tick=0070] state=IN_DANGER | Goblin Diplomacy (QP=8): routing safely to General Bentnoze
```

`GET /varps?id=62` → **`1`**: the quest had been accepted. A level-3 account with an empty bag
(only `Red dye` + 55 GP) was walking 250 tiles toward the Goblin Village, took a wizard's hit at the
Draynor crossroad (`HP 10 → 2`) and was rescued by the field operator into Draynor bank.

### 20.2 The root cause — a one-character semantic error

```python
# before (quests_module.py)
if goblin_state == 0:                                   # only while NOT started
    prep = self._prepare_goblin_diplomacy_supplies(ctx)
    if prep is not None:
        return prep
return self._walk_and_talk(..., "General Bentnoze", ...)
```

Varp `62` moves `0 → 1` the instant the quest is accepted. In state `1` the guard was **false**, so
the entire supply chain — onions → dyes → combines → crates → three mails — was skipped and the very
next directive was the hand-in walk.

This is not a routing bug, a coordinate bug, or a client bug. It is the *cheapest* kind of bug we can
have and the most expensive kind to miss: **the gate described "not started" while the intent was
"not finished".**

### 20.3 The same class existed on Cook's Assistant

```python
if cook_state < 2:
    if cook_state == 0:          # identical defect: Varp 29 == 1 skipped procurement
```

A started-but-unfinished Cook's Assistant (`Varp 29 == 1`) would have walked to the Cook empty-handed
in exactly the same way. Both gates now read "not finished" (`< 6` / `< 2`), and the redundant nested
`if` was flattened.

### 20.4 The structural guard (the part that matters)

Fixing one `==` is worthless if the next quest repeats it. `T141` sweeps **every** quest across
**every** live intermediate varp value with an empty bag and an empty bank, and asserts that the
engine never interacts with a turn-in NPC:

| quest | varp | states swept |
|---|---|---|
| Cook's Assistant | `29` | `0, 1` |
| Sheep Shearer | `179` | `0, 1, 2, 10, 20` |
| Romeo & Juliet | `144` | `0, 20, 30, 40` |
| Goblin Diplomacy | `62` | `0, 1, 2, 3, 4, 5` |

Any future quest added to the bundle should be added to this table; the failure message names the
NPC and quotes the directive, so the diagnosis is immediate.

### 20.5 Verification

| test | guarantee |
|---|---|
| `T140` | the exact live case: `Varp 62 = 1`, empty bag → supply work, never the general; with all three mails held the general is correctly targeted again |
| `T141` | the structural sweep (14 quest-state combinations × 2 economy conditions) |
| `T142` | `Varp 29 = 1` procures egg + milk + flour before visiting the Cook |

Full suite: **`150 passed`** (`T01..T142`), `0 failed`.

### 20.6 Operational note recorded with this release

The bot was rescued at **2 HP with no food**. `ReactiveSafetyWatchdog` behaved exactly as designed
(`EMERGENCY_NO_FOOD_FLEE` on the surface keeps it running instead of freezing inside an aggressor's
range, `PR-19.5`), and natural regeneration inside the bank brings it back to 10/10 — but the mission
should not resume at 2 HP. Wait for full HP (or stage food with `--require-food-count`) before the next
`attach-bot` run. The engine cannot out-heal a level-20 wizard; it can only out-run one.

---

## 21. `PR-20.6` — The death tax, and why no operator money was ever needed (field incident #11)

### 21.1 What the field actually lost

Incident #10 killed the account. OSRS keeps only the three most valuable items on death, so the bot
respawned in Lumbridge with `Red dye` + `Shears` + **1 GP**; the other 54 GP of Fred's reward stayed on
the ground. Outstanding dye budget: `5 (yellow) + 20 (woad) + 5 (blue) = 30 GP`.

### 21.2 The two field proposals, and why one of them is off the table

| proposal | verdict |
|---|---|
| **Put 50 GP into the account / sell the bow+axe by hand at the shop** | ❌ **Rejected.** This is an operator hand-out and an operator mouse click — precisely what the ratified `Zero-Touch Invariant` forbids (`"⛔ نرفض الخيار (ب) والتدخل اليدوي رفضاً قاطعاً!"`). The protocol exists so the project proves an engine, not a puppeteer. |
| **PR-20.6: the bot restores its own funds** | ✅ Accepted — and it turned out not to need a shop at all |

### 21.3 The account "was never broke" — RETRACTED by field evidence

> ⚠️ **`RETRACTED` (field incident #12).** This section originally asserted that `acc_02` still held
> the **10,000 GP** banked by `PR-17.5`'s Stronghold run. The operator's live probe destroyed that
> assumption outright:
>
> ```
> GET /bank -> {"open": true, "count": 6, "items": [Shortbow(841), Wooden shield(1171),
>              Bronze axe(1351), Small fishing net(303), Raw shrimps(317), Fancy boots(9005)]}
> ```
>
> **Zero coins.** `Fancy boots (9005)` proves the Stronghold deposit *happened*; the coins are gone
> (spent or lost afterwards — the record cannot say which). The engine's response was correct and
> immediate: `[tick=0002] dir=ESCALATE_HUMAN | SUPPLY_UNSOURCED:Coins`, with **no** phantom 200-tile
> trek. Only the documentation was wrong, and this is the same rule that demoted every wiki tile:
> **field ground truth outranks our own documents.**
>
> Lesson recorded: never state bank contents without a fresh `GET /bank` in the same breath.

Two bugs in the *funding* path were real regardless, and were fixed:

1. **`_coin_float_resolved` was a one-way latch.** Once the bot had been funded, it could never be
   funded *again*. After the death it kept asking Aggie for a dye it could not pay for, forever.
2. **Funding only knew the anchor bank.** Standing 13 tiles from `DRAYNOR_BANK`, the engine insisted
   on the Grand Exchange anchor **200+ tiles away** — through the corridor that had already killed the
   account once.

### 21.4 The fix

```python
# 1. re-derive the purse every tick (same stateless rule as the supply engine)
held = _inv_count(ctx.inventory_items, item_id=COINS_ID, noted=False)
if held >= self.COIN_FLOAT_GP: ... return None
self._coin_float_resolved = False

# 2. coin funding banks at the NEAREST verified bank
BANK_NODES = ("DRAYNOR_BANK", "VARROCK_WEST_BANK", "FALADOR_WEST_BANK",
              "EDGEVILLE_BANK", "AL_KHARID_BANK", "GRAND_EXCHANGE")
```

The nearest-bank rule is **scoped to funding only** (`nearest=(item_id == COINS_ID)`). The one-time bank
audit and the mission anchor keep their original semantics — a first attempt that applied the rule
everywhere broke five tests (`T86`, `T91`, `T95`, `T102`, `T107`) by dragging the audit walk to another
town. Surgical scope, again.

Also accepted: `_audit_bank_once` now treats **any** of the six verified banks as a valid place to take
the live audit (previously a hardcoded GE distance check), so opening the Draynor bank counts.

### 21.5 Shop selling — stated honestly as `PLANNED`, not implemented

Liquidating banked gear at a general store is a legitimate *future* self-funding tier, but it is **not
implementable without guessing**: the sale is a shop-interface click, and while `WIDGET_CLICK` is live
proven for Make-X (`270:15`), the shop's `groupId`/`childId` pairs and the shopkeeper's live dialogue
text have never been captured. Guessing widget coordinates is forbidden by the execution mandate.

Unblocking it needs exactly one **read-only probe** (no mouse, no gameplay):

```bash
curl -s "http://127.0.0.1:8081/widgets?groupId=<shop>"   # with a shop open in the client
```

Until that exists, `PR-20.6` deliberately stops at "withdraw from your own bank", which is sufficient
for this account and keeps the invariant intact.

### 21.6 Verification

| test | guarantee |
|---|---|
| `T143` | funding routes to the nearest bank (Draynor from Draynor, Falador West from the north-west) |
| `T144` | the withdrawal is exactly the missing amount (34 of a 35 GP budget) from the bot's own bank |
| `T145` | a funded bot that dies asks the bank again (the latch is gone) and settles once topped up |
| `T146` | an account with no coins anywhere never ping-pongs between banks (revised by `PR-20.7`: it earns) |

Full suite: **`154 passed`** (`T01..T146`), `0 failed`.

---

## 22. `PR-20.7` — Zero-Touch coin EARNING (field incident #12)

### 22.1 The constraint that decided everything

After the bank came back empty, two field proposals were on the table:

| proposal | verdict |
|---|---|
| **Operator drops 50 GP into the account, or sells the banked bow + axe at the general store by hand** | ❌ **REFUSED.** A hand-out *and* a manual click — the two things the ratified `Zero-Touch Invariant` exists to forbid (`"⛔ نرفض الخيار (ب) والتدخل اليدوي رفضاً قاطعاً!"`). Accepting it would prove a puppet-master, not an engine. |
| **The engine earns the 30 GP itself** | ✅ Implemented here |

### 22.2 Why pickpocketing, and not the shop

Every other income path needs an interface we have never seen live:

| path | blocker |
|---|---|
| sell banked gear at a general store | the sale is a **shop-interface widget click**; the shop's `groupId`/`childId` and the shopkeeper's dialogue text are unobserved. `WIDGET_CLICK` is proven for Make-X `270:15` only. Guessing widget ids is forbidden. |
| Grand Exchange | no `/grand-exchange` endpoint exists in the AgentServer at all |
| `Doric's Quest` (180 GP reward) | needs `Mining 15` for the iron ore |
| Stronghold chests | one-time rewards, already collected (`Fancy boots` is the proof) |
| **pickpocket a citizen** | **nothing new**: `Pickpocket` is a name-based `action` on `/npcs/interact` — the exact call shape already `LIVE_WIRE_VERIFIED` for `Talk-to` (Aggie/Wyson/Fred) **and** `Shear` (Sheep, 20 wool). |

`Shear` is the decisive precedent: a non-dialogue action verb, name-resolved, already proven on the
live wire. `Pickpocket` rides the same rail. A citizen yields **3 GP**, and there is no downside risk
for a level-3 account: a failed pickpocket only stuns.

### 22.3 The engine rule

```
bank has the coins  -> withdraw (PR-20.6, nearest bank)
bank is empty       -> EARN: pickpocket citizens at the Draynor village centre
nothing reachable   -> SUPPLY_UNSOURCED:Coins:pickpocket_exhausted(<n>)
```

Details that matter:

* **Demand-driven, three levels deep.** Coins are only pursued while an outstanding goblin mail still
  needs a dye that is neither in the bag nor in the known bank (`_coins_still_needed`). A bot whose
  `Orange dye` + `Blue dye` are already banked never pickpockets at all (`T151`).
* **Bounded.** `COIN_EARN_ATTEMPT_CAP = 80`; on exhaustion it disarms (`_coin_float_unavailable`) and
  the dye step reports the precise blocker. No infinite loop (`T149`).
* **Where it stands.** `COIN_EARN_NODE = DRAYNOR_BANK`, `COIN_EARN_RADIUS = 14`: the village centre the
  bot is already in, with the dark wizards outside the radius. Earning never walks into the corridor
  that killed the account.
* **Stateless purse.** The earning target is re-derived from the live inventory every tick; three
  pickpockets pay for the yellow dye alone, twelve pay the whole budget.

### 22.4 Fakes were made faithful, not weakened

The first run of the new rule broke four older tests. Every one of them was a **fake limitation**,
never an engine regression:

* `tests/test_pr18_...goblin_dye_crafting.py` (`T68`) — the hand-written wire stub only answered
  `/npcs/interact` for `General Bentnoze`. It now models `Pickpocket -> +3 GP`, exactly as the real
  mechanic does, and its Bentnoze assertion excludes leftover coins (the bot keeps its change — the
  same relaxation `T69` already carries).
* `osrs_ee/runtime/fake_adapter.py` — `INTERACT_NPC` now models `Pickpocket -> 3 GP`, deterministically
  (a fake proves the chain, never the dice).

### 22.5 Verification

| test | guarantee |
|---|---|
| `T147` | the live regression: an empty bank makes the bot EARN instead of escalating at tick 2 |
| `T148` | proven-shape `Pickpocket`, target alternation, and an immediate stop at the 35 GP budget |
| `T149` | bounded failure: `SUPPLY_UNSOURCED:Coins:pickpocket_exhausted(80)`, then disarmed |
| `T150` | the whole 0 GP → funded run, tick by tick: 12 successful pickpockets, nothing withdrawn |
| `T151` | anti-false-positive: dyes already banked ⇒ zero pickpockets |

Full suite: **`159 passed`** (`T01..T151`), `0 failed`.

---

## 23. `PR-20.8` — Incident #13: a rejected interaction is not an unlucky one

### 23.1 The live log

`PR-20.7` shipped and the bot did exactly what it was told:

```
[tick=0026] dir=INTERACT_NPC | Zero-Touch earnings: pickpocketing Man (0/35 GP, attempt 1)
[tick=0027] dir=INTERACT_NPC | Zero-Touch earnings: pickpocketing Woman (0/35 GP, attempt 2)
 ...
[tick=0035] dir=INTERACT_NPC | Zero-Touch earnings: pickpocketing Man (0/35 GP, attempt 10)
```

`POST /npcs/interact {"name": "Man", "action": "Pickpocket"}` → `{"success": false, "reason": "NPC not found"}`.

A live `GET /npcs?maxDistance=30` census settled it: Draynor holds `Banker`, `Bank guard`,
`Wise Old Man`, `Miss Schism`, `Ned`, `Aggie`, `Diango`, `Fortunato` — **no `Man`, no `Woman`**.

### 23.2 Two defects, and the second one is the dangerous one

1. **Wrong location.** `PR-20.7` assumed a citizen-rich village because the bot happened to be parked
   there. It never asked. Fix: `COIN_EARN_NODES = ("LUMBRIDGE_CASTLE_COURTYARD", "VARROCK_SQUARE")` —
   the field-confirmed hub (level-2 citizens, zero risk at level 3), already a live graph node
   `(3222,3218,0)` with edges to the kitchen, the sheep farm and the north stairs.

2. **The engine was blind to the outcome.** `_earn_coins` incremented `_coin_earn_attempts` and fired
   the next pickpocket *whether the last one worked or not*. A rejected interaction and an unlucky one
   are **the same input** to a counter — so the bot would have spent all 80 attempts against an NPC
   that was never there and only then escalated, after ~80 silent ticks. This is the same defect family
   as incident #10: **the engine could not tell "finished" from "started", and here it cannot tell
   "refused" from "unlucky".** Watch the *effect*, never assume the *action*.

### 23.3 The fix: strike counting on the purse, not on the call

```
1 new GP        -> strikes = 0          (a slow-but-working hub is never abandoned — T155)
+6 silent tries -> relocate to the next hub
last hub silent -> SUPPLY_UNSOURCED:Coins:no_progress(<node>)   (one line, not eighty)
80 attempts     -> SUPPLY_UNSOURCED:Coins:pickpocket_exhausted(<n>)   (unchanged backstop)
```

Relocating does **not** consume the pickpocket budget, and the baseline purse is adopted once
(`_coin_best_gp = -1` means "not observed yet") so establishing it never wipes strikes already earned.

### 23.4 Verification

| test | guarantee |
|---|---|
| `T152` | the hubs are the courtyard + Varrock square; Draynor is explicitly excluded; the walk is routable |
| `T153` | six rejected pickpockets force a **relocation**, not a 7th attempt and not the 80-tick burn |
| `T154` | the last silent hub ends in `no_progress(VARROCK_SQUARE)` and the bot disarms |
| `T155` | one successful pickpocket clears five strikes (bad luck ≠ wrong target) |

Full suite: **`163 passed`** (`T01..T155`), `0 failed`.

---

## 24. `PR-20.9` — Incident #14: an unlucky roll is not a barren courtyard

### 24.1 The live log (eight ticks that cost a 250-tile march)

```
[tick=0002] dir=INTERACT_NPC | pickpocketing Man at LUMBRIDGE_CASTLE_COURTYARD (0/35 GP, attempt 1)
[tick=0003] dir=INTERACT_NPC | pickpocketing Woman at LUMBRIDGE_CASTLE_COURTYARD (0/35 GP, attempt 2)
[tick=0004] state=IN_DANGER | ... attempt 3
[tick=0005] state=IN_DANGER | ... attempt 4
[tick=0006] state=IN_DANGER | ... attempt 5
[tick=0007] state=IN_DANGER | ... attempt 6
[tick=0008] dir=NAVIGATE_TO | no coins from LUMBRIDGE_CASTLE_COURTYARD in 6 attempts; relocating to VARROCK_SQUARE
```

The location was **right** this time: the live census found **6 men and 3 women**, the nearest one tile
away, at `(3219,3219)`.

### 24.2 The game rule the engine ignored

A **failed** pickpocket stuns the player for about **3.0 seconds (5 game ticks)** — the little birds
circling the head — and during the stun the client refuses any further attempt.

`PR-20.8` issued one attempt **per tick**. So the six "strikes" were really **one unlucky roll plus
five interactions swallowed by the stun**, all inside 3.6 seconds. The engine then reasoned: *six
attempts, zero coins, this place is barren* — and marched a level-3 character 250 tiles to Varrock
square, through the dark-wizard corridor.

This is the **third** member of one family, and the family is the point:

| incident | the engine confused | with |
|---|---|---|
| `#10` | "started" (`Varp 62 = 1`) | "finished" |
| `#13` | "refused" (`NPC not found`) | "unlucky" |
| `#14` | "unlucky" | "barren" — and acted on it with a trek |

Every one of them is the same original sin: **trusting the call was sent instead of measuring the
world**.

### 24.3 The fix

```python
COIN_EARN_SETTLE_TICKS = 5     # the stun window; one attempt per window
COIN_EARN_NODE = "LUMBRIDGE_CASTLE_COURTYARD"   # single field-confirmed hub
```

* **Action grace.** After each attempt the bot emits `WAIT_NEXT_TICK` for the rest of the window. A
  waiting tick is **not** a strike — `_coin_no_progress` only moves when an attempt is actually issued.
  The wait reason carries the countdown (`letting the pickpocket stun expire (2/5 ticks)`) so an
  operator can see the pacing working.
* **No dangerous fallback.** `COIN_EARN_NODES` is gone; `VARROCK_SQUARE` was removed deliberately. If
  the courtyard is genuinely barren after six *fairly spaced* attempts, the engine reports
  `SUPPLY_UNSOURCED:Coins:no_progress(LUMBRIDGE_CASTLE_COURTYARD)` — one line, zero walking. Varrock
  square is a fine hub for a level-30 account; it is not one for this account, and the engine may not
  gamble the character to avoid admitting defeat.
* **Danger is not yours to override.** While `ctx.in_danger` the earning loop holds
  (`WAIT_NEXT_TICK`, no attempt, no strike) and the safety layer owns the tick. The live log issued
  pickpockets through five consecutive `IN_DANGER` ticks.

### 24.4 What the false-alarm budget now is

With the stun respected, six consecutive genuinely-failed pickpockets happen with probability
`~0.5^6 ≈ 1.6%` at level 1 — and the consequence is a **safe stop with a precise reason**, not a trek.
That is the right trade: a stopped bot is recoverable in one operator command, a dead one is not.

### 24.5 Verification

| test | guarantee |
|---|---|
| `T152` | single field-confirmed hub; Varrock excluded; `COIN_EARN_NODES` no longer exists |
| `T153` | attempts are paced by the stun window; a waiting tick adds no strike |
| `T154` | a barren hub reports `no_progress(LUMBRIDGE_CASTLE_COURTYARD)` and disarms — never a trek |
| `T155` | one successful pickpocket clears five strikes |
| `T156` | an `IN_DANGER` tick never starts a new pickpocket and never burns a strike |

Full suite: **`164 passed`** (`T01..T156`), `0 failed`.

---

## 25. `PR-21` — Incident #15: Thieving is members-only, and three PRs died for it

### 25.1 The retraction

`PR-20.7`, `PR-20.8` and `PR-20.9` taught the bot to pickpocket Lumbridge citizens for the last
**30 GP** of the Goblin Diplomacy dyes. They fixed a wrong location, added a progress watcher, and
paced attempts around the stun window. All three were well-engineered and **all three were worthless**:

> OSRS Wiki, `Members` — *"Out of the 24 skills in Old School RuneScape, nine of them are only
> available in members' worlds: Herblore, Hunter, **Thieving**, Slayer, ..."*

Live confirmation: every attempt reached the client (`{"ok": true}`), and
`GET /skills -> {"name": "Thieving", "level": 1, "xp": 0}` never moved. **A free-to-play world refuses
the skill outright.**

### 25.2 Why the engine could not have caught this on its own

`POST /npcs/interact` answers `{"ok": true}` whenever the call reaches the client — whether the game
performs the action or refuses it. There is no error field for "members only", no XP delta the engine
can read mid-tick, and no way to distinguish a refusal from a slow response. **The engine cannot see a
legality refusal.** Therefore the rule has to live in the bundle's *contract*, enforced at review time.

Hence `osrs_ee/bots/f2p_legality.py`: `MEMBERS_ONLY_SKILLS`, `MEMBERS_ONLY_ACTIONS`,
`assert_f2p_safe()`, and `scan_module_source()` — a source scan (string literals only, so the comment
explaining the ban does not trip it) that `T157` runs over the whole bots package.

The pickpocket path is **deleted, not disabled**: `_earn_coins`, `COIN_EARN_*`, `PICKPOCKET_TARGETS`,
the `FakeGameAdapter` shim and the `T68` wire shim are all gone, and `T147..T156` are retired with this
commit.

### 25.3 What the engine reports now

```
SUPPLY_UNSOURCED:Coins:short_by_34GP:next=DORIC_QUEST_180GP
```

An exact number and a named way out. And a funding escalation **never stalls the free half of the
stage**: the plain brown goblin mail costs nothing, so the bot keeps gathering it while the money
question is open (`T159b`).

### 25.4 The `10,000 GP` audit — closed

The live `GET /bank` on `acc_02` returns six items, all Tutorial-Island starter gear plus
`Fancy boots (9005)`, and **zero coins**. `Fancy boots` proves `PR-17.5` really did clear the Stronghold
and really did deposit; the coins were never in the vault. Recorded as: **the "10,000 GP banked" claim
in `PROGRESS.md` was never verified by a probe and must not be cited as fact.** (A second audit finding
sits next to it: `_bank_audit_done` latched forever, so a bank audited *before* a payout stayed
"empty" for the rest of the run — the same one-way-latch family as `_coin_float_resolved`. The snapshot
now merges monotonically, and an un-reported `bank_items` is treated as silence, never as zero.)

### 25.5 The one legal F2P income, and what it costs

**Doric's Quest** is the only free quest that pays coins with **no requirements**:

| | |
|---|---|
| reward | **180 coins** + 1 QP + 1300 Mining XP |
| items | 6 clay · 4 copper ore · 2 iron ore |
| tool | Doric **gives a bronze pickaxe** if you have none |
| gate | Mining **15** — only to *mine* the iron |
| danger | Rimmington mine is safe; the Dwarven Mine has scorpions |

180 coins against a 35-coin budget: a 5× margin, re-earnable, deterministic, and 100% zero-touch.

Status: `PLANNED`. Building it needs three **read-only** probes first — the wiki is a hint, the probe is
truth:

1. `GET /skills?id=14` → the account's **Mining level** (iron ore needs 15; if it is below, the chain
   must train first).
2. `GET /objects?name=Iron rocks&maxDistance=40` at Rimmington / Varrock east → the **rock object ids**.
3. `GET /dialogue` while talking to **Doric** → his live option texts (the same discipline that produced
   `"What could you make for me?"` for Aggie).

No tile, id or dialogue line will be committed from the wiki.

### 25.6 Verification

| test | guarantee |
|---|---|
| `T157` | no module in `osrs_ee/bots/` may contain a members-only action or skill as a literal |
| `T158` | the guard raises rather than trusting a `{"ok": true}` from the client |
| `T159` | a broke account reports `short_by_34GP:next=DORIC_QUEST_180GP` — once |
| `T159b` | a funding dead end never stalls the free half of the goblin stage |
| `T160` | the carried purse is capped at the working float, so a death costs the float, not the account |

Full suite: **`159 passed`**, `0 failed` (`T01..T146`, `T157..T160`; `T147..T156` retired with the
members-only path).

---

## 26. `PR-21.1` — `OPERATOR_FUNDED_RECOVERY`: the operator's decision, made mechanical

### 26.1 The decision, recorded

The operator holds the authority and exercised it. `acc_02` is funded **by hand, once**, as
compensation for a loss the engine caused:

> **`OPERATOR_FUNDED_RECOVERY (50 GP — bug compensation for Incident #10)`**

This is not a re-opening of the `Zero-Touch Invariant`. It is the invariant's own recovery clause: when
the engine itself destroys value through a defect, the operator may restore the account to the state the
defect stole — **once, labelled, and never counted as an engine achievement.**

### 26.2 The numbers, verified (the scope case was correct)

| claim | verified |
|---|---|
| Mining level 15 needs `2,411` XP from zero | OSRS Wiki, *Free-to-play Mining training* |
| copper/tin ore = `17.5` XP each | same |
| zero → 15 = **138** ores; with the account's existing `35` XP ≈ **136** ores | same |
| iron ore is gated behind Mining **15** | same |

So the Doric's Quest route is not "a few rocks": it is a **mining subsystem** (pickaxe acquisition,
~136-gather loop with drops, Rimmington routing) that exists only to recover 30 GP. Correctly classified
as out-of-scope for a 10-QP bundle. And note the circularity: Doric's Quest *rewards* 1,300 Mining XP —
but it cannot be **started** without ore that needs the level that reward would grant.

### 26.3 What `PR-21.1` adds: provenance, not permission

The danger in an operator-funded run is not the 50 GP. It is that six months from now, someone reads
`COMPLETE_TASK` and believes a level-3 account cleared 13 QP unaided. So the bundle now records
**where the purse came from**, mechanically:

```python
class FundingProvenance(str, Enum):
    UNKNOWN                    # nothing observed yet
    ZERO_TOUCH                 # the engine issued its own BANK_TURNAROUND withdrawal
    PREEXISTING_CARRY          # whole purse on first observation — attributed, never claimed
    OPERATOR_FUNDED_RECOVERY   # a shortfall was PROVEN, then coins appeared with no directive of ours
```

`COMPLETE_TASK` carries it:

```json
{"funding_provenance": "OPERATOR_FUNDED_RECOVERY", "funded_gp": 50, "quest_points": 13}
"Stage 1A Quest Bundle completed (QP=13) and anchored at GRAND_EXCHANGE [funding=OPERATOR_FUNDED_RECOVERY]"
```

Design note worth keeping: the "give up" latch (`_coin_float_unavailable`) is now checked **after** the
purse. A latch that stops the engine re-asking must never stop it *seeing* — the first draft did exactly
that and `T161` caught it.

### 26.4 The acceptance split

| account | status the engine will print | what it means |
|---|---|---|
| `acc_02` (this run) | `[funding=OPERATOR_FUNDED_RECOVERY]` | the quest work is real; the money was restored by the operator after a proven defect |
| `acc_01` (next) | `[funding=ZERO_TOUCH]` required | the real acceptance: 0 GP start, Sheep Shearer's 60 GP, 13 QP, nobody touching anything |

`Stage 1A` is only `LIVE_WIRE_VERIFIED` when the second row prints. The first row is
`OPERATOR_FUNDED_RECOVERY` — accurate, documented, and not a pass.

### 26.5 Verification

| test | guarantee |
|---|---|
| `T161` | shortfall proven + coin infusion with no directive ⇒ `OPERATOR_FUNDED_RECOVERY`, amount recorded |
| `T162` | an engine-issued withdrawal ⇒ `ZERO_TOUCH` |
| `T163` | a purse full on first sight ⇒ `PREEXISTING_CARRY`, never claimed |
| `T164` | `COMPLETE_TASK` payload and reason both carry the tag |

Full suite: **`163 passed`**, `0 failed` (`T01..T146`, `T157..T164`).

---

## 27. Fleet triage — two accounts, two different verdicts

### 27.1 The decision

| account | role | required verdict |
|---|---|---|
| `acc_01` (`[GAME-02]`, clean) | **the acceptance run** — first | `[funding=ZERO_TOUCH]` |
| `acc_02` (`[GAME-01]`, damaged) | the trophy run — second | `[funding=OPERATOR_FUNDED_RECOVERY]` |

Run the **clean** account first. Not for ceremony: `PR-21.1` makes provenance mechanical, and only
`ZERO_TOUCH` proves the bundle works. A `RECOVERY` completion proves the *quest logic*; a `ZERO_TOUCH`
completion proves the *system*. Doing the expensive proof first also means the operator's 50 GP becomes
optional cleanup instead of a prerequisite.

### 27.2 Three pre-flight facts from `state.db` (2026-10-30)

1. **`acc_01` has never been scanned.**
   `status = PENDING_DISCOVERY`, `stage = STAGE_0_TUTORIAL`, **`tutorial_varp_281 = NULL`**.

   The quest bundle refuses to run below tutorial completion:
   `PRECONDITION_FAILED_TUTORIAL_VARP_281_INCOMPLETE`. A `NULL` here is not "probably fine" — it is
   **unknown**, and unknown must be measured before 3,000 ticks are committed to it.
   → `scan-profile` first; if `varp 281 < 1000`, run `--recipe-id stage0_tutorial_island` first.

2. **`acc_02`'s row is stale and contradicts the live census.**
   The row (written `2026-10-28T04:10:30Z`) claims `quest_points = 10`, `total_level = 110`,
   `stage = STAGE_3_F2P_SKILLING`; the live character reports **8 QP, total 40**. Two days of drift, and
   a claim that was never probe-verified — the same failure mode as the "10,000 GP banked" line retired
   in §25.4.

   Nothing breaks: the QP latch is **baseline-relative** (`qp > baseline`), so a stale high value cannot
   fake a completion. But `status`/`--status` output must not be used for decisions until re-scanned.

3. **Port isolation is a safety rule, not housekeeping.**
   `acc_02` is bound to **8081**. The adapter identifies the character by **port**, never by the
   `--account-id` string. Launching a second client on 8081 means the engine drives whichever character
   answers — while the ledger writes another account's name next to it. That is a *silent* cross-account
   contamination, and it is exactly the class of bug that produces a false `LIVE_WIRE_VERIFIED`.

   > **Rule: one character per port. `acc_01` → `8082` (slot 1). `acc_02` → `8081` (slot 0).**
   > Never two clients on one port, and never re-use a port while its previous client is still logged in.

### 27.3 The runbook

```bash
# 0. isolate: close acc_02's client, or leave it — but acc_01 MUST take 8082
python -m osrs_ee live-probe   --port 8082 --account-id acc_01      # read-only handshake
python -m osrs_ee scan-profile --port 8082 --account-id acc_01 --read-only
#    -> check tutorial_varp_281 >= 1000 and the real QP/total level
#    -> if varp 281 < 1000:  attach-bot --recipe-id stage0_tutorial_island --port 8082 --account-id acc_01

python -m osrs_ee attach-bot --account-id acc_01 --recipe-id stage1a_f2p_quests_10qp \
       --port 8082 --slot-id 1 --max-ticks 8000
```

Expected final line:

```
Stage 1A Quest Bundle completed (QP=13) and anchored at GRAND_EXCHANGE [funding=ZERO_TOUCH]
```

Budget honestly: a clean account has to shear **20 sheep**, spin **20 balls of wool**, run the
flour/milk/egg chain and the cadava-berry trip before the dyes even start. This is **hours of game
time**, not ten minutes. `--max-ticks 8000` is a ceiling, not an estimate.

### 27.4 Credential hygiene, restated

Account credentials, 2FA codes and mailboxes never enter this repository, `PROGRESS.md`, a commit
message, or this transcript. The vault holds them (`vault://accounts/...`), and `credential_ref` is the
only identifier any tracked file may carry.

---

## 28. PERMANENT INVARIANT — Stage 1A zero-touch economics (`AD-1`)

> **Status: RATIFIED BY THE OPERATOR. Do not revise without new field evidence.**
> Every number below was checked against the OSRS Wiki before it was written down; the checks are
> cited so the next reader can re-run them instead of trusting this text.

### 28.1 `AD-1.1` — The 60 GP ground truth

Turning in **20 balls of wool** to Fred the Farmer (`Varp 179 = 21`) pays exactly **60 GP straight into
the inventory**. Not a bank transfer, not an item, not a voucher — spendable cash in the bag.

### 28.2 `AD-1.2` — The suite costs exactly 35 GP

| line item | cost | verification |
|---|---|---|
| 2x woad leaves (Wyson, Falador park) | **20 GP** | 20 coins => 2 leaves |
| red dye (3 redberries -> Aggie) | **5 GP** | *"each dye costs 5 coins to make"* — OSRS Wiki `Money making guide/Making dyes` |
| yellow dye (2 onions -> Aggie) | **5 GP** | same |
| blue dye (2 woad leaves -> Aggie) | **5 GP** | same |
| orange dye | **0 GP** | free combine of red + yellow |
| **total** | **35 GP** | `GOBLIN_DYE_BUDGET_GP = 20 + 5 * 3` |

`60 - 35 = +25 GP` net surplus on an account that starts from zero.

### 28.3 `AD-1.3` — The invariant itself

A free-to-play account completing `stage1a_f2p_quests_10qp` requires:

* **ZERO** external GP funding
* **ZERO** bank cash withdrawals
* **ZERO** Thieving (members-only — see §25)
* **ZERO** manual operator intervention
* **ZERO** shop sales

The natural order `Cook's Assistant -> Sheep Shearer -> Romeo & Juliet -> Goblin Diplomacy` is **already
self-funding by construction**: the only stage that spends money is the last one, and the quest that pays
money is the second one. The coin float is demand-driven and sits exactly where the spend happens
(`_prepare_goblin_diplomacy_supplies`), so a 0 GP account is never asked to fund anything early.

### 28.4 `AD-1.4` — The forensic lesson

Every historical failure to buy dyes was **a death, not a cash shortage**. `Incident #10` was a routing
bug (the `Varp 62 == 0` gate -> a 250-tile hand-in walk) that ended in the Dark Wizard corridor with 55 GP
in the bag. The 60 GP was real; it was dropped on the ground.

The correct remedies are therefore the ones already shipped — **routing** (`PR-20.5`, `PR-20.6`) and
**supply semantics** — and *not* a funding subsidy. `OPERATOR_FUNDED_RECOVERY` (§26) exists only for an
account whose cash was **destroyed by our own defect**, and it is always tagged as such in
`COMPLETE_TASK`.

### 28.5 Capital protection — declared, deliberately NOT wired to a bank detour

`CAPITAL_PROTECT_SURPLUS_GP` declares the policy ("never hand-carry more than the working float"). It is
**not** wired to a bank trek before the acceptance run, on purpose:

* the exposure at risk is the **25 GP surplus**, not the 60;
* the only bank on the Lumbridge->Varrock leg is **Draynor**, ~90 tiles off-route, and the detour crosses
  the same hazard class the invariant exists to avoid;
* the engine already carries the cheaper protection that actually addresses Incident #10: a **safe
  corridor planner** (`plan_safe_route`, `VARROCK_SOUTH_DARK_WIZARDS` hazard-gated at combat 26) and the
  **nearest-bank withdrawal** (`PR-20.6`) that removed the 200-tile march.

Trading 25 GP of tail exposure for a ~180-tile round trip through hostile ground is a net loss. Recorded
here so it is a **decision**, not an oversight. If a run ever dies carrying more than 35 GP, revisit.

### 28.6 Fleet decision (2026-10-30)

| account | tutorial | QP | role | port |
|---|---|---|---|---|
| **`acc_01`** | complete (`varp 281 = 1000`, operator-verified) | 0 | **the acceptance run — first** | **8082 / slot 1** |
| `acc_02` | complete | 8 | trophy run, `OPERATOR_FUNDED_RECOVERY` | 8081 / slot 0 |
| `GAME-02` (`zozozooz`) | **not started** | 0 | future: tutorial + quests from absolute zero | TBD |

`acc_01` is the acceptance run because `AD-1` predicts it finishes with `[funding=ZERO_TOUCH]` on **0 GP
of external money**. That single completion validates `AD-1` end to end.

---

## 29. Live log 2026-10-30 (8081) — first clean run, and the route audit that preceded it

### 29.1 What the engine did, and why it is exactly right

```
[tick=0001] state=IN_GAME | dir=WAIT_NEXT_TICK | Zero-Touch supply: bank audited live (1 usable stacks)
[tick=0002] state=IN_GAME | dir=NAVIGATE_TO   | Zero-Touch supply: walking to LUMBRIDGE_CHICKEN_COOP for Egg
```

Tick 1 audits the vault, tick 2 goes and gets the egg in the world. That is `AD-1.3` behaving as
designed: **a 0 GP account is never asked for money** — the egg, the milk and the flour cost nothing, so
the only thing that can happen is a gather.

### 29.2 The route audit (run before committing 250 ticks to it)

`ConstrainedWaypointGraph.plan_safe_route` from `VARROCK_WEST_BANK`, capabilities = combat 3 / HP 10:

| destination | waypoints | tiles | hazard nodes on path |
|---|---|---|---|
| `LUMBRIDGE_CHICKEN_COOP` | 7 | 413 | none |
| `LUMBRIDGE_KITCHEN` | 6 | 333 | none |
| `LUMBRIDGE_SHEEP_FARM` | 6 | 379 | none |
| `VARROCK_REDBERRY_BUSHES` | 5 | 238 | none |
| `DRAYNOR_AGGIE` | 6 | 247 | none |
| `FALADOR_PARK` | 7 | 243 | none |
| `GOBLIN_VILLAGE` | 7 | 369 | none |
| `VARROCK_CADAVA_BUSHES` | 4 | 230 | none |
| `LUMBRIDGE_WINDMILL_TOP` | 10 | 470 | none |

Every quest node is reachable, and the router prunes **4** edges consistently on all of them — the
dark-wizard corridor among them. `LUMBRIDGE_CHICKEN_COOP (3175,3303,0)` is a real graph node with edges
(a phantom node would have raised `NoSafeRouteError`), so tick 2's walk was legitimate, not a guess.

### 29.3 The account on 8081 is NOT the account `acc_02`'s row describes

| | ledger row (`state.db`) | live character (operator, 2026-10-30) |
|---|---|---|
| name | `acc_02` (recorded as `zaza266`) | **`ZizoScouttt`** |
| QP | `10` | **1** |
| stage | `STAGE_3_F2P_SKILLING` | fresh — `Varp 29/179/144/62` all `0` |
| GP | — | **0** |

So the row is describing a character that is no longer on the port. Nothing breaks — the QP latch is
baseline-relative, so a stale high QP cannot fabricate a completion — but the **ledger is lying about
who is being driven**, which is the same class of error as the retired "10,000 GP" line.

→ **`scan-profile --port 8081 --account-id acc_02` before the long run.** Ten seconds, and the ledger and
the port agree again.

### 29.4 The upside nobody planned

A **1 QP / 0 GP character with all four quest varps at zero** is *exactly* the acceptance scenario of
`AD-1`. If this run completes, its `COMPLETE_TASK` will print `[funding=ZERO_TOUCH]` — and the acceptance
artifact is produced on 8081, not 8082. The plan does not need to change; it needs to be **recognised**.

### 29.5 Tick budget

`--max-ticks 250` under-budgets the bundle by roughly an order of magnitude: the first leg alone is a
413-tile walk. A tick ceiling is a **stop**, not a target — when it expires mid-chain the bot halts with
a persisted `Varp`/inventory state, which a re-attach resumes cleanly, but it wastes the run. Use a
ceiling that comfortably covers the whole bundle and let it finish early:

```
--max-ticks 12000
```

---

## 30. `PR-21.5` — Incident #16: the cellar object tile, and the ring that could not reach it

### 30.1 The live log

```
[Walker] walk rejected: target WorldPoint(x=3216, y=9625, plane=0) has no walkable tile within 0
    in the collision map (nearest walkable WorldPoint(x=3215, y=9624, plane=0));
    check the destination coordinate
```

The bot stood still in the Lumbridge cellar. Two defects, one visible and one structural.

### 30.2 Defect 1 — the anchor was an object tile

`LUMBRIDGE_CASTLE_BASEMENT` pointed at `(3216,9625)`: the tile the **bucket spawns on**. An object tile
is never a walkable destination, and `Rs2Walker` refuses the whole walk (`threshold = 0`).

This is the **fifth** member of the coordinate family — dairy cow `(3172,3317)` → `(3171,3316)`
(`PR-19.3`), the Al Kharid throne room (`PR-19.6`), the redberry bushes (`PR-20.3`), Aggie's doorstep
(`PR-20.4`), and now the cellar. The rule has not changed: **the probe is truth; the wiki, and our own
first guess, are hints.** The anchor is now `(3215,9624)` — the neighbour the client itself named.

### 30.3 Defect 2 — the self-healing ring was switched off exactly where it was needed

`PR-19.3` added `FINAL_TARGET_SNAP_OFFSETS`: when a route stalls on its **final** waypoint, rotate the
destination through a deterministic ring until the client accepts a tile. Correct fix, wrong gate:

```python
if (
    current_pos.y < 5000          # <-- surface only
    and on_final_waypoint
    and prog.stationary_ticks >= self.max_stalled_ticks_before_reroute
):
```

The gate was there to protect the underground **5-tick recovery contract**. The consequence: the ring
was unreachable **in the one region where destinations are objects** — the whole Lumbridge cellar is
`9xxx` coordinates. A guard built to fix "the destination itself is unwalkable" was disabled precisely
where destinations are unwalkable objects.

Fix: give underground the **same fast budget** its reroute already uses, instead of excluding the region.

```python
snap_budget = min(5, self.max_stalled_ticks_before_reroute) if current_pos.y >= 5000 \
              else self.max_stalled_ticks_before_reroute
if on_final_waypoint and prog.stationary_ticks >= snap_budget:
```

The contract is preserved (`T166b` proves rotation still arrives inside ~5 ticks) and the ring works
below ground (`T166`).

### 30.4 Why this matters more than the tile

Without defect 2, defect 1 is a **halt**. With it fixed, defect 1 would have been a **nuisance**: the
ring rotates `(3216,9625)` → `(3215,9624)` on its own and the bot carries on. Fixing the coordinate
alone would have left the same class of halt waiting for the next object tile — and the next one is
already known (`LUMBRIDGE_WINDMILL_TOP`, the mill hopper, the goblin crates are all object tiles).

Both are fixed. The coordinate is corrected because the anchor should describe reality; the ring is
enabled because no anchor will ever be right about every tile on the map.

### 30.5 Verification

| test | guarantee |
|---|---|
| `T165` | anchor == the walker-accepted neighbour, ≠ the object tile, and inside the cellar rectangle (T101 discipline) |
| `T166` | the ring rotates a rejected destination **underground** (the live signature, replayed at `y=9625`) |
| `T166b` | rotation still arrives within the fast (~5 tick) underground window |
| `T167` | the bucket is still collected: `PICKUP` is name-resolved, so moving the anchor one tile costs nothing |

Full suite: **`167 passed`**, `0 failed` (`T01..T146`, `T157..T167`).

---

## 31. `PR-21.6` — العطلات الثلاثة الباقية بعد قبول `Stage 1A` الحي

### 31.1 السياق: القبول الحي تحقق، ثم بدأ الفشل المتكرر

على `8081` / `acc_02` (`ZizoScouttt`) وصل الحساب إلى **`13 QP`**: `Cook's Assistant` (`Varp 29 = 2`) +
`Sheep Shearer` (`Varp 179 = 21`) + `Romeo & Juliet` (`Varp 144 = 100`) + `Goblin Diplomacy`، مع هبوط
`Gold bar (2357)` و`Fancy boots (9005)` في بنك `GRAND_EXCHANGE` وحقيبة `0/28`.

لكن التشغيل احتاج **تدخلات**: أربع وقفات `UNRECOGNIZED_QUEST_DIALOGUE_OPTION`، ووقفة
`SUPPLY_EXIT_BLOCKED:LUMBRIDGE_CASTLE_BASEMENT`، ووقفة `SUPPLY_CLIMB_FAILED:MILL_LANE_MILL`. هذا القسم
يغلق الثلاثة، وكل واحدة منها **عيب بنيوي** لا «نص ناقص»:

| # | العطل | السبب الجذري | لماذا لم يكتشفه أي اختبار سابق |
|---|---|---|---|
| 1 | `SUPPLY_EXIT_BLOCKED:LUMBRIDGE_CASTLE_BASEMENT` | قائمة أسماء أولاً + ميزانية `//6` | الاختبارات كانت تُشغّل الخروج بميزانية غير محدودة |
| 2 | `SUPPLY_CLIMB_FAILED:MILL_LANE_MILL` | قفزة `0 -> 2` على سلّم طابق واحد | **المحاكي كان يقفز `0 <-> 2` أيضاً** — فالاختبار كان يوافق على الخلل |
| 3 | `UNRECOGNIZED_QUEST_DIALOGUE_OPTION` ×4 | مقارنة نصية خام + نصان ناقصان | كل اختبار حوار كان يمرّر النص الذي كتبناه نحن على الشاشة |

### 31.2 العطل 1 — القبو: الميزانية كانت تُنفَد قبل أن يُسمَّى السلّم

القبو فيه **مخرج واحد فقط**: `Ladder` (`17385`) عند `(3209,9616)`. القائمة كانت
`("Staircase","Stairs","Ladder")` — أي اسمين غير موجودين في الغرفة أولاً.

والحلقة `targets[min(len-1, (exits-1)//6)]` مع `max_climb_attempts = 12` تعني:

| المحاولة | الاسم المرسل |
|---|---|
| 1..6 | `Staircase` ❌ |
| 7..12 | `Stairs` ❌ |
| 13..18 | `Ladder` ✅ — **لا تحدث أبداً** |

إعادة الترتيب وحدها تجعل `Ladder` يُرسَل ست مرات ثم يعلق على اسمين غير موجودين — أي **تأخير** للفشل
لا إلغاء له. لذلك أُصلح الاثنان معاً:

```python
budget = max(int(self.max_climb_attempts), required_climb_attempts(targets))
```
```python
def required_climb_attempts(targets: tuple[str, ...]) -> int:
    return CLIMB_ATTEMPTS_PER_NAME * (len(targets) - 1) + 1   # الاسم الأخير يُجرَّب في المحاولة 6(n-1)+1
```

والترتيب صار **وجود-أولاً**: `("Ladder", "Trapdoor", "Staircase")`، و`at_x/at_y` على بلاطة السلّم
`(3210,9616)` — بلاطة الكائن ليست وجهة مشي (`PR-21.5`).

### 31.3 العطل 2 — الطاحونة ثلاث طوابق، والكود كان يعرف طابقاً واحداً

`12964` (0→1) · `12965` (1→2) · `12966` (2→1). الكود كان يرسل `CLIMB` واحدة بـ `target_plane=2`
و`object_id=12964`: السلّم الموجود في الطابق الأرضي فقط. البوت يصعد طابقاً، يفقد السلّم بالمُعرِّف
المثبَّت، ويعيد المحاولة حتى تنفَد الميزانية.

| الطريق | قبل | بعد |
|---|---|---|
| صعود | `0 -> 2` (خطوة واحدة، `12964`) | `0 -> 1` (`12964`) ثم `1 -> 2` (بالاسم) |
| هبوط | `2 -> 0` (خطوة واحدة، `12964`) | `2 -> 1` ثم `1 -> 0` (بالاسم) |

**درس المحاكي:** كل `FakeGameAdapter`/fake في الاختبارات كان يكتب `plane = 2 if Climb-up else 0` —
أي أنه **يعيد إنتاج الخلل نفسه**: قفزة طابقين «تنجح» في الاختبار لأن المحاكي يقفز طابقين. أُصلحت
**كل** المحاكيات إلى `+1 / -1` مقيّدة بـ `0..2`، فصار الاختبار يرفض أي خطوة تتخطى طابقاً.

### 31.4 العطل 3 — الفاصلة كانت تُوقف التشغيل

الجملة الحقيقية من العميل: `"Can you make dyes for me, please?"`. النسخة في الكود (منقولة من
transcript): `"Can you make dyes for me please?"`. المقارنة كانت
`opt.strip().casefold().rstrip(".!?")` → **الفاصلة لا تُطبَّع** → `UNRECOGNIZED_QUEST_DIALOGUE_OPTION`
عند النبضات `1565`، `133`، `617`، `474`.

الحل ليس «أضف النص الذي فشل» (سيتكرر مع أول فاصلة جديدة)، بل مقارنة **الكلمات** لا **التنسيق**:
`osrs_ee/bots/dialogue_text.py:normalise_option()` تحوّل كل ما ليس حرفاً/رقماً إلى مسافة وتطوي
المسافات وتُطبِّع الحالة. أي اختلاف في **الكلمات** يبقى فشلاً صريحاً (لا تخمين أبداً).

كذلك أُضيف الخياران المفقودان من قائمة `Romeo` الفرعية (`"Where can I find Juliet?"` و
`"Is there anything else you can tell me about Juliet?"`) — وهما في القائمة الحقيقية لكنهما كانا
غائبين عن `VERIFIED_QUEST_DIALOGUE_OPTIONS`، فكان البوت يتوقف بعد قبول المهمة مباشرة.

وأصبح التصعيد نفسه يحمل الدليل:
`UNRECOGNIZED_QUEST_DIALOGUE_OPTION:['...', '...']` — الخيارات كما ظهرت على الشاشة، حرفياً، حتى يكون
تقرير الميدان القادم **إصلاح سطر واحد** لا تحقيقاً كاملاً.

### 31.5 العطل 4 — `FALADOR_PARK` كان في الطرف الخطأ من الحديقة

`FALADOR_PARK` عند `(3000,3375)` هو الطرف الغربي (حيث `Sir Tiffy Cashien`)، و`Wyson the gardener`
يدور عند كوخه في الطرف الشرقي ~31 بلاطة بعيداً. الخطوة كانت تستخدم التسامح العام (3 بلاطات) فلم
يُوجَد الـ NPC إطلاقاً → `SUPPLY_STEP_STALLED:Woad leaves:NPC:Wyson the gardener`.

العلاج هو نفس علاج حظيرة الخراف (`PR-20.1`): **توسيع موقع العمل لا التسمّح العام**. العقدة انتقلت إلى
`(3028,3379)` والخطوة أعلنت `area_radius=32`. هذا التوسيع **مسجّل ومحدود** في `T172c`: أربع خطوات فقط
في المحرك تحمل `area_radius` — الخراف، الكادافا، التوت الأحمر، وويسون. أي خطوة خامسة = فشل اختبار.

### 31.6 لماذا هذا مهم للحساب نفسه

الإصلاحات الثلاثة الأولى كانت تُستهلك **حياة الحساب**: كل وقفة تعني تدخلاً بشرياً، وكل تدخل يهدم
`Zero-Touch` الذي هو شرط القبول نفسه. الفرق بين «12 QP» و«12 QP بصفر لمسات» هو هذا القسم بالتحديد.

### 31.7 التحقق

| اختبار | الضمانة |
|---|---|
| `T168` | كل مخرج تحت الأرض: الميزانية تغطي القائمة كاملة، والفهرس الأخير يُجرَّب فعلاً |
| `T169` | مخرج القبو كما حدث حياً: `Ladder` يُرسَل داخل الميزانية بلا تصعيد |
| `T169b` | **السبب الجذري يبقى قابلاً للتنفيذ:** القائمة القديمة + الميزانية القديمة = `Ladder` غير قابل للوصول رياضياً |
| `T170` | الطاحونة: أربع قفزات، كل واحدة **طابق واحد**، وصعود/هبوط بالترتيب |
| `T170b` | طابق الوصول = طابق عمل الـ Hopper (لا وصول لطابق واستخدام آخر) |
| `T171` (بأربع صيغ `parametrize`) | الفاصلة/المسافات/النقطة لا تُغيّر المعنى؛ والكلمة المختلفة تَظل فشلاً صريحاً |
| `T172` | نصا `Romeo` و`Aggie` في القائمة المتحققة + لا تكرار مخفي بعد التطبيع |
| `T172b` | عقدة الحديقة قريبة من كوخ ويسون ونصف قطر الخطوة يغطي المسافة |
| `T172c` | **نطاق جراحي:** لا خطوة خامسة اكتسبت نصف قطر أو ميزانية |

Full suite: **`180 passed`**, `0 failed` (`T01..T146`, `T157..T172c`).
