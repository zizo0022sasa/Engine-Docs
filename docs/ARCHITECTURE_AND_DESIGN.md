# 🏗️ التصميم المعماري الموحد، مصفوفة الملكية، وحدود العزل (`ARCHITECTURE_AND_DESIGN.md`)

> **الإصدار:** `2.1 — Consolidated Normative Architecture`  
> **الحالة:** معتمد للتنفيذ المرحلي (`ADR-001` .. `ADR-008`)

---

## 1. نظرة عامة على البنية المعمارية الموحدة

تتبع منظومة `OSRS_ee` معمارية **التحكم المحلي ذي الكاتب الوحيد والبوابة الحصرية للأوامر (`Single-Writer State Core & Exclusive Adapter Gate`)**:

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                        Telegram Operator Gateway (PR-07)                     │
│  • RBAC (user_id + chat_id + role)  • Single-Use Bound Approvals             │
│  • Zero Secrets / Aliases Only      • Bounded Persistent Outbox              │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ Structured Intents & Events
                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│               Single-Writer State Store (SQLite WAL + JSON View)             │
│  • Accounts FSM (12 States)   • Exclusive Leases & session_epoch             │
│  • Write-Ahead CommandJournal • Event Dedupe (event_id) • Approvals Ledger   │
└──────────┬────────────────────┴──────────────────────┬───────────────────────┘
           │ Read Snapshot                             │ Atomic Lease & Journal
           ▼                                           ▼
┌────────────────────────────┐              ┌──────────────────────────────────┐
│  Scheduler & Policy Engine │              │   Process & Slot Supervisor      │
│  • Circadian Window Check  │              │  • Fresh JVM per Session         │
│  • Regional World Pool     │              │  • Pre-Launch Profile Seeding    │
│  • TaskRecipe Allowlist    │              │  • Pause / Drain / Abort Timers  │
│  • 10QP+100TTL+20h Gates   │              │  • Tick vs Liveness Watchdog     │
└────────────────────────────┘              └──────────────────┬───────────────┘
                                                               │
                    ┌──────────────────────────────────────────┼───────────────┐
                    ▼                                          ▼               ▼
       ┌─────────────────────────┐                 ┌────────────────────┐  ┌──────────────────┐
       │   Task Worker Loop      │                 │ AI Advisory (PR-08)│  │ Per-Slot SOCKS5h │
       │ 1. Read Observation     │                 │ • Proposal Schema  │  │ Loopback Sidecar │
       │ 2. Check Precondition   │                 │ • NO Adapter Token │  │ 127.0.0.1:1900X  │
       │ 3. Journal Intent       │                 │ • Redacted Context │  │ • Fail-Closed    │
       │ 4. Dispatch via Gate    │                 └────────────────────┘  └────────┬─────────┘
       │ 5. Verify Postcondition │                                                  │
       └────────────┬────────────┘                                                  │
                    │ Epoch + Lease + Instance Verified                             │
                    ▼                                                               ▼
       ┌─────────────────────────┐    Loopback HTTP + X-Agent-Token   ┌───────────────────────┐
       │   GameAdapterGate       │ ─────────────────────────────────► │ Isolated Microbot JVM │
       │  (Fake or HTTP Adapter) │                                    │ • Dedicated user.home │
       └─────────────────────────┘                                    │ • hwid-agent + Cache  │
                                                                      │ • --disable-telemetry │
                                                                      └───────────────────────┘
