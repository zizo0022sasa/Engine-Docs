# تقرير التدقيق الهندسي العدائي لوثائق OSRS_Bot

**التاريخ:** 2026-10-23 — **الإصدار:** 1.0  
**الحكم:** **NO-GO للتشغيل الآلي غير المراقب بالوثائق الحالية. GO مشروط لتصحيح العقود وبناء نموذج محاكاة أحادي الحساب.**

هذا تدقيق معماري ووثائقي مع فحص انتقائي للمصدر واختبارات محلية، وليس شهادة اختبار اختراق أو ضمانًا لسلامة الحسابات. لم أعدل مستودعاتك أو أشغل حسابات اللعبة. الملف المرافق `OSRS_DOCUMENT_PATCHES_AR.md` يقدم نصوصًا بديلة وخطة تنفيذ واختبارات قبول.

## 1. الملخص التنفيذي

المشكلة ليست نقص السكريبتات. المشكلة هي أن الوثائق لا تتفق على من يملك الجلسة، ومن يرسل الأوامر، ومتى يتحقق النجاح، وكيف تستعاد الحالة بعد الفشل. بعض الأقسام تقدم مبادئ جيدة ثم تنقضها أقسام أخرى، وكلها موسومة بأنها «معتمدة للتنفيذ».

**29 نتيجة: 6 من P0، و20 من P1، و3 من P2.** هذه درجات أولوية تشغيلية وليست CVSS:

- **P0:** يحسم قبل إدخال أسرار حقيقية أو السماح بأوامر مؤثرة في اللعبة.
- **P1:** يحسم قبل تشغيل متكامل غير مراقب.
- **P2:** يحسم قبل التوسع أو اتخاذ قرار مالي بناءً على التقارير.

### اختبارات نُفذت فعليًا في هذه المراجعة

| الفحص | النتيجة | حدود الاستنتاج |
|---|---|---|
| أمثلة JSON في وثائق المحركات السبع | 6 من 8 كتل موسومة json تفشل في json.loads | بعضها JSONC أو جزء كائن؛ ليست رسائل نقل جاهزة |
| مثال execute_bank_turnaround مع withdrawX=False | يرجع SUCCESS | عيب مثبت في المثال لا تعميم على كل كود المشروع |
| إيداع العتاد في المثال | صفر استدعاءات depositEquipment | الكود لا يحقق الوعد النصي |
| وصفة candidate_names دون name | KeyError: 'name' | القيمة الافتراضية لـdict.get تُقيّم حتى مع وجود المفتاح |
| .gitignore الحالي | لا يستبعد accounts_database.json أو accounts.txt أو Root/accounts_database.json | لا يثبت أن تلك الملفات سُربت؛ ينقض ضمان حمايتها |
| سقف اللوجز حسابيًا | 15 حسابًا × 4 ملفات × 10MB = 600MB | يتجاوز 500MB قبل الصور واللوجز العامة |
| اختبارات test_safety.py | 44 ناجحًا، صفر فشل/خطأ، الشبكة محظورة | اختبارات محاكاة، ليست اعتمادًا للنظام المقترح أو تشغيل اللعبة |
| فحص الـGist | نمط توكن Telegram في موضعين | لم أستخدمه أو أختبر صلاحيته |

أُعيدت الفحوص بعد انقطاع جلسة العمل وأنتجت النتائج نفسها. فقدت الملفات المؤقتة بعد توقف لاحق؛ النتائج أعلاه مثبتة في مخرجات الأدوات بسجل المحادثة، لكن سجلاتها الخام ليست مرفقة بهذه النسخة. لا يُدّعى وجود artifacts لم تُسلّم.

## 2. النطاق والمصادر

### النسخ المثبتة

| الرمز | المصدر | Commit / revision |
|---|---|---|
| O | zizo0022sasa/OSRS_Bot | d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b |
| U | chsami/Microbot | a2114401bbbc35e9762db9e8aa666d70ffcd24d8 |
| H | chsami/Microbot-Hub | ee6e066e04b29c1e12e554b3f73439217bdc0eed |
| G | Gist المرسل | 497001676deee744940271033908b1dcb19d851e |
| C | belalalibb/chat_engen، التبعية المذكورة بالوثائق | cb4ff32e5812931aeb8c7423a88255110c53acf5 |

قُرئت وثائق المحركات السبع كاملة، وقورنت بنهاية الشات وبمصادر upstream وكود منتقى. ليس هذا تدقيقًا سطرًا بسطر لكل المستودعات.

### اختصارات الوثائق

جميع D0–D6 داخل `OSRS_Bot/وثائق_المحركات/` في O:

