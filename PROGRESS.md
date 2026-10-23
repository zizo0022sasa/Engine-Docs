# 🧭 سجل التقدم وبوصلة الاستئناف الإلزامية (`PROGRESS.md`)

> **تنبيه إلزامي عند كتابة «تابع» أو استئناف الجلسة:** اقرأ هذا الملف أولاً، ثم نفّذ قائمة الفحص السبعية في [`docs/RESUME_PROTOCOL.md`](./docs/RESUME_PROTOCOL.md) قبل لمس أي كود. لا تبدأ من الصفر ولا تكرر المهام الموسومة بـ `VERIFIED`.

---

## 1. الحالة الراهنة للمشروع (`Current Snapshot`)

* **المرحلة الحالية:** اكتمال التأسيس والتوحيد الوثائقي (`PR-00` + `PR-01`) والانتقال المباشر إلى تنفيذ العقود المعيارية (`PR-02`).
* **آخر نقطة تحقق مكتملة:** `PR-01 — Consolidated Multi-Agent Baseline & Harmonized Docs`
* **المهمة الجارية حالياً (`CURRENT_TASK`):** `TASK-02-01` و`TASK-02-02` (`PR-02`: بناء مخططات `schemas/*.schema.json` وحزمة `osrs_ee/contracts/` واختبارات `T20, T26, T32`).
* **حالة الأمان (`Secret Scan`):** نظيف 100% (`[SECRET_SCAN_OK] Zero secret patterns detected`).

---

## 2. ملخص ما أُنجز وتُحقق منه فعلياً (`Verified Milestones`)

1. **`PR-00` (كوميت `67b589d` — `VERIFIED`):**
   * تنقيح نمطي توكن Telegram المكشوفين في `للمعمارية.md` دون مساس بتاريخ المستودع.
   * تحصين `.gitignore` لحجب الأسرار وقواعد البيانات والساندبوكس والتوكنات.
   * بناء واختبار `osrs_ee/security/redaction.py` و`osrs_ee/security/secret_scan.py` واجتياز 5 اختبارات في `tests/test_pr00_security.py` (`T01`, `T02`).
2. **`PR-01` (التوثيق المعياري الموحد — `VERIFIED`):**
   * دمج تقارير الوكلاء الثلاثة (`OSRS_ENGINEERING_AUDIT_AR.md` + `OSRS_DOCUMENT_PATCHES_AR.md`, `report.md`, `تقرير_التدقيق_المعماري_الشامل_OSRS_Bot.md`) في `docs/AUDIT_REGISTER.md` (`AUD-01..35`) وحسم التعارضات الستة بالأدلة.
   * توثيق القرارات الحاكمة `ADR-001..008` والقرارات المعلقة `DEC-PEND-01..03` في `docs/DECISIONS.md`.
   * إنشاء حزمة الوثائق المعيارية الكاملة في `docs/` و`TASKS.md` و`README.md` وتحديث جميع ملفات `وثائق_المحركات/00..06` لإزالة التناقضات وإصلاح كافة كتل JSON.

---

## 3. القرارات المعلقة والافتراضات الآمنة المطبقة (`Pending Decisions`)

* **`DEC-PEND-01`:** نظام تشغيل جهاز الإنتاج (`Linux + Xvfb` أم `Windows`) ◄ نطبق كوداً عابراً للأنظمة حتى مرحلة `PR-05`.
* **`DEC-PEND-02`:** نمط حساب التجربة الحي الأول (`JAGEX_SESSION` أم `LEGACY`) ◄ العقود تدعم الاثنين عبر `credential_ref` حتى مرحلة `PR-06`.
* **`DEC-PEND-03`:** تدوير توكنات Telegram القديمة على `@BotFather` وتقييد الـ Gist الخارجي ◄ بوابة إلزامية قبل `PR-07`.

---

## 4. العوائق الحالية (`Active Blockers`)

* **لا توجد عوائق أمام المراحل المعزولة (`PR-02`, `PR-03`, `PR-04`)**؛ كافة متطلباتها متاحة محلياً وقابلة للاختبار الكامل بدون لعبة حية.

---

## 5. الخطوة التالية المصرح بها فوراً (`Next Authorized Step`)

1. تنفيذ واختبار **`PR-02`** (`schemas/` + `osrs_ee/contracts/` + `tests/test_pr02_contracts.py` لتغطية `T05, T10, T14, T20, T26, T32`) ثم الالتزام والرفع.
2. الانتقال مباشرة إلى **`PR-03`** (`osrs_ee/state/` + `tests/test_pr03_state_core.py` لتغطية `T03, T04, T07, T08, T09, T11, T31`) ثم الالتزام والرفع.
3. الانتقال مباشرة إلى **`PR-04`** (`osrs_ee/runtime/` + `tests/test_pr04_fake_runtime.py` لتغطية `T06, T10, T12, T13, T16, T17, T21, T23, T27`) ثم الالتزام والرفع.
