# نصوص تصحيح الوثائق وخطة التنفيذ — OSRS_Bot

**التاريخ:** 2026-10-23  
**المرجع:** OSRS_ENGINEERING_AUDIT_AR.md، النتائج F01–F29.  
**الحالة:** مقترح للاعتماد؛ لم يُطبق على المستودع. **استبدل الأقسام المتعارضة، لا تضف هذه النصوص أسفلها مع إبقائها سارية.**

## 1. قرارات اعتماد قبل كتابة المحركات

| ADR | القرار المقترح | الافتراضي الآمن لحين الاعتماد |
|---|---|---|
| ADR-001 | baseline معيارية واحدة وتراتبية واضحة للقرارات | الشات ليس مواصفة تنفيذ |
| ADR-002 | AI استشاري فقط في MVP | لا أفعال مولدة من AI |
| ADR-003 | SQLite محلي للحالة وjournal، أو JSON ببروتوكول durability كامل | لا كتابات متزامنة متعددة |
| ADR-004 | JVM جديدة لكل session في MVP، وAdapter بوابة الأفعال الوحيدة | تزامن=1 |
| ADR-005 | تحديثات staging مع موافقة على إصدار معين | لا git pull في runtime |
| ADR-006 | البيع/النقل وAST mutation والاكتشاف التنفيذي التلقائي خارج MVP | قدرات معطلة |
| ADR-007 | OS ونسخ Python/JDK/client/plugins مقفلة | محاكاة offline فقط |

SQLite هنا لبرنامج محلي، وليس خدمة Cloudflare. لا تغير قرار JSON دون موافقة؛ إن بقي JSON طبّق البروتوكول البديل واختبره. عدم الرد على ADR ليس موافقة ضمنية.

## 2. خريطة التعديل على الملفات

استخدم أسماء D0–D6 المعرفة في التقرير، وكلها داخل OSRS_Bot/وثائق_المحركات.

| الوثيقة | يستبدل/يحذف | يضاف | النتائج |
|---|---|---|---|
| D0 | ضمانات 100% وصفر ثوابت وAST المتعارض واعتماد غير المختبر | النص A، ADRs، ownership، نطاق MVP | F04–F09/F15/F21/F25–F28 |
| D1 | JSON الحالي ومثال البنك وتعميم depositAll واستنتاج الموت | النص B، عقود الأوامر واللقطات وpostconditions | F07/F10/F12/F13/F16/F17/F20/F23 |
| D2 | plaintext credentials و7 QP وREADY بعد زر/crash وضمان replace | النص C، transitions، journal/dedup/recovery | F02/F08–F11/F21/F23 |
| D3 | كلمات المرور وchat-only auth وdebounce كضمان وسقف500MB | النص D، approvals/outbox/quotas | F01–F03/F16/F24/F29 |
| D4 | path injection وAST كأهلية مؤكدة وgit pull وheap=RSS | النص E، client identity/leases/update/resource gates | F04/F06/F14–F19/F26/F27 |
| D5 | التنفيذ مرتين وتأكيد التحذيرات والثقة كصلاحية | النص F، proposal schema/budget/capability | F04/F05/F22 |
| D6 | 7 QP وضمان20 دقيقة والأرباح وSafe غير المثبتة | النص G، capability rules وcatalog status | F11/F13/F14/F24/F25 |

## 3. النص A — المرجع الحاكم، يوضع في D0