- D0: `00_المرجع_المعماري_الموحد.md`
- D1: `01_محرك_عامل_المهمة_Task_Worker_Adapter.md`
- D2: `02_محرك_إدارة_الحسابات_Accounts_Manager.md`
- D3: `03_محرك_حارس_التليجرام_والسجلات_Telegram_Logs_Guardian.md`
- D4: `04_محرك_المايسترو_ومشرف_التشغيل_Master_Orchestrator.md`
- D5: `05_محرك_الذكاء_الاصطناعي_AI_Triage_Solver.md`
- D6: `مصفوفة_السكربتات_المفتوحة_وخريطة_المراحل.md`

الأسطر تشير إلى النسخ المثبتة، لا إلى main المتغير. «فجوة عقد» تعني أن الوثائق لم تحدد الآلية اللازمة، وليس نفي وجود نسخة خاصة على جهازك.

**خارج النطاق:** استخدام الأسرار، استغلال خدمات عامة، تشغيل عميل أو حساب، بناء Java/Gradle، اختبار بروكسي أو مزود AI حي، انقطاع كهرباء فعلي، والتحقق من قرص Windows المذكور بالشات. نتائج الوحدة لا تثبت postconditions اللعبة ولا صحة جميع إضافات Hub.

## 3. نتائج P0 — حواجز البدء

### F01 — توكن منشور دون إثبات إغلاق حادث التسريب

**الدليل:** G:822 و1071 يحتويان نمط توكن Telegram. D0:10–13 يمنع ذلك. لم أكرر القيمة أو أختبر صلاحيتها.

**سيناريو الفشل:** حذف السطر مع بقاء التوكن فعالًا؛ التاريخ والنسخ السابقة قد تحتفظ به. امتلاك التوكن يتيح إساءة استخدام واجهات البوت ضمن صلاحياته، لكنه وحده لا يثبت تجاوز تفويض تطبيقك.

**الإصلاح:** إلغاء/تدوير أولًا؛ مراجعة webhook/polling والنشاط المتاح؛ تنظيف المصدر والتاريخ حيث أمكن؛ سجل حادث بلا السر؛ secret scan للكود والتاريخ وCI. إذا أُلغي سابقًا، وثق ذلك.

**T01:** دليل إلغاء من المالك وعدم إعادة نشر السر. فحص الأنماط ليس ضمانًا شاملًا لغياب الأسرار.

### F02 — التصميم نفسه يرسل كلمات المرور إلى Telegram

**الدليل:** D3:69–87 يستقبل كلمات المرور، و187–219 يعرضها في رسائل 2FA؛ D2:58–75 يخزنها في JSON. تعارض مباشر مع D0:10–13. اختبار ignore أثبت غياب استبعاد أسماء قاعدة الحسابات المقترحة.

**الفشل:** تسرب عبر إشعار أو استثناء أو نسخة احتياطية أو commit. ignore ليس تشفيرًا ولا يحمي ملفًا سبق تتبعه.

**الإصلاح:** aliases فقط في Telegram؛ credential_ref بدل password في الحالة؛ مخزن أسرار محلي بصلاحيات OS مقيدة؛ تعطيل رفع الاعتمادات عبر Telegram في MVP؛ عدم توريث جميع أسرار المشرف للعمليات الفرعية.

**T02:** canary password تجريبية لا تظهر في stdout/logs/Telegram/AI/screenshots/artifacts؛ فحص ACL وgit ls-files بجانب ignore.

### F03 — تفويض Telegram ومنع التكرار ناقصان

**الدليل:** D3:34–46 يفحص effective_chat.id فقط؛ 179–182 يعتمد تعطيل الأزرار. لا ربط دائم بين الموافقة والخطة والجلسة أو انتهاء صلاحية.

**الفشل:** عضو غير مخول داخل مجموعة مسموحة، callback مكرر، أو زر قديم يطبق خطة على حالة جديدة.

**الإصلاح:** فحص user ID وchat ID والدور لكل رسالة وcallback؛ approval يرتبط بـincident/account/session_epoch/plan_hash/expires_at ويستهلك ذريًا مرة واحدة. /resume لا يتجاوز الحراس؛ /filter لغة محدودة لا eval؛ حد لحجم الرفع.

**T03:** رفض المستخدم غير المخول وreplay والزر المنتهي والخطة المتغيرة قبل أي أثر.

### F04 — حل AI متأخر قد يُرسل لحساب آخر

**الدليل:** D2:214–225 وD5:15–21 يستبدلان الحساب المتعثر فورًا؛ D5:75–93 ينفذ التصحيح في نافذته؛ D4:94–105 يعيد استعمال السلوت. D1:25–40 يغفل client_instance_id وlease epoch.

**الفشل:** A يتعثر ثم يحل B مكانه، ثم تصل نقرة حل A إلى B. إذا أغلقت نافذة A فلا هدف حي للتصحيح أصلًا.

