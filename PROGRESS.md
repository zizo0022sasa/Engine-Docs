# 🧭 سجل التقدم وبوصلة الاستئناف الإلزامية (`PROGRESS.md`)

> **تنبيه إلزامي عند كتابة «تابع» أو استئناف أي جلسة مستقبلية:** اقرأ هذا الملف أولاً، ثم نفّذ قائمة الفحص السبعية في [`docs/RESUME_PROTOCOL.md`](./docs/RESUME_PROTOCOL.md).

---

## 1. الحالة الراهنة للمشروع (`Current Snapshot`)

* **المرحلة الحالية:** **اكتمال الإصلاح الجراحي `PR-15.1` (حسم حلقة إلغاء المشي كل `0.6s` عند كوبري البرابرة `GUNNARSGRunn_BRIDGE_EAST (3105, 3420)` عبر Debounce لمدة `5` نبضات = `3.0s` + إضافة حافتي الكوبري ونار البرابرة `BARBARIAN_RIVER_FIRE <-> BARBARIAN_VILLAGE` + الاستئناف الذكي لطهي الجمبري `270:14` حتى مع رسائل ترقية مستوى الطبخ)** لإتمام `stage2_stronghold_10k` بنظام `Zero-Touch` 100%.
* **نتيجة فحص الأسرار (`Secret Scan`):** `[SECRET_SCAN_OK] Zero secret patterns detected under .`
* **نتيجة الاختبارات الفعلية المنفذة (`pytest`):** **`57 passed` (0 failed)** عبر 16 ملف اختبار تغطي مصفوفة القبول الشاملة `T01..T51`.
* **التمييز المعرفي الدقيق (`Epistemic Status`):**
  * **`LIVE_WIRE_VERIFIED` (على عميل `microbot-2.6.34.jar` الحي — `127.0.0.1:8081` + تشغيل حي داخل عالم OSRS على `acc_02` في Run 1 & Run 2 & Run 3):**
    1. استجابة `GET /state` في `<10ms` وثبات `HTTP/1.1 Keep-Alive`، وبوابة التليجرام الحية `@orangeconnebot` (`/status`).
    2. **Run 3 (`PR-15`):** إثبات حي كامل لبروتوكول الاكتفاء الذاتي (`Zero-Touch Fallback`) وتفعيل الجري الذاتي (`run_enabled: true`): فحص الحقيبة `(1/6)` -> الذهاب لبنك دراينور وفحصه -> التوجه لمستنقع لومبريدج -> أخذ شبكة الصيد مجاناً من المدرب -> صيد `11x Raw shrimps` -> قطع `400+` بلاطة بدم `10/10 HP` حتى مدخل كوبري البرابرة `(3105, 3420)`.
    3. **Run 1 & 2:** اجتياز الممر الآمن، نزول `20790`، تخطي الطابقين 1 و2 بـ `20786` و`19005`، نزول السلالم `20785`/`19004`/`23706` وتأكيد `579:17`، دخول الطابق الرابع واجتياز `Portal of Death [1/4]` وحل سؤال الأمان.
  * **`HERMETIC_VERIFIED`:** حزمة `PR-15` + `PR-15.1` (`T47..T51` في `test_pr15_stronghold_live_hardening.py` — `57 passed`).
  * **`PENDING_LIVE_RUN_3`:** التحقق الميداني الحي للجولة الثالثة (`Live Run 3`) لإتمام سحب `Fancy boots` (`270:15`) من `Cradle of Life` (`23731`)، الصعود عبر `Bone chain` (`23732`)، وإيداع الغنائم في بنك `GRAND_EXCHANGE`.

---

## 2. سجل الدفعات المنجزة والمرفوعة إلى GitHub (`Verified Commits on main`)

