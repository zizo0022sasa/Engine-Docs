# 🧭 سجل التقدم وبوصلة الاستئناف الإلزامية (`PROGRESS.md`)

> **تنبيه إلزامي عند كتابة «تابع» أو استئناف الجلسة:** اقرأ هذا الملف أولاً، ثم نفّذ قائمة الفحص السبعية في [`docs/RESUME_PROTOCOL.md`](./docs/RESUME_PROTOCOL.md) قبل تعديل أي ملف. لا تبدأ من الصفر ولا تكرر المهام الموسومة بـ `VERIFIED`.

---

## 1. الحالة الراهنة للمشروع (`Current Snapshot`)

* **المرحلة الحالية:** اكتمال التأسيس الموحد والنواة البرمجية المعزولة بالكامل من **`PR-00` حتى `PR-05a`**، واجتياز جميع الاختبارات الآلية الـ **26** بنجاح.
* **آخر نقطة تحقق مكتملة:** `PR-05a — Sandbox Profile Seeder, Golden Cache Hygiene, JVM Guard & Loopback MicrobotHttpAdapter`
* **المهمة التالية الجاهزة للتنفيذ الفوري (`NEXT_AUTHORIZED_TASK`):**
  1. **`TASK-05-02` (`PR-05b`):** بناء واختبار خادم التحويل المحلي `LocalSocks5Sidecar` (`socks5h` + `Fail-Closed` شبكي معزول `T19`).
  2. **`TASK-08-01` (`PR-08`):** بناء `LocalDialogueSolver` + `AIAdvisoryClient` المعزول بميزانية وقاطع دائرة `429/timeout` (`T05`, `T22`).
  3. **`TASK-07-01` (`PR-07a`):** بناء `BoundedOutbox` و`LogQuotaGuardian` (`<= 500 MB`) ودفتر المحاسبة المنقح (`T24`, `T29`).
* **نتيجة فحص الأسرار (`Secret Scan`):** `[SECRET_SCAN_OK] Zero secret patterns detected`
* **نتيجة الاختبارات الفعلية المنفذة (`pytest`):** **`26 passed` (0 failed)** عبر 6 ملفات اختبار معزولة.

---

## 2. سجل الدفعات المنجزة والمرفوعة إلى GitHub (`Verified Commits`)

| المرحلة | الكوميت على `main` | الملخص الهندسي الدقيق لما نُفذ وتُحقق منه | الاختبارات المجتازة |
| :---: | :---: | :--- | :---: |
| **`PR-00`** | `67b589d` | تنقيح توكنات Telegram في `للمعمارية.md`، تحصين `.gitignore`، وبناء `Redactor` و`secret_scan.py`. | `5 passed` (`T01, T02`) |
| **`PR-01`** | `a29cc8a` | توحيد تقارير الوكلاء الثلاثة (`AUD-01..35`)، حسم التعارضات الستة بالأدلة، اعتماد `ADR-001..008`، إنشاء حزمة `docs/` وبروتوكول الاستئناف، وتحديث `وثائق_المحركات/00..06` جراحياً. | `+4 passed` (`T20, T25, T28`) |
| **`PR-02`** | `2214972` | بناء مخططات `schemas/*.schema.json`، مدقق المخططات الصارم، `RuntimeConfigSnapshot` المختوم بـ `sha256`، ومشتق البصمة `PersonaProfile` (`Bounded Log-Normal` + انتظار رتيب قابل للإلغاء). | `+7 passed` (`T05, T10, T14, T20, T26, T32`) |
| **`PR-03`** | `e57c42f` | بناء `StateStore` الذري فوق `SQLite (WAL)` مع تصدير `state_snapshot.json`، آلة الحالات ذات الـ 13 حالة، الإيجارات والجيل `session_epoch`، سجل الأوامر `CommandJournal`، استرداد الانهيار إلى `RECOVERY_REQUIRED`، بوابة `10QP+100TTL+20h`، النوم الليلي الإقليمي، وموافقات RBAC وحيدة الاستخدام. | `+4 passed` (`T03, T04, T07, T08, T09, T11, T31`) |
| **`PR-04`** | `fd31aba` | بناء `SingleSlotSupervisor` (`pause / drain / abort` + حارس `game_tick` وقاطع الدائرة)، `GameAdapterGate`، `TaskWorker` (بروتوكول البنك المحقق وحجر الموت `DEATH_QUARANTINE` وطلب `REPLAN`)، و`FakeGameAdapter` بحقن الأعطال. | `+4 passed` (`T06, T10, T12, T13, T16, T17, T21, T23`) |
| **`PR-05a`** | *(التزام الإغلاق الحالي)* | بناء `SandboxProfileSeeder` (تفعيل `AgentServerPlugin`، توكن `0600`، تطعيم `Golden Cache` باستثناء `random.dat`، رايات `--disable-telemetry` و`-Xmx768m`، عوالم `EG -> EU`) و`MicrobotHttpAdapter` المختبر عبر مقبس HTTP محلي حقيقي (`127.0.0.1`). | `+2 passed` (`T06, T10, T19` — إجمالي **`26 passed`**) |

---

## 3. القرارات المعلقة بانتظار المالك (`Pending Owner Decisions`)

هذه النقاط الثلاث **لا تعطل** إكمال الوحدات المحلية (`TASK-05-02`, `TASK-08-01`, `TASK-07-01`) عند كتابة «تابع»، لكن يلزم حسمها قبل بدء التشغيل الحي على حسابات فعلية (`PR-06` وربط Telegram الحي):

1. **`DEC-PEND-01` (بيئة التشغيل الحية):** هل سيتم تشغيل العميل الحي على **`Windows`** أم **`Linux VPS (Ubuntu + Xvfb)`**؟ وما مسار `JDK 17` وملف `Microbot JAR` المعتمد لديك؟
2. **`DEC-PEND-02` (نوع الحساب التجريبي الأول في `PR-06`):** هل الحساب التجريبي الأول حساب قديم (`LEGACY email|pass`) أم حساب حديث (`JAGEX_SESSION JX_*`)؟
3. **`DEC-PEND-03` (احتواء أمني خارجي قبل `PR-07`):** تأكيد تنفيذ `/revoke` لتوكني بوت Telegram القديمين عبر `@BotFather` وتقييد/حذف الـ Gist الخارجي العام.

---

## 4. كيف تستأنف الجلسة القادمة بكلمة «تابع»؟

بمجرد إرسال كلمة **«تابع»** (سواء مع الإجابة عن القرارات الثلاثة أعلاه أو بدونها):
1. ينفذ المهندس فحص `docs/RESUME_PROTOCOL.md` في ثوانٍ دون إعادة أي مرحلة من `PR-00 .. PR-05a`.
2. يبدأ مباشرة في تنفيذ واختبار **`TASK-05-02` (`LocalSocks5Sidecar` Fail-Closed Proxy Bridge)** ثم **`TASK-08-01` (`AIAdvisoryClient` & `LocalDialogueSolver`)** ثم **`TASK-07-01` (`BoundedOutbox` & `LogQuotaGuardian`)**، وإذا توفرت إجابات المالك لـ `DEC-PEND-01/02` ينتقل بعدها إلى فحص القراءة والتشغيل التجريبي المراقب (`PR-06`).