> **حالة المواصفات:** هذه الوثائق تحدد سلوكًا مطلوبًا ولا تثبت أنه منفذ. ترتيب السلطة: ADR معتمد أحدث يذكر ما يلغيه، ثم العقود وschemas المعتمدة، ثم وثائق المحركات المتوافقة. الشات والـREADME التاريخي غير معياريين. أي تعارض بين مصدرين فعالين يوقف الميزة حتى يحسم، ولا يعالجه المنفذ بالتخمين.
>
> **نطاق MVP:** مشرف محلي واحد، مخزن حالة ذو كاتب منطقي واحد، حساب افتراضي واحد، عميل واحد، ومهمة معروفة. الاختبار يبدأ بـFake Adapter. لا بيع أو نقل موارد، لا mutation، لا تحميل إضافات غير معتمدة، ولا تصحيح AI مباشر. التوسع مرحلة مستقلة بأدلة عزل.
>
> **سلطة الأوامر:** Adapter بوابة الأفعال الوحيدة. لا يملك AI أوTelegram أوScheduler اتصالًا مباشرًا بواجهات الأفعال. تُفحص الهوية وlease والجيل والسياسة قبل كل إرسال. إذا استطاع العامل القديم تجاوز البوابة فالعزل غير محقق.
>
> **UNKNOWN:** فشل القراءة أو timeout أو فقد الرد لا يعني صفرًا أو فشلًا مؤكدًا أو نجاحًا. لا retry لفعل غير idempotent حتى مصالحة أثره أو تدخل بشري. ACK/HTTP200 لا يعني تحقق النتيجة داخل اللعبة.
>
> **أولوية الأمان:** صحة الهوية والحالة تسبق استمرار الإنتاج. تُمنع الأفعال التي تعتمد على معلومة مجهولة، مع السماح باستمرار المكونات المستقلة مثل السجلات المحلية.
>
> **التوقيت والإعدادات:** safety limits حتمية وtunables ضمن حدود. لكل جلسة config snapshot/hash. UTC للتدقيق وmonotonic للمهل داخل العملية. الانتظار قابل للإلغاء؛ التنويع لا يغير سقوف السلامة.
>
> **حدود الادعاء:** لا ضمان anti-ban أو استحالة تعطل. compile لا يثبت السلوك، وXmx لا يحد RSS، وatomic rename لا يثبت durability المطلقة. ما لم يُختبر موسوم UNVERIFIED.

### مصفوفة الملكية

| المكون | يملك | لا يملك |
|---|---|---|
| Accounts Store | معاملات الحالة وleases وjournal والإصدارات | واجهات اللعبة |
| Supervisor | العمليات وclient instances وreadiness/drain | READY دون حراس |
| Scheduler | اختيار task من snapshot وdescriptor | تبديل المهمة أثناء فعل جارٍ |
| Worker | state machine وpre/postconditions | كتابة حالة خارج Store أو تغيير الحساب |
| Adapter | هوية العميل والترجمة والسياسة وإرسال الأفعال | تخمين UNKNOWN أو التخطيط |
| Telegram | تفويض الطلبات وعرض aliases | الأسرار أو DB writes المباشرة |
| AI | diagnosis/proposal | التنفيذ أوshell أوالأسرار |

قد تكون المكونات وحدات داخل عملية واحدة في MVP. الفصل البرمجي ليس بالضرورة عشر خدمات HTTP، وفصل العملية لا يثبت وحده عزل الصلاحيات.

## 4. النص B — Worker/Adapter والبنك، يوضع في D1

> لكل خطوة: تحقق من lease وهوية العميل، اقرأ الحقول اللازمة ضمن freshness، ثم قيّم precondition. خزّن intent بمعرف command_id قبل الإرسال. انتقل DISPATCHING قبل نافذة الشبكة. الرد يثبت حالة النقل فقط؛ VERIFIED يحتاج postcondition من قراءة جديدة للحساب والعميل الصحيحين. crash أوtimeout أثناء الإرسال يؤدي UNKNOWN، ويوقف الأفعال المتعارضة حتى reconciliation.
>
> رقم المنفذ أوPID منفردًا لا يثبت الحساب. Binding يشمل client_instance_id/start time/profile/session_epoch؛ إذا تعذر إثبات الهوية فلا فعل مؤثر. endpoint يعاد تفسيره حسب عقده، لا success عام لكل APIs.
>
> snapshots تحمل source/observed_at/quality لكل حقل. عدة endpoints لا توفر تلقائيًا snapshot ذرية؛ إذا كان الاتساق BEST_EFFORT يُصرح به ويعاد فحص الحقول الحساسة قبل الفعل. local state_version ليس revision لسيرفر اللعبة.

### استبدال مثال البنك بعقد متحقق

