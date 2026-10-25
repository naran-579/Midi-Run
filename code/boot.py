# boot.py - runs once at power-up / hard reset (NOT on auto-reload).
# After editing, unplug and replug the board for it to take effect.
import usb_midi
from kmk.bootcfg import bootcfg

# KMK's helper sets up the HID devices and the USB manufacturer/product strings.
# Leaving vid/pid alone keeps the stock CircuitPython IDs.
bootcfg(
    midi=True,
    usb_id={'manufacturer': 'DIY', 'product': 'MiDi RUN'},
)

# Name of the MIDI port as shown in DAWs
try:
    usb_midi.set_names(
        streaming_interface_name='MiDi RUN',
        audio_control_interface_name='MiDi RUN',
        in_jack_name='MiDi RUN In',
        out_jack_name='MiDi RUN',
    )
except Exception as e:  # older CircuitPython without set_names
    print('usb_midi.set_names:', e)