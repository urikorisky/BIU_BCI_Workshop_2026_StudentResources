"""
Encapsulated Downstream Biofeedback Consumer Example

Demonstrates how a separate Python script (stimulus, game, or Arduino actuator)
reads processed features & events from Biofeedback Studio over LSL without
knowing anything about filtering, calibration, or sensors.

Requirements:
    pip install pylsl
"""

from __future__ import annotations

from pylsl import StreamInlet, resolve_byprop


def main() -> None:
    print("Looking for 'BiofeedbackFeatures' LSL stream...")
    feat_streams = resolve_byprop("name", "BiofeedbackFeatures", timeout=10.0)
    if not feat_streams:
        raise SystemExit("Could not find 'BiofeedbackFeatures'. Start Biofeedback Studio first.")
    feat_inlet = StreamInlet(feat_streams[0])

    ev_streams = resolve_byprop("name", "BiofeedbackEvents", timeout=2.0)
    ev_inlet = StreamInlet(ev_streams[0]) if ev_streams else None
    print("Connected! Press Ctrl+C to stop.")

    try:
        while True:
            sample, _ = feat_inlet.pull_sample(timeout=1.0)
            if sample is not None:
                # Feature order matches biofeedback.models.FEATURE_CHANNELS
                bpm = sample[1]
                eda_tonic = sample[5]
                eda_phasic = sample[6]
                scr_rate = sample[9]
                print(
                    f"BPM={bpm:5.1f} | EDA_tonic={eda_tonic:6.2f} uS | "
                    f"EDA_phasic={eda_phasic:+6.2f} uS | SCR_rate={scr_rate:4.1f}/min"
                )
                # Example biofeedback trigger:
                # if bpm > 85.0: send_arduino_command(...)

            if ev_inlet is not None:
                ev_sample, _ = ev_inlet.pull_sample(timeout=0.0)
                if ev_sample:
                    print(f"  >> EVENT: {ev_sample[0]}")
    except KeyboardInterrupt:
        print("\nDisconnected.")


if __name__ == "__main__":
    main()