1. تحقق أن المهمة تسمح بالبنك وأنه متاح. tutorial/PIN/انقطاع القراءة مسارات منفصلة.
2. افتح البنك وتحقق من open ومن freshness للعناصر؛ غياب قائمة العناصر ليس بنكًا فارغًا.
3. Snapshot قبل الإيداع؛ سياسة إيداع خاصة بالمهمة توضح inventory/equipment. لا depositAll افتراضيًا لكل مهمة.
4. resolve إلى canonical item identity وحالة noted/charged؛ الأسماء للعرض والبحث الأولي. ambiguity⇒رفض.
5. اختر بديلًا مؤهلًا للمهارات والعضوية؛ وجود الأداة لا يثبت صلاحيتها.
6. اسحب كمية موجبة؛ تحقق من نتيجة endpoint ثم delta والكمية النهائية. السحب الجزئي ليس نجاحًا كاملًا.
7. تحقق من وضع السحب والعتاد والمساحة والشنطة النهائية، ثم الإغلاق.
8. SUCCESS يتطلب evidence IDs للـpostconditions؛ UNKNOWN لا يصبح FAILED تلقائيًا.

وسم الأمثلة pseudocode إن لم تكن قابلة للتنفيذ؛ لا تستدعِ Java من Python دون bridge موثق. اختر Java plugin أوPython عبر Agent Server وعرّف حدود كل منهما.

## 5. النص C — الحسابات والحالة والتخزين، يوضع في D2

> الحالة تخزن credential_ref وproxy_ref لا كلمات مرور أوtokens أوURI بأسرار. account_id ثابت غير مشتق من البريد؛ alias عرض فقط.
>
> Store كاتب منطقي واحد عبر API معاملات؛ بقية الوحدات لا تكتب الملف/القاعدة مباشرة. state+lease+dedupe updates ذرية عند الحاجة. لا network calls خلال lock/transaction. schema version وbackup/restore/migration جزء من العقد.
>
> restart يحول الجلسات غير المغلقة بإثبات إلى RECOVERY_REQUIRED لاREADY. يُحسم مصير العمليات القديمة وتُسحب سلطتها وتُراجع الأوامر المجهولة قبل تخصيص جديد. موافقة بشرية تبدأ RECONCILING ولا تثبت نجاح اللعبة.

### جدول انتقالات موحد

| من | الحدث والحارس | إلى |
|---|---|---|
| NEW | استيراد alias وsecret reference صالحين وهوية فريدة | PENDING_DISCOVERY |
| PENDING_DISCOVERY | lease وعميل صالحان | DISCOVERING |
| DISCOVERING | حقائق لازمة KNOWN وقدرات مثبتة | READY |
| DISCOVERING | timeout/حقائق ناقصة | BLOCKED أوRECOVERY_REQUIRED حسب الأثر |
| READY | الراحة انتهت ولاincident مانع وموارد متاحة وlease حصرية | LEASED |
| LEASED | إطلاق هوية client/profile/endpoint صحيحة | STARTING |
| STARTING | readiness ولاUNKNOWN سابق متعارض | RUNNING |
| RUNNING | pause/end/update، وقف عمل جديد وبدء مهلة | DRAINING |
| DRAINING | خروج وانتهاء عمليات مثبتان وتطبيق delta مرة واحدة | RESTING |
| RESTING | انتهاء الراحة وإعادة فحص الشروط | READY |
| حالة تشغيل | crash/lease uncertainty مع تعطيل الأفعال | RECOVERY_REQUIRED |
| RUNNING | عائق معلوم وتسوية آخر command وتحرير منضبط | BLOCKED |
| BLOCKED | approval صالحة غير مكررة | RECONCILING |
| RECOVERY_REQUIRED | القديم مفصول والأوامر المجهولة مفحوصة | RECONCILING |
| RECONCILING | الشروط مثبتة والراحة محترمة | RESTING أوREADY |
| أي حالة | تعطيل مالك/دليل حظر موثق | DISABLED |

reason codes مستقلة: HUMAN_AUTH_REQUIRED / BANK_PIN_REQUIRED / INSUFFICIENT_RESOURCES / PROXY_UNAVAILABLE / CAPABILITY_UNKNOWN / POLICY_DENIED. لا تُبتكر statuses متضاربة في كل ملف.

### التخزين: خياران يحتاج أحدهما اعتمادًا

**المقترح SQLite:** transactions وUNIQUE للـevent_id/command_id وoptimistic version وقيود ownership؛ WAL وFULL synchronous إذا لائما OS/filesystem؛ backup عبر آلية آمنة واختبار restore. لا ادعاء مقاومة كل عطل تخزين.