**الإصلاح:** slot ليس هوية عميل. lease حصرية وجيل متزايد، والتحقق من الحساب والجلسة والعميل والجيل قبل كل أمر. MVP لا يعيد استخدام JVM بين حسابين؛ AI استشاري ويعاد فحص سياقه في جلسة صحيحة. انتهاء lease لا يكفي إن استطاع العامل القديم تجاوز بوابة الأوامر.

**T04:** تأخير الحل حتى تبديل الجلسة ⇒ STALE_CONTEXT دون أثر؛ مشرفان متنافسان ⇒ مالك واحد.

### F05 — صلاحية AI تتجاوز المرجع وتؤكد تحذيرات خطرة

**الدليل:** D0:25 يمنع سلطة AI المباشرة؛ D2:223–225 يشترط موافقة؛ D5:75–87 ينفذ تلقائيًا مرتين؛ D5:44 يؤكد تحذيرات دخول منطقة أو إسقاط غرض؛ 57–70 يعتمد JSON وثقة ذاتية.

**الفشل:** prompt injection من سجل/صورة، أو تفسير خاطئ لتحذير؛ confidence=0.98 لا يثبت الأمان.

**الإصلاح:** proposal لا تنفيذ؛ schemas لكل نوع، قائمة أفعال مغلقة، pre/postconditions؛ منع shell والملفات وتغيير الشبكة والأسرار والبيع/الإسقاط وتجاوز التحذيرات المجهولة. موافقة الإنسان لا تلغي سياسة السلامة أو freshness.

**T05:** حقول زائدة، خيار خارج الحدود، تعليمات خبيثة وخطة قديمة تُرفض ولو confidence=1.

### F06 — لا عقد منفذ/توكن/هوية لتعدد العملاء

**الدليل:** U docs/AGENT_SERVER.md:38–74 يحدد loopback و8081 افتراضيًا ويتخطى إنشاء الخادم إن انشغل المنفذ ويستخدم X-Agent-Token. D1:25–40 يغفل endpoint/token reference. O run_stronghold_bot.py:18–21 يثبت BASE_URL على 8081.

**الفشل:** العامل الثاني يصل إلى خادم الأول أو عميله لا ينشئ خادمًا. user.home منفصل لا يحل تعارض TCP.

**الإصلاح:** endpoint فريد وtoken reference وPID+start time وprofile/home/account binding؛ readiness بالهوية لا PID فقط؛ عدم نشر Agent Server للإنترنت؛ Adapter allowlist للـendpoints، لأن التوكن العام ليس تفويضًا دقيقًا لكل فعل.

**T06:** منفذ مشغول أو توكن/PID/profile خاطئ يمنع التشغيل دون لمس حساب آخر.

## 4. نتائج الاعتمادية والعقود

### F07 — P1: الاستئناف دون journal للأوامر

**الدليل:** D1:71–83 يذكر SENT/CONFIRMED؛ D4:201–219 يعد باستكمال آخر نقطة دون تعريف حفظ النية وstate version وdedupe.

**الفشل:** الأثر يحدث والرد يضيع ثم crash؛ إعادة الأمر تكرر أثرًا. Varbits لا تغطي كل سحب/بيع.

**الإصلاح:** PREPARED → DISPATCHING → ACKNOWLEDGED → VERIFIED، أو UNKNOWN/REJECTED؛ حفظ intent قبل الإرسال؛ command ID وpre/postconditions. ACK ليس أثرًا. لا وعد exactly-once عبر اللعبة؛ UNKNOWN يفرض reconciliation.

**T07:** انقطاع قبل/بعد الإرسال وبعد الأثر وقبل checkpoint؛ لا replay أعمى أو نجاح مفترض.

### F08 — P1: atomic replace ليس ضمان durability

**الدليل:** D2:260–266 وD4:207–208 يعطيان os.replace+filelock ضمانًا مطلقًا، دون read-modify-write boundaries أو fsync أو recovery.

**الفشل:** lost update من عمليتين، tmp مشتركة، قطع كهرباء قبل مزامنة القرص، أو disk full.

**الإصلاح:** أوصي بـADR لاعتماد SQLite محلي بمعاملات وقيود وWAL/FULL synchronous بحسب اختبار البيئة. ليس تغييرًا صامتًا لقرار JSON. بديل JSON: كاتب واحد، قفل دورة التعديل كلها، tmp فريدة بنفس filesystem، flush/fsync ودليل حيث مدعوم، replace وjournal ونسخة سابقة متحققة. لا lock خلال HTTP/AI ولا ضمان ضد كل عطل قرص.

**T08:** kill في كل نقطة حفظ، كتابتان، فساد ومساحة ممتلئة وrestore؛ نسخة صحيحة أو quarantine لا READY مجهولة.

### F09 — P1: READY تستخدم كحل عام للأعطال

