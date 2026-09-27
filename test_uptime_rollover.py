import unittest


TICKS_PERIOD = 1 << 30
TICKS_MAX = TICKS_PERIOD - 1
TICKS_HALFPERIOD = TICKS_PERIOD // 2


def ticks_diff(current, previous):
    diff = (current - previous + TICKS_HALFPERIOD) % TICKS_PERIOD - TICKS_HALFPERIOD
    return diff


class ExtendedUptime:
    def __init__(self, initial_tick=0):
        self._uptime_last_tick = initial_tick
        self._uptime_ms = 0

    def sample(self, now):
        elapsed = ticks_diff(now, self._uptime_last_tick)
        self._uptime_ms += elapsed
        self._uptime_last_tick = now
        return self._uptime_ms


class ExtendedUptimeTests(unittest.TestCase):
    def test_simple_progress(self):
        clock = ExtendedUptime(initial_tick=1000)
        self.assertEqual(clock.sample(1500), 500)
        self.assertEqual(clock.sample(2000), 1000)

    def test_crosses_raw_ticks_rollover(self):
        clock = ExtendedUptime(initial_tick=TICKS_MAX - 100)
        self.assertEqual(clock.sample(TICKS_MAX - 50), 50)
        self.assertEqual(clock.sample(25), 126)
        self.assertEqual(clock.sample(125), 226)

    def test_many_rollovers_when_sampled_frequently(self):
        clock = ExtendedUptime(initial_tick=0)
        raw_tick = 0
        expected = 0
        step = 6 * 60 * 60 * 1000  # 6 hours; safely less than half the tick period

        for _ in range(100):
            expected += step
            raw_tick = (raw_tick + step) % TICKS_PERIOD
            self.assertEqual(clock.sample(raw_tick), expected)

        self.assertGreater(expected, TICKS_PERIOD * 2)

    def test_log_timestamps_remain_monotonic_across_rollover(self):
        clock = ExtendedUptime(initial_tick=TICKS_MAX - 10)
        timestamps = [
            clock.sample(TICKS_MAX - 5),
            clock.sample(2),
            clock.sample(100),
        ]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertEqual(timestamps, [5, 13, 111])


if __name__ == '__main__':
    unittest.main()