**بديل JSON:** كاتب واحد؛ قفل read-validate-modify-write كله؛ tmp فريدة بنفسfilesystem؛ flush/fsync للملف والدليل حيث مدعوم؛ replace؛ journal دائم؛ checksum/version ونسخة سابقة متحققة؛ اختبار المنصة. إن لم يتحقق مستوى المتانة المطلوب يظل التشغيل غير المراقب محظورًا.

cooldown لا يصفر بعد crash؛ يحتسب من آخر نشاط مثبت وبسياسة محافظة عند الجهل. event_id مكرر لا يضاف ثانية؛ base_version قديم لا يطغى على حالة جديدة.

## 6. النص D — Telegram والسجلات، يوضع في D3

> Telegram قناة إدارة aliases فقط. يحظر استقبال/عرض الاعتمادات في MVP، بما فيها /2fa والصور والأخطاء. كل message/callback يفحص user/chat/role مع default deny. /resume طلب تحقق لا كتابة READY.
>
> approval سجل دائم: incident_id/account_id/session_epoch/plan_hash/state_version/approver/expires_at/used_at. يُستهلك مرة واحدة في transaction. تغير الخطة/الجلسة أو انتهاء الوقت يبطله. تعطيل زر UI تحسين واجهة وليس dedupe.
>
> outbox محدودة بالعدد والحجم والعمر؛ telemetry تدمج؛ critical events تحفظ محليًا بquota وreserve. احترام retry_after. إذا تعذر حفظ intent فلا يرسل الفعل. incidents المفتوحة تحتفظ بملخص محمي ولا تحذف عمياء.

رسالة 2FA البديلة:

```text
الحساب: acc_demo
الحالة: BLOCKED
السبب: HUMAN_AUTH_REQUIRED
الحادث: incident_demo
المطلوب: إتمام التحقق عبر الواجهة المحلية الرسمية.
زر: تم الإجراء — ابدأ إعادة التحقق
الزر لا يثبت النجاح ولا يتجاوز شروط الاستئناف.
```

أوامر منفصلة:
- pause: لا عمل جديد، إنهاء تعاوني محدود.
- stop_shift: وقف الجدولة وتصريف الجلسات بمهلة.
- abort: سحب سلطة الأوامر فورًا وتصعيد محلي؛ حالة اللعبة قد تستمر على السيرفر.
- resume: RECONCILING فقط مع احترام الراحة والأمان.

## 7. النص E — Supervisor/Registry/Updates، يوضع في D4

> لكل session client instance مستقلة بهوية وprocess start time وhome/profile وendpoint وtoken reference. lease من Store وepoch متزايد. expiry لا يثبت توقف العميل القديم؛ لا تعاد الملكية قبل fencing فعلي ومراجعة in-flight commands. MVP لا يعيد استخدام JVM للحساب التالي.
>
> Registry allowlist لا تشغيل تلقائي لملف يُلقى في المجلد. لكل artifact: runtime kind، client/plugin version، hash، class/source directory، dependencies/resources، license/source، capabilities، pre/postconditions وhealth. الناقص QUARANTINED. AST ينتج اقتراح metadata لا أهلية مؤكدة.
>
> Updater يستعلم remote ولا يعدل runtime. يبني ويختبر staging ثم lock manifest وموافقة إصدار محدد؛ drain محدود وcanary وتفعيل بين الجلسات. rollback يشمل توافق البيانات والبروتوكول. لا تشغيل latest لمجرد أنه أحدث.
>
> Watchdog يفصل liveness/readiness/tick/task progress. لا freeze من الموقع وحده؛ restart budget/circuit breaker. قياس RSS/native/GC/CPU لاheap فقط؛ حدود التزامن من قياس على جهاز الهدف.

حالات الإصدار: DETECTED → DOWNLOADED → BUILT → CONTRACT_TESTED → APPROVED → DRAINING → CANARY → ACTIVE.

عند الفشل تبقى نسخة متوافقة سابقة أو توقف صريح. إذا تغير بروتوكول اللعبة، لا rollback لعميل قديم غير صالح. UPSTREAM_UNAVAILABLE لا يلغي حراس السلامة.

## 8. النص F — AI الاستشاري، يوضع في D5

