# 🧭 سجل التقدم وبوصلة الاستئناف الإلزامية (`PROGRESS.md`)

> **تنبيه إلزامي عند كتابة «تابع» أو استئناف أي جلسة مستقبلية:** اقرأ هذا الملف أولاً، ثم نفّذ قائمة الفحص السبعية في [`docs/RESUME_PROTOCOL.md`](./docs/RESUME_PROTOCOL.md).

---

## 1. الحالة الراهنة للمشروع (`Current Snapshot`)

* **المرحلة الحالية:** **اكتمال البنية البرمجية والتوثيقية الشاملة ومعالجة العيوب الميدانية الأربعة (`PR-12` / `ade6240`) + رقعة التوافق السلكي الكامل مع عميل Microbot الحي (`PR-12.1`)**، متضمنة النواة التفاعلية غير المعطلة `osrs_ee/core/`، ومحرك الملاحة المقيد بالقدرات `osrs_ee/navigation/`، وواجهة المهام المعزولة `osrs_ee/bots/`، وتوافق بورت `8081` المباشر (`/state`, `hitpoints`/`/skills`, `WIDGET_CLICK -> /widgets/click`).
* **نتيجة فحص الأسرار (`Secret Scan`):** `[SECRET_SCAN_OK] Zero secret patterns detected under .`
* **نتيجة الاختبارات الفعلية المنفذة (`pytest`):** **`45 passed` (0 failed)** عبر 13 ملف اختبار تغطي مصفوفة القبول الموسعة `T01..T39`.
* **التمييز المعرفي الدقيق (`Epistemic Status`):**
  * **`HERMETIC_VERIFIED`:** جميع المحركات، العقود، قواعد `SQLite WAL`، خزنة الأسرار `0600`، خادم التحويل المحلي `LocalSocks5Sidecar`، العزل، مشرف الجلسة، حلال الحوارات، مستشار الـ AI، بوابة التليجرام وحارس `<500MB`، حاسبة `RSS` وبوابة `Canary`، دفتر البورصة `GEOrderLedger`، وواجهة `CLI` محققة آلياً بالكامل.
  * **`PENDING_OWNER_HOST_RUN`:** التشغيل الحي ضد خوادم Jagex الفعلية وواجهة Telegram الحية يتم على جهاز المالك (Windows أو Linux VPS) باتباع خطوات `كيفيه_الاستخدام_من_الالف_للياء.md` بعد تدوير توكن البوت وتوفير ملف `Microbot JAR` وبروكسيات `SOCKS5`.

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
| **`PR-12.1`** | *(رقعة التوافق السلكي الحي)* | إغلاق فجوات التوافق السلكي الثلاث مع عميل Microbot الحي (`AUD-40..42` / `ADR-013`): تطبيع نقاط الحياة ضد `None` (`hitpoints/maxHitpoints` + `/skills?name=Hitpoints`)، دعم `WIDGET_CLICK -> POST /widgets/click`، واعتماد `status_endpoint="/state"` افتراضياً مع سقوط تلقائي ذكي لـ `/status`. | **`45 passed`** (`T01..T39`) |

---

## 3. ما العمل عند كتابة «تابع» مستقبلاً؟

إذا أرسل المالك مخرجات تشغيل حي من جهازه (مثل سجلات ربط `launch-slot` مع `Microbot JAR` أو طلب إضافة وصفة `TaskRecipe` جديدة):
1. شغل `PYTHONPATH=. python3 -m osrs_ee doctor` و`PYTHONPATH=. pytest -v` للتحقق من سلامة خط الأساس (`38 passed`).
2. أضف أي وصفات أو تحسينات جديدة عبر نفس دورة الانضباط (`CONCEPT_ONLY -> HERMETIC_TESTED` باختبار آلي أولاً ثم تحديث الوثائق وفحص الأسرار).