| المرحلة | الكوميت على `main` | الملخص الهندسي الدقيق لما نُفذ وتُحقق منه | الاختبارات المجتازة (تراكمياً) |
| :---: | :---: | :--- | :---: |
| **`PR-00`** | `67b589d` | تنقيح توكنات Telegram في `للمعمارية.md`، تحصين `.gitignore`، وبناء `Redactor` و`secret_scan.py`. | `5 passed` (`T01, T02`) |
| **`PR-01`** | `a29cc8a` | توحيد تقارير الوكلاء الثلاثة (`AUD-01..35`)، حسم التعارضات الستة، اعتماد `ADR-001..008`، وتحديث `docs/` و`وثائق_المحركات/*` `v2.1`. | `9 passed` (`T20, T25, T28`) |
| **`PR-02`** | `2214972` | بناء مخططات `schemas/*.schema.json`، مدقق العقود، `RuntimeConfigSnapshot`، ومشتق `PersonaProfile`. | `16 passed` (`T05, T10, T14, T20, T26, T32`) |
| **`PR-03`** | `e57c42f` | بناء `StateStore` (`SQLite WAL` + `state_snapshot.json`)، آلة الـ 13 حالة، `Lease + session_epoch`، `CommandJournal`، واسترداد الانهيار `RECOVERY_REQUIRED`. | `20 passed` (`T03, T04, T07, T08, T09, T11, T31`) |
| **`PR-04`** | `fd31aba` | بناء `SingleSlotSupervisor`، `GameAdapterGate`، `TaskWorker`، و`FakeGameAdapter`. | `24 passed` (`T06, T10, T12, T13, T16, T17, T21, T23`) |
| **`PR-05a`** | `45a5534` | بناء `SandboxProfileSeeder`، تطعيم `Golden Cache`، مصفوفة `EG -> EU`، و`MicrobotHttpAdapter`. | `26 passed` (`T06, T10, T19`) |
| **`PR-05b`** | `5b7b499` | بناء `LocalSecretVault (0600)` مع مستورد `Legacy & Jagex`، خادم `LocalSocks5Sidecar` (`RFC1928/1929` + `Fail-Closed`)، و`ManagedProcessTree`. | `29 passed` (`T02, T16, T19`) |
| **`PR-06` + `08`** | `19ddbe1` | بناء `DeterministicStrongholdSolver`، `CuratedRecipeRegistry`، `SingleSlotPilotRunner`، و`AIAdvisoryClient` المعزول بقاطع دائرة `429`. | `31 passed` (`T05, T14, T22, T27`) |
| **`PR-07`** | `cb1d888` | بناء `LogQuotaGuardian` (`< 500MB` مع حماية الحوادث المفتوحة)، `BoundedOutbox`، و`TelegramGateway` (`Default-Deny RBAC`). | `34 passed` (`T02, T03, T29`) |
| **`PR-09` + `10`** | `4d04f9d` | بناء `RssCapacityCalculator`، `StaggeredLaunchPlanner (>=90s)`، `MicrobreakCoordinator`، `ReleaseStagingGate`، و`GEOrderLedger` بضريبة 2% وقفل أدوات الوصفة النشطة. | `37 passed` (`T15, T18, T24, T30`) |
| **`PR-11`** | `b06ec3c` | بناء واجهة التشغيل الموحدة `osrs_ee/cli.py` + `.env.example` + اختبار التكامل الشامل `test_pr11_cli_e2e.py` + الدليل الكامل `كيفيه_الاستخدام_من_الالف_للياء.md`. | `38 passed` (`T01..T32`) |
| **`PR-12`** | `ade6240` | المعالجة الجذرية للعيوب الميدانية الأربعة (`AUD-36..39` / `ADR-009..012`): `ReactiveSafetyWatchdog` بدون انتظار ساكن، `ConstrainedWaypointGraph` لممرات المستوى 3 الآمنة، `SessionLifecycleManager` بالإنعاش الذاتي، وفصل `ExecutionEngineKernel` عن `osrs_ee/bots/`. | `42 passed` (`T01..T36`) |
| **`PR-12.1`** | `390419c` / `783a735` | إغلاق فجوات التوافق السلكي الثلاث مع عميل Microbot الحي (`AUD-40..42` / `ADR-013`): تطبيع نقاط الحياة ضد `None` (`hitpoints/maxHitpoints` + `/skills?name=Hitpoints`)، دعم `WIDGET_CLICK -> POST /widgets/click`، واعتماد `status_endpoint="/state"` افتراضياً مع سقوط تلقائي ذكي لـ `/status` — **ومعتمد بشهادة فحص سلكي حي على `microbot-2.6.34.jar` (`127.0.0.1:8081`)**. | `45 passed` (`T01..T39`) |
| **`PR-13`** | `c664c81` | بناء واستكمال الوحدات السبع المعتمدة في `osrs_ee/bots/` (`StrongholdTaskModule` 4 طوابق كاملة + 39 سؤالاً + `270:15` + إيداع البنك، `TutorialIslandTaskModule`، `TradeUnlockQuestsTaskModule` 12 QP، `TotalLevel100TaskModule`، `SkillingGathererTaskModule` لـ 3 دورات، و`BotModuleRegistry`) مع أوامر `live-probe` و`attach-bot`. | `50 passed` (`T01..T44`) |
| **`PR-14`** | `37072a3` / `7ed863c` / `a2709e2` | محمل `.env` قياسي، تفعيل `@orangeconnebot` الحي، دعم `DIRECT` في `LocalSocks5Sidecar`، بث سجلات النبضات الحية في `attach-bot`، وإثبات محاكاة الطوابق الأربعة في `simulate-pilot`. | `52 passed` (`T01..T46`) |
| **`PR-15`** | `58adeb9` | تفعيل الجري الذاتي غير المتذبذب (`Varp 173` + `160:27`)، التجهيز التلقائي للطعام قبل المغامرة (`DRAYNOR_BANK` + سقوط تلقائي لصيد وطهي الجمبري عند خلو البنك + تطبيع `bank.items`)، العداد الرتيب الداخلي عند `game_tick is None`، وتوسيع مدى عبور بوابات الطابق الرابع إلى `chebyshev_distance <= 2` مع حسم معرف الباب الأمامي (`23728` مقابل `23727`) وفلتر `Widget 270`. | `56 passed` (`T01..T50`) |
| **`PR-15.1`** | *(حسم تعليق كوبري البرابرة وطهي الجمبري Zero-Touch)* | ربط حافتي `safe_bridge_east_to_barbarian_river_fire` (`14.0`) و`safe_barbarian_river_fire_to_stronghold_entrance` (`26.0`)، ومنع تكرار `POST /walk` اللحظي عبر مهلة Debounce مقدارها `5` نبضات (`3.0s`) على السطح، واستئناف الطهي الذكي من الكوبري مباشرة وعبر رسائل ترقية مستوى الطبخ حتى اكتمال الشواء ودخول `20790`. | **`57 passed`** (`T01..T51`) |

---

## 3. ما العمل عند كتابة «تابع» مستقبلاً؟

إذا أرسل المالك مخرجات تشغيل حي من جهازه (مثل سجلات ربط `launch-slot` مع `Microbot JAR` أو طلب إضافة وصفة `TaskRecipe` جديدة):
1. شغل `PYTHONPATH=. python3 -m osrs_ee doctor` و`PYTHONPATH=. pytest -v` للتحقق من سلامة خط الأساس (`38 passed`).
2. أضف أي وصفات أو تحسينات جديدة عبر نفس دورة الانضباط (`CONCEPT_ONLY -> HERMETIC_TESTED` باختبار آلي أولاً ثم تحديث الوثائق وفحص الأسرار).