> نجاح المسار الأساسي لا يعتمد علىAI. يستدعى لحادث جديد بعد dedupe بسجل منقح وصورة محدودة عند الضرورة وموافقة إرسال البيانات. gateway/model/capability/auth/budget مثبتة. لا نقل أسرار أوأسماء حساسة.
>
> النموذج يعيد proposal ضمن schema؛ لا يملك endpoint/token الأفعال. الصورة والسجل غير موثوقين. confidence للتفسير لاالتفويض. فشل الشبكة/النموذج يبقي الحادث BLOCKED ولا يولد cost loop.
>
> policy validator ثم موافقة عند الحاجة ثم Worker بسياق حديث وجلسة صحيحة. تغير السياق يلغي الخطة. popup مجهول أوفعل مالي/إسقاط/تحقق أمني لا ينفذ تلقائيًا.

مثال JSON صحيح نحويًا، مقترح لا response من خدمة شُغلت هنا:

```json
{
  "schema_version": "1.0",
  "incident_id": "incident_demo",
  "account_id": "acc_demo",
  "session_epoch": 1,
  "observed_state_version": 7,
  "diagnosis": "Requires operator review",
  "suggested_action": "NO_ACTION",
  "parameters": {},
  "requires_human_approval": true,
  "confidence": 0.5
}
```

MVP يسمح بـNO_ACTION/REQUEST_REOBSERVATION/REQUEST_HUMAN_REVIEW فقط. أفعال إضافية تتطلب ADR/schema واختبارات، لا string حرًا.

## 9. النص G — الأهلية والاقتصاد، يوضع في D6

> الأهلية capabilities لاstage فقط: tutorial/skills/membership/trade/asset/client health. UNKNOWN في شرط إلزامي يمنع المهمة. تحفظ قواعد التداول بمصدر وتاريخ وحدود مختبرة؛ لا7QP ولا تجاهل لوقت اللعب ولا افتراض أن وقت البرنامج يساويه. تختلف القيود بحسب الحساب والعنصر.
>
> الكتالوج يصنف AVAILABLE_SOURCE/BUILT/CONTRACT_TESTED/LIVE_VERIFIED بالدليل. المصدر المفتوح لا يثبت التشغيل أو الأمان. تقديرات المدة وGP/hr افتراضات حتى تسند بقياس وعينة وتكاليف.
>
> الإيداع ليس ربحًا، وقيمة الحساب غير المحققة ليست إيرادًا. فصل quantity/cashflow/realized profit/valuation؛ أسعار ورسوم مؤرخة وdedupe. زيادة الرصيد وحدها لا تثبت إنتاجًا أو اكتمال مهمة.

## 10. العقود المطلوبة قبل التنفيذ

هذه قائمة حقول وحراس معيارية مقترحة، وليست ملفات JSON Schema منفذة أو مختبرة. تنفيذ schemas وfixtures جزء من PR-02 أدناه.

### SessionLaunch

schema_version، run_id، session_id، account_id، session_epoch، lease_id، lease_expires_at، client_instance_id، task_id، task_descriptor_hash، artifact_hash، config_hash، credential_ref، adapter_endpoint، adapter_token_ref، max_session_seconds، drain_timeout_seconds.

الشروط: IDs opaque، epoch موجب، hashes تحقق محتوى محمل فعليًا، endpoint من سجل موارد معتمد وليس chat input، secrets references فقط. في MVP loopback؛ UDS يحتاج اختبار capabilities للمنصة.

### Observation

observation_id، account_id، client_instance_id، session_epoch، observed_at، state_version، consistency، fields.

كل field: quality/source/value؛ UNKNOWN⇒null لا0. BEST_EFFORT ليست لقطة ذرية للعبة. الاسم الداخلي plane؛ تحويل aliases مسؤولية Adapter.

### CommandIntent

command_id، account_id، session_id، client_instance_id، session_epoch، lease_id، expected_state_version، operation، params، precondition_ref، postcondition_ref، issued_at، expires_at، policy_version.

كل operation لها parameters schema وidempotency class وpostcondition واختبارات فشل. لا parameter map مفتوحة لأفعال حساسة.

