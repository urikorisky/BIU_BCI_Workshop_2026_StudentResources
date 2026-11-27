"""
Arduino Serial (ASCII) -> LabStreamingLayer (LSL) Bridge (GUI)

Launches the tkinter GUI from serial_to_lsl_standalone.py.

Requirements:
    pip install pyserial pylsl
"""

from serial_to_lsl_standalone import main

if __name__ == "__main__":
    main()
