

import touchio
from adafruit_midi.note_off import NoteOff
from adafruit_midi.note_on import NoteOn
from adafruit_midi.pitch_bend import PitchBend
from supervisor import ticks_ms

from kmk.modules import Module

_MASK = 0x1FFFFFFF  # supervisor.ticks_ms() wraps at 2**29
_IDLE, _PENDING, _ACTIVE = 0, 1, 2
_BEND_CENTER = 8192
_NEIGHBOUR_FLOOR = 0.3  # ignore weak crosstalk from neighbouring pads


def _since(t):
    return (ticks_ms() - t) & _MASK


class TouchMidi(Module):
    '''
    Two playing modes, switchable at runtime:

      PAD mode    each pad is an independent note. Velocity comes from how hard
                  the finger lands (peak signal in the first few ms).
      RIBBON mode the seven pads act as one continuous strip. One note at a
                  time; sliding your finger glides the pitch between the scale
                  notes using pitch bend (plus a retrigger when you cross into
                  the next pad, so the bend range never needs to exceed +-2).

    Signals are normalised so 1.0 == "touch threshold". A soft tap peaks around
    1.2-1.5, a firm press maybe 3-5. Turn on debug=True and read the serial
    console to find your own numbers.

    Hold the buttons in `toggle_combo` for `toggle_hold_ms` to switch modes.
    '''

    def __init__(
        self,
        midi,  # the MidiKeys() instance (used for .send())
        pins,  # touch pins, in note order
        scale,  # one MIDI note per pin
        *,
        vel_soft=1.3,  # peak signal that maps to vel_min
        vel_hard=4.0,  # peak signal that maps to vel_max
        vel_min=25,
        vel_max=127,
        vel_window_ms=10,  # sampling window before the note is sent
        touch_delta=None,  # raw counts above baseline = "touched" (None = CircuitPython default)
        release_ratio=0.6,  # release below 60% of the touch level (hysteresis)
        ribbon=False,  # start in ribbon mode?
        bend_range=2.0,  # your synth's pitch-bend range in semitones (+-)
        smoothing=0.4,  # 0..1, lower = smoother but laggier ribbon
        toggle_combo=(0, 2),  # switch indices that must be held together
        toggle_hold_ms=1000,
        on_mode_change=None,  # callback(ribbon: bool), e.g. to change LEDs
        debug=False,
    ):
        self.midi = midi
        self.scale = list(scale)
        self.vel_soft = vel_soft
        self.vel_hard = vel_hard
        self.vel_min = vel_min
        self.vel_max = vel_max
        self.vel_window_ms = vel_window_ms
        self.touch_delta = touch_delta
        self.release_ratio = release_ratio
        self.ribbon = ribbon
        self.bend_range = bend_range
        self.smoothing = smoothing
        self.toggle_combo = tuple(toggle_combo)
        self.toggle_hold_ms = toggle_hold_ms
        self.on_mode_change = on_mode_change
        self.debug = debug

        n = len(pins)
        self.pads = [touchio.TouchIn(p) for p in pins]
        self._base = [0] * n
        self._delta = [0] * n

        # pad-mode state
        self._state = [_IDLE] * n
        self._t0 = [0] * n
        self._peak = [0.0] * n

        # ribbon-mode state
        self._r_state = _IDLE
        self._r_t0 = 0
        self._r_peak = 0.0
        self._r_pos = 0.0
        self._r_idx = 0
        self._r_note = 0
        self._r_vel = 0
        self._r_bend = _BEND_CENTER
        self._r_bend_t = 0

        # mode-toggle gesture
        self._combo_down = {c: False for c in self.toggle_combo}
        self._combo_t0 = None
        self._combo_fired = False

        self.calibrate()

    # ---- calibration -------------------------------------------------------

    def calibrate(self):
        '''Measure the untouched baseline. Keep your fingers off the pads!'''
        for i, pad in enumerate(self.pads):
            total = 0
            for _ in range(16):
                total += pad.raw_value
            base = total / 16
            if self.touch_delta:
                delta = self.touch_delta
            elif self._delta[i]:
                delta = self._delta[i]  # keep the first-run span on re-calibration
            else:
                delta = max(30, pad.threshold - base)
            self._base[i] = base
            self._delta[i] = delta
            pad.threshold = int(base + delta)
            if self.debug:
                print('pad', i, 'baseline', int(base), 'touch delta', int(delta))

    # ---- public API --------------------------------------------------------

    def set_ribbon(self, on):
        self._all_off()
        self.ribbon = on
        if self.debug:
            print('mode:', 'RIBBON' if on else 'PADS')
        if self.on_mode_change:
            self.on_mode_change(on)

    # ---- KMK module hooks --------------------------------------------------

    def during_bootup(self, keyboard):
        return None

    def before_matrix_scan(self, keyboard):
        self._check_toggle()
        if self.ribbon:
            self._update_ribbon()
        else:
            self._update_pads()

    def after_matrix_scan(self, keyboard):
        return None

    def process_key(self, keyboard, key, is_pressed, int_coord):
        if int_coord in self._combo_down:
            self._combo_down[int_coord] = is_pressed
        return key

    def before_hid_send(self, keyboard):
        return None

    def after_hid_send(self, keyboard):
        return None

    def on_powersave_enable(self, keyboard):
        return None

    def on_powersave_disable(self, keyboard):
        return None

    # ---- internals ---------------------------------------------------------

    def _send(self, msg):
        self.midi.send(msg)

    def _read(self):
        return [
            (p.raw_value - b) / d for p, b, d in zip(self.pads, self._base, self._delta)
        ]

    def _velocity(self, peak):
        x = (peak - self.vel_soft) / (self.vel_hard - self.vel_soft)
        x = 0.0 if x < 0 else 1.0 if x > 1 else x
        return int(self.vel_min + x * (self.vel_max - self.vel_min))

    def _check_toggle(self):
        if self.toggle_combo and all(self._combo_down.values()):
            if self._combo_t0 is None:
                self._combo_t0 = ticks_ms()
            elif not self._combo_fired and _since(self._combo_t0) >= self.toggle_hold_ms:
                self._combo_fired = True
                self.set_ribbon(not self.ribbon)
        else:
            self._combo_t0 = None
            self._combo_fired = False

    def _all_off(self):
        for i in range(len(self.pads)):
            if self._state[i] == _ACTIVE:
                self._send(NoteOff(self.scale[i], 0))
            self._state[i] = _IDLE
        if self._r_state == _ACTIVE:
            self._send(NoteOff(self._r_note, 0))
        self._r_state = _IDLE
        if self._r_bend != _BEND_CENTER:
            self._send(PitchBend(_BEND_CENTER))
            self._r_bend = _BEND_CENTER

    # ---- pad mode ----------------------------------------------------------

    def _update_pads(self):
        for i, n in enumerate(self._read()):
            st = self._state[i]
            if st == _IDLE:
                if n >= 1.0:
                    self._state[i] = _PENDING
                    self._t0[i] = ticks_ms()
                    self._peak[i] = n
            elif st == _PENDING:
                if n > self._peak[i]:
                    self._peak[i] = n
                released = n < self.release_ratio
                if released or _since(self._t0[i]) >= self.vel_window_ms:
                    vel = self._velocity(self._peak[i])
                    if self.debug:
                        print('pad', i, 'peak %.2f' % self._peak[i], 'vel', vel)
                    self._send(NoteOn(self.scale[i], vel))
                    self._state[i] = _ACTIVE
                    if released:  # very short tap
                        self._send(NoteOff(self.scale[i], 0))
                        self._state[i] = _IDLE
            else:  # _ACTIVE
                if n < self.release_ratio:
                    self._send(NoteOff(self.scale[i], 0))
                    self._state[i] = _IDLE

    # ---- ribbon mode -------------------------------------------------------

    def _centroid(self, sig, k):
        '''Finger position in pad units (0.0 .. len-1), centred on pad k.'''
        wsum = 0.0
        psum = 0.0
        for j in range(max(0, k - 1), min(len(sig), k + 2)):
            w = sig[j] if j == k else sig[j] - _NEIGHBOUR_FLOOR
            if w > 0:
                wsum += w
                psum += w * j
        if wsum <= 0:
            return float(k)
        return psum / wsum

    def _nearest(self, pos):
        i = int(pos + 0.5)
        return 0 if i < 0 else len(self.scale) - 1 if i >= len(self.scale) else i

    def _pitch_at(self, pos):
        '''Continuous pitch (in semitones) for a position between pads.'''
        s = self.scale
        if pos <= 0:
            return s[0]
        if pos >= len(s) - 1:
            return s[-1]
        i = int(pos)
        return s[i] + (s[i + 1] - s[i]) * (pos - i)

    def _send_bend(self, force=False):
        bend = self._pitch_at(self._r_pos) - self._r_note
        val = _BEND_CENTER + int(bend / self.bend_range * 8191)
        val = 0 if val < 0 else 16383 if val > 16383 else val
        if not force and (
            abs(val - self._r_bend) < 16 or _since(self._r_bend_t) < 4
        ):
            return  # don't flood USB with tiny changes
        self._r_bend = val
        self._r_bend_t = ticks_ms()
        self._send(PitchBend(val))

    def _ribbon_off(self):
        if self._r_state == _ACTIVE:
            self._send(NoteOff(self._r_note, 0))
        self._r_state = _IDLE
        # Bend is left where it is so the release tail doesn't jump in pitch.
        # It is re-sent before the next note and reset when switching modes.

    def _update_ribbon(self):
        sig = self._read()
        top = max(sig)
        k = sig.index(top)
        st = self._r_state

        if st == _IDLE:
            if top >= 1.0:
                self._r_state = _PENDING
                self._r_t0 = ticks_ms()
                self._r_peak = top
                self._r_pos = self._centroid(sig, k)
            return

        if st == _PENDING:
            if top > self._r_peak:
                self._r_peak = top
            self._r_pos = self._centroid(sig, k)
            released = top < self.release_ratio
            if released or _since(self._r_t0) >= self.vel_window_ms:
                self._r_vel = self._velocity(self._r_peak)
                self._r_idx = self._nearest(self._r_pos)
                self._r_note = self.scale[self._r_idx]
                if self.debug:
                    print('ribbon start', self._r_idx, 'vel', self._r_vel)
                self._send_bend(force=True)  # bend first, so the note starts in tune
                self._send(NoteOn(self._r_note, self._r_vel))
                self._r_state = _ACTIVE
                if released:
                    self._ribbon_off()
            return

        # _ACTIVE
        if top < self.release_ratio:
            self._ribbon_off()
            return

        self._r_pos += self.smoothing * (self._centroid(sig, k) - self._r_pos)

        if abs(self._r_pos - self._r_idx) > 0.6:  # crossed into the next pad
            old = self._r_note
            self._r_idx = self._nearest(self._r_pos)
            self._r_note = self.scale[self._r_idx]
            self._send_bend(force=True)
            self._send(NoteOn(self._r_note, self._r_vel))
            self._send(NoteOff(old, 0))
        else:
            self._send_bend()