الحراس الدلالية، لا يكفي JSON Schema لها:
1. lease فعلية ومصدر مخول وbindings متطابقة.
2. issued_at<expires_at والوقت داخل policy.
3. state حديث أوأعيد تقييم preconditions.
4. لاUNKNOWN متعارض ولاstop يمنع الفعل.
5. duplicate command ID يعيد النتيجة السابقة؛ محتوى مختلف بنفسID يرفض.
6. hashes تطابق artifacts/configs المعتمدة.
7. مسارات canonical محصورة؛ لاtraversal/symlink خارجي/shell interpolation.
8. VERIFIED يحتاج evidence قابلة للملاحظة، لا مجرد قبول الطلب.

### CommandResult/Event

event_id، command_id، session_id، session_epoch، state_version، transport_status، effect_status، reason_code، evidence_refs، occurred_at.

- transport_status: NOT_SENT / ACKNOWLEDGED / REJECTED / TIMEOUT / CONNECTION_LOST.
- effect_status: NOT_ATTEMPTED / UNKNOWN / VERIFIED / FAILED_VERIFIED.
- REJECTED يثبت NOT_ATTEMPTED فقط إذا عقد المصدر يثبت الرفض قبل الأثر؛ وإلاUNKNOWN.
- FAILED_VERIFIED ليس اسمًا بديلًا للـtimeout.
- event dedupe وversion checks قبل تطبيق delta.

## 11. مصفوفة اختبارات القبول

**T01–T29 هنا اختبارات مقترحة للنظام المستهدف، لا نتائج نجاح من تشغيل حي.** الفحوص المنفذة فعليًا مبينة بالتقرير منفصلة.

| ID | Given / When | Then المطلوب |
|---|---|---|
| T01 | سر منشور وتدوير | دليل إلغاء وعدم إعادة نشر؛ سجل منقح |
| T02 | canary password في inputs/errors | صفر تسرب بالمخرجات والصور |
| T03 | مستخدم خطأ وcallback مكرر/منتهي | رفض بلا أثر؛ موافقة مرة واحدة |
| T04 | حلA متأخر بعد تشغيلB، مشرفان متنافسان | stale رفض؛ lease واحدة |
| T05 | prompt injection/JSON زائد/ثقة1 | policy deny بلا فعل حر |
| T06 | منفذ/توكن/PID/profile غير صحيح | readiness fail، لا لمس عميل آخر |
| T07 | crash عند كل boundary للإرسال والحفظ | UNKNOWN/reconcile، لا تكرار أثر |
| T08 | kill/disk-full/تلف/كتابتان/restore | حالة صحيحة أوquarantine بلاlost update |
| T09 | delta قديم/مكرر وزر2FA كاذب | dedupe، لاREADY تلقائية |
| T10 | بنك مغلق وقراءة خاطئة/ناقصة | UNKNOWN، لا أصفار أوstage مختلقة |
| T11 | QP/level/time حول الحدود | لاtrade غير مستحقة |
| T12 | withdraw false/partial/noted/PIN | لاSUCCESS كاذبة |
| T13 | عنصر مجهول/محجوز وGE جزئي | no-sell وledger غير مكرر |
| T14 | source بلاdescriptor/traversal/تبعية ناقصة | quarantine قبل التحميل |
| T15 | update/build/canary/migration تفشل | لا تفعيل مختلط؛ rollback متوافق أووقف |
| T16 | abort والبنك/HTTP غير متاح | سحب السلطة بمهلة، لاlogout كاذب |
| T17 | موقع ثابت وticks حية وcrash storm | لاfalse positive، circuit breaker |
| T18 | ramp/soak/native-memory pressure | limits وreserve مبنية على قياس |
| T19 | proxy outage وDNS/IPv6/sticky expiry | fail-closed حسب السياسة وسبب دقيق |
| T20 | missing/extra/wrong-type/version/unit | schemas ترفض الخاطئ وتقبل الصحيح |
| T21 | تغير الموارد بعد التخطيط | replan من المالك، لااختيار سري |
| T22 | AI بلاvision/401/429/timeout/budget | لاcost loop أوأفعال أووقفWorker |
| T23 | موت/respawn/PIN/2FA مختلف | blocked/reconcile بلاحركة تخمينية |
| T24 | إيداع وبيع مخزون سابق وreplay | accounting صحيح غير مضاعف |
| T25 | ادعاءات السلامة والأداء | لا ضمان مطلق غير مسند |
| T26 | config خاطئ/reload/clock jump | رفض أوsnapshot منضبطة |
| T27 | fresh checkout وmain=False | build قابل للتكرار وnonzero للفشل |
| T28 | عقدان متعارضان/claim بلاevidence | release gate يتوقف |
| T29 | Telegram outage/flood/صور/disk-full | موارد محدودة وstate/stop لهما أولوية |

