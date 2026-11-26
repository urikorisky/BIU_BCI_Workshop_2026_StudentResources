# Getting Started

Welcome to the **BCI Workshop 2026**!  
To avoid spending classroom time on installations, please complete the setup process **before Sunday at 09:00**.

---

## 📥 Step 1: Download BCI Workshop Hub

**BCI Workshop Hub** is a bilingual (Hebrew/English) onboarding app that scans your computer, offers guided optional installations for the workshop tools, verifies everything **before any hardware is connected**, and sends a readiness report to the instructor.

> 🔗 **Official Download Page (Latest Release):**  
> **[Download BCI Workshop Hub from GitHub Releases](https://github.com/urikorisky/BCI_Workshop_2026_Hub/releases/latest)**

* **Windows (64-bit):** Download and run the `.exe` (or `.msi`) installer.
* **macOS (Apple Silicon M1–M4 / Intel):** Download and open the `.dmg` file.

---

## 🛡️ Step 2: First-Launch Security Prompt

Because this is an internal workshop tool, your operating system will show a one-time security prompt on first launch — this is expected:

### Windows (SmartScreen)
1. If you see **"Windows protected your PC"**:
2. Click **More info**.
3. Click **Run anyway**.

### macOS (Gatekeeper)
1. Open the `.dmg` and drag **BCI Workshop Hub** into **Applications**.
2. Try opening the app from Applications once (if blocked, click **Done / OK**).
3. Open **System Settings** → **Privacy & Security**.
4. Scroll down to the *BCI Workshop Hub* notice, click **Open Anyway**, and confirm with your Mac password.

---

## 🚀 Step 3: Inside the App

1. **Sign in with Google**: Review the privacy notice and sign in with your Google account so the workshop team knows your machine is ready.
2. **Pre-Scan & Tool Selection**: Review what is already installed and choose which tools to install (recommended for Day 1: **Workshop Python**, **JupyterLab**, **Arduino IDE**, and **CH340/FTDI drivers**).
   - *Windows tip:* Workshop files are stored under `C:\BCI_Workshop_2026` to avoid issues with non-ASCII user folder names.
3. **Run System Verification (Diagnostics)**: Click **Run System Verification**, then click **Send Diagnostic Report to Instructor**.
   - Approve any *Windows Firewall* or macOS *Microphone* prompts if they appear.
   - If you get stuck at any step, click **I'm Stuck — Send Issue Report** and briefly describe what happened.

---

## 🧰 Day 1 Manual Fallback (If the Hub Is Blocked)

If your machine cannot run the Hub for any reason, you can install the 3 core Day 1 components manually:

1. **Arduino IDE 2.3.4**:
   - [Windows (64-bit)](https://downloads.arduino.cc/arduino-ide/arduino-ide_2.3.4_Windows_64bit.exe)
   - [macOS (Apple Silicon M1–M4)](https://downloads.arduino.cc/arduino-ide/arduino-ide_2.3.4_macOS_arm64.dmg) | [macOS Intel](https://downloads.arduino.cc/arduino-ide/arduino-ide_2.3.4_macOS_64bit.dmg)
2. **Arduino USB Driver (CH340)**:
   - [Windows CH340 Driver](https://www.wch-ic.com/downloads/CH341SER_EXE.html) | [macOS CH340 Driver](https://www.wch-ic.com/downloads/CH34XSER_MAC_ZIP.html)
3. **Workshop Code & Exercises**:
   - Download directly as a ZIP from GitHub: **[Download All Workshop Files (ZIP)](https://github.com/urikorisky/BIU_BCI_Workshop_2026_StudentResources/archive/refs/heads/main.zip)**
