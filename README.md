# 🛡️ OSRS_ee — منظومة التشغيل الموثوق والعزل وإدارة الحالة

> **الحالة الحالية للمستودع:** جميع المراحل البرمجية والتوثيقية (`PR-00` حتى `PR-10` + واجهة التشغيل الموحدة `CLI`) **مكتملة ومحققة بـ 38 اختباراً آلياً ناجحاً (`38 passed`)** وخالية 100% من أي أسرار مكشوفة (`[SECRET_SCAN_OK]`).  
> **دليل الاستخدام الشامل خطوة بخطوة:** اقرأ **[`كيفيه_الاستخدام_من_الالف_للياء.md`](./كيفيه_الاستخدام_من_الالف_للياء.md)**.  
> **نقطة الدخول للاستئناف الهندسي («تابع»):** اقرأ [`PROGRESS.md`](./PROGRESS.md) ثم [`docs/RESUME_PROTOCOL.md`](./docs/RESUME_PROTOCOL.md).

---

## 1. نبذة عن المشروع وفلسفته الهندسية

مشروع **`OSRS_ee`** هو النواة الهندسية الموحدة لإدارة وتشغيل جلسات عميل `Microbot / RuneLite` على أنظمة **`Windows`** و**`Linux`** بعزل صارم للبيانات والشبكة (`Per-Slot Loopback SOCKS5h Sidecar` + `Fail-Closed Kill-Switch`)، وخزنة أسرار محلية بصلاحية المالك فقط (`LocalSecretVault 0600` تدعم حسابات `Legacy` و`Jagex Account`)، وحفظ معاملاتي للحالة (`SQLite WAL` مع تصدير لقطة `state_snapshot.json` منقحة 100%)، وإيجارات جلسات مفحوصة الجيل (`Lease + session_epoch`)، وسجل أوامر مسبق التدوين (`Write-Ahead Command Journal`)، واسترداد آمن بعد الانهيار أو انقطاع الكهرباء.

### مبدأ الصدق المعرفي الصارم (`ADR-001`)
* يميز هذا المستودع بصرامة بين ما هو **منفذ ومختبر فعلياً بالاختبارات الآلية وحقن الأعطال (`HERMETIC_VERIFIED` — 38 اختباراً ناجحاً)**، وما يتطلب **التشغيل الحي على جهاز المالك (`PENDING_OWNER_HOST_RUN`)** لارتباطه بتثبيت `Microbot JAR` وبروكسيات السكن الفعلية وتدوير توكن التليجرام لدى `@BotFather`.
* الهندسة المطبقة تقلل أعطال التشغيل والتلوث الشبكي/السلوكي وفقدان البيانات وتمنع بيع أدوات الوصفات النشطة، **لكنها لا تدّعي ضماناً مطلقاً (100%) ضد الحظر من مطور اللعبة**.

---

## 2. خريطة المستودع والحزم المنفذة (`Repository Layout`)

```text
Engine-Docs/
├── كيفيه_الاستخدام_من_الالف_للياء.md      # الدليل التشغيلي الكامل من الصفر حتى الإطلاق والطوارئ
├── README.md                              # البوابة الرئيسية للمشروع وخريطة الوحدات
├── PROGRESS.md                            # بوصلة التقدم وسجل الإغلاق الهندسي
├── TASKS.md                               # سجل المهام الذرية الموحد (PR-00 .. PR-11)
├── .env.example                           # قالب متغيرات البيئة الآمن (انسخه إلى .env محلياً)
├── pyproject.toml                         # تعريف الحزمة البرمجية ونقطة دخول `osrs-ee`
├── .gitignore                             # قواعد منع تسرب الأسرار وقواعد البيانات والساندبوكس
├── docs/                                  # 9 وثائق هندسية معيارية حاكمة (AUD-01..35 + ADR-001..008)
├── وثائق_المحركات/                         # وثائق المحركات التفصيلية المحدثة والمتوافقة (v2.1)
├── schemas/                               # 6 مخططات JSON معيارية قابلة للتحقق الآلي (PR-02)
├── osrs_ee/                               # الحزمة البرمجية الكاملة (100% Python 3.11+ Stdlib)
│   ├── cli.py / __main__.py               # واجهة الأوامر الموحدة لويندوز ولينكس (`python -m osrs_ee`)
│   ├── security/                          # Redactor + secret_scan + LocalSecretVault + ingest_pipe_lines
│   ├── contracts/                         # مدقق المخططات + RuntimeConfigSnapshot + PersonaProfile
│   ├── state/                             # StateStore (SQLite WAL) + 13-State FSM + Leases + RBAC
│   ├── runtime/                           # SingleSlotSupervisor + GameAdapterGate + TaskWorker + FakeGameAdapter
│   ├── sandbox/                           # SandboxProfileSeeder + MicrobotHttpAdapter + ManagedProcessTree
│   ├── network/                           # LocalSocks5Sidecar (RFC1928/1929 + ATYP=0x03 DNS + Kill-Switch)
│   ├── tasks/                             # CuratedRecipeRegistry + SingleSlotPilotRunner
│   ├── ai/                                # DeterministicStrongholdSolver + AIAdvisoryClient (Breaker + Guard)
│   ├── telemetry/                         # LogQuotaGuardian (<500MB) + BoundedOutbox + TelegramGateway
│   ├── orchestrator/                      # RssCapacityCalculator + StaggeredLaunchPlanner + ReleaseStagingGate
│   └── economy/                           # DefaultDenySellPolicy + ActiveRecipeInputLock + GEOrderLedger (2% Tax)
└── tests/                                 # 38 اختبار قبول وحقن أعطال آلي تغطي المصفوفة T01..T32
```

---

## 3. البدء السريع وأوامر التحقق (`Quick Verification`)

يعمل النظام مباشرة على **Python 3.11+** بدون أي مكتبات خارجية للتشغيل (`Zero Runtime Dependencies` — وفقط `pytest` للاختبارات):

```bash
# 1. الفحص الصحي للنظام وحساب سعة الرام وفحص خلو المستودع من الأسرار
PYTHONPATH=. python3 -m osrs_ee doctor

# 2. تشغيل حزمة الاختبارات الآلية وحقن الأعطال الكاملة (38 اختباراً)
PYTHONPATH=. pytest -v

# 3. تهيئة مجلد البيانات المحلي المعزول وتجربة دورة طيار محاكاة كاملة
PYTHONPATH=. python3 -m osrs_ee init
PYTHONPATH=. python3 -m osrs_ee simulate-pilot --recipe-id stage2_stronghold_10k
```

للتفاصيل الكاملة لإدخال الحسابات (`Legacy` و`Jagex`) وتشغيل عميل `Microbot` الحي على Windows أو Linux وإعداد التليجرام، راجع **[`كيفيه_الاستخدام_من_الالف_للياء.md`](./كيفيه_الاستخدام_من_الالف_للياء.md)**.
