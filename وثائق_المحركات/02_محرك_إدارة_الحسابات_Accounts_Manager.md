# 👑 وثيقة المواصفات الهندسية: محرك إدارة الحسابات ومخزن الحالة الذري
## Engine Specification Document: Accounts Manager & Transactional State Store (v2.1)
> **حالة الوثيقة:** مواصفة معيارية موحدة (`Normative v2.1` — النواة منفذة ومختبرة في `osrs_ee/state/`)  
> **المرجعية الحاكمة:** متوافقة مع `ADR-003`, `ADR-007` و`docs/DATA_CONTRACTS_AND_STATE.md`.

---

## 🎯 1. الهوية والمسؤوليات الجوهرية

* **الدور الهندسي:** المالك المنطقي الوحيد لحالة جميع الحسابات، الإيجارات الحصرية (`Leases`)، فترات الراحة والنوم الليلي الإقليمي (`Circadian Windows`)، أهلية مراحل التقدم، وسجل الموافقات البشرية وحيدة الاستخدام.
* **القواعد الدستورية الحاكمة:**
  1. **صفر أسرار في مخزن الحالة (`AUD-02`):** لا تُخزن كلمات المرور أو التوكنات أو روابط البروكسي الخام في قاعدة الحالة أو لقطات JSON؛ يُحفظ `credential_ref` و`proxy_ref` فقط.
  2. **قاعدة العزل الشبكي الموحدة (`AUD-08 / C-06`):** كل حساب نشط يرتبط بعنوان IP مخصص له وحده (`1 Active Account = 1 Dedicated IP`) ومقفل على نطاقه الإقليمي (`region`).
  3. **منع العودة العمياء إلى `READY` (`AUD-14`):** الانهيار ينقل الحساب إلى `RECOVERY_REQUIRED`، والموافقة البشرية تنقله إلى `RECONCILING` لإعادة الفحص الفعلي مع عدم تصفير `cooldown_until`.

---

## 📥 2. استيعاب الحسابات بوضع المصادقة المزدوج (`Dual-Auth Onboarding` — `AUD-11`)

يدعم المحلل الموحد استيراد الحسابات من مصدر محلي محمي بصلاحية `0600` (ويُحظر لصق كلمات المرور في شات التليجرام العام):
1. **`LEGACY_CREDENTIALS`:** الحسابات التراثية القديمة المعتمدة على مرجع بريد وكلمة مرور محفوظين في الخزنة المحلية (`vault://accounts/acc_XX/legacy`).
2. **`JAGEX_SESSION`:** حسابات Jagex الحديثة (الإلزامية لكافة الحسابات الجديدة) المعتمدة على متغيرات الجلسة (`JX_SESSION_ID`, `JX_CHARACTER_ID`, `JX_DISPLAY_NAME`) المحفوظة في الخزنة المحلية (`vault://accounts/acc_XX/jagex`).
* فور الاستيعاب، يحصل الحساب على معرف معتم دائم (`acc_01`)، وبذرة شخصية ثابتة (`persona_seed`)، وحالة أولية `PENDING_DISCOVERY`.

---

## 🗄️ 3. محرك التخزين الذري ولقطة القراءة البشرية (`ADR-003 / AUD-13`)

حسماً لمشكلة فقدان التحديثات وخطأ `WinError 5` على Windows:
1. **المصدر الوحيد للحقيقة (`SQLite WAL`):** تُنفذ كافة التحديثات والإيجارات ومنع تكرار `event_id` و`command_id` داخل معاملات ذرية في قاعدة `SQLite` بوضع `WAL`.
2. **لقطة القراءة البصرية (`state_snapshot.json`):** يصدر المخزن بعد كل معاملة ملف JSON منقحاً وصالحاً نحوياً (`RFC 8259`) للقراءة البشرية السريعة دون أن يُستخدم كمدخل كتابة متنازع عليه:

```json
{
  "schema_version": "1.0",
  "exported_at": "2026-10-23T14:10:00Z",
  "accounts": {
    "acc_01": {
      "account_id": "acc_01",
      "auth_mode": "JAGEX_SESSION",
      "credential_ref": "vault://accounts/acc_01/jagex",
      "proxy_ref": "vault://proxies/eg_sticky_01",
      "region": "EG",
      "timezone": "Africa/Cairo",
      "persona_seed": 918273645,
      "status": "RESTING",
      "reason_code": "NONE",
      "state_version": 12,
      "session_epoch": 3,
      "cooldown_until": "2026-10-23T18:10:00Z",
      "progression": {
        "stage": "STAGE_1B_SEASONING",
        "tutorial_completed": true,
        "quest_points": 10,
        "total_level": 104,
        "playtime_minutes": 380,
        "trade_unlocked": false,
        "last_position": {"x": 3206, "y": 3214, "plane": 0}
      }
    }
  }
}
```

---

## ⏰ 4. إدارة الورديات، الراحة المقدسة، والنوم الليلي الإقليمي (`AUD-29, AUD-31`)

1. **نطاقات محكومة بسقوف أمان (`ADR-008`):** تُسحب مدة الجلسة (مثلاً `[110, 165]` دقيقة) ومدة الراحة بعدها (مثلاً `[240, 310]` دقيقة — لا تقل عن الحد الأدنى للأمان `240` دقيقة) من لقطة الإعدادات المحققة.
2. **قدسية عداد الراحة (`Cooldown Immutability`):** لا يُسمح لأي انهيار أو إعادة تشغيل أو موافقة تليجرام بتصفير أو تقليص `cooldown_until`.
3. **الساعة البيولوجية والنوم الليلي الإقليمي (`Geo-Aligned Circadian Window`):** يرتبط كل حساب بالمنطقة الزمنية لنطاقه (`EG -> Africa/Cairo`, `DE -> Europe/Berlin`, `US_EAST -> America/New_York`) ويُفرض عليه بلوك نوم ليلي متصل (`7.5 إلى 10 ساعات`) يرفض المجدول خلاله إطلاقه.