```

---

## 2. مصفوفة الملكية الصارمة للمكونات (`Strict Ownership Matrix`)

منعاً لتعدد ملاك القرار (`AUD-05`, `AUD-16`)، يلتزم كل مكون بحدود الملكية الآتية:

| المجال / المورد | المالك الحصري للكتابة/التنفيذ | المسموح لهم بالقراءة | المحظورات الصريحة |
| :--- | :--- | :--- | :--- |
| **حالة الحساب والجلسة والـ Cooldown** | `StateStore` (عبر واجهته المعاملاتية فقط) | `Scheduler`, `Supervisor`, `Telegram`, `Worker` | يُحظر تعديل ملفات JSON يدوياً من الخيوط أو تصفير `cooldown_until` عند الانهيار. |
| **اختيار الحساب والمهمة (`Task Assignment`)** | `Scheduler` | `Supervisor`, `Worker` | يُحظر على `TaskWorker` تبديل المهمة سراً داخل البنك؛ عند نقص الموارد يطلب `REPLAN`. |
| **إطلاق وقتل عملية الـ JVM والنفق الشبكي** | `SlotSupervisor` | `StateStore` (يسجل الهوية) | يُحظر إعادة استخدام نفس عملية الـ JVM لحساب ثانٍ أو ترك عمليات فرعية يتيمة. |
| **إرسال الأوامر المؤثرة إلى اللعبة** | `GameAdapterGate` (لحامل الإيجار النشط فقط) | لا أحد يكتب مباشرة سواها | يُحظر اتصال `Telegram` أو `AI Advisory` أو عامل منتهي الإيجار بالـ `AgentServer` مباشرة. |
| **إصدار الموافقات البشرية (`2FA / Risk`)** | المشغل البشري عبر `ApprovalRecord` ذري | `Supervisor` (يستهلكها مرة واحدة) | يُحظر نقل الحساب مباشرة إلى `READY` فور ضغط الزر؛ ينتقل أولاً إلى `RECONCILING`. |
| **تشخيص الانسداد غير المألوف** | `AI Advisory` (يصدر `AIProposal` فقط) | `Supervisor`, `Telegram` | يُحظر على الـ AI تنفيذ نقر إحداثي `Click(x,y)` أو تأكيد نوافذ التحذير أو لمس البروكسي. |

---

## 3. عزل الساندبوكس، البروفايل، الكاش، وهوية العتاد (`Isolated Sandbox Blueprint`)

لكل حساب مجلد ساندبوكس دائم خاص به (`sandboxes/<account_id>/`) منفصل تماماً عن مسار المستخدم الأصلي:

1. **التطعيم المسبق للبروفايل (`Pre-Launch Profile Seeding` — علاج `AUD-07` و`AUD-25`):**
   بما أن `AgentServerPlugin` معطل افتراضياً في `Microbot` (`enabledByDefault = false`)، يكتب المشرف قبل الإقلاع إعدادات البروفايل لتضمن:
   * `runelite.agentserverplugin=true`
   * `agentServer.port=<dedicated_loopback_port>` (فريد لكل سلوت، مثل `18081`، وليس مشتركاً).
   * `-Dmicrobot.agent.token=<per_session_csprng_token>` مرتبط بملف توكن الجلسة بصلاحية `0600`.
   * تعطيل التليمتري السحابي: `--disable-telemetry` و`-Dmicrobot.disableTelemetry=true`.
   * ضبط الإطارات وتعطيل إضافات الرسوميات الثقيلة (`gpu=false`, `hd117=false`, `fpscontrol` مضبوط بتنوع طفيف بين `12–18 FPS`).
   * ضبط أبعاد النافذة بانحراف ثابت مشتق من `account_persona_seed` (مثلاً ضمن `784x592` إلى `828x618`) بدلاً من التطابق الجماعي `800x600`.
2. **حقن الكاش النظيف (`Golden Cache Bootstrap` — علاج `AUD-26`):**
   تُنسخ ملفات أصول اللعبة الثابتة العامة (`jagexcache` الثابت فقط، **دون أي ملف `random.dat` أو بروفايل أو إعدادات هوية**) محلياً من نسخة مرجعية نظيفة قبل فتح نفق البروكسي السكني؛ فلا يستنزف كل حساب مئات الميجابايت من باقة البروكسي.
3. **عزل بصمة العتاد (`Hardware Identity`):**
   تُمرر راية `-javaagent:hwid-agent.jar` مع `-Dhwid.store=sandboxes/<account_id>/hwid.properties` و`-Duser.home=sandboxes/<account_id>/home` بحيث يحتفظ كل حساب بـ `random.dat` وبصمة جهاز ثابتة خاصة به وحده.

---

## 4. عزل الشبكة وبروتوكول `Per-Slot Local SOCKS5 Sidecar`

1. **منع سقوط `ProxyConfiguration.java` (`AUD-08`):** يفتح المشرف خادم تحويل محلياً على `127.0.0.1:<slot_proxy_port>` ويمرر للعميل دائماً `--proxy=socks5://127.0.0.1:<slot_proxy_port>`.
2. **منع تسريب الـ IP والـ DNS (`Fail-Closed`):** يتولى الجسر المحلي مصادقة مزود البروكسي الخارجي وتوجيه طلبات الـ DNS عبر النفق (`socks5h`). إذا انقطع المزود الخارجي، يغلق الجسر المقبس فوراً ويرفض أي تمرير مباشر عبر إنترنت الجهاز المضيف.
3. **قاعدة `1 Active Account = 1 Dedicated IP` وتجديد `Microbreak` (`AUD-27`):** لا يتشارك حسابان نشطان نفس الـ IP الخارجي، وتُربط الدولة بمصفوفة العوالم الإقليمية الأقرب كموناً (`EG -> EU DE/UK`). عند اقتراب انتهاء صلاحية جلسة الـ Sticky IP، يجدول العامل استراحة خروج آمنة قصيرة (`POST /logout`) لتدوير الجلسة ضمن نفس النطاق الجغرافي قبل متابعة اللعب.

