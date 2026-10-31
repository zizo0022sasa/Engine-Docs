# 📜 عقود البيانات المعيارية وآلات الحالات الموحدة (`DATA_CONTRACTS_AND_STATE.md`)

> **الإصدار:** `2.1 — Normative Contracts & FSMs`  
> **ملاحظة تنفيذية:** جميع أمثلة JSON في هذه الوثيقة مطابقة نحوياً لمعيار JSON الصارم (`RFC 8259`) ومحققة آلياً عبر `schemas/*.schema.json` و`osrs_ee/contracts/` واختبار `test_pr01_docs_integrity.py`.

---

## 1. القواعد المشتركة لجميع العقود (`Global Schema Rules`)

1. **رفض الحقول غير المعرفة (`additionalProperties: false`):** أي حقل زائد أو ناقص أو بنوع خاطئ يُرفض فوراً قبل التنفيذ.
2. **معرفات الحسابات معتمة (`Opaque Account IDs`):** `account_id` معرف داخلي مثل `"acc_01"`؛ يُحظر استخدام البريد الإلكتروني كمعرف في العقود أو السجلات.
3. **التمييز بين الصفر و`UNKNOWN`:** عند تعذر القراءة أو إغلاق الواجهة (مثل البنك المغلق)، تكون جودة القراءة `"UNKNOWN"` وقيمتها `null`، ولا تُحوَّل إلى `0` أبداً (`AUD-17`).
4. **الوحدات الزمنية والرتابة:** التوابع الزمنية المخزنة بصيغة `ISO-8601 UTC`، والمدد محددة الوحدة بالاسم (`_ms` أو `_sec`)، وحسابات المهل وقت التشغيل تعتمد حصراً على `time.monotonic()`.

---

## 2. آلة حالات الحساب الموحدة (`Unified Account Lifecycle FSM`)

### أ. الحالات القانونية الاثنتا عشرة (`12 Canonical Account States`)

| الحالة | الوصف الهندسي الدقيق | هل تسمح بجدولة جلسة عمل روتينية؟ |
| :--- | :--- | :---: |
| **`NEW`** | حساب مضاف حديثاً بمراجع أسرار صالحة ولم تُفحص قدراته بعد. | لا (يقبل جلسة `DISCOVERY` فقط) |
| **`PENDING_DISCOVERY`** | مجدول لجلسة قراءة استكشافية أولى (`Varp 281`, المهارات, QP, الجرد). | لا |
| **`DISCOVERING`** | جلسة الاستكشاف القرائية نشطة حالياً بعقد إيجار حصري. | لا |
| **`READY`** | مكتمل الفحص، غير محجوز، خارج فترة الراحة/النوم، ومستوفٍ لشروط التشغيل. | **نعم** |
| **`LEASED`** | حُجز بعقد إيجار حصري (`lease_id`, `session_epoch`) وجارٍ تحضير الساندبوكس. | لا |
| **`STARTING`** | أُطلقت عملية الـ JVM والنفق المحلي وجارٍ التحقق من هوية `AgentServer`. | لا |
| **`RUNNING`** | جلسة العمل نشطة ومرتبطة بعامل المهمة وبوابة الأوامر. | لا |
| **`DRAINING`** | جارٍ الإغلاق التعاوني الآمن (إيقاف الإضافة ◄ `POST /logout` ◄ قتل الشجرة). | لا |
| **`RESTING`** | في استراحة بين الورديات (`cooldown_until`) أو نافذة النوم الليلي (`Circadian`). | لا (حتى ينقضي الوقت) |
| **`BLOCKED`** | متوقف لسبب يتطلب قراراً أو تدخلاً بشرياً صريحاً مع `reason_code` محدد. | لا |
| **`RECOVERY_REQUIRED`** | انتهت جلسته السابقة بانهيار أو `abort` أو أمر `UNKNOWN` غير مصالح. | لا (يتطلب مصالحة أولاً) |
| **`RECONCILING`** | بعد موافقة بشرية أو بدء استرداد؛ يُعاد فحص حالته الفعلية قبل تقرير مصيره. | لا |
| **`DISABLED`** | معطل إدارياً أو محظور نهائياً بقرار المشغل (`BANNED` / `RETIRED`). | لا |

*(ملاحظة: المجموع 13 حالة شاملة `DISABLED` النهائية؛ جميع الانتقالات محكومة بجدول الحراسة في `osrs_ee/state/fsm.py`).*

### ب. رموز أسباب التوقف الإلزامية (`Reason Codes`)
* `NONE` — لا يوجد عائق.
* `HUMAN_AUTH_REQUIRED` — مطلوب تدخل مصادقة بشرية / OTP / Jagex Session.
* `BANK_PIN_REQUIRED` — واجهة الرقم السري للبنك مفتوحة ومجهولة الحل الآلي.
* `INSUFFICIENT_RESOURCES` — نقص أداة أساسية أو ذهب أو مدخلات غير قابلة للاستبدال.
* `PROXY_UNAVAILABLE` — فشل نفق البروكسي المخصص أو عدم استيفاء شرط `Fail-Closed`.
* `CAPABILITY_UNKNOWN` — تعذر إثبات قدرات الحساب أو إغلاق البنك أثناء الفحص.
* `DEATH_QUARANTINE` — حجر صحي بعد موت الشخصية لمنع حلقة الموت المتكررة (`AUD-21`).
* `CRASH_UNCLOSED_SESSION` — إقلاع المشرف مع وجود جلسة سابقة غير مغلقة بإثبات (`AUD-14`).
* `UNKNOWN_COMMAND_EFFECT` — انقطاع الاتصال بعد إرسال أمر غير متكافئ قبل التحقق البعدي (`AUD-12`).
* `POLICY_DENIED` — رفض أمر أو تحديث لمخالفة السياسة أو غياب الاعتماد.