---

## 🔍 5. بروتوكول الكشف الحدثي وشروط المراحل الحقيقية (`AUD-17, AUD-18 / T10, T11`)

عند تشغيل جلسة الاستكشاف لحساب في حالة `PENDING_DISCOVERY` (بسقف مهلة أمان `max_probe_timeout_sec = 90`):
1. **فحص جزيرة التعليمات الصحيح (`Varp 281`):** يُقرأ **`Varp 281` (`VarPlayerID.TUTORIAL` عبر `/varps?id=281`)** — وليس `Varbit 281`! إذا كانت القيمة `>= 1000` يُثبت إتمام جزيرة التعليمات.
2. **قراءة البنك المشروطة:** إذا كان البنك مغلقاً (`bank.is_open == false`)، تُحفظ حالة محتويات البنك كـ `UNKNOWN` ولا تُفسر كبنك فارغ.
3. **سلم التسكين المرحلي الدقيق وفجوة الـ 20 ساعة (`STAGE_1B_SEASONING`):**
   * **`STAGE_0_TUTORIAL`:** لم يُتم `Varp 281 == 1000`.
   * **`STAGE_1A_QP_AND_TOTAL`:** أتم الجزيرة ولكن `quest_points < 10` أو `total_level < 100`.
   * **`STAGE_1B_SEASONING`:** حقق **`quest_points >= 10` و`total_level >= 100`** ولكن وقت اللعب الفعلي داخل السيرفر **`playtime_minutes < 1200` (أقل من 20 ساعة)**؛ في هذه المرحلة يعمل الحساب في مهام جمع وتطوير مهارات ويودع الإنتاج في البنك **دون أي محاولة لبيع العناصر المقيدة في الـ GE** حتى يعبر حاجز الـ 20 ساعة رسمياً!
   * **`STAGE_2_CAPITAL` / `STAGE_3_F2P_SKILLING`:** استوفى شروط فك قيد التجارة الثلاثة مجتمعة (`QP >= 10`, `Total >= 100`, `Playtime >= 1200m`).

---

## 🔀 6. سجل الوصفات المعتمد ومطابقة الأهلية (`Allowlisted TaskRecipe` — `ADR-006`)

كل مهمة مسجلة في الفهرس المعتمد تتبع عقد `TaskRecipe` صالحاً نحوياً ومختوماً بالهاش:
```json
{
  "schema_version": "1.0",
  "recipe_id": "f2p_draynor_willow",
  "display_name": "Draynor Willow Woodcutter",
  "plugin_id": "net.runelite.client.plugins.microbot.woodcutting.AutoWoodcuttingPlugin",
  "verification_stage": "CONTRACT_TESTED",
  "stage_tier": "STAGE_3_F2P_SKILLING",
  "min_skills": {"woodcutting": 30},
  "required_varps": {"281": 1000},
  "tool_candidates": ["Rune axe", "Adamant axe", "Mithril axe", "Steel axe", "Iron axe", "Bronze axe"],
  "sellable_yields": [],
  "clear_equipment": false
}
```
والقائمة المولدة للمجدول (`EligibleTasksPool`) تحتوي فقط الوصفات المستوفية لشروط المرحلة والمستوى وحالة التحقق المطلوبة:
```json
{
  "account_id": "acc_03",
  "session_epoch_candidate": 5,
  "stage": "STAGE_3_F2P_SKILLING",
  "eligible_recipes": [
    {"recipe_id": "f2p_draynor_willow", "skill": "woodcutting", "weight": 0.45},
    {"recipe_id": "f2p_barbarian_fly_fishing", "skill": "fishing", "weight": 0.35},
    {"recipe_id": "f2p_alkharid_mining", "skill": "mining", "weight": 0.20}
  ]
}
```

---

## 🚨 7. حوكمة التوقف، الموافقات البشرية، والمصالحة (`BLOCKED -> RECONCILING`)

1. عند تعثر الحساب، يُنهى إيجار الجلسة النشطة أولاً قبل تحرير السلوت لأي حساب آخر (`AUD-05` — فلا يمكن لمستشار ذكي أو عامل متأخر إرسال أوامر للسلوت بعد إخلائه).
2. إذا تطلب العائق تدخلاً بشرياً (`HUMAN_AUTH_REQUIRED` أو `BANK_PIN_REQUIRED`)، ينشئ المخزن سجل `ApprovalRecord` وحيد الاستخدام مرتبطاً بـ `(incident_id, account_id, session_epoch, plan_hash, expires_at)`.
3. عند ضغط المشغل على زر الإتمام في Telegram، ينتقل الحساب ذرياً إلى **`RECONCILING`** (وليس `READY`) لتتحقق جلسة مصالحة سريعة من زوال العائق فعلياً قبل إعادته إلى `READY`.

---

## 🧪 8. اختبارات القبول المرتبطة (`Acceptance Gate`)
تُحقق هذه المواصفة آلياً عبر الاختبارات المعيارية: `T02`, `T03`, `T04`, `T08`, `T09`, `T11`, `T20`, `T31` في `tests/test_pr03_state_core.py`.