---

## 5. نموذج الوصفات المعتمدة (`Allowlisted TaskRecipe`) وبوابة الإصدارات

* لا يعتمد النظام على `@ScriptDescriptor` أو حقن ملفات `.java` الخام أو تحوير المتغيرات (`AST Mutator`).
* كل مهمة مسجلة في النظام تحمل عقد `TaskRecipe` يحدد: `recipe_id`، `plugin_id`، `config_overrides`، `required_varps`، `required_levels`، `required_items`، `sellable_yields`، `postconditions`، و`verification_stage`.
* تحديثات `Microbot` و`Microbot-Hub` لا تُسحب في وقت التشغيل؛ بل تُبنى وتُفحص في مسار `staging/` وتُعتمد صراحة قبل الترقية بين الجلسات.

---

## 6. معمارية النواة التفاعلية المعزولة (`PR-12 Field-Remediated Core Architecture`)

استجابةً لتقرير الفحص الميداني الحي (`AUD-36..39`)، تطبق طبقة التشغيل الحي الفصل الصارم التالي بين النواة الصلبة والمهام القابلة للتركيب:

1. **درع دورة حياة الجلسة (`SessionLifecycleManager` — `osrs_ee/core/lifecycle_manager.py`):**
   * يدير الحالات الخمس الصريحة: `DISCONNECTED -> AT_LOGIN -> LOGGING_IN -> IN_GAME <-> IN_DANGER`.
   * عند سقوط المقبس أو الخروج لشاشة الـ Login (`loggedIn: false`)، يعزل المهام تماماً ويختصر استعلامات العميل إلى نبضة `/status` أو `/state` واحدة خفيفة، ثم ينفذ الإنعاش الذاتي بتراجع أسي (`1s -> 2s -> 4s -> 8s...`) حتى استقرار `IN_GAME`.
2. **حارس السلامة التفاعلي غير المعطل (`ReactiveSafetyWatchdog` — `osrs_ee/core/reactive_watchdog.py`):**
   * خالٍ 100% من `time.sleep`؛ يقيم الدم وطاقة الجري وسلامة الإحداثيات في الذاكرة خلال `<0.1ms`، ويضخ أوامر `CONSUME_FOOD` و`SET_RUN_MODE` بالتوازي التسلسلي (`PIPELINED_NEXT_TICK`) مع أمر الحركة `WALK_TO_WORLD_POINT` في نفس النبضة دون إيقاف الشخصية تحت ضربات الوحوش، مع التحقق من شفاء الدم في النبضة التالية واحترام مهلة الأكل (`3 Game Ticks`).
3. **محرك الملاحة المقيد بقدرات الحساب (`ConstrainedWaypointGraph` — `osrs_ee/navigation/`):**
   * يربط حساب المسار بسياق قدرات الحساب (`AccountCapabilitiesContext`) فيقص الاختصارات المهارية غير المتاحة (مثل اختصار الحائط غرب البورصة `(3149, 3480)` المتطلب `Agility 21` وعضوية) ومناطق الوحوش العدوانية (`Dark Wizards (3226, 3369)`)، ويوجه حسابات المستوى 3 عبر ممرات آمنة (`Level 3 Safe Corridors`) مع البث المسبق لنقاط الطريق (`Lookahead Streaming`) وإعادة التوجيه الآلي عند التعثر لـ 3 نبضات.
4. **واجهة المهام المفصولة (`TaskModule` — `osrs_ee/bots/`):**
   * ترتبط جميع المهام بالنواة (`ExecutionEngineKernel`) عبر دالة نقية واحدة `evaluate_tick(TickContext) -> TaskDirective` دون أي تداخل مع الشبكة أو السلامة أو إدارة الجلسات.
