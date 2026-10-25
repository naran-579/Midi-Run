
from adafruit_midi.control_change import ControlChange
from microcontroller import pin

from kmk.extensions.media_keys import MediaKeys
from kmk.extensions.rgb import RGB, AnimationModes
from kmk.keys import KC
from kmk.kmk_keyboard import KMKKeyboard
from kmk.modules.encoder import EncoderHandler
from kmk.modules.midi import MidiKeys
from kmk.modules.potentiometer import PotentiometerHandler
from kmk.scanners.keypad import KeysScanner

from code.touch_midi import TouchMidi

keyboard = KMKKeyboard()
# Create these before the keymap uses KC.MIDI_NOTE / KC.VOLU
midi = MidiKeys()
media_keys = MediaKeys()

# LEDs: rainbow in pad mode, breathing rainbow in ribbon mode
rgb = RGB(
    pixel_pin=pin.GPIO3,
    num_pixels=3,
    val_default=80,
    animation_mode=AnimationModes.RAINBOW,
)


def on_mode_change(ribbon):
    rgb.effect_init = True
    rgb.animation_mode = (
        AnimationModes.BREATHING_RAINBOW if ribbon else AnimationModes.RAINBOW
    )


# Touch pads 1-7 -> C major scale. Don't touch the pads while the board boots!
touch = TouchMidi(
    midi,
    pins=[pin.GPIO13, pin.GPIO12, pin.GPIO11, pin.GPIO10, pin.GPIO9, pin.GPIO8, pin.GPIO7],
    scale=[60, 62, 64, 65, 67, 69, 71],
    toggle_combo=(0, 2),  # hold SW1 + SW3 for 1 s to switch pads <-> ribbon
    on_mode_change=on_mode_change,
    debug=True,  # prints baselines / peaks / velocities to the serial console
)

# Switches SW1-SW3 are keys 0-2 now (the touch pads no longer go through KMK)
keyboard.matrix = [
    KeysScanner([pin.GPIO4, pin.GPIO5, pin.GPIO6], value_when_pressed=False, pull=True),
]
keyboard.keymap = [[
    KC.MIDI_NOTE(36), KC.MIDI_NOTE(38), KC.MIDI_NOTE(42),  # kick, snare, hi-hat
]]

# Rotary encoder -> computer volume (A = TX, B = RX, no button wired)
encoder = EncoderHandler()
encoder.pins = ((pin.GPIO43, pin.GPIO44, None),)
encoder.map = [((KC.VOLD, KC.VOLU, KC.NO),)]

# Sliders -> MIDI CC 1 (mod wheel) and CC 7 (volume)
sliders = PotentiometerHandler()
sliders.pins = (
    (pin.GPIO2, lambda state: midi.send(ControlChange(1, state.position))),
    (pin.GPIO1, lambda state: midi.send(ControlChange(7, state.position))),
)

keyboard.modules = [midi, encoder, sliders, touch]
keyboard.extensions = [media_keys, rgb]

if __name__ == '__main__':
    keyboard.go()