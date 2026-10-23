# 📋 سجل المهام التنفيذية الموحد (`TASKS.md`)

> **قاعدة التحديث:** لا تُوسم مهمة بـ `VERIFIED` إلا بعد تنفيذ الكود وتشغيل الاختبار المرتبط فعلياً وتوثيق رقم الكوميت.  
> **الحالات المعتمدة:** `VERIFIED` | `IN_PROGRESS` | `PLANNED` | `BLOCKED_PENDING_DECISION` | `DEFERRED_POST_MVP`

---

## المرحلة `PR-00`: احتواء الأسرار وتحصين المستودع (`Security & Hygiene Gate`)

| ID | المهمة | الأولوية | المصادر | الاختبارات | الحالة | الدليل / الكوميت |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **`TASK-00-01`** | تنقيح توكنات Telegram المكشوفة في `للمعمارية.md` دون حذف التاريخ، وتحصين `.gitignore` ضد قواعد البيانات والأسرار والساندبوكس. | `P0` | `AUD-01`, `AUD-02` | `T01`, `T02` | `VERIFIED` | `67b589d` (`test_pr00_security.py`) |
| **`TASK-00-02`** | بناء محرك التنقيح `osrs_ee.security.Redactor` وفاحص الأسرار وصلاحيات الملفات `secret_scan.py`. | `P0` | `AUD-01`, `AUD-02` | `T01`, `T02` | `VERIFIED` | `67b589d` (`5 passed`) |

---

## المرحلة `PR-01`: توحيد تقارير الوكلاء وتأسيس المرجع المعياري (`Normative Baseline`)

| ID | المهمة | الأولوية | المصادر | الاختبارات | الحالة | الدليل / الكوميت |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **`TASK-01-01`** | دمج تقارير الوكلاء الثلاثة (`SRC-A`, `SRC-B`, `SRC-C`) في `docs/AUDIT_REGISTER.md` وحسم التعارضات الستة بالأدلة وتوثيق `ADR-001..008` في `docs/DECISIONS.md`. | `P0` | `AUD-01..35` | `T28` | `VERIFIED` | `PR-01` (`test_pr01_docs_integrity.py`) |
| **`TASK-01-02`** | إنشاء حزمة التوثيق الهندسي الموحدة في `docs/` + `README.md` + `TASKS.md` + `PROGRESS.md` + `docs/RESUME_PROTOCOL.md`. | `P0` | `ي.txt` | `T25`, `T28` | `VERIFIED` | `PR-01` (`test_pr01_docs_integrity.py`) |
| **`TASK-01-03`** | تحديث `وثائق_المحركات/00..06` جراحياً لإزالة كافة التناقضات وإصلاح جميع كتل JSON التالفة وبناء اختبار سلامة الوثائق الآلي. | `P0` | `AUD-15`, `AUD-34` | `T20`, `T25`, `T28` | `VERIFIED` | `PR-01` (`test_pr01_docs_integrity.py`) |

---

## المرحلة `PR-02`: العقود المعيارية، لقطة الإعدادات، والبصمة السلوكية (`Contracts & Config`)

| ID | المهمة | الأولوية | المصادر | الاختبارات | الحالة | الدليل / الكوميت |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **`TASK-02-01`** | كتابة المخططات المعيارية في `schemas/*.schema.json` ونماذج التحقق الصارم في `osrs_ee/contracts/` (`SessionLaunch`, `Observation`, `CommandIntent`, `CommandResult`, `AIProposal`, `TaskRecipe`) مع قاعدة `UNKNOWN -> None`. | `P0` | `AUD-05`, `AUD-06`, `AUD-10`, `AUD-15`, `AUD-17` | `T05`, `T10`, `T14`, `T20` | `VERIFIED` | `PR-02` (`test_pr02_contracts.py`) |
| **`TASK-02-02`** | بناء `RuntimeConfigSnapshot` لفصل سقوف السلامة عن النطاقات التكيفية المختومة بالهاش، ومولد التوقيت `Bounded Log-Normal` وأبعاد النوافذ المشتقة من `persona_seed` مع انتظار رتيب قابل للإلغاء. | `P1` | `AUD-29`, `AUD-32` | `T26`, `T32` | `VERIFIED` | `PR-02` (`test_pr02_contracts.py`) |

---

## المرحلة `PR-03`: نواة الحالة المعاملاتية، الإيجارات، وسجل الأوامر (`Transactional State Core`)

