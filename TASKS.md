# 📋 سجل المهام التنفيذية الموحد (`TASKS.md`)

> **قاعدة التحديث:** لا تُوسم مهمة بـ `HERMETIC_VERIFIED` إلا بعد تنفيذ الكود وتشغيل الاختبار الآلي المرتبط فعلياً وتوثيق الدليل. وتبقى خطوات التشغيل الحي على عتاد المالك موسومة بوضوح تحت `PENDING_OWNER_HOST_RUN`.

---

## 1. المراحل البرمجية والتوثيقية المنفذة والمحققة آلياً بالكامل (`PR-00` .. `PR-11`)

| ID | المرحلة | المهمة المنفذة | الاختبارات | الحالة | الدليل القابل للإعادة |
| :---: | :---: | :--- | :---: | :---: | :--- |
| **`TASK-00-01`** | `PR-00` | تنقيح توكنات Telegram المكشوفة في `للمعمارية.md` وتحصين `.gitignore` ضد قواعد البيانات والأسرار والساندبوكس. | `T01`, `T02` | `HERMETIC_VERIFIED` | `67b589d` (`test_pr00_security.py`) |
| **`TASK-00-02`** | `PR-00` | بناء محرك التنقيح `Redactor` وفاحص الأسرار وصلاحيات الملفات `secret_scan.py`. | `T01`, `T02` | `HERMETIC_VERIFIED` | `67b589d` (`test_pr00_security.py`) |
| **`TASK-01-01..03`** | `PR-01` | دمج تقارير الوكلاء الثلاثة (`AUD-01..35`)، اعتماد `ADR-001..008`، توحيد حزمة `docs/` وتحديث `وثائق_المحركات/00..06` جراحياً إلى `v2.1`. | `T20`, `T25`, `T28` | `HERMETIC_VERIFIED` | `a29cc8a` (`test_pr01_docs_integrity.py`) |
| **`TASK-02-01..02`** | `PR-02` | مخططات `schemas/*.schema.json`، مدقق العقود الصارم (`UNKNOWN -> None`)، `RuntimeConfigSnapshot` المختوم، ومشتق `PersonaProfile` (`Bounded Log-Normal`). | `T05`, `T10`, `T14`, `T20`, `T26`, `T32` | `HERMETIC_VERIFIED` | `2214972` (`test_pr02_contracts.py`) |
| **`TASK-03-01..02`** | `PR-03` | مخزن `StateStore` الذري (`SQLite WAL` + `state_snapshot.json`)، آلة الـ 13 حالة، بوابة `10 QP + 100 TTL + 1200m`، النوم الليلي، `Lease + session_epoch`، `CommandJournal`، واسترداد الانهيار `RECOVERY_REQUIRED`. | `T03`, `T04`, `T07`, `T08`, `T09`, `T11`, `T31` | `HERMETIC_VERIFIED` | `e57c42f` (`test_pr03_state_core.py`) |
| **`TASK-04-01..03`** | `PR-04` | `SingleSlotSupervisor` (`pause/drain/abort` + حارس `game_tick`)، `GameAdapterGate`، `TaskWorker` (`execute_bank_turnaround`، `Varp 281`، `REPLAN`، `DEATH_QUARANTINE`)، و`FakeGameAdapter`. | `T06`, `T07`, `T10`, `T12`, `T13`, `T16`, `T17`, `T21`, `T23` | `HERMETIC_VERIFIED` | `fd31aba` (`test_pr04_fake_runtime.py`) |
| **`TASK-05-01`** | `PR-05a` | `SandboxProfileSeeder`، تطعيم `Golden Cache` بدون `random.dat`، مصفوفة العوالم `EG -> EU`، و`MicrobotHttpAdapter` على `127.0.0.1`. | `T06`, `T10`, `T19` | `HERMETIC_VERIFIED` | `45a5534` (`test_pr05a_sandbox_and_http_adapter.py`) |
| **`TASK-05-02`** | `PR-05b` | خزنة الأسرار `LocalSecretVault (0600)` مع مستورد `Legacy & Jagex`، خادم `LocalSocks5Sidecar` (`RFC1928/1929` + `ATYP=0x03` + `Fail-Closed` + `Geo-Pinning`)، ومشغل العمليات `ManagedProcessTree` لويندوز ولينكس. | `T02`, `T16`, `T19` | `HERMETIC_VERIFIED` | `5b7b499` (`test_pr05b_network_and_vault.py`) |
| **`TASK-06-01` & `08-01`** | `PR-06` + `PR-08` | حلال حوارات `Stronghold` المحلي، سجل الوصفات المعتمد `CuratedRecipeRegistry`، مشغل الجلسة `SingleSlotPilotRunner`، وعميل الاستشارة المعزول `AIAdvisoryClient` بقاطع دائرة `429` وحارس الرؤية. | `T05`, `T14`, `T22`, `T27` | `HERMETIC_VERIFIED` | `19ddbe1` (`test_pr06_pr08_pilot_and_ai.py`) |
| **`TASK-07-01`** | `PR-07` | حارس المساحة `LogQuotaGuardian` (`<= 400MB < 500MB` مع حماية صور الحوادث المفتوحة)، الطابور المتين `BoundedOutbox` الحافظ لتنبيهات `P0`، وبوابة `TelegramGateway` بنظام `Default-Deny RBAC`. | `T02`, `T03`, `T29` | `HERMETIC_VERIFIED` | `cb1d888` (`test_pr07_telegram_and_logs.py`) |
| **`TASK-09-01` & `10-01`** | `PR-09` + `PR-10` | حاسبة السعة بـ `RSS` الفعلي `RssCapacityCalculator` (`<= 6` على 16GB)، مخطط الإقلاع المتدرج `>= 90s`، منسق الاستراحات، بوابة إصدارات `Canary` ذات الـ 7 مراحل، ودفتر البورصة `GEOrderLedger` بضريبة 2% وقفل أدوات الوصفة النشطة. | `T15`, `T18`, `T24`, `T30` | `HERMETIC_VERIFIED` | `4d04f9d` (`test_pr09_pr10_orchestrator_and_economy.py`) |
| **`TASK-11-01`** | `PR-11` | واجهة الأوامر الموحدة `osrs_ee/cli.py` + الدليل الشامل `كيفيه_الاستخدام_من_الالف_للياء.md`. | `T01..T32` E2E | `HERMETIC_VERIFIED` | `b06ec3c` (`test_pr11_cli_e2e.py`) |
| **`TASK-12-01..04`** | `PR-12` | المعالجة الجذرية للعيوب المعمارية الأربعة المثبتة ميدانياً: `ReactiveSafetyWatchdog` غير المعطل (`Zero Sleep`)، محرك الملاحة المقيد بقدرات الحساب `ConstrainedWaypointGraph` (حظر فخ `(3149,3480)` لـ Agility 1)، درع `SessionLifecycleManager` بالإنعاش الذاتي والتراجع الأسي، وفصل النواة `ExecutionEngineKernel` عن `osrs_ee/bots/` (`TaskModule`). | `T33`, `T34`, `T35`, `T36` | `HERMETIC_VERIFIED` | `ade6240` (`test_pr12_field_remediation.py` — `42 passed`) |
| **`TASK-12-05..07`** | `PR-12.1` | معالجة فجوات التوافق السلكي الثلاث مع عميل Microbot الحي (`8081`): تطبيع نقاط الحياة الشامل ضد `None` (`hitpoints/maxHitpoints` + `/skills?name=Hitpoints`)، إضافة `WIDGET_CLICK` (`POST /widgets/click` + تطبيع `clicked/opened/closed`)، وتفعيل `status_endpoint="/state"` افتراضياً مع سقوط تلقائي ذكي لـ `/status` عند `404` (تأجيل استكمال طوابق `StrongholdTaskModule` مع السكربتات). | `T37`, `T38`, `T39` | **`LIVE_WIRE_VERIFIED` (`microbot-2.6.34.jar` @ `8081`)** + `HERMETIC_VERIFIED` | `390419c` (`45 passed` + شهادة الفحص الحي) |

---

## 2. إجراءات التشغيل الحي على جهاز المالك (`PENDING_OWNER_HOST_RUN`)

هذه الإجراءات مشروحة بالتفصيل الكامل في [`كيفيه_الاستخدام_من_الالف_للياء.md`](./كيفيه_الاستخدام_من_الالف_للياء.md) وتُنفذ على جهاز المالك عند الإطلاق الفعلي:
1. تنفيذ `/revoke` لتوكنات Telegram القديمة عبر `@BotFather` وتعيين `TELEGRAM_TOKEN_ROTATED=true` محلياً (`DEC-PEND-03`).
2. بناء ملف `Microbot JAR` على جهاز المالك (`JDK 17`) وتمرير مساره إلى `python -m osrs_ee dry-run-slot` ثم `launch-slot` لحساب تجريبي أول (`DEC-PEND-01 / 02`).