---

## 3. آلة حالات سجل الأوامر (`Write-Ahead Command Journal FSM`)

لا يُرسل أي أمر مؤثر عبر `GameAdapterGate` إلا بعد تدوينه في قاعدة البيانات وفق التسلسل:

```text
PREPARED ──► DISPATCHING ──► ACKNOWLEDGED ──► VERIFIED
   │              │               │
   │              │               ├─────────► FAILED_VERIFIED (ثبت عدم حدوث الأثر ويمكن التخطيط بأمان)
   │              ▼               ▼
   │           UNKNOWN ◄─────── UNKNOWN       (انقطع النقل أو تعذرت القراءة البعدية؛ يُحظر التكرار الأعمى!)
   ▼
REJECTED (فشل الشرط المسبق أو انتهى الإيجار/الجيل قبل الإرسال؛ لم يغادر الأمر للجافا قط)
```

---

## 4. المخططات المعيارية للأجسام المتبادلة (`Canonical JSON Contracts`)

### 4.1 عقد إطلاق الجلسة (`SessionLaunch`)
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

### 4.2 عقد الرصد المهيكل (`Observation`)
```json
{
  "schema_version": "1.0",
  "observation_id": "obs_000105",
  "observed_at": "2026-10-23T14:05:12Z",
  "monotonic_ms": 9182450,
  "account_id": "acc_01",
  "session_id": "sess_20261023_0001",
  "client_instance_id": "jvm_77c109be",
  "session_epoch": 4,
  "login_state": "LOGGED_IN",
  "game_tick": 1482,
  "world": 308,
  "position": {
    "x": 3081,
    "y": 3421,
    "plane": 0,
    "quality": "KNOWN"
  },
  "tutorial_varp_281": {
    "value": 1000,
    "quality": "KNOWN"
  },
  "quest_points": {
    "value": 10,
    "quality": "KNOWN"
  },
  "total_level": {
    "value": 112,
    "quality": "KNOWN"
  },
  "playtime_minutes": {
    "value": 420,
    "quality": "KNOWN"
  },
  "skills_levels": {
    "hitpoints": 10,
    "woodcutting": 22,
    "fishing": 12,
    "cooking": 9
  },
  "dialogue_open": false,
  "bank": {
    "is_open": false,
    "pin_prompt_open": false,
    "items_quality": "UNKNOWN",
    "items": null
  },
  "inventory": {
    "free_slots": 26,
    "quality": "KNOWN",
    "items": [
      {"item_id": 1351, "name": "Bronze axe", "quantity": 1, "noted": false},
      {"item_id": 995, "name": "Coins", "quantity": 10000, "noted": false}
    ]
  }
}
```

### 4.3 عقد نية الأمر (`CommandIntent`)
```json
{
  "schema_version": "1.0",
  "command_id": "cmd_20261023_00042",
  "session_id": "sess_20261023_0001",
  "account_id": "acc_01",
  "client_instance_id": "jvm_77c109be",
  "lease_id": "lease_9f81c2a0",
  "session_epoch": 4,
  "base_state_version": 18,
  "command_type": "BANK_WITHDRAW_ITEM",
  "idempotency_class": "NON_IDEMPOTENT",
  "payload": {
    "item_id": 1359,
    "quantity": 1,
    "noted": false
  },
  "precondition": "bank.is_open == true and bank.pin_prompt_open == false",
  "expected_postcondition": "inventory.count(1359, noted=false) >= 1",
  "timeout_ms": 5000
}
```

### 4.4 عقد نتيجة الأمر (`CommandResult`)
```json
{
  "schema_version": "1.0",
  "command_id": "cmd_20261023_00042",
  "event_id": "evt_20261023_00089",
  "session_id": "sess_20261023_0001",
  "session_epoch": 4,
  "transport_status": "HTTP_200",
  "effect_status": "VERIFIED",
  "completed_at": "2026-10-23T14:05:15Z",
  "post_observation_id": "obs_000106",
  "error_code": null,
  "diagnostic_note": "Withdrew Rune axe (1359) x1 and verified inventory delta."
}
```

### 4.5 عقد اقتراح المستشار الذكي (`AIProposal` — `ADR-002`)
```json
{
  "schema_version": "1.0",
  "proposal_id": "prop_00012",
  "incident_id": "inc_20261023_004",
  "account_id": "acc_01",
  "session_id": "sess_20261023_0001",
  "session_epoch": 4,
  "based_on_observation_id": "obs_000105",
  "diagnosis_category": "UNEXPECTED_WIDGET_OVERLAY",
  "recommended_disposition": "REQUEST_HUMAN_REVIEW",
  "rationale": "Unrecognized modal interface blocking bank interaction; advisory mode forbids blind confirmation.",
  "requires_human_approval": true
}
```

---

> `skills_levels` (`PR-22`): خريطة اختيارية بأسماء مهارات lowercase ومستوياتها الحية، تُغذّى من
> `GET /skills` مرة كل 5 ثوانٍ حين يُسقط `/state` حقل `totalLevel`. يستخدمها
> `ExecutionEngineKernel` لتحديث `AccountCapabilitiesContext` وسط التشغيل، وليست بديلاً عن
> `total_level` (الرقم الرسمي يبقى هو المصدر).