**الدليل:** D2:78 لا يسرد حالات مستخدمة في 50؛ D4:215 و228–232 يعيد READY بعد crash؛ D3:225 بعد زر 2FA.

**الفشل:** عملية قديمة ما زالت حية، تجاوز الراحة، delta متأخر يطغى على جلسة أحدث أو يكرر GP.

**الإصلاح:** state machine واحدة؛ RECOVERY_REQUIRED بعد crash وRECONCILING بعد موافقة؛ event ID وbase version وتطبيق مرة واحدة؛ فصل session_completed عن stage_completed.

**T09:** تقرير مكرر/قديم، restart، زر 2FA دون إتمامه؛ لا READY غير مستحقة ولا احتساب مضاعف.

### F10 — P1: Discovery يخلط Varbit وVarp ويخمن البنك

**الدليل:** D2:129 يسمي 281 Varbit؛ U gameval/VarPlayerID.java:130 يعرف TUTORIAL=281. U BankHandler:59–71 يعيد العناصر عند فتح البنك فقط. D2:134 يصنف tutorial من QP/المستوى.

**الفشل:** قراءة namespace خطأ؛ غياب قائمة البنك يصبح بنكًا فارغًا؛ إعادة tutorial لحساب خرج منها. عدة endpoints ليست لقطة ذرية تلقائيًا.

**الإصلاح:** قراءات typed بمصدر/نسخة، KNOWN/UNKNOWN/STALE ووقت وهوية؛ discovery جزئي؛ البنك خطوة مشروطة؛ اختبار postcondition التعليم بدل الاعتماد على رقم/موقع منفرد.

**T10:** بنك مغلق وحساب بالمين لاند بلا مهام وقراءة ناقصة/قديمة ⇒ لا أصفار مصطنعة أو تسكين متسرع.

### F11 — P1: شروط التداول خاطئة

**الدليل:** D6:39 يذكر 7 QP؛ 53 يسقط الوقت؛ D2:135 يستخدم QP<7. مرجع OSRS Wiki/Account المقروء يذكر 10 QP و100 total level ووقت لعب يتجاوز 20 ساعة للحسابات المجانية الجديدة، ويذكر Oak logs ضمن المقيد. D6:65 وD3:227–229 يعدان ببيع الأوك خلال 20 دقيقة.

**الفشل:** Trade-Ready خطأ ثم fallback بيع غير ممكن.

**الإصلاح:** capability حسب الحساب والعضوية والعنصر وقواعد مؤرخة؛ وقت الجهاز لا يساوي وقت اللعب؛ إزالة ضمان 20 دقيقة وعدم تشغيل fallback بقدرة غير مثبتة.

**T11:** 7/9/10 QP، 99/100 level، حدود الوقت وعضوية مجهولة؛ لا أهلية مستنتجة من مرحلة فقط.

### F12 — P1: مثال البنك ينتج SUCCESS كاذبة

**الدليل المُعاد إنتاجه:** D1:102–154 يتجاهل withdrawX=False ولا يفحص الكمية النهائية ولا يستدعي depositEquipment؛ 133 ينهار دون name. Python يستدعي أسماء Java بلا bridge معرف. U Rs2Bank:512–513 يجعل hasItem(name) غير exact افتراضيًا.

**الإصلاح:** typed Adapter أو pseudocode موسوم؛ canonical item identity وحالة noted/charged؛ فحص أهلية البدائل والأثر الفعلي والشنطة/العتاد. لا depositAll عام لكل tutorial/مهمة.

**T12:** فشل/سحب جزئي/اسم ملتبس/أداة غير مؤهلة/noted/PIN/شنطة ممتلئة ⇒ لا SUCCESS كاذبة.

### F13 — P1: بيع الفائض غير معرف بما يكفي

**الدليل:** D1:214–232 يفترض isTool/isQuestItem من metadata؛ لم تظهر هاتان الدالتان في ItemComposition وRs2ItemModel المراجعين، وليس ذلك نفيًا لكل بديل بالمشروع. 234–254 يبيع ويترك العرض معلقًا دون order ledger.

**الفشل:** بيع خام مطلوب أو تحصين كل المخزون أو تكرار عرض/احتساب.

**الإصلاح:** البيع معطل في MVP؛ classifier موثق وUNKNOWN⇒لا بيع؛ reservations للكميات، canonical ID، سعر مؤرخ وحد خسارة وGE ledger للجزئي/الإلغاء/الجمع. وجود المورد بالبنك لا يثبت أنه فائض منتج.

**T13:** عنصر مجهول أو محجوز وعرض جزئي وانقطاع بعد الأثر؛ لا بيع خاطئ أو ازدواج.

### F14 — P1: ملف Java/Python ليس artifact قابلًا للتحميل

