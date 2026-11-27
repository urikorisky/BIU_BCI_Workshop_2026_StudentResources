---
sidebar_position: 5
sidebar_label: "AD8232 ECG Sensor Guide"
---

# Rudimentary Guide: Measuring ECG with the AD8232

The **AD8232** is a single-lead **ECG (Electrocardiogram)** analog front-end module designed to extract tiny cardiac biopotentials from the skin.

Because raw ECG at the skin is only **`~1 mV`** (less than a single step of Arduino's 10-bit ADC!), the AD8232 performs three steps before sending the signal to the Arduino:
1. **Differential amplification:** Subtracts two active electrodes to boost the heartbeat while rejecting common-mode `50 Hz` room hum.
2. **Analog bandpass filtering (`~0.5–40 Hz`):** Removes slow DC drift and high-frequency muscle/RF noise.
3. **Mid-supply DC bias:** Centers `OUTPUT` around `~1.65 V` (`3.3 V / 2`), so both positive and negative ECG waves fit inside Arduino's `0–5 V` analog input range.

---

## ⚠️ Mandatory Safety Rules (Read Before Connecting!)

- **Battery power only:** Whenever electrodes are attached to a person, **unplug the laptop from its mains charger** (run on battery only).
- **`3.3V` power only:** The AD8232 is a **`3.3V`** chip—connecting `VIN` to `5V` can damage the board.

---

## 1. Wiring to Arduino UNO

| AD8232 Pin | Arduino UNO Pin | Purpose |
| :--- | :--- | :--- |
| **`3.3V`** | `3.3V` | Power supply (do **not** use `5V`!) |
| **`GND`** | `GND` | Common ground |
| **`OUTPUT`** | `A0` | Amplified, filtered analog ECG signal |
| **`LO-`** | `Pin 11` *(optional)* | Leads-off detection |
| **`LO+`** | `Pin 10` *(optional)* | Leads-off detection |
| **`SDN`** | Leave unconnected | Shutdown pin (keep floating) |

---

## 2. Electrode Placement (3 Leads)

Plug the 3-lead cable into the 3.5 mm jack and attach the snap electrodes according to our kit color legend:

- 🔴 **Red (`LA`):** **Left Chest** — below the left collarbone (above the bra line).
- 🟡 **Yellow (`RA`):** **Right Chest** — below the right collarbone (above the bra line).
- 🟢 **Green (`RL` / Reference):** **Right Abdomen** — lower right ribcage / abdomen (below the bra band and above the waistband).

![AD8232 ECG Electrode Placement (Male and Female, Front View)](/img/ad8232_electrode_placement.png)

> **Orientation Note (`R` / `L` — Mirror Image):**  
> The illustration shows a person **facing you**. The subject's **Right side (`R` — Yellow & Green)** appears on the **left** side of the image, and the subject's **Left side (`L` — Red)** appears on the **right** side of the image.  
> When wearing a bra: place the upper pads cleanly above the bra and the green reference pad cleanly below the bra band (above pants waistband), on relaxed skin clear of straps or underwire to minimize EMG/motion noise.

### How This 3-Lead Setup Measures the Heart's Electrical Currents
With each heartbeat, a wave of electrical depolarization sweeps across the heart muscle (from the atria down through the ventricles), creating a changing **electric dipole** inside the chest. That dipole drives weak ionic currents through conductive body tissues, producing tiny voltage differences (`~1 mV`) across the skin:
- **Differential Measurement Pair (`RA` Yellow & `LA` Red):** The AD8232 measures the **voltage difference (`LA − RA`)** across the chest (approximating **Lead I**), capturing the P–QRS–T waveform as the cardiac dipole sweeps across the torso. Because both electrodes pick up nearly identical ambient interference (such as `50 Hz` mains hum), subtracting them cancels common-mode noise while preserving the heart's signal.
- **Reference / Driven Electrode (`RL` Green on Right Abdomen):** Acts as the electrical reference (**Right Leg Drive / RLD**), anchoring the body's baseline potential to the amplifier's `~1.65 V` midpoint and actively suppressing common-mode interference.

---

## 3. Minimal Arduino Sketch

This sketch samples at `~250 Hz` (`115200` baud) and works directly with both Arduino **Serial Plotter** and **`serial_to_lsl.exe`**:

```cpp
const int PIN_ECG = A0;
const int PIN_LO_PLUS = 10;
const int PIN_LO_MINUS = 11;

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LO_PLUS, INPUT);
  pinMode(PIN_LO_MINUS, INPUT);
}

void loop() {
  if (digitalRead(PIN_LO_PLUS) == 1 || digitalRead(PIN_LO_MINUS) == 1) {
    // Leads off: output flat mid-scale placeholder
    Serial.println(512);
  } else {
    int ecg = analogRead(PIN_ECG);
    Serial.println(ecg);
  }
  delay(4); // ~250 Hz
}
```

---

## 4. Quick Check & Troubleshooting

1. Open **Tools → Serial Plotter** and set the baud rate to **`115200`**.
2. Sit still with relaxed arms—you should see sharp **QRS** spikes centered around `~330–350` ADC counts (`~1.65 V`).
3. **Thick `50 Hz` hum:** Make sure the laptop is unplugged from mains power and all three electrode pads have firm skin contact.
