import json
import os
import tempfile
import unittest

from log_persistence import PersistentLogManager, build_persisted_payload


class FakeTime:
    def __init__(self, now=0):
        self.now = now

    def ticks_ms(self):
        return self.now

    def ticks_diff(self, current, previous):
        return current - previous


class PersistentLogManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.file_path = os.path.join(self.temp_dir.name, 'state_log.json')
        self.backup_path = os.path.join(self.temp_dir.name, 'state_log.bak.json')
        self.fake_time = FakeTime()

    def make_manager(self, **overrides):
        options = {
            'file_path': self.file_path,
            'backup_path': self.backup_path,
            'time_module': self.fake_time,
            'flush_interval_ms': 30000,
            'flush_line_threshold': 20,
            'max_bytes': 262144,
        }
        options.update(overrides)
        return PersistentLogManager(**options)

    def read_payload(self, path=None):
        with open(path or self.file_path) as f:
            return json.load(f)

    def test_flushes_on_interval(self):
        manager = self.make_manager()
        state_log = [{'timestamp': 0, 'event': 'System Start', 'details': 'ok'}]

        self.assertFalse(manager.mark_dirty(state_log))
        self.assertFalse(os.path.exists(self.file_path))

        self.fake_time.now = 30000
        self.assertTrue(manager.maybe_flush(state_log))

        payload = self.read_payload()
        self.assertEqual(payload['entries'][0]['event'], 'System Start')

    def test_flushes_on_line_threshold(self):
        manager = self.make_manager(flush_line_threshold=2)
        state_log = []

        state_log.append({'timestamp': 0, 'event': 'A', 'details': ''})
        self.assertFalse(manager.mark_dirty(state_log))
        self.assertFalse(os.path.exists(self.file_path))

        state_log.append({'timestamp': 5, 'event': 'B', 'details': ''})
        self.assertTrue(manager.mark_dirty(state_log))

        payload = self.read_payload()
        self.assertEqual([entry['event'] for entry in payload['entries']], ['A', 'B'])

    def test_load_entries_hydrates_for_ram_log(self):
        payload = build_persisted_payload(
            [{'timestamp': 1000, 'event': 'Before Reset', 'details': 'saved'}],
            flushed_at_ticks_ms=5000
        )
        with open(self.file_path, 'w') as f:
            json.dump(payload, f)

        manager = self.make_manager()
        hydrated = manager.load_entries()

        self.assertEqual(
            hydrated,
            [{'timestamp': -4000, 'event': 'Before Reset', 'details': 'saved'}]
        )

    def test_preserves_wall_timestamps_for_rendering(self):
        payload = build_persisted_payload(
            [{
                'timestamp': 1500,
                'event': 'Before Reset',
                'details': 'saved',
                'wall_timestamp': 1720000001500
            }],
            flushed_at_ticks_ms=2000
        )
        with open(self.file_path, 'w') as f:
            json.dump(payload, f)

        manager = self.make_manager()
        hydrated = manager.load_entries()

        self.assertEqual(hydrated[0]['timestamp'], -500)
        self.assertEqual(hydrated[0]['wall_timestamp'], 1720000001500)

    def test_rotation_and_size_cap_keep_bounded_checkpoint(self):
        manager = self.make_manager(max_bytes=260)
        state_log = []
        for index in range(6):
            state_log.append({
                'timestamp': index * 10,
                'event': 'Event %s' % index,
                'details': 'x' * 80
            })

        manager.flush(state_log, current_ticks_ms=1000)
        payload = self.read_payload()

        self.assertLessEqual(len(json.dumps(payload)), 260)
        self.assertLess(len(payload['entries']), len(state_log))
        self.assertNotEqual(payload['entries'][0]['event'], 'Event 0')

        state_log.append({'timestamp': 1100, 'event': 'Latest', 'details': 'ok'})
        manager.flush(state_log, current_ticks_ms=1200)
        self.assertTrue(os.path.exists(self.backup_path))

    def test_load_uses_backup_if_current_checkpoint_is_corrupt(self):
        manager = self.make_manager(flush_line_threshold=1)
        state_log = [{'timestamp': 0, 'event': 'Healthy', 'details': 'backup copy'}]
        manager.mark_dirty(state_log)

        state_log.append({'timestamp': 10, 'event': 'Newer', 'details': 'current copy'})
        manager.mark_dirty(state_log)

        with open(self.file_path, 'w') as f:
            f.write('{not-json')

        recovered = manager.load_entries()
        self.assertEqual(recovered[0]['event'], 'Healthy')


if __name__ == '__main__':
    unittest.main()