**الدليل:** D4:116–179 يفترض @ScriptDescriptor واستخراج min_skills/required_tools؛ U PluginDescriptor:68–168 لا يحتوي تلك الحقول. DynamicScriptDeployHandler:78–113 يحتاج source directory وبناء/تحميل؛ H يعتمد plugin/resources/dependencies. D4:324 يختبر أي Java/Python.

**الفشل:** ضياع dependencies/resources، حقن مسار بدل بناء، أهلية مختلقة من AST.

**الإصلاح:** registry allowlist مراجع يحدد runtime/plugin class/artifact hash/client compatibility والمتطلبات والـpostconditions؛ AST اقتراح لا اعتماد. plugin داخل JVM ليس sandbox. فحص رخص التبعيات قبل التوزيع.

**T14:** descriptor ناقص أو dependency مفقودة أو health broken أو path traversal/symlink خارج الجذر ⇒ QUARANTINED. العدد الذي تحقق بالشجرة هو 172 اسم ملف ينتهي بـPlugin.java، وليس 172 مهمة مختبرة أو إثباتًا لنسخة جهازك المذكورة كـ170.

### F15 — P1: تحديثات تناقض الاعتماد الصريح

**الدليل:** D0:24 يتطلب staging/اختبار/اعتماد؛ D4:181–199 يصف git pull وmutation واستئنافًا تلقائيًا. git rev-parse HEAD محلي وحده لا يكشف remote أحدث. D0:22 يؤجل mutator بينما D4:179/191 يشترطه.

**الإصلاح:** watcher يكتشف فقط؛ artifact immutable وlock للتبعيات؛ staging/build/contracts ثم موافقة لإصدار محدد، drain محدود وcanary وrollback يراعي البيانات والبروتوكول. mutation خارج MVP.

**T15:** build/canary/download/migration تفشل ⇒ لا تشغيل خليط إصدارات؛ رجوع متوافق أو توقف صريح.

### F16 — P1: الطوارئ تنتظر الوصول إلى البنك

**الدليل:** D3:61–65 وD1:257–264 وD4:185–187 تربط الإيقاف بإنهاء الدورة والبنك دون deadline كافٍ.

**الفشل:** مسار مغلق/قتال/thread عالق يجعل stop لا ينتهي. إيقاف العميل لا يوقف سيرفر اللعبة أو يضمن سلامة الشخصية.

**الإصلاح:** فصل pause/drain/abort؛ منع أفعال جديدة فورًا؛ إنهاء تعاوني محدود ثم تصعيد لشجرة العمليات المحلية؛ UNKNOWN/RECOVERY_REQUIRED إذا لم يُثبت الخروج.

**T16:** بنك غير متاح وHTTP عالق وقتال وفشل kill ⇒ سحب السلطة بمهلة، بلا ادعاء logout كاذب.

### F17 — P1: سكون اللاعب ليس تجمدًا

**الدليل:** D1:255–256 يستخدم 60 ثانية؛ D4:225–232 يستخدم 3 دقائق وعدم تغير WorldPoint أو tick.

**الفشل:** قتل نشاط ثابت/AFK مشروع أو restart storm؛ PID حي مع deadlock.

**الإصلاح:** فصل liveness/readiness/tick freshness/task progress؛ deadlines لكل حالة، monotonic clock، restart budget وcircuit breaker. لا حكم من الإحداثيات وحدها.

**T17:** موقع ثابت وticks حية، AFK، HTTP عالق، crashes متكررة ⇒ لا false positive ولا عاصفة إعادة تشغيل.

### F18 — P1: Xmx512m ليس سقف RAM العملية

**الدليل:** D0:73 وD4:237–241 يضمنان 512MB/نافذة و15–20 نافذة على 16GB.

**الفشل:** native/metaspace/stacks/graphics خارج heap؛ GC/OOM؛ انتظار نهاية الجلسة عند ضغط الموارد يأتي متأخرًا.

**الإصلاح:** قياس RSS/commit/native/GC/CPU/FDs؛ حدود تحذير/admission/drain/terminate مع reserve للنظام؛ تزامن مشتق من load test. Xmx ليس علاجًا لتسريب الذاكرة.

**T18:** ramp وsoak وضغط RSS على جهاز الهدف؛ لا تعلن النتائج قبل قياسها. لم يُنفذ soak حي هنا.

### F19 — P1: فحص proxy قبل الإطلاق لا يضمن fail-closed أثناءه

**الدليل:** D4:56–59 يعطي ضمان Zero Real-IP Leakage بعد probe قبل الإطلاق؛ D2:47 يدعي دعم كل المزودين. D0:70/73 يشترط التطابق الجغرافي بينما D4:244 يوجه مصر لأوروبا.

**الفشل:** DNS/IPv6 أو اتصال جديد خارج السياسة؛ sticky session تتغير؛ API مزود يفشل؛ لا عالم بنفس الدولة؛ network failure يُصنف quota exhaustion.

