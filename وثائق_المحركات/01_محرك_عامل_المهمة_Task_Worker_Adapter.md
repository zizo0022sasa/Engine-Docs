# ⚙️ وثيقة المواصفات الهندسية: محرك عامل المهمة وبوابة محول اللعبة
## Engine Specification Document: Task Worker & Game Adapter Gate (v2.1)
> **حالة الوثيقة:** مواصفة معيارية موحدة (`Normative v2.1` — النواة المحاكية منفذة في `osrs_ee/runtime/`؛ الربط الحي مؤجل إلى `PR-05/06`)  
> **المرجعية الحاكمة:** متوافقة مع `ADR-001..008` و`docs/DATA_CONTRACTS_AND_STATE.md`.

---

## 🎯 1. الهوية وحدود الملكية الصارمة

* **الدور الهندسي:** تنفيذ دورة المهمة الموكلة للجلسة الواحدة عبر بوابة المحول الحصرية (`GameAdapterGate`) وفق مبدأ:
  **"اقرأ الحالة المهيكلة ◄ تحقق من الشرط المسبق ◄ دوّن نية الأمر في `CommandJournal` ◄ أرسل عبر البوابة المفحوصة بالجيل والإيجار ◄ تحقق من الأثر البعدي داخل اللعبة"**.
* **حدود الملكية (`AUD-16`):**
  * العامل **ينفذ** المهمة المختارة في عقد الإطلاق (`task_id`) أو يرفضها إذا فشلت شروطها المسبقة (`INSUFFICIENT_RESOURCES`) طالباً إعادة التخطيط (`REPLAN`) من المجدول.
  * **يُحظر** على العامل تبديل المهمة سراً من تلقاء نفسه، أو الاحتفاظ بكلمات المرور، أو بيع عناصر في الـ Grand Exchange خلال مرحلة الـ MVP.

---

## 📥 2. عقود الإطلاق والإغلاق المعيارية (`Valid JSON Contracts`)

### أ. عقد إطلاق الجلسة (`SessionLaunch` — خالٍ تماماً من الأسرار الخام):
```json
{
  "schema_version": "1.0",
  "session_id": "sess_20261023_0001",
  "account_id": "acc_01",
  "slot_id": 0,
  "lease_id": "lease_9f81c2a0",
  "session_epoch": 4,
  "auth_mode": "JAGEX_SESSION",
  "credential_ref": "vault://accounts/acc_01/auth",
  "proxy_ref": "vault://proxies/eg_sticky_01",
  "local_socks5_port": 19081,
  "adapter_port": 18081,
  "adapter_token_ref": "runtime://tokens/sess_20261023_0001",
  "sandbox_home": "sandboxes/acc_01/home",
  "hwid_store_path": "sandboxes/acc_01/hwid.properties",
  "allowed_worlds": [301, 308, 316, 335],
  "task_id": "STAGE0_STRONGHOLD",
  "config_hash": "sha256:4a8d9e1b2c3f4a5b6c7d8e9f00112233445566778899aabbccddeeff00112233",
  "telemetry_disabled": true
}
```

### ب. عقد تقرير إغلاق الجلسة (`SessionClosureReport`):
```json
{
  "schema_version": "1.0",
  "session_id": "sess_20261023_0001",
  "event_id": "evt_close_0001",
  "account_id": "acc_01",
  "session_epoch": 4,
  "closure_status": "COMPLETED",
  "reason_code": "NONE",
  "final_observation": {
    "position": {"x": 3081, "y": 3421, "plane": 0, "quality": "KNOWN"},
    "tutorial_varp_281": {"value": 1000, "quality": "KNOWN"},
    "quest_points": {"value": 10, "quality": "KNOWN"},
    "total_level": {"value": 112, "quality": "KNOWN"},
    "playtime_minutes": {"value": 420, "quality": "KNOWN"}
  },
  "metrics": {
    "duration_sec": 2140,
    "commands_verified": 48,
    "commands_unknown": 0,
    "incidents_count": 0
  }
}
```

---

## 🔄 3. دورة الأمر المحققة ومعالجة ضياع الاستجابة (`AUD-12 / T07`)

1. **القراءة المهيكلة (`Observation`):** فشل القراءة أو إغلاق الواجهة (مثل البنك المغلق) يعيد `quality = "UNKNOWN"` وقيمة `null`، ويُمنع تحويل الخطأ إلى `0`.
2. **فحص سريان الإيجار والجيل (`Epoch & Lease Fencing`):** تتحقق `GameAdapterGate` من تطابق `(account_id, session_id, client_instance_id, session_epoch, lease_id)`؛ أي أمر متأخر من جلسة سابقة يُرفض فوراً بـ `STALE_CONTEXT`.
3. **التدوين المسبق للنية (`Write-Ahead Journal`):** يُسجل الأمر في `CommandJournal` بحالة `PREPARED` ثم `DISPATCHING` قبل الإرسال الشبكي.
4. **فصل حالة النقل عن الأثر (`Transport vs Effect`):**
   * `HTTP 200` لا يعني تحقق الهدف داخل اللعبة؛ لا يُوسم الأمر `VERIFIED` إلا بعد قراءة `Observation` بعدية تثبت تحقق الشرط البعدي.
   * إذا انقطع الاتصال أثناء النقل (`Timeout / ConnectionReset`)، يُوسم الأمر `effect_status = "UNKNOWN"`، ويُحظر تكراره التلقائي إذا كان `NON_IDEMPOTENT` حتى تُجرى قراءة مصالحة تحسم هل حدث الأثر في السيرفر أم لا.

