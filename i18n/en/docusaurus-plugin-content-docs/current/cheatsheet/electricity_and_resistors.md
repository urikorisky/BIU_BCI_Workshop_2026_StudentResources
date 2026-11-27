---
sidebar_position: 1
sidebar_label: "Basic Electricity & Resistor Color Code"
slug: /cheatsheet/electricity-and-resistors
unlisted: true
---

# Cheatsheet: Basic Electricity & 5-Band Resistors

A quick reference for the workshop's core electricity formulas and 5-band resistor color decoding.

---

## 1. Basic Electricity Equations

### Ohm's Law
Relates voltage (`V`, Volts), current (`I`, Amperes), and resistance (`R`, Ohms `Ω`):

```text
V = I * R      |      I = V / R      |      R = V / I
```

*(Always convert prefixes to base units before plugging in: `1 mA = 0.001 A`, `1 kΩ = 1,000 Ω`, `1 MΩ = 1,000,000 Ω`.)*

### Power & Resistor Heat Limit
Power is measured in Watts (`W`, energy per second):

```text
P = V * I      |      P = I^2 * R      |      P = V^2 / R
```

- **Kit safety limit:** Standard resistors in your kit are rated for **¼ Watt (`P_max = 0.25 W`)**. Exceeding `0.25 W` causes the resistor to overheat or burn.

### Series vs. Parallel Connections
- **Parallel:** Every branch sees the same voltage (`V_branch = V_supply`), and total current is the sum of branch currents (`I_total = I_1 + I_2 + ...`).
- **Series:** Current is identical through every component in the branch (`I_branch = I_1 = I_2`), and voltage drops add up (`V_branch = V_1 + V_2 + ...`).

### LED Current-Limiting Resistor Formula
```text
R_calc = (V_supply - V_LED) / I_target
```
- Example on Arduino (`V_supply = 5 V`, standard LED drop `V_LED ≈ 2.0 V`, target `10–15 mA`):  
  `V_R = 3.0 V` → when picking a physical resistor from your kit, **always round UP** (e.g., `220 Ω` or `330 Ω`).

### Conductance (for Day 2 EDA / GSR)
Conductance (`G`, in Siemens `S`) is the reciprocal of resistance:

```text
G = 1 / R      |      G_μS = 1,000,000 / R_Ω
```

### Common Metric Prefixes
| Prefix | Meaning | Multiplier | Example |
| :--- | :--- | :--- | :--- |
| `m` (milli) | one-thousandth | `10^-3` (`0.001`) | `20 mA = 0.02 A` |
| `μ` (micro) | one-millionth | `10^-6` (`0.000001`) | `10 μS = 0.00001 S` |
| `k` (kilo) | one thousand | `10^3` (`1,000`) | `10 kΩ = 10,000 Ω` |
| `M` (mega) | one million | `10^6` (`1,000,000`) | `1 MΩ = 1,000,000 Ω` |

---

## 2. 5-Band Resistor Color Legend (IEC 60062)

On the blue 5-band metal-film resistors in your kit:
- **Bands 1, 2, 3:** First three significant digits (`d1 d2 d3`).
- **Band 4:** Multiplier (`×M`).
- **Band 5:** Tolerance (`±%`).

> **Which end is Band 1?**  
> Look for the **slightly wider gap between Band 4 and Band 5** (Band 5 sits closer to its end cap). Read starting from the end where the bands are packed closer together.

| Color | Band 1 (100s) | Band 2 (10s) | Band 3 (1s) | Band 4 (Multiplier) | Band 5 (Tolerance) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| <span className="resistor-swatch" style={{backgroundColor: '#1c1917'}} /> **Black** | — | `0` | `0` | `×1 Ω` | — |
| <span className="resistor-swatch" style={{backgroundColor: '#78350f'}} /> **Brown** | `1` | `1` | `1` | `×10 Ω` | `±1%` |
| <span className="resistor-swatch" style={{backgroundColor: '#dc2626'}} /> **Red** | `2` | `2` | `2` | `×100 Ω` | `±2%` |
| <span className="resistor-swatch" style={{backgroundColor: '#ea580c'}} /> **Orange** | `3` | `3` | `3` | `×1 kΩ` | — |
| <span className="resistor-swatch" style={{backgroundColor: '#eab308'}} /> **Yellow** | `4` | `4` | `4` | `×10 kΩ` | — |
| <span className="resistor-swatch" style={{backgroundColor: '#16a34a'}} /> **Green** | `5` | `5` | `5` | `×100 kΩ` | `±0.5%` |
| <span className="resistor-swatch" style={{backgroundColor: '#2563eb'}} /> **Blue** | `6` | `6` | `6` | `×1 MΩ` | `±0.25%` |
| <span className="resistor-swatch" style={{backgroundColor: '#7c3aed'}} /> **Violet** | `7` | `7` | `7` | `×10 MΩ` | `±0.1%` |
| <span className="resistor-swatch" style={{backgroundColor: '#6b7280'}} /> **Grey** | `8` | `8` | `8` | `×100 MΩ` | `±0.05%` |
| <span className="resistor-swatch" style={{backgroundColor: '#f8fafc'}} /> **White** | `9` | `9` | `9` | `×1 GΩ` | — |
| <span className="resistor-swatch" style={{backgroundColor: '#d97706'}} /> **Gold** | — | — | — | `×0.1 Ω` | `±5%` |
| <span className="resistor-swatch" style={{backgroundColor: '#94a3b8'}} /> **Silver** | — | — | — | `×0.01 Ω` | `±10%` |

---

## 3. Worked Example on One Resistor: `Brown – Black – Black – Yellow – Brown`

Read from left to right (the single brown band separated by a wider gap on the right is Band 5):

1. **Band 1 (Brown):** Hundreds digit = **`1`**
2. **Band 2 (Black):** Tens digit = **`0`**
3. **Band 3 (Black):** Ones digit = **`0`**  
   → Together, the 3-digit base is **`100`**
4. **Band 4 (Yellow):** Multiplier = **`×10,000 Ω` (`×10 kΩ`)**
5. **Band 5 (Brown):** Tolerance = **`±1%`**

```text
R = 100 × 10,000 Ω = 1,000,000 Ω = 1 MΩ ±1%
```

*(Quick check on your Day 1 LED resistor: **Red – Red – Black – Black – Brown** → `220 × 1 Ω = 220 Ω ±1%`.)*

---

## 4. Interactive Interpreter

Pick the 5 bands from the dropdowns or type a 5-color series (in English or Hebrew) to compute the resistance in Ohms:

<ResistorInterpreter />