**الإصلاح:** capabilities لكل مزود وبروتوكول واختبار توجيه على OS الهدف؛ منع fallback مخالف للسياسة؛ لا تبديل أثناء أمر مجهول؛ اختيار عالم من قائمة مسموحة بقيود وقدرات وlatency مقاسة. لا ضمان ضد كشف اللعبة.

**T19:** سقوط proxy أثناء الجلسة وDNS/IPv6 وانتهاء sticky وغياب عالم مناسب ⇒ توقف صحيح السبب لا bypass.

### F20 — P1: الرسائل ليست عقودًا متوافقة

**الدليل:** 6/8 JSON غير صالح؛ D1:25–65 وD2:148–159 يختلفان entry_file_path/script_entry_path؛ z مقابل plane؛ SUPPLIES_MISSING خارج final_status.

**الفشل:** defaults صامتة أو parser crash أو خطأ طابق/وحدات. ليس كل endpoint يعيد success؛ BankHandler يعيد opened/closed لبعض العمليات.

**الإصلاح:** schemas versioned، required/enums/حدود/additionalProperties:false، وحدات بالأسماء، فصل transport/effect/session status، adapter normalization لكل endpoint.

**T20:** missing/extra/wrong-type/unknown-version وunit mismatch ترفض؛ كل الأمثلة تمر parser وschema في CI.

### F21 — P1: أكثر من مالك للتخطيط والحالة

**الدليل:** D2:142–207 وD4:159–177 يختاران المهام؛ D1:183–191 يعيد الاختيار؛ مسار الحوادث مختلف بين D2/D3/D5.

**الفشل:** اختيار العامل يناقض scheduler، كُتّاب متزامنون للحالة والإحصاءات، incidents مكررة.

**الإصلاح:** Store كاتب منطقي واحد؛ Scheduler يخطط من snapshot؛ Worker يعيد التحقق ويرفض أو يطلب replan، لا يغير المهمة خفية؛ Adapter منفذ وحيد؛ event envelope وdedupe.

**T21:** مورد يختفي بعد التخطيط أو قراران متزامنان ⇒ replan من المالك لا تعارض.

### F22 — P1: URL لـAI لا يثبت capability الصورة

**الدليل:** D5:48–70 يحدد localhost:8000/base64 دون model/auth/budget/error contract. C يثبت /v1/chat/completions، وINTEGRATION_GUIDE:440–459 يوضح مصادقة يمكن أن تكون معطلة افتراضيًا.

**الفشل:** مزود نصي لا يقبل الصور، payload خاطئ، timeout/429، retries مكلفة. غياب /v1/images لا يعني غياب تحليل الصورة عبر chat؛ لم أخلط بينهما.

**الإصلاح:** pin gateway/model/OpenAPI؛ اختبار multimodal message وdata URL والحجم؛ auth صريح وbudget/deadline/concurrency/redaction. polling محلي كل 200ms ليس استدعاء LLM كل 200ms.

**T22:** بلا vision أو401/429/5xx/timeout/JSON غير صالح أوbudget منتهية ⇒ BLOCKED معلوم، بلا loop تكاليف أو تعطيل Worker.

### F23 — P1: الموت و2FA وPIN ليست حالة واحدة

**الدليل:** D1:259–261 يفترض Lumbridge وثلاثة أغراض دائمًا؛ D2:245–256 وD3:187–229 يخلطان التحقق وحوارات اللعبة؛ D6:64 يربط 2FA بالأبواب والمكافآت بشكل عام. safety.py:29–35 يتوقف عند انتقال غير متوقع.

**الفشل:** respawn مختلف/غرض مفقود/Bank PIN؛ زر تم يعتبر إثباتًا؛ تحريك قبل فهم الحالة.

**الإصلاح:** reason codes مستقلة؛ لا تجاوز لمتطلبات تحقق رسمية؛ re-probe بعد تدخل المالك؛ لا افتراض أدوات محفوظة أو موضع ثابت؛ postconditions لكل recovery.

**T23:** انتقال قانوني مقابل موت، respawn مختلف وinventory ناقص و2FA لم يكتمل ⇒ لا READY أو حركة تخمينية.

### F24 — P2: حساب الربح يخلط التدفقات والمخزون

**الدليل:** D1:193–202 وD3:127–148 بلا تعريف محاسبي كامل؛ D6:103 يقسم على Ban Risk Weight غير معرف.

**الفشل:** إيداع يحسب دخلًا، تسييل مخزون قديم يحسب إنتاجًا، تكرار event يضاعف GP، قيمة حساب تقديرية تعرض كدخل نقدي.

**الإصلاح:** فصل quantity/cashflow/realized proceeds/input costs/fees/inventory valuation/external transfers؛ أسعار وtax policy مؤرخة؛ dedupe؛ قيمة الحساب غير المحققة ليست revenue.

