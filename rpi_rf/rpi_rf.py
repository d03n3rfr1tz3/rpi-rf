"""
Sending and receiving 433/315Mhz signals with low-cost GPIO RF Modules on a Raspberry Pi.
"""

import logging
import time
from collections import namedtuple

from RPi import GPIO

MAX_CHANGES = 67

_LOGGER = logging.getLogger(__name__)

Protocol = namedtuple('Protocol',
                      ['pulselength',
                       'sync_high', 'sync_low',
                       'zero_high', 'zero_low',
                       'one_high', 'one_low',
	               'inverted'])
PROTOCOLS = (None,
             Protocol(350, 1, 31, 1, 3, 3, 1, False),		# protocol 1
             Protocol(650, 1, 10, 1, 2, 2, 1, False),		# protocol 2
             Protocol(100, 30, 71, 4, 11, 9, 6, False),		# protocol 3
             Protocol(380, 1, 6, 1, 3, 3, 1, False),		# protocol 4
             Protocol(500, 6, 14, 1, 2, 2, 1, False),		# protocol 5
             Protocol(500, 6, 14, 1, 2, 2, 1, False),		# protocol 5
             Protocol(450, 23, 1, 1, 2, 2, 1, True),		# protocol 6 (HT6P20B)
             Protocol(150, 2, 62, 1, 6, 6, 1, False),		# protocol 7 (HS2303-PT, i. e. used in AUKEY Remote)
             Protocol(200, 3, 130, 7, 16, 3, 16, False),	# protocol 8 (Conrad RS-200 RX)
             Protocol(200, 130, 7, 16, 7, 16, 3, True),		# protocol 9 (Conrad RS-200 TX)
             Protocol(365, 18, 1, 3, 1, 1, 3, True),		# protocol 10 (1ByOne Doorbell)
             Protocol(270, 36, 1, 1, 2, 2, 1, True),		# protocol 11 (HT12E)
             Protocol(320, 36, 1, 1, 2, 2, 1, True),		# protocol 12 (SM5212)
             Protocol(500, 1, 14, 1, 3, 3, 1, False),		# protocol 13 (Blyss Doorbell Ref. DC6-FR-WH 656185)
             Protocol(415, 1, 30, 1, 3, 4, 1, False),		# protocol 14 (sc2260R4)
             Protocol(250, 20, 10, 1, 1, 3, 1, False),		# protocol 15 (Home NetWerks Bathroom Fan Model 6201-500)
             Protocol(80, 3, 25, 3, 13, 11, 5, False),		# protocol 16 (ORNO OR-GB-417GD)
             Protocol(82, 2, 65, 3, 5, 7, 1, False),		# protocol 17 (CLARUS BHC993BF-3)
             Protocol(560, 16, 8, 1, 1, 1, 3, False)		# protocol 18 (NEC)
            )


