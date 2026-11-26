"""
Mic-to-Arduino Controller (Volume or Pitch -> 0.0 to 1.0 over Serial)
---------------------------------------------------------------------
Reads raw audio from the laptop microphone (bypassing Windows AI noise
cancellation via WASAPI exclusive mode so sustained sounds aren't muted),
maps volume (dB) or pitch (Hz) to 0.0-1.0, and streams it over Serial.

Install dependencies:
    pip install sounddevice numpy pyserial
"""

import time
import numpy as np
import sounddevice as sd

# --- Settings ---
MODE        = "pitch"  # "volume" or "pitch"
SERIAL_PORT = "COM9"    # e.g. "COM9", or None to test without Arduino
BAUD_RATE   = 115200

CHUNK       = 2048      # ~43 ms per chunk (~23 updates/sec)
MIN_DB      = -55       # Quiet threshold -> 0.0 (0 deg)
MAX_DB      = -30       # Loud threshold  -> 1.0 (180 deg)
MIN_HZ      = 150       # Lowest pitch    -> 0.0 (0 deg)
MAX_HZ      = 1200      # Highest pitch   -> 1.0 (180 deg)
GATE_DB     = -60       # Minimum loudness to update pitch
SMOOTHING   = 0.1       # 0.0 = instant, 0.9 = very smooth
PRINT_EVERY = 4         # Print ~5 lines/sec (keeps IDLE fast)


def open_raw_mic():
    """Use WASAPI exclusive mode on Windows to bypass AI noise suppression."""
    try:
        api = next(a for a in sd.query_hostapis() if "WASAPI" in a["name"])
        idx = api["default_input_device"]
        info = sd.query_devices(idx)
        return sd.InputStream(
            device=idx,
            samplerate=int(info["default_samplerate"]),
            channels=info["max_input_channels"],
            blocksize=CHUNK,
            extra_settings=sd.WasapiSettings(exclusive=True),
        )
    except Exception:
        return sd.InputStream(channels=1, blocksize=CHUNK)


ser = None
if SERIAL_PORT:
    import serial
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE)
    time.sleep(2)  # Wait for Arduino auto-reset

value = 0.0
step = 0
peak_hz = 0.0

try:
    with open_raw_mic() as stream:
        freqs = np.fft.rfftfreq(CHUNK, 1 / stream.samplerate)
        pitch_band = (freqs >= MIN_HZ) & (freqs <= MAX_HZ)
        print(f"Listening ({int(stream.samplerate)} Hz) | Mode: {MODE} | Ctrl+C to stop.")

        while True:
            audio, _ = stream.read(CHUNK)
            mono = audio[:, 0]
            db = float(20 * np.log10(np.sqrt(np.mean(mono ** 2)) + 1e-9))

            if MODE == "volume":
                raw = np.clip((db - MIN_DB) / (MAX_DB - MIN_DB), 0.0, 1.0)
            else:  # MODE == "pitch"
                if db < GATE_DB:
                    raw = value  # Hold last angle when quiet
                else:
                    spectrum = np.abs(np.fft.rfft(mono * np.hanning(CHUNK)))
                    peak_hz = float(freqs[pitch_band][np.argmax(spectrum[pitch_band])])
                    raw = np.clip((peak_hz - MIN_HZ) / (MAX_HZ - MIN_HZ), 0.0, 1.0)

            value = SMOOTHING * value + (1 - SMOOTHING) * raw

            if ser:
                ser.write(f"{value:.2f}\n".encode())

            step += 1
            if step % PRINT_EVERY == 0:
                angle = int(value * 180)
                reading = f"{db:5.1f} dB" if MODE == "volume" else f"{peak_hz:6.0f} Hz"
                bar = "#" * int(value * 30)
                print(f"[{MODE}] {reading} -> {angle:3d} deg ({value:.2f}) | {bar:<30}")
except KeyboardInterrupt:
    print("Stopped.")
finally:
    if ser:
        ser.close()
