# התקנה והכנה לסדנה (Getting Started)

ברוכים הבאים לסדנת ממשקי מוח-מכונה (**BCI Workshop 2026**)!  
כדי שלא נבזבז זמן יקר בכיתה על התקנות, יש להשלים את תהליך ההכנה **לפני תחילת הסדנה ביום ראשון בשעה 09:00**.

---

## 📥 שלב 1: הורדת אפליקציית הסדנה (BCI Workshop Hub)

אפליקציית **BCI Workshop Hub** היא תוכנת פתיחה בעברית (עם אפשרות לאנגלית) שתסרוק את המחשב שלכם, תציע התקנה מוסברת של התוכנות הדרושות (כולן אופציונליות), תבדוק שהכל עובד עוד **לפני חיבור חומרה**, ותשלח למרצה אישור שהמחשב מוכן.

> 🔗 **עמוד ההורדה הרשמי (גרסה אחרונה):**  
> **[הורידו את BCI Workshop Hub מ-GitHub Releases](https://github.com/urikorisky/BCI_Workshop_2026_Hub/releases/latest)**

* **משתמשי Windows (64-bit):** הורידו והפעילו את קובץ ה-`.exe` (או `.msi`).
* **משתמשי Mac (Apple Silicon M1–M4 / Intel):** הורידו ופתחו את קובץ ה-`.dmg`.

---

## 🛡️ שלב 2: אישור חלונות האבטחה בפתיחה הראשונה

מאחר שזוהי תוכנה פנימית של הסדנה, מערכת ההפעלה תקפיץ אזהרת אבטחה חד-פעמית בפתיחה הראשונה — זה צפוי ותקין:

### ב-Windows (SmartScreen)
1. אם מופיע מסך כחול **"Windows protected your PC"** / **"Windows הגן על המחשב שלך"**:
2. לחצו על הקישור הקטן **More info** (*מידע נוסף*).
3. לחצו על הכפתור **Run anyway** (*הפעל בכל זאת*).

### ב-Mac (macOS Gatekeeper)
1. פתחו את קובץ ה-`.dmg` וגררו את **BCI Workshop Hub** לתוך תיקיית **Applications**.
2. נסו לפתוח את האפליקציה מתוך Applications (אם מופיעה הודעת חסימה, לחצו **Done / OK**).
3. פתחו **System Settings** (הגדרות מערכת) ← **Privacy & Security** (פרטיות ואבטחה).
4. גללו למטה עד להודעה על *BCI Workshop Hub*, ולחצו **Open Anyway** (*פתח בכל זאת*) ואשרו עם סיסמת ה-Mac.

---

## 🚀 שלב 3: מה עושים בתוך האפליקציה?

1. **התחברות עם Google**: אשרו את הודעת הפרטיות והתחברו עם חשבון ה-Google שלכם (כדי שצוות הסדנה ידע שהמחשב שלכם מוכן ליום ראשון).
2. **סריקה ובחירת רכיבים**: האפליקציה תבדוק מה כבר מותקן ותאפשר לכם לבחור אילו כלים להתקין (מומלץ ליום 1: **Python של הסדנה**, **JupyterLab**, **Arduino IDE**, ו**דרייברים CH340/FTDI**).
   - *טיפ למשתמשי Windows:* קבצי הסדנה יישמרו תחת `C:\BCI_Workshop_2026` כדי למנוע תקלות במחשבים עם שם משתמש בעברית.
3. **הרצת בדיקת מערכת (Diagnostics)**: לחצו על **"הרץ בדיקות מערכת עכשיו"**, ולאחר מכן על **"שלח דוח אבחון למרצה"**.
   - אם קופץ חלון של *Windows Firewall* או בקשת *מיקרופון* ב-Mac, אשרו אותו.
   - אם נתקעתם בכל שלב, לחצו על **"נתקעתי — שלח דוח תקלה למרצה"** ותארו בקצרה מה קרה.

---

## 🧰 גיבוי ידני ליום 1 (Manual Fallback — במקרה שאפליקציית ההתקנה נחסמה)

אם מסיבה כלשהי המחשב שלכם לא מצליח להריץ את ה-Hub, אין מה לדאוג — **אף סטודנט לא ייתקע בבוקר יום ראשון**. התקינו ידנית את 3 הרכיבים הבסיסיים ליום 1:

1. **Arduino IDE 2.3.4**:
   - [הורדה ל-Windows (64-bit)](https://downloads.arduino.cc/arduino-ide/arduino-ide_2.3.4_Windows_64bit.exe)
   - [הורדה ל-Mac (Apple Silicon M1–M4)](https://downloads.arduino.cc/arduino-ide/arduino-ide_2.3.4_macOS_arm64.dmg) | [Mac Intel](https://downloads.arduino.cc/arduino-ide/arduino-ide_2.3.4_macOS_64bit.dmg)
2. **דרייבר USB ללוחות Arduino (CH340)**:
   - [הורדת דרייבר CH340 ל-Windows](https://www.wch-ic.com/downloads/CH341SER_EXE.html) | [הורדה ל-Mac](https://www.wch-ic.com/downloads/CH34XSER_MAC_ZIP.html)
3. **קבצי התרגול של הסדנה**:
   - הורידו ישירות כ-ZIP ממאגר GitHub: **[הורדת כל קבצי הסדנה (ZIP)](https://github.com/urikorisky/BIU_BCI_Workshop_2026_StudentResources/archive/refs/heads/main.zip)**

---

## English Quick Guide

1. Download the latest **BCI Workshop Hub** installer for Windows (`.exe`) or macOS (`.dmg`) from **[GitHub Releases](https://github.com/urikorisky/BCI_Workshop_2026_Hub/releases/latest)**.
2. **First-launch security prompt**:
   - **Windows**: Click *More info → Run anyway* on SmartScreen.
   - **macOS**: Drag into *Applications*, try opening once, then go to *System Settings → Privacy & Security → Open Anyway*.
3. Sign in with Google, review the pre-scan, install the optional workshop tools (recommended for Day 1: Workshop Python, JupyterLab, Arduino IDE, and CH340/FTDI drivers), run **System Verification**, and click **Send Diagnostic Report to Instructor** before Sunday 09:00.
4. **Day 1 Manual Fallback** (if needed): Use the direct Arduino IDE, CH340 driver, and StudentResources ZIP links above.
