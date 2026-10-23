# 🧪 مصفوفة اختبارات القبول الموحدة وبوابات التحقق (`ACCEPTANCE_TESTS.md`)

> **الإصدار:** `1.1 — Unified Acceptance Matrix (T01–T32)`  
> **القاعدة الإلزامية:** لا تُوسم خانة بـ `HERMETIC_VERIFIED` إلا باجتياز اختبار آلي فعلي في `tests/`؛ ولا تُوسم `LIVE_VERIFIED` إلا بعد التشغيل على جهاز الهدف مع عميل حقيقي (`PR-05b / PR-06+`).

---

## مصفوفة اختبارات القبول (`T01` .. `T32`)

| ID | الفئة | سيناريو الفحص ومعيار النجاح الحتمي | المرحلة | الحالة الفعلية والدليل القابل للإعادة |
| :--- | :--- | :--- | :---: | :--- |
| **`T01`** | أمان | مسح المستودع بالكامل (`secret_scan`) للتأكد من صفر توكنات أو مفاتيح مكشوفة، وكشف الأنماط الاصطناعية دون طباعة قيمتها. | `PR-00` | `HERMETIC_VERIFIED` (`tests/test_pr00_security.py`) *(يتبقى تدوير توكن Telegram الخارجي `DEC-PEND-03`)* |
| **`T02`** | أمان | ضخ كلمة مرور `Canary` وبريد وبروكسي وتوكن عبر مسارات السجل والبيانات والتأكد من حجبها 100% واستبعاد ملفات الحالة بـ `.gitignore`. | `PR-00` | `HERMETIC_VERIFIED` (`tests/test_pr00_security.py`) |
| **`T03`** | حوكمة | رفض مستخدم/محادثة غير مصرحة (`Default-Deny RBAC`)، ورفض إعادة استخدام موافقة `ApprovalRecord` مستهلكة أو منتهية أو باختلاف `session_epoch` أو `plan_hash`. | `PR-03` / `PR-07` | `HERMETIC_VERIFIED` للنواة (`tests/test_pr03_state_core.py`)؛ ربط بوت Telegram في `PR-07` |
| **`T04`** | ملكية | عامل قديم أو اقتراح متأخر يحمل `session_epoch` أو `lease_id` سابقاً بعد تدوير السلوت يُرفض فوراً بـ `STALE_CONTEXT`. | `PR-03` / `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T05`** | AI | رفض أي اقتراح `AIProposal` خارج التعداد المغلق أو يحمل إحداثيات نقر `Click(x,y)` أو أوامر خطرة نتيجة Prompt Injection. | `PR-02` / `PR-08` | `HERMETIC_VERIFIED` للعقد (`tests/test_pr02_contracts.py`)؛ العميل الشبكي في `PR-08` |
| **`T06`** | عزل | `GameAdapterGate` و`MicrobotHttpAdapter` يرفضان غياب `--disable-telemetry`، ويرفضان غير `127.0.0.1` والتوكن الخاطئ و`/scripts/deploy`، ويطعمان البروفايل مسبقاً. | `PR-04` / `PR-05a` | `HERMETIC_VERIFIED` (`test_pr04_fake_runtime.py`, `test_pr05a_sandbox_and_http_adapter.py`) |
| **`T07`** | أوامر | انقطاع النقل بعد حدوث سحب البنك يسجل الأمر `UNKNOWN` ويمنع التكرار التلقائي ويوقف الجلسة للمصالحة. | `PR-03` / `PR-04` | `HERMETIC_VERIFIED` (`test_pr03_state_core.py`, `test_pr04_fake_runtime.py`) |
| **`T08`** | متانة | محاكاة انهيار مفاجئ وإعادة فتح `StateStore`: بقاء قاعدة `SQLite` سليمة، تحول الجلسات غير المغلقة إلى `RECOVERY_REQUIRED`، وتطابق `state_snapshot.json`. | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T09`** | حالة | منع العودة العمياء إلى `READY` بعد الانهيار أو الموافقة (المرور بـ `RECOVERY_REQUIRED / RECONCILING`)، حفظ `cooldown_until`، ومنع تكرار `event_id`. | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T10`** | لعبة | قراءة `/varps?id=281` لجزيرة التعليمات بدلاً من `/varbits?id=281`، وإرجاع `bank.items = None (UNKNOWN)` عندما يكون البنك مغلقاً. | `PR-02`..`05a` | `HERMETIC_VERIFIED` (`test_pr02_contracts.py`, `test_pr04_fake_runtime.py`, `test_pr05a_sandbox_and_http_adapter.py`) |
| **`T11`** | لعبة | بوابة فتح التداول لـ F2P ترفض اعتبار الحساب مفتوح التجارة عند `7 QP` أو قبل تمام `10 QP + 100 Total Level + 1200 دقيقة لعب` (`STAGE_1B_SEASONING`). | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T12`** | بنك | فحص مستوى الأداة قبل سحبها، تفريغ العتاد عند اشتراطه، كشف فشل `withdraw` الصامت، والتوقف عند `Bank PIN`. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T13`** | اقتصاد | تسييل البورصة معطل صراحة في MVP (`PolicyDeniedError`)؛ وفي `PR-10` يخضع لـ `Default-Deny` وقفل المدخلات ودفتر الأوامر. | `PR-04` / `PR-10` | `HERMETIC_VERIFIED` لبوابة MVP (`test_pr04_fake_runtime.py`)؛ دفتر الـ GE في `PR-10` |
| **`T14`** | وصفات | رفض تشغيل أي وصفة بحالة نضج أقل من بوابة التشغيل المطلوبة (`CONCEPT_ONLY / AVAILABLE_SOURCE < HERMETIC_TESTED`). | `PR-02` / `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr02_contracts.py`) |
| **`T15`** | تحديثات | عدم وجود أي سحب تلقائي في وقت التشغيل، ورفض تفعيل إصدار `staging` غير متوافق أو غير معتمد. | `PR-09` | `DEFERRED -> PR-09` |
| **`T16`** | إيقاف | التزام `pause` بوقف الأوامر الجديدة فوراً، وكبح `AutoLoginScript` في `drain`، وتحول `drain` المتعثر إلى `abort` وقتل الـ JVM. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T17`** | مراقبة | التمييز بين نشاط ثابت الإحداثيات يتقدم فيه `game_tick` وبين تجمد `game_tick` الفعلي، وتفعيل قاطع دائرة إعادة التشغيل. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T18`** | موارد | حاسبة السعة تحسب من `RSS` المقاس + احتياطي النظام وتمنع تجاوز حد الذاكرة المتاحة. | `PR-09` | `DEFERRED -> PR-09` |
| **`T19`** | شبكة | فرض `--proxy=socks5://127.0.0.1:<port>`، رفض `HTTP`، استبعاد `random.dat` من `Golden Cache`، منع تشارك البروكسي النشط، وربط `EG -> EU`. | `PR-03` / `PR-05a` / `PR-09` | `HERMETIC_VERIFIED` للإعداد والعزل (`test_pr03`, `test_pr05a`)؛ خادم التحويل الحي في `PR-05b` |
| **`T20`** | عقود | جميع المخططات ترفض الحقول الزائدة والناقصة والأنواع الخاطئة وتتحقق من كافة أمثلة الوثائق 100%. | `PR-01` / `PR-02` | `HERMETIC_VERIFIED` (`test_pr01_docs_integrity.py`, `test_pr02_contracts.py`) |
| **`T21`** | ملكية | عند غياب مورد لازم في البنك، يعيد العامل `REPLAN_REQUESTED (INSUFFICIENT_RESOURCES)` دون تغيير المهمة ذاتياً. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T22`** | AI | كبح استدعاء الـ AI عند استنفاد الميزانية أو خطأ `429/timeout` والتراجع الآمن دون كسر العامل، وعدم إرسال صور لمسار نصي فقط. | `PR-08` | `DEFERRED -> PR-08` |
| **`T23`** | سلامة | رصد موت اللاعب ينقل الحساب فوراً إلى `BLOCKED (DEATH_QUARANTINE)` ويمنع جدولة نفس المهمة الخطرة مباشرة. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T24`** | محاسبة | فصل صافي التدفق النقدي (بعد خصم 2% ضريبة GE وتكلفة المدخلات) عن التغير المخزني وعدم تكرار القيد لنفس `event_id`. | `PR-03` / `PR-07` / `PR-10` | `PARTIAL` (عدم تكرار `event_id` محقق في `test_pr03`؛ دفتر الـ GE في `PR-10`) |
| **`T25`** | وثائق | خلو كافة الوثائق المعيارية من ادعاءات الحماية المطلقة (`100% anti-ban`) ومن التناقضات الملغاة. | `PR-01` | `HERMETIC_VERIFIED` (`tests/test_pr01_docs_integrity.py`) |
| **`T26`** | إعدادات | رفض أي إعداد به `min > max` أو يتجاوز حدود السلامة، وتثبيت `config_hash`، وإلغاء الانتظار الرتيب فوراً عند `abort`. | `PR-02` | `HERMETIC_VERIFIED` (`tests/test_pr02_contracts.py`) |
| **`T27`** | بناء | تشغيل `pytest` في بيئة نظيفة بدون لعبة أو إنترنت ينجح 100% ويعيد كود خروج `0`. | `PR-00`..`05a` | `HERMETIC_VERIFIED` (`26 passed`) |
| **`T28`** | حوكمة | وجود سجل ADRs مكتمل وترابط معرفات `AUD-*` و`ADR-*` وصحة جميع كتل JSON في المستودع. | `PR-01` | `HERMETIC_VERIFIED` (`tests/test_pr01_docs_integrity.py`) |
| **`T29`** | مساحة | إثبات التزام سياسة تدوير السجلات والصور وطابور الرسائل بسقف `< 500 MB` مع حماية الحوادث المفتوحة. | `PR-07` | `DEFERRED -> PR-07` |
| **`T31`** | سلوك | المجدول يمنع تشغيل الحساب داخل نافذة نومه الليلي الإقليمي (`Circadian Window`). | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T32`** | سلوك | مشتق البصمة الشخصية (`PersonaProfile`) ينتج أبعاد نافذة وتوزيعات `Bounded Log-Normal` ملتوية لليمين داخل الحدود. | `PR-02` | `HERMETIC_VERIFIED` (`tests/test_pr02_contracts.py`) |
