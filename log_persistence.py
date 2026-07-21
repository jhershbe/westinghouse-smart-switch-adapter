try:
    import ujson as json
except ImportError:
    import json

import os
import time

PERSISTED_LOG_FILE = 'state_log.json'
PERSISTED_LOG_BACKUP_FILE = 'state_log.bak.json'
PERSISTED_LOG_FLUSH_INTERVAL_MS = 30000
PERSISTED_LOG_FLUSH_LINE_THRESHOLD = 20
PERSISTED_LOG_MAX_BYTES = 262144


def normalize_log_entry(entry):
    if isinstance(entry, dict):
        normalized = {
            'timestamp': int(entry.get('timestamp', 0)),
            'event': entry.get('event', ''),
            'details': entry.get('details', '')
        }
        wall_timestamp = entry.get('wall_timestamp')
        if wall_timestamp is not None:
            normalized['wall_timestamp'] = int(wall_timestamp)
        return normalized

    timestamp, event, details = entry
    return {
        'timestamp': int(timestamp),
        'event': event,
        'details': details
    }


def build_persisted_payload(entries, flushed_at_ticks_ms, max_bytes=PERSISTED_LOG_MAX_BYTES):
    payload = {
        'version': 1,
        'flushed_at_ticks_ms': int(flushed_at_ticks_ms),
        'entries': []
    }

    for entry in entries:
        normalized = normalize_log_entry(entry)
        persisted_entry = {
            'age_ms': max(0, int(flushed_at_ticks_ms) - normalized['timestamp']),
            'event': normalized['event'],
            'details': normalized['details']
        }
        if 'wall_timestamp' in normalized:
            persisted_entry['wall_timestamp'] = normalized['wall_timestamp']
        payload['entries'].append(persisted_entry)

    while payload['entries'] and len(json.dumps(payload)) > max_bytes:
        payload['entries'].pop(0)

    return payload


def hydrate_persisted_entries(payload):
    flushed_at_ticks_ms = int(payload.get('flushed_at_ticks_ms', 0))
    hydrated = []

    for entry in payload.get('entries', []):
        age_ms = entry.get('age_ms')
        if age_ms is None:
            age_ms = max(0, flushed_at_ticks_ms - int(entry.get('timestamp', 0)))

        hydrated_entry = {
            # Restore as a negative offset from the current boot so the existing
            # web log view can render pre-reset events before time sync.
            'timestamp': -int(age_ms),
            'event': entry.get('event', ''),
            'details': entry.get('details', '')
        }
        wall_timestamp = entry.get('wall_timestamp')
        if wall_timestamp is not None:
            hydrated_entry['wall_timestamp'] = int(wall_timestamp)
        hydrated.append(hydrated_entry)

    return hydrated


class PersistentLogManager:
    def __init__(
        self,
        file_path=PERSISTED_LOG_FILE,
        backup_path=PERSISTED_LOG_BACKUP_FILE,
        flush_interval_ms=PERSISTED_LOG_FLUSH_INTERVAL_MS,
        flush_line_threshold=PERSISTED_LOG_FLUSH_LINE_THRESHOLD,
        max_bytes=PERSISTED_LOG_MAX_BYTES,
        time_module=time,
        os_module=os
    ):
        self.file_path = file_path
        self.backup_path = backup_path
        self.flush_interval_ms = int(flush_interval_ms)
        self.flush_line_threshold = int(flush_line_threshold)
        self.max_bytes = int(max_bytes)
        self.time = time_module
        self.os = os_module
        self.pending_lines = 0
        self.last_flush_ticks_ms = self._ticks_ms()

    def load_entries(self):
        payload = self._read_payload(self.file_path)
        if payload is None:
            payload = self._read_payload(self.backup_path)
        if payload is None:
            return []
        return hydrate_persisted_entries(payload)

    def mark_dirty(self, state_log):
        self.pending_lines += 1
        if self.pending_lines >= self.flush_line_threshold:
            return self.flush(state_log)
        return False

    def maybe_flush(self, state_log):
        if self.pending_lines <= 0:
            return False

        current_ticks_ms = self._ticks_ms()
        if self._ticks_diff(current_ticks_ms, self.last_flush_ticks_ms) < self.flush_interval_ms:
            return False

        return self.flush(state_log, current_ticks_ms=current_ticks_ms)

    def flush(self, state_log, current_ticks_ms=None):
        if current_ticks_ms is None:
            current_ticks_ms = self._ticks_ms()

        payload = build_persisted_payload(state_log, current_ticks_ms, self.max_bytes)
        temp_path = self.file_path + '.tmp'

        with open(temp_path, 'w') as f:
            json.dump(payload, f)

        if self._path_exists(self.backup_path):
            self.os.remove(self.backup_path)
        if self._path_exists(self.file_path):
            self.os.rename(self.file_path, self.backup_path)
        self.os.rename(temp_path, self.file_path)

        self.pending_lines = 0
        self.last_flush_ticks_ms = int(current_ticks_ms)
        return True

    def _read_payload(self, path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return None

    def _path_exists(self, path):
        try:
            self.os.stat(path)
            return True
        except Exception:
            return False

    def _ticks_ms(self):
        if hasattr(self.time, 'ticks_ms'):
            return int(self.time.ticks_ms())
        return int(self.time.time() * 1000)

    def _ticks_diff(self, current, previous):
        if hasattr(self.time, 'ticks_diff'):
            return int(self.time.ticks_diff(current, previous))
        return int(current - previous)
