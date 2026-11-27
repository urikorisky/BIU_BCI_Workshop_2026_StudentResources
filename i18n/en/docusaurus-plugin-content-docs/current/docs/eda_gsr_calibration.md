---
sidebar_position: 4
sidebar_label: "EDA / GSR Calibration (Conductance in μS)"
---

# EDA / GSR Calibration: Ground-Truthing Readings in Siemens (μS)

The **ODAX EDA Maker** board measures electrodermal activity (EDA / GSR) and outputs an analog voltage (`VOUT`) that the Arduino reads on an analog pin (`0–1023`).

Because raw ADC counts depend on the board's internal gain, a quick calibration with **known resistors** lets you convert any reading into physical skin conductance in **microsiemens (μS)**.

---

## 1. Why the Sensor Is Linear in Conductance (`G = 1/R`)

Internally, the board uses a **Transimpedance Amplifier (TIA)**: it applies a fixed low bias voltage (`V_bias`) across the electrodes and measures the resulting skin current. By Ohm's Law:

```text
I = V_bias / R_skin = V_bias * G_skin
```

Because the TIA converts that current into an output voltage (`V_out = V_0 + k * G`), **within the sensor's linear, unsaturated range (`~0.1 V – 3.5 V`)**, `VOUT` is **directly proportional to conductance `G` (Siemens)**:

- Very high resistance (dry skin / open circuit) → `G ≈ 0 μS` → low voltage.
- Lower resistance (sweat / sympathetic arousal) → higher `G` → higher voltage.

---

## 2. Quick Resistor-Substitution Calibration

To find the exact conversion formula for your specific board:

### Step 1: Pick 4–5 Known Resistors from Your Kit
Compute the conductance of each resistor in **microsiemens (`G_μS = 10^6 / R_Ω`)**:

| Resistor (`R`) | Conductance (`G = 1/R`) | Notes |
| :--- | :--- | :--- |
| Open circuit | **`0.00 μS`** | Electrodes unconnected |
| `1 MΩ` | **`1.00 μS`** | Low end of dry skin |
| `470 kΩ` | **`2.13 μS`** | Typical resting skin |
| `220 kΩ` | **`4.55 μS`** | Typical resting skin |
| `100 kΩ` | **`10.00 μS`** | Moderate–high conductance |
| `47 kΩ` | **`21.28 μS`** | High end of palmar EDA |

### Step 2: Record the Arduino Reading for Each Resistor
1. Wire the ODAX board to Arduino (`VIN` → `5V`, `GND` → `GND`, `VOUT` → `A0`).  
   *(When electrodes contact human skin, run the laptop on battery power—never plugged into a mains charger.)*
2. Connect each resistor in turn **across the two electrode leads (`E+` and `E-`)**.  
   ⚠️ **Never connect either electrode lead to `GND`.**
3. Record the steady Arduino reading for each resistor (`ADC` in `0–1023`, or voltage `V = ADC * 5.0 / 1023`).

### Step 3: Fit a Straight Line
Fit a linear model across your measured points:

```text
G_μS = m * V_out + b    (or: G_μS = m_adc * ADC + b)
```

- Sanity-check linearity: `100 kΩ` (`10 μS`) should sit roughly halfway between open circuit (`0 μS`) and `47 kΩ` (`21.3 μS`).
- Do **not** include a direct short circuit (`0 Ω`) in the linear fit—shorting drives the amplifier into saturation (`~3.78 V` / `~774` ADC counts).

---

## 3. Typical Human EDA Range
Normal resting palmar/finger skin conductance is typically **`1 μS` to `20 μS`**.  
Using the ODAX datasheet's preliminary bench values (`~0.1 V` at `1 MΩ` = `1 μS` and `~1.2 V` at `50 kΩ` = `20 μS`), a skin reading around `1.0 V – 1.2 V` (`~200–250` ADC counts) corresponds to roughly **`16–20 μS`**—right in the normal physiological range.