**T24:** نقل شنطة↔بنك ربحه صفر؛ لا تكرار احتساب البيع/الأحداث، ومصالحة GE الجزئي.

### F25 — P2: ضمانات منع الحظر والتشخيص السببي بلا إثبات

**الدليل:** D4:45–69 يجعل الربط مستحيلًا، 171 يمنع الكشف بالتنويع، و295–313 يستنتج سبب الحظر من IP؛ D0:72 يضمن استحالة التعليق.

**الإصلاح:** حذف الضمانات؛ FACT/HYPOTHESIS/MEASURED/UNKNOWN مع الدليل؛ الارتباط لا يثبت السبب؛ تغير hash ليس إثبات عدم كشف؛ tick ليس ضمان حفظ دائم. البوتات وRWT قد تخالف قواعد اللعبة؛ جودة النظام لا تلغي مخاطر الحظر.

**T25:** كل ادعاء أداء/مخاطر له مصدر ومنهج وحدود؛ لا 100% غير مسندة.

### F26 — P1: صفر ثوابت يناقض المواصفات واختباراتها

**الدليل:** D0:69–72 يمنع الأرقام؛ D1:249–256 وD5:80–93 يضعان حدودًا ثابتة؛ D4:78 يسمح 60–140 ثانية و322 يشترط≥90؛ D2:25/276 يفرضان 4 ساعات و114 يسمح 210 دقائق.

**الفشل:** إعداد مسموح يفشل acceptance؛ reload يغير deadline؛ sleep يحجب الطوارئ؛ خلط ms/s.

**الإصلاح:** safety limits حتمية وtunables ضمن حدود؛ min<=max؛ config snapshot/hash للجلسة؛ waits قابلة للإلغاء وmonotonic؛ لا إعادة قراءة سياسة متغيرة في كل فعل دون version.

**T26:** config invalid وتغيير أثناء الأمر وقفزة الساعة ووحدات خاطئة ⇒ رفض أو تطبيق منضبط.

### F27 — P1: اختبارات الوحدة لا تثبت المحركات وبيئة البناء غير مقفلة

**الدليل:** D1:274–281 وD4:318–325 دون fixtures/artifacts كاملة. شجرة O العامة لا تظهر حزم engines المقترحة في رؤوس الوثائق، وتضم نسخ Java في microbot_stage0_* وnet/…؛ لا حكم على جهازك الخاص. run_stronghold_bot.py:700–704 يستدعي main دون تحويل False المحتملة في689–696 إلى exit code فاشل.

**الإصلاح:** مصدر أصلي واحد للكود، lock للـOS/Python/JDK/client/plugin؛ CI hermetic واختبارات fault injection وexit codes؛ لكل PASS commit/config/command/output.

**T27:** fresh checkout وبناء على OS الهدف؛ main=False ⇒ nonzero؛ لا إعلان Java build أوE2E دون تنفيذه. الـ44 اختبارًا دليل جزئي إيجابي فقط.

### F28 — P2: اعتماد الوثائق بلا إدارة نسخ أو سلطة قرارات

**الدليل:** D0:89–95 يعتمد الجميع رغم التعارض؛ G:5898–5951 يصف تنزيلًا وتشغيلًا محليًا وzero regression دون أدلة قابلة للتحقق هنا.

**الإصلاح:** ADR index؛ normative baseline واحدة؛ الشات تاريخي؛ PROPOSED/ACCEPTED/IMPLEMENTED/VERIFIED/DEPRECATED منفصلة؛ owner/change-impact/tests لكل تغيير.

**T28:** لا عقدان فعالان للمفهوم نفسه؛ كل requirement مرتبط بمالك واختبار؛ غير المختبر موسوم.

### F29 — P1: سقف اللوجز والطوابير غير مضمون

**الدليل:** D3:241–248 يضع 10MB و3 أرشيفات لكل حساب ثم سقفًا عالميًا500MB؛ الحساب المحلي ينقضه. 254–257 async/retry بلا queue bound أوoutbox retention.

**الفشل:** انقطاع Telegram يملأ RAM/disk، الصور تمنع حفظ الحالة، pruning يحذف حادثًا مفتوحًا، retry storm.

**الإصلاح:** quotas إجمالية للصور واللوجز وoutbox، bounded queues/coalescing/drop policy، reserve للحالة وملخص حادث محمي، retry_after وحد عمر/عدد؛ لا إرسال فعل إن تعذر حفظ intent.

**T29:** outage/flood/صور/disk full ⇒ موارد منضبطة؛ state/stop لا يتعطلان بسبب Telegram.

## 5. ترتيب التنفيذ الموصى به

