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