class RFDevice:
    """Representation of a GPIO RF device."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments
    def __init__(self, gpio,
                 tx_proto=1, tx_pulselength=None, tx_repeat=10, tx_length=24, rx_tolerance=80, tx_inverted=None):
        """Initialize the RF device."""
        self.gpio = gpio
        self.tx_enabled = False
        self.tx_proto = tx_proto
        if tx_pulselength:
            self.tx_pulselength = tx_pulselength
        else:
            self.tx_pulselength = PROTOCOLS[tx_proto].pulselength
        self.tx_repeat = tx_repeat
        self.tx_length = tx_length
        if tx_inverted:
            self.tx_inverted = tx_inverted
        else:
            self.tx_inverted = PROTOCOLS[tx_proto].inverted
        self.rx_enabled = False
        self.rx_tolerance = rx_tolerance
        # internal values
        self._rx_timings = [0] * (MAX_CHANGES + 1)
        self._rx_last_timestamp = 0
        self._rx_change_count = 0
        self._rx_repeat_count = 0
        # successful RX values
        self.rx_code = None
        self.rx_code_timestamp = None
        self.rx_proto = None
        self.rx_bitlength = None
        self.rx_pulselength = None

        GPIO.setmode(GPIO.BCM)
        _LOGGER.debug("Using GPIO " + str(gpio))

    def cleanup(self):
        """Disable TX and RX and clean up GPIO."""
        if self.tx_enabled:
            self.disable_tx()
        if self.rx_enabled:
            self.disable_rx()
        _LOGGER.debug("Cleanup")
        GPIO.cleanup()

    def enable_tx(self):
        """Enable TX, set up GPIO."""
        if self.rx_enabled:
            _LOGGER.error("RX is enabled, not enabling TX")
            return False
        if not self.tx_enabled:
            self.tx_enabled = True
            GPIO.setup(self.gpio, GPIO.OUT)
            _LOGGER.debug("TX enabled")
        return True

    def disable_tx(self):
        """Disable TX, reset GPIO."""
        if self.tx_enabled:
            # set up GPIO pin as input for safety
            GPIO.setup(self.gpio, GPIO.IN)
            self.tx_enabled = False
            _LOGGER.debug("TX disabled")
        return True

    def tx_code(self, code, tx_proto=None, tx_pulselength=None, tx_length=None, tx_inverted=None):
        """
        Send a decimal code.

        Optionally set protocol, pulselength and code length.
        When none given reset to default protocol, default pulselength and set code length to 24 bits.
        """
        if tx_proto:
            self.tx_proto = tx_proto
        else:
            self.tx_proto = 1
        if tx_pulselength:
            self.tx_pulselength = tx_pulselength
        elif not self.tx_pulselength:
            self.tx_pulselength = PROTOCOLS[self.tx_proto].pulselength
        if tx_length:
            self.tx_length = tx_length
        elif self.tx_proto == 6:
            self.tx_length = 32
        elif self.tx_proto == 8:
            self.tx_length = 12
        elif (code > 16777216):
            self.tx_length = 32
        else:
            self.tx_length = 24
        if tx_inverted:
            self.tx_inverted = tx_inverted
        else:
            self.tx_inverted = PROTOCOLS[self.tx_proto].inverted		
        rawcode = format(code, '#0{}b'.format(self.tx_length + 2))[2:]
        if self.tx_proto == 6:
            nexacode = ""
            for b in rawcode:
                if b == '0':
                    nexacode = nexacode + "01"
                if b == '1':
                    nexacode = nexacode + "10"
            rawcode = nexacode
            self.tx_length = 64
        _LOGGER.debug("TX code: " + str(code))
        return self.tx_bin(rawcode)

    def tx_bin(self, rawcode):
        """Send a binary code."""
        _LOGGER.debug("TX bin: " + str(rawcode))
        for _ in range(0, self.tx_repeat):
            if self.tx_proto == 6:
                if not self.tx_sync():
                    return False
            for byte in range(0, self.tx_length):
                if rawcode[byte] == '0':
                    if not self.tx_l0():
                        return False
                else:
                    if not self.tx_l1():
                        return False
            if not self.tx_sync():
                return False

        return True

    def tx_l0(self):
        """Send a '0' bit."""
        if not 0 < self.tx_proto < len(PROTOCOLS):
            _LOGGER.error("Unknown TX protocol")
            return False
        return self.tx_waveform(PROTOCOLS[self.tx_proto].zero_high,
                                PROTOCOLS[self.tx_proto].zero_low)

    def tx_l1(self):
        """Send a '1' bit."""
        if not 0 < self.tx_proto < len(PROTOCOLS):
            _LOGGER.error("Unknown TX protocol")
            return False
        return self.tx_waveform(PROTOCOLS[self.tx_proto].one_high,
                                PROTOCOLS[self.tx_proto].one_low)

    def tx_sync(self):
        """Send a sync."""
        if not 0 < self.tx_proto < len(PROTOCOLS):
            _LOGGER.error("Unknown TX protocol")
            return False
        return self.tx_waveform(PROTOCOLS[self.tx_proto].sync_high,
                                PROTOCOLS[self.tx_proto].sync_low)

    def tx_waveform(self, highpulses, lowpulses):
        """Send basic waveform."""
        if not self.tx_enabled:
            _LOGGER.error("TX is not enabled, not sending data")
            return False
			
        if not self.tx_inverted:
            GPIO.output(self.gpio, GPIO.HIGH)
            self._sleep((highpulses * self.tx_pulselength) / 1000000)
            GPIO.output(self.gpio, GPIO.LOW)
            self._sleep((lowpulses * self.tx_pulselength) / 1000000)
        else:
            GPIO.output(self.gpio, GPIO.LOW)
            self._sleep((highpulses * self.tx_pulselength) / 1000000)
            GPIO.output(self.gpio, GPIO.HIGH)
            self._sleep((lowpulses * self.tx_pulselength) / 1000000)		
        return True

    def enable_rx(self):
        """Enable RX, set up GPIO and add event detection."""
        if self.tx_enabled:
            _LOGGER.error("TX is enabled, not enabling RX")
            return False
        if not self.rx_enabled:
            self.rx_enabled = True
            GPIO.setup(self.gpio, GPIO.IN)
            GPIO.add_event_detect(self.gpio, GPIO.BOTH)
            GPIO.add_event_callback(self.gpio, self.rx_callback)
            _LOGGER.debug("RX enabled")
        return True

    def disable_rx(self):
        """Disable RX, remove GPIO event detection."""
        if self.rx_enabled:
            GPIO.remove_event_detect(self.gpio)
            self.rx_enabled = False
            _LOGGER.debug("RX disabled")
        return True

    # pylint: disable=unused-argument
    def rx_callback(self, gpio):
        """RX callback for GPIO event detection. Handle basic signal detection."""
        timestamp = int(time.perf_counter() * 1000000)
        duration = timestamp - self._rx_last_timestamp

        if duration > 5000:
            if duration - self._rx_timings[0] < 200:
                self._rx_repeat_count += 1
                self._rx_change_count -= 1
                if self._rx_repeat_count == 2:
                    for pnum in range(1, len(PROTOCOLS)):
                        if self._rx_waveform(pnum, self._rx_change_count, timestamp):
                            _LOGGER.debug("RX code " + str(self.rx_code))
                            break
                    self._rx_repeat_count = 0
            self._rx_change_count = 0

        if self._rx_change_count >= MAX_CHANGES:
            self._rx_change_count = 0
            self._rx_repeat_count = 0
        self._rx_timings[self._rx_change_count] = duration
        self._rx_change_count += 1
        self._rx_last_timestamp = timestamp

    def _rx_waveform(self, pnum, change_count, timestamp):
        """Detect waveform and format code."""
        code = 0
        delay = int(self._rx_timings[0] / PROTOCOLS[pnum].sync_low)
        delay_tolerance = delay * self.rx_tolerance / 100

        for i in range(1, change_count, 2):
            if (self._rx_timings[i] - delay * PROTOCOLS[pnum].zero_high < delay_tolerance and
                    self._rx_timings[i+1] - delay * PROTOCOLS[pnum].zero_low < delay_tolerance):
                code <<= 1
            elif (self._rx_timings[i] - delay * PROTOCOLS[pnum].one_high < delay_tolerance and
                  self._rx_timings[i+1] - delay * PROTOCOLS[pnum].one_low < delay_tolerance):
                code <<= 1
                code |= 1
            else:
                return False

        if self._rx_change_count > 6 and code != 0:
            self.rx_code = code
            self.rx_code_timestamp = timestamp
            self.rx_bitlength = int(change_count / 2)
            self.rx_pulselength = delay
            self.rx_proto = pnum
            return True

        return False
           
    def _sleep(self, delay):      
        _delay = delay / 100
        end = time.time() + delay - _delay
        while time.time() < end:
            time.sleep(_delay)
