# 🎻 وثيقة المواصفات الهندسية: محرك مشرف التشغيل والجدولة
## Engine Specification Document: Slot Supervisor, Scheduler & Isolation Engine (v2.1)
> **حالة الوثيقة:** مواصفة معيارية موحدة (`Normative v2.1` — مشرف السلوت الواحد منفذ في `osrs_ee/runtime/`؛ التوسع المتعدد وبوابة `Staging/Canary` في `PR-09`)  
> **المرجعية الحاكمة:** متوافقة مع `ADR-004`, `ADR-005`, `ADR-006`, `ADR-008` و`docs/ARCHITECTURE_AND_DESIGN.md`.

---

## 🎯 1. الهوية والمسؤوليات الجوهرية

* **الدور الهندسي:** المشرف المسؤول عن حجز مقاعد التشغيل (`Slots`)، تجهيز الساندبوكس المعزول وجسر البروكسي المحلي، إطلاق وإنهاء عمليات الـ JVM، مراقبة النبض والموارد، وإدارة دورة التحديثات المعزولة.
* **نطاق التزامن المرحلي:** يبدأ النظام بسلوت واحد مستقر (`concurrency = 1`) في مراحل `PR-04 .. PR-06`، ولا يُرفع التزامن في `PR-09` إلا بناءً على قياس `RSS` الفعلي على جهاز الهدف.

---

## 🛡️ 2. عزل الجلسات والشبكة والهوية (بدون ادعاءات ضمان مطلق — `ADR-001, ADR-004, ADR-005`)

تهدف ضوابط العزل الآتية إلى **منع الارتباط التقني المباشر بين الجلسات وتقليل البصمات النمطية**، مع الإقرار الهندسي الصريح بعدم وجود حماية مطلقة (100%) ضد أنظمة كشف مطور اللعبة:

1. **عملية JVM جديدة ومستقلة لكل جلسة (`Fresh JVM per Session` — علاج `C-03`):**
   * يُمنع إعادة استخدام نفس عملية الـ JVM عند تدوير السلوت بين حسابين؛ لأن `-Duser.home` (`random.dat`) و`socksProxyHost` و`hwid.store` تُثبت على مستوى عملية الـ JVM عند الإقلاع.
   * عند انتهاء وردية حساب، يُوقف العامل الإضافة (`/scripts/stop`) ويرسل `POST /logout` لكبح `AutoLoginScript` (`AUD-22`)، ثم **يقتل المشرف شجرة عمليات الـ JVM بالكامل** (`POSIX Process Group` أو `Windows Job Object`) قبل إطلاق عملية جديدة للحساب التالي.
2. **التطعيم المسبق للبروفايل والكاش وهوية العتاد (`Pre-Launch Sandbox & Golden Cache` — `AUD-03, AUD-07, AUD-26`):**
   * تفعيل `AgentServerPlugin` مسبقاً في البروفايل (`enabledByDefault = false` في الكود الأصلي) على بورت محلي وتوكن CSPRNG فريدين للجلسة.
   * فرض رايات تعطيل التليمتري السحابي: `--disable-telemetry` و`-Dmicrobot.disableTelemetry=true`.
   * نسخ الكاش الثابت النظيف محلياً (`Golden Cache` بدون أي ملف هوية أو `random.dat`) لتفادي استنزاف مئات الميجابايت من باقة البروكسي السكني.
   * تفعيل `-javaagent:hwid-agent.jar` بملف مستقل للحساب، وتخصيص أبعاد نافذة ثابتة لكل حساب مشتقة من `persona_seed` (ضمن `784x592`..`828x618`) مع كتم الأصوات وتعطيل الإضافات الرسومية الثقيلة (`GPU`, `117HD`).
3. **جسر البروكسي المحلي المعزول (`Per-Slot Local SOCKS5h Sidecar` — `AUD-08, AUD-27`):**
   * يتصل عميل الجافا دائماً بـ `--proxy=socks5://127.0.0.1:<slot_proxy_port>` لتفادي انهيار `ProxyConfiguration.java` بصيغ `HTTP` أو نوافذ `Swing` المعطلة.
   * يطبق الجسر المحلي قاعدة `1 Active Account = 1 Dedicated IP`، وحل `DNS` عن بُعد (`socks5h`)، وسياسة `Fail-Closed` الفورية، ومطابقة النطاق بمصفوفة العوالم الإقليمية الأقرب كموناً (`EG -> EU DE/UK`).
   * يُجدَّد الـ Sticky IP من نفس النطاق الإقليمي فقط خلال استراحة خروج آمنة مجدولة (`Microbreak IP Refresh`).

---

## 🔄 3. سجل الوصفات المعتمد وبوابة التحديثات المحكومة (`ADR-006 / AUD-10, AUD-28`)

إصلاحاً لافتراض وجود `@ScriptDescriptor` وتحوير المتغيرات `AST Mutator` والسحب التلقائي كل 6 ساعات:
1. **إلغاء المسح الخام وتحوير الكود المصدري:** إضافات `Microbot-Hub` حزم متعددة الملفات تُبنى بـ Gradle وتُضبط عبر `ConfigManager`، ومترجم `javac` يزيل أسماء المتغيرات المحلية؛ لذا يُعتمد حصراً **سجل الوصفات المختوم (`Allowlisted TaskRecipe Registry`)**:

