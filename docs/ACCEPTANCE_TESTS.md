# 🧪 مصفوفة اختبارات القبول الموحدة وبوابات التحقق (`ACCEPTANCE_TESTS.md`)

> **الإصدار:** `2.0 — Complete Hermetic Acceptance Matrix (T01–T32)`  
> **القاعدة الإلزامية:** جميع البنود أدناه محققة فعلياً بالاختبارات الآلية (`HERMETIC_VERIFIED — 38 passed`). أما التحقق الحي على خوادم اللعبة (`LIVE_VERIFIED`) فيُجرى على جهاز المالك وفق دليل [`كيفيه_الاستخدام_من_الالف_للياء.md`](../كيفيه_الاستخدام_من_الالف_للياء.md).

---

## مصفوفة اختبارات القبول (`T01` .. `T32`)

| ID | الفئة | سيناريو الفحص ومعيار النجاح الحتمي | المرحلة | الحالة الفعلية والدليل القابل للإعادة |
| :--- | :--- | :--- | :---: | :--- |
| **`T01`** | أمان | مسح المستودع بالكامل (`secret_scan`) للتأكد من صفر توكنات أو مفاتيح مكشوفة، وكشف الأنماط الاصطناعية دون طباعة قيمتها. | `PR-00` | `HERMETIC_VERIFIED` (`tests/test_pr00_security.py`) *(مع بوابة `DEC-PEND-03` قبل تفعيل HTTP لبوت Telegram)* |
| **`T02`** | أمان | ضخ كلمة مرور `Canary` وبريد وبروكسي وتوكن عبر مسارات السجل والخزنة والتليجرام والـ CLI والتأكد من حجبها 100%. | `PR-00`..`11` | `HERMETIC_VERIFIED` (`test_pr00_security.py`, `test_pr05b`, `test_pr07`, `test_pr11_cli_e2e.py`) |
| **`T03`** | حوكمة | إسقاط غير المصرحين صامتاً (`Default-Deny RBAC`)، ورفض إعادة استخدام موافقة `ApprovalRecord` مستهلكة أو منتهية أو باختلاف `plan_hash`. | `PR-03` / `PR-07` | `HERMETIC_VERIFIED` (`test_pr03_state_core.py`, `test_pr07_telegram_and_logs.py`) |
| **`T04`** | ملكية | عامل قديم أو اقتراح متأخر يحمل `session_epoch` أو `lease_id` سابقاً بعد تدوير السلوت يُرفض فوراً بـ `STALE_CONTEXT`. | `PR-03` / `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T05`** | AI | رفض أي اقتراح `AIProposal` خارج التعداد المغلق أو يحمل إحداثيات نقر `Click(x,y)` أو أوامر خطرة نتيجة Prompt Injection وعزله في `<untrusted_game_data>`. | `PR-02` / `PR-08` | `HERMETIC_VERIFIED` (`test_pr02_contracts.py`, `test_pr06_pr08_pilot_and_ai.py`) |
| **`T06`** | عزل | `GameAdapterGate` و`MicrobotHttpAdapter` يرفضان غياب `--disable-telemetry`، ويرفضان غير `127.0.0.1` والتوكن الخاطئ و`/scripts/deploy`. | `PR-04` / `PR-05a` | `HERMETIC_VERIFIED` (`test_pr04_fake_runtime.py`, `test_pr05a_sandbox_and_http_adapter.py`) |
| **`T07`** | أوامر | انقطاع النقل بعد حدوث سحب البنك يسجل الأمر `UNKNOWN` ويمنع التكرار التلقائي ويوقف الجلسة للمصالحة. | `PR-03` / `PR-04` | `HERMETIC_VERIFIED` (`test_pr03_state_core.py`, `test_pr04_fake_runtime.py`) |
| **`T08`** | متانة | محاكاة انهيار مفاجئ وإعادة فتح `StateStore`: بقاء قاعدة `SQLite` سليمة، تحول الجلسات غير المغلقة إلى `RECOVERY_REQUIRED`، وتطابق `state_snapshot.json`. | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T09`** | حالة | منع العودة العمياء إلى `READY` بعد الانهيار أو الموافقة (المرور بـ `RECOVERY_REQUIRED / RECONCILING`)، حفظ `cooldown_until`، ومنع تكرار `event_id`. | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T10`** | لعبة | قراءة `/varps?id=281` لجزيرة التعليمات بدلاً من `/varbits?id=281`، وإرجاع `bank.items = None (UNKNOWN)` عندما يكون البنك مغلقاً. | `PR-02`..`05a` | `HERMETIC_VERIFIED` (`test_pr02_contracts.py`, `test_pr04_fake_runtime.py`, `test_pr05a_sandbox_and_http_adapter.py`) |
| **`T11`** | لعبة | بوابة فتح التداول لـ F2P ترفض اعتبار الحساب مفتوح التجارة عند `7 QP` أو قبل تمام `10 QP + 100 Total Level + 1200 دقيقة لعب` (`STAGE_1B_SEASONING`). | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T12`** | بنك | فحص مستوى الأداة قبل سحبها، تفريغ العتاد عند اشتراطه، كشف فشل `withdraw` الصامت، والتوقف عند `Bank PIN`. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T13`** | اقتصاد | بوابة التسييل ترفض الأوامر غير المصرحة في MVP، وتخضع في `GEOrderLedger` لسياسة `Default-Deny` وقفل مدخلات الوصفة النشطة. | `PR-04` / `PR-10` | `HERMETIC_VERIFIED` (`test_pr04_fake_runtime.py`, `test_pr09_pr10_orchestrator_and_economy.py`) |
| **`T14`** | وصفات | رفض تشغيل أي وصفة بحالة نضج أقل من بوابة التشغيل المطلوبة (`CONCEPT_ONLY < HERMETIC_TESTED`). | `PR-02` / `PR-06` | `HERMETIC_VERIFIED` (`test_pr02_contracts.py`, `test_pr06_pr08_pilot_and_ai.py`) |
| **`T15`** | تحديثات | بوابة `ReleaseStagingGate` ذات الـ 7 مراحل تمنع تخطي `CONTRACT_TESTED + APPROVED + CANARY` وتحافظ على النسخة المستقرة عند فشل الكناري. | `PR-09` | `HERMETIC_VERIFIED` (`tests/test_pr09_pr10_orchestrator_and_economy.py`) |
| **`T16`** | إيقاف | التزام `pause` بوقف الأوامر الجديدة، وكبح `AutoLoginScript` في `drain`، وقتل شجرة العمليات كاملة بدون عمليات يتيمة في `ManagedProcessTree`. | `PR-04` / `PR-05b` | `HERMETIC_VERIFIED` (`test_pr04_fake_runtime.py`, `test_pr05b_network_and_vault.py`) |
| **`T17`** | مراقبة | التمييز بين نشاط ثابت الإحداثيات يتقدم فيه `game_tick` وبين تجمد `game_tick` الفعلي، وتفعيل قاطع دائرة إعادة التشغيل. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T18` / `T30`** | موارد | حاسبة السعة `RssCapacityCalculator` تحسب من `RSS` الفعلي (`<= 6` على مضيف 16GB)، وترفض الإقلاع عند انخفاض الذاكرة الحرة، وتفرض فارق إقلاع `>= 90s` ومنع تزامن الاستراحات. | `PR-09` | `HERMETIC_VERIFIED` (`tests/test_pr09_pr10_orchestrator_and_economy.py`) |
| **`T19`** | شبكة | فرض `--proxy=socks5://127.0.0.1:<port>`، تمرير `ATYP=0x03` عبر `LocalSocks5Sidecar` مع مصادقة `RFC1929`، وقطع الاتصال فورياً (`Fail-Closed`) ومنع خرق `Geo-Pinning`. | `PR-05a` / `PR-05b` | `HERMETIC_VERIFIED` (`test_pr05a_sandbox_and_http_adapter.py`, `test_pr05b_network_and_vault.py`) |
| **`T20`** | عقود | جميع المخططات ترفض الحقول الزائدة والناقصة والأنواع الخاطئة وتتحقق من كافة أمثلة الوثائق 100%. | `PR-01` / `PR-02` | `HERMETIC_VERIFIED` (`test_pr01_docs_integrity.py`, `test_pr02_contracts.py`) |
| **`T21`** | ملكية | عند غياب مورد لازم في البنك، يعيد العامل `REPLAN_REQUESTED (INSUFFICIENT_RESOURCES)` دون تغيير المهمة ذاتياً. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T22`** | AI | كبح استدعاء الـ AI عند استنفاد الميزانية أو خطأ `429/timeout` بفتح قاطع الدائرة والتراجع الآمن، وعدم إرسال صور لمسار نصي فقط (`vision_verified=False`). | `PR-08` | `HERMETIC_VERIFIED` (`tests/test_pr06_pr08_pilot_and_ai.py`) |
| **`T23`** | سلامة | رصد موت اللاعب ينقل الحساب فوراً إلى `BLOCKED (DEATH_QUARANTINE)` ويمنع جدولة نفس المهمة الخطرة مباشرة. | `PR-04` | `HERMETIC_VERIFIED` (`tests/test_pr04_fake_runtime.py`) |
| **`T24`** | محاسبة | قفل أدوات الوصفة النشطة ضد البيع، حساب ضريبة البورصة الرسمية `2% GE Tax`، فصل الربح المحقق `COLLECTED` عن غير المحقق، وفرض نافذة شراء `4h Buy-Limit`. | `PR-03` / `PR-10` | `HERMETIC_VERIFIED` (`test_pr03_state_core.py`, `test_pr09_pr10_orchestrator_and_economy.py`) |
| **`T25`** | وثائق | خلو كافة الوثائق المعيارية من ادعاءات الحماية المطلقة (`100% anti-ban`) ومن التناقضات الملغاة. | `PR-01` | `HERMETIC_VERIFIED` (`tests/test_pr01_docs_integrity.py`) |
| **`T26`** | إعدادات | رفض أي إعداد به `min > max` أو يتجاوز حدود السلامة، وتثبيت `config_hash`، وإلغاء الانتظار الرتيب فوراً عند `abort`. | `PR-02` | `HERMETIC_VERIFIED` (`tests/test_pr02_contracts.py`) |
| **`T27`** | بناء | تشغيل `pytest` و`simulate-pilot` في بيئة نظيفة بدون لعبة أو إنترنت ينجح 100% ويعيد كود خروج صريح (`0` للنجاح، غير صفري للفشل). | `PR-00`..`11` | `HERMETIC_VERIFIED` (`38 passed` + `test_pr06_pr08` + `test_pr11_cli_e2e.py`) |
| **`T28`** | حوكمة | وجود سجل ADRs مكتمل وترابط معرفات `AUD-*` و`ADR-*` وصحة جميع كتل JSON في المستودع. | `PR-01` | `HERMETIC_VERIFIED` (`tests/test_pr01_docs_integrity.py`) |
| **`T29`** | مساحة | إثبات سقف `<= 400MB < 500MB` حسابياً وعملياً، حماية صور الحوادث المفتوحة من الحذف، وحماية تنبيهات `P0_INCIDENT` في `BoundedOutbox`. | `PR-07` | `HERMETIC_VERIFIED` (`tests/test_pr07_telegram_and_logs.py`) |
| **`T31`** | سلوك | المجدول يمنع تشغيل الحساب داخل نافذة نومه الليلي الإقليمي (`Circadian Window`). | `PR-03` | `HERMETIC_VERIFIED` (`tests/test_pr03_state_core.py`) |
| **`T32`** | سلوك | مشتق البصمة الشخصية (`PersonaProfile`) ينتج أبعاد نافذة وتوزيعات `Bounded Log-Normal` ملتوية لليمين داخل الحدود. | `PR-02` | `HERMETIC_VERIFIED` (`tests/test_pr02_contracts.py`) |