| ID | المهمة | الأولوية | المصادر | الاختبارات | الحالة | الدليل / الكوميت |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **`TASK-03-01`** | بناء مخزن `StateStore` فوق `SQLite (WAL)` بمعاملات ذرية وتصدير تلقائي للقطة منقحة `state_snapshot.json`، وتنفيذ آلة حالات الحساب الموحدة (13 حالة + `reason_code`) وبوابة فك تداول F2P (`10 QP + 100 TTL + 1200m`) والنوم الليلي الإقليمي. | `P0` | `AUD-11`, `AUD-13`, `AUD-14`, `AUD-18`, `AUD-31` | `T08`, `T09`, `T11`, `T31` | `VERIFIED` | `PR-03` (`test_pr03_state_core.py`) |
| **`TASK-03-02`** | تنفيذ الإيجارات الحصرية متزايدة الجيل (`Lease + session_epoch`)، سجل الأوامر `Write-Ahead CommandJournal`، منع تكرار `event_id`، استرداد الانهيار إلى `RECOVERY_REQUIRED`، وسجل الموافقات وحيدة الاستخدام `ApprovalRecord`. | `P0` | `AUD-04`, `AUD-05`, `AUD-12`, `AUD-14` | `T03`, `T04`, `T07`, `T08`, `T09` | `VERIFIED` | `PR-03` (`test_pr03_state_core.py`) |

---

## المرحلة `PR-04`: المشرف الأحادي، بوابة المحول، والعامل المحاكي بحقن الأعطال (`Hermetic Runtime`)

| ID | المهمة | الأولوية | المصادر | الاختبارات | الحالة | الدليل / الكوميت |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **`TASK-04-01`** | بناء `GameAdapterGate` للتحقق من هوية العميل والإيجار والجيل وقائمة المسارات البيضاء ورايات تعطيل التليمتري، وبناء `FakeGameAdapter` القابل لحقن الأعطال عند كل حد. | `P0` | `AUD-03`, `AUD-05`, `AUD-07`, `AUD-12` | `T04`, `T06`, `T07` | `VERIFIED` | `PR-04` (`test_pr04_fake_runtime.py`) |
| **`TASK-04-02`** | بناء `TaskWorker` (دورة القراءة ◄ التحقق المسبق ◄ تدوين النية ◄ الإرسال ◄ التحقق البعدي، عقد البنك `execute_bank_turnaround` المحقق، فحص `Varp 281`، رفض `INSUFFICIENT_RESOURCES` بطلب `REPLAN`، وحجر `DEATH_QUARANTINE`). | `P0` | `AUD-16`, `AUD-17`, `AUD-19`, `AUD-20`, `AUD-21` | `T10`, `T12`, `T13`, `T21`, `T23` | `VERIFIED` | `PR-04` (`test_pr04_fake_runtime.py`) |
| **`TASK-04-03`** | بناء `SingleSlotSupervisor` بمستويات الإيقاف الثلاثة (`pause / drain / abort` وكبح `AutoLoginScript`)، وحارس `game_tick` مقابل ثبات الإحداثيات وقاطع دائرة إعادة التشغيل. | `P1` | `AUD-22`, `AUD-23`, `AUD-24` | `T16`, `T17`, `T27` | `VERIFIED` | `PR-04` (`test_pr04_fake_runtime.py`) |

---

## المراحل اللاحقة المرتبطة بالبيئة الحية والتوسع (`PR-05` .. `PR-10`)

| المرحلة | المهمة المجمعة | الاعتمادات والقرارات المطلوبة | الاختبارات | الحالة |
| :---: | :--- | :--- | :---: | :---: |
| **`PR-05`** | محول `MicrobotHttpAdapter` القرائي + مُطعم الساندبوكس والبروفايل والكاش + جسر `LocalSocks5Sidecar` بسياسة `Fail-Closed`. | `PR-04` + حسم `DEC-PEND-01` (نظام تشغيل الهدف ومسار العميل) | `T06`, `T10`, `T19` | `BLOCKED_PENDING_DECISION` (للجزء الحي) |
| **`PR-06`** | ترحيل وتشغيل مهمة تجريبية واحدة مراقبة (`Single-Slot Task Pilot` — `Stronghold` بقاموس الأسئلة المحلي الحتمي). | `PR-05` + حسم `DEC-PEND-02` (نوع الحساب التجريبي) | تشغيل حي مراقب | `BLOCKED_PENDING_DECISION` |
| **`PR-07`** | بوابة Telegram الآمنة (RBAC، موافقات مربوطة، طابور `Outbox` وحصص سجلات `<= 500MB` ومحاسبة مفصولة). | `PR-06` + حسم `DEC-PEND-03` (تدوير توكن Telegram القديم) | `T03`, `T24`, `T29` | `DEFERRED_POST_MVP` |
| **`PR-08`** | عميل الاستشارة الذكية المعزول (`AIAdvisoryClient` بميزانية ومهلة وقاطع دائرة وفحص قدرة الرؤية). | `PR-04` | `T05`, `T22` | `DEFERRED_POST_MVP` |
| **`PR-09`** | التوسع المتعدد المحكوم بالـ `RSS` الفعلي + تجديد `Microbreak Sticky-IP` + بوابة تحديثات `Staging/Canary`. | `PR-06`, `PR-07` | `T15`, `T18`, `T19` | `DEFERRED_POST_MVP` |
| **`PR-10`** | دفتر أوامر البورصة (`GE Order Ledger`) + قفل مدخلات الوصفات (`Active Recipe Input Lock`) + المحاسبة بعد ضريبة `2%`. | `PR-09` | `T13`, `T24` | `DEFERRED_POST_MVP` |