### طريقة اختبارات الانقطاع

ابدأ بـFake Adapter لا حسابات فعلية. ضع fault points قبل/بعد intent commit، dispatch، response، verification، event commit، lease release. احتفظ بـStore بين إعادة تشغيل المكونات. oracle هو عدد الأفعال فيfake وملكية العميل وحالةjournal، لا وجود كلمةSUCCESS فيlog.

سجل لكل تجربة: test ID، commit، fixture version، config hash، platform، seed إن استُخدم، expected/observed، وروابط artifacts منقحة. PASS بلاسجل لا يغلق finding.

## 12. خطة PRs قابلة للبدء

| PR | المخرجات | بوابة الانتقال |
|---|---|---|
| PR-00 Security | تدوير السر، منعpassword بـTelegram، scan وACL/ignore | T01/T02 |
| PR-01 Baseline | اعتمادADRs واستبدال النصوص المتعارضة D0–D6 | T25/T28 |
| PR-02 Contracts | JSON Schemas وexamples وenums وownership/transitions | T20/T26 |
| PR-03 State Core | Store/leases/journal/dedupe/recovery | T04/T07/T08/T09؛ لاgame I/O |
| PR-04 Fake Runtime | Supervisor/Worker/Fake Adapter/cancellation | T16/T17/T21؛ تزامن1 |
| PR-05 Read-only Adapter | عميل واحد وهوية وتوكن وtyped reads | T06/T10؛ جهازالهدف |
| PR-06 Task Pilot | مهمة واحدة مراقبة وpostconditions وexit codes | T07/T12/T23/T27؛ موافقةمالك |
| PR-07 Telegram | RBAC/aliases/approvals/outbox/quotas | T02/T03/T29 |
| PR-08 AI Advisory | capability/budget/privacy/gateway contract | T05/T22؛ بلاexecution |
| PR-09 Release/Capacity | staging/update/rollback وresource soak | T14/T15/T18/T19/T27 |
| PR-10 Optional Economy | trade rules/reservations/GE/accounting | T11/T13/T24؛ اعتمادمستقل |

لا موعد مضمون قبل معرفة OS/الجهاز/نسخ العميل والتبعيات. PR-00 إلىPR-04 يمكن تنفيذها بعيدًا عن اللعبة.

### Definition of Done

- استبدال النص المنقوض لا إضافة تناقض جديد.
- ربط requirement بالـfinding والاختبار والمالك.
- أمثلة صحيحة وخاطئة ونتائج اختبارات فعلية.
- لا أسرار أوحسابات حقيقية فيfixtures/artifacts.
- migration/rollback إذا تغير التخزين أوالعقد.
- ACCEPTED ثمIMPLEMENTED ثمVERIFIED فقط بالدليل.
- مراجعة أثر التغيير على المحركات الأخرى قبلmerge.

## 13. أمر جاهز لإرساله للمنفذ

> نفذ تصحيح الوثائق أولًا فقط. قارن main الحالي بـcommit d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b قبل التطبيق. اقرأ تقرير التدقيق وهذه الحزمة. لا تشغل حسابات ولا تستخدم أسرارًا ولا تنفذ git pull داخل runtime. طبق PR-00 ثمPR-01 ثمPR-02، واطلب اعتماد التخزين وOS صراحة. استبدل المقاطع المتعارضة وثبتschemas/examples واختباراتها. اعرضdiff ونتائج فعلية. لا تعتبر اختبارات القبول ناجحة حتىتنفذ. لا تبدأPR-03 قبل اعتماد القرارات، ولا تفترض توافقupstream أحدث دون إعادةاختبار العقود المتأثرة.

**النتيجة المتوقعة بعد التطبيق:** نواة واضحة قابلة للاختبار والبدء التدريجي، لا ضمان تشغيل كامل أوأرباح أوعدم حظر أوتوافق كل إضافاتHub.