```json
{
  "schema_version": "1.0",
  "catalog_revision": "2026.10.23.1",
  "recipes": [
    {
      "recipe_id": "stage0_stronghold_security",
      "plugin_id": "osrs_ee.plugins.stronghold.StrongholdPlugin",
      "verification_stage": "HERMETIC_TESTED",
      "min_client_version": "2.6.38",
      "stage_tier": "STAGE_2_CAPITAL",
      "min_skills": {},
      "required_varps": {"281": 1000},
      "tool_candidates": [],
      "sellable_yields": [],
      "clear_equipment": false
    },
    {
      "recipe_id": "f2p_draynor_willow",
      "plugin_id": "net.runelite.client.plugins.microbot.woodcutting.AutoWoodcuttingPlugin",
      "verification_stage": "AVAILABLE_SOURCE",
      "min_client_version": "2.6.38",
      "stage_tier": "STAGE_3_F2P_SKILLING",
      "min_skills": {"woodcutting": 30},
      "required_varps": {"281": 1000},
      "tool_candidates": ["Rune axe", "Adamant axe", "Mithril axe", "Steel axe", "Iron axe", "Bronze axe"],
      "sellable_yields": [],
      "clear_equipment": false
    }
  ]
}
```

2. **بوابة الإصدارات المعزولة (`Staging & Canary Release Gate`):**
   * يُحظر تنفيذ `git pull` داخل مسار التشغيل الحي.
   * تمر التحديثات بالدورة: `DETECTED (git fetch) ◄ STAGED ◄ BUILT ◄ CONTRACT_TESTED (minClientVersion) ◄ OPERATOR_APPROVED ◄ CANARY_SLOT ◄ ACTIVE`، ولا تُطبق إلا بين الجلسات بعد خروج آمن.

---

## 🚨 4. مراقبة النبض، الموارد، والتعافي بعد الانهيار (`AUD-14, AUD-24, AUD-25`)

1. **موازنة الذاكرة وحساب السعة (`Heap + Off-Heap RSS`):**
   * يُضبط الـ Heap عند `-Xms256m -Xmx768m -XX:MaxMetaspaceSize=192m -XX:+UseG1GC`.
   * تُحسب سعة النوافذ المتزامنة من متوسط `RSS` الفعلي المقاس + هامش أمان للنظام، وليس من رقم الـ Heap وحده.
2. **حارس النبض وقاطع الدائرة (`Watchdog & Circuit Breaker`):**
   * يميز الحارس بين ثبات الإحداثيات في نشاط ثابت (مثل الطبخ) وبين توقف `game_tick` الفعلي.
   * عند انهيار العميل أو قتله بـ `abort`، **ينتقل الحساب إجبارياً إلى `RECOVERY_REQUIRED` (وليس `READY`)** مع حفظ عداد الراحة، ويمنع قاطع الدائرة حلقات إعادة الإقلاع اللانهائية عند تكرار الفشل.

---

## 🎛️ 5. لقطة الإعدادات الموحدة وتوليد التوقيتات الملتوية (`ADR-008 / AUD-29, AUD-32`)

تُفحص جميع النطاقات قبل الجلسة (`min <= max` وضمن سقوف السلامة الحتمية، مثل `launch_stagger >= 90s` و`cooldown >= 240m`) وتُختم بـ `config_hash` صالح نحوياً:

```json
{
  "schema_version": "1.0",
  "hard_safety_ceilings": {
    "max_probe_timeout_sec": 90,
    "max_drain_timeout_sec": 25,
    "max_abort_timeout_sec": 5,
    "max_tick_stall_sec": 60,
    "min_launch_stagger_sec": 90,
    "min_cooldown_minutes": 240
  },
  "tunable_ranges": {
    "session_duration_minutes": [110, 165],
    "cooldown_duration_minutes": [240, 310],
    "window_launch_stagger_sec": [90, 145],
    "afk_microbreak_interval_minutes": [25, 45],
    "afk_microbreak_duration_sec": [45, 130],
    "bank_interaction_delay_ms": [450, 950],
    "action_reaction_delay_ms": [650, 1850]
  },
  "persona_distribution": {
    "family": "BOUNDED_LOGNORMAL",
    "sigma_range": [0.22, 0.38]
  }
}
```

* بدلاً من `random.gauss` المتماثل (`Skewness = 0`) و`time.sleep` غير القابل للإلغاء، تُولد الأزمنة بتوزيع **`Bounded Log-Normal`** مشتق من `persona_seed` للحساب وتُنفذ عبر انتظار رتيب قابل للإلغاء الفوري عند إشارة `abort`.

---

## 🧪 6. اختبارات القبول المرتبطة (`Acceptance Gate`)
تُحقق هذه المواصفة عبر الاختبارات المعيارية: `T04`, `T06`, `T14`, `T15`, `T16`, `T17`, `T18`, `T19`, `T26`, `T32`.