1. إغلاق حادث الأسرار ومنع الاعتمادات عبر Telegram.
2. اعتماد D0 حاكمًا وتصحيح العقود المتعارضة، واختيار التخزين وOS صراحة.
3. Store/leases/journal/recovery وFake Adapter، تزامن=1.
4. read-only Adapter لعميل واحد على جهاز الهدف، ثم مهمة واحدة مراقبة بعد الموافقة.
5. Telegram مضبوط، ثم AI استشاري اختياري، ثم staging/updates وقياس الموارد.
6. التوسع والبيع/التسييل يحتاجان اعتمادًا مستقلًا، ولا يبدأان لأن عدد السكريبتات كبير.

عميل Java والعمليات الطويلة والتخزين المحلي تحتاج جهازًا أوVM مناسبًا؛ Cloudflare Pages ليس بيئة تشغيلها. لوحة متابعة منفصلة ممكنة لكنها ليست المنظومة المحلية ولا جزءًا مما نُفذ هنا.

## 6. المصادر وروابط الأدلة

- [وثائق D0–D6 المثبتة](https://github.com/zizo0022sasa/OSRS_Bot/tree/d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b/OSRS_Bot/وثائق_المحركات)
- [شجرة O المثبتة](https://github.com/zizo0022sasa/OSRS_Bot/tree/d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b)
- [gitignore](https://github.com/zizo0022sasa/OSRS_Bot/blob/d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b/.gitignore#L25-L35)
- [اختبارات safety](https://github.com/zizo0022sasa/OSRS_Bot/blob/d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b/OSRS_Bot/test_safety.py)
- [run_stronghold_bot.py](https://github.com/zizo0022sasa/OSRS_Bot/blob/d98a10ed89fbe52af42f2e70a9fc2e8fffc7c80b/OSRS_Bot/run_stronghold_bot.py#L654-L708)
- [Gist المثبت — قد يحتوي أسرارًا؛ لا تعِد نشر محتواه](https://gist.github.com/wewe00220033/9ee35367b892b836456eb24fd91bfd5d/497001676deee744940271033908b1dcb19d851e)
- [Agent Server](https://github.com/chsami/Microbot/blob/a2114401bbbc35e9762db9e8aa666d70ffcd24d8/docs/AGENT_SERVER.md#L38-L74)
- [PluginDescriptor](https://github.com/chsami/Microbot/blob/a2114401bbbc35e9762db9e8aa666d70ffcd24d8/runelite-client/src/main/java/net/runelite/client/plugins/PluginDescriptor.java#L68-L168)
- [DynamicScriptDeployHandler](https://github.com/chsami/Microbot/blob/a2114401bbbc35e9762db9e8aa666d70ffcd24d8/runelite-client/src/main/java/net/runelite/client/plugins/microbot/agentserver/handler/DynamicScriptDeployHandler.java#L78-L118)
- [BankHandler](https://github.com/chsami/Microbot/blob/a2114401bbbc35e9762db9e8aa666d70ffcd24d8/runelite-client/src/main/java/net/runelite/client/plugins/microbot/agentserver/handler/BankHandler.java)
- [VarPlayerID، TUTORIAL](https://github.com/chsami/Microbot/blob/a2114401bbbc35e9762db9e8aa666d70ffcd24d8/runelite-api/src/main/java/net/runelite/api/gameval/VarPlayerID.java#L130)
- [Rs2Bank](https://github.com/chsami/Microbot/blob/a2114401bbbc35e9762db9e8aa666d70ffcd24d8/runelite-client/src/main/java/net/runelite/client/plugins/microbot/util/bank/Rs2Bank.java)
- [Hub README](https://github.com/chsami/Microbot-Hub/blob/ee6e066e04b29c1e12e554b3f73439217bdc0eed/README.md)
- [chat_engen API](https://github.com/belalalibb/chat_engen/blob/cb4ff32e5812931aeb8c7423a88255110c53acf5/docs/API.md)
- [chat_engen Integration Guide](https://github.com/belalalibb/chat_engen/blob/cb4ff32e5812931aeb8c7423a88255110c53acf5/docs/INTEGRATION_GUIDE.md)
- [OSRS Wiki: Account](https://oldschool.runescape.wiki/w/Account): مرجع مجتمع مقروء بتاريخ التدقيق، لا commit ثابت ولا عقد API؛ أعد تحقق قواعد المجال عند التنفيذ.

## 7. الحكم النهائي

لا أوصي بإعادة كتابة المشروع كله. أوصي بإيقاف توسيع الميزات مؤقتًا وتصحيح طبقة العقود والملكية والتخزين والنتائج المجهولة. بعد ذلك يمكن تنفيذ نواة صغيرة قابلة للاختبار، ثم توصيل مهمة واحدة. كلمة «معتمد» في الوثيقة ليست بديلًا عن دليل اختبار، ونجاح إعادة الصياغة لا يغلق finding تشغيلية حتى يجتاز تنفيذها اختبار القبول.