---

## 🏦 4. بروتوكول البنك المحقق وعقد سحب المعدات (`AUD-19 / T12`)

إصلاحاً للعيوب المثبتة في المسودة الأولى (تجاهل القيمة المرجعة من السحب، عدم فحص `Bank PIN`، وافتراض دوال ميتاداتا غير موجودة):

1. **فحص فتح البنك وحاجز `Bank PIN`:**
   * إذا ظهر حوار الرقم السري للبنك (`bank.pin_prompt_open == true`) ولا توجد سياسة حل مصرحة، يتوقف العامل فوراً ويغلق الجلسة بحالة `BLOCKED` ورمز `BANK_PIN_REQUIRED`.
   * لا يُقرأ مخزون البنك إلا إذا كان `bank.is_open == true`؛ خلاف ذلك تكون قائمة البنك `UNKNOWN`.
2. **إيداع الحقيبة والعتاد حسب سياسة الوصفة:**
   * يرسل أمر إيداع الحقيبة، وإذا اشترطت الوصفة `clear_equipment = true` يرسل أمر إيداع العتاد الملبوس، ثم يتحقق بالقراءة البعدية من فراغ الخانات المطلوبة فعلياً.
3. **اختيار الأداة المؤهلة وسحبها بالتحقق البعدي:**
   * يختار أعلى أداة متاحة في البنك **يستوفي الحساب مستواها الفعلي** (منع سحب فأس `Rune` لحساب مستواه `Woodcutting < 41` أو محاولة ارتدائه دون `Attack 40`).
   * يضبط وضع السحب (`noted = false` للأدوات والمدخلات المباشرة)، ويرسل أمر السحب، ثم **يتحقق من زيادة الكمية الفعلية في `inventory` بعد السحب**؛ إذا لم تتغير الكمية لا يُرجع `VERIFIED` أبداً.
   * عند عدم توفر متطلبات المهمة في البنك، يعيد نتيجة قانونية `FAILED_PRECONDITION` برمز `INSUFFICIENT_RESOURCES` ويطلب `REPLAN` من المشرف دون تغيير المهمة ذاتياً.

---

## 🏛️ 5. حوكمة الأصول وتسييل الفائض (`ADR-006 / AUD-20 / T13`)

* **في مرحلة النواة الأساسية (`MVP`):** التسييل التلقائي في الـ Grand Exchange ونقل الذهب بين الحسابات **معطلان بالكامل**.
* **في مرحلة الاقتصاد المتقدمة (`PR-10`):**
  1. لا يُعتمد على دوال غير موجودة مثل `ItemComposition.isTool` أو `isQuestItem`.
  2. تُطبق سياسة المنع الافتراضي الصارم (`Default-Deny`): كل غرض في البنك محصن من البيع ما لم يكن مدرجاً بالمعرف والاسم معاً في قائمة `sellable_yields` للوصفة المعتمدة، وغير محجوز في قفل مدخلات الوصفات المؤهلة (`Active Recipe Input Lock`)، وفوق عتبة الاحتياطي المحددة، وعبر دفتر أوامر `GE Order Ledger` بعد استيفاء شروط فتح التداول كاملة (`10 QP + 100 Total Level + 20h Playtime`).

---

## 🛑 6. مستويات الإيقاف، مراقبة النبض، والحجر الصحي بعد الموت

1. **مستويات الإيقاف الثلاثة بمهل حتمية (`AUD-23 / T16`):**
   * `PAUSE`: إيقاف إرسال أي أمر جديد فوراً.
   * `DRAIN`: إنهاء الدورة الحالية، إرسال `/scripts/stop` ثم `POST /logout` لكبح `AutoLoginScript` (`AUD-22`)، ضمن مهلة قصوى حتمية؛ فإذا انتهت المهلة يتحول تلقائياً إلى `ABORT`.
   * `ABORT`: سحب سلطة الإيجار فوراً، إلغاء فترات الانتظار القابلة للإلغاء، وقتل شجرة العمليات ووسم الجلسة `RECOVERY_REQUIRED`.
2. **التمييز بين النشاط الثابت وتجمد العميل (`AUD-24 / T17`):**
   * ثبات إحداثيات اللاعب وحده لا يعني التجمد (الطبخ وصيد السمك أنشطة ثابتة المكان)؛ يُقاس التجمد بتوقف تقدم `game_tick` أو فشل استجابة المحول متجاوزاً سقف السلامة مع قاطع دائرة لإعادة التشغيل (`Restart Circuit Breaker`).
3. **الحجر الصحي بعد الموت (`Post-Death Quarantine` — `AUD-21 / T23`):**
   * عند رصد موت اللاعب، تُوقف المهمة الجارية فوراً وينتقل الحساب إلى `BLOCKED (reason_code = "DEATH_QUARANTINE")`. يُحظر إرجاع الحساب لنفس المهمة أو العودة لاسترجاع القبر تلقائياً منعاً لحلقات الموت المتكررة (`Death Loops`).

---

## 🧪 7. اختبارات القبول المرتبطة (`Acceptance Gate`)
تُحقق هذه المواصفة آلياً عبر الاختبارات المعيارية: `T04`, `T06`, `T07`, `T10`, `T12`, `T13`, `T16`, `T17`, `T21`, `T23` في `tests/test_pr04_fake_runtime.py`.
