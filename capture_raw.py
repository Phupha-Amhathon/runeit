"""
Quick ground-truth capture: logs raw bytes from the board's UART straight
to a file, bypassing any web terminal. Use this to check whether a
password comes back shorter than requested because of a real firmware
bug, or because a browser-based terminal is dropping bytes under a fast
burst (the current leading suspect).

Usage:
    python3 capture_raw.py /dev/ttyACM0    # or your board's port

Then interact with the device normally (answer y/n, type a length) in
this same terminal - your keystrokes are forwarded to the board, and
everything the board sends back is both printed and saved to
capture_raw.log. Ctrl-C to stop.
"""
import sys
import threading

import serial

def main():
    if len(sys.argv) != 2:
        print("usage: python3 capture_raw.py <serial-port>")
        sys.exit(1)

    port = sys.argv[1]
    ser = serial.Serial(port, 115200, timeout=0.1)
    log = open("capture_raw.log", "ab")

    def reader():
        while True:
            chunk = ser.read(4096)
            if chunk:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                log.write(chunk)
                log.flush()

    threading.Thread(target=reader, daemon=True).start()

    print(f"Connected to {port} at 115200. Type to send, Ctrl-C to quit.")
    try:
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            ser.write(line.encode())
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
        log.close()

if __name__ == "__main__":
    main()
