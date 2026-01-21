import machine
import time
import asyncio
import network
import ujson
import gc

CONFIG_FILE = 'config.json'

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            config = ujson.load(f)
        # Ensure values are ints
        config = {k: int(v) for k, v in config.items()}
        return config
    except:
        return {
            "maintenance_interval_days": 7,
            "maintenance_duration_minutes": 10,
            "cool_down_duration_minutes": 15,
            "maintenance_start_hour": 12,
            "maintenance_start_minute": 0
        }

def save_config(config):
    with open(CONFIG_FILE, 'w') as f:
        ujson.dump(config, f)

config = load_config()

# Fake RTC
rtc_synced = True  # Always consider synced, starting from 0
rtc_base_minutes = 0
rtc_base_ticks = time.ticks_ms()

def get_current_minutes():
    elapsed_minutes = (time.ticks_ms() - rtc_base_ticks) // 60000
    return (rtc_base_minutes + elapsed_minutes) % 1440

in_run_request = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_UP)
led_run_request = machine.Pin(16, machine.Pin.OUT)
led_running = machine.Pin(17, machine.Pin.OUT)
led_cool_down = machine.Pin(18, machine.Pin.OUT)
led_maintenance = machine.Pin(19, machine.Pin.OUT)
in_run_sense = machine.Pin(27, machine.Pin.IN, machine.Pin.PULL_UP)
relay_start_gen = machine.Pin(32, machine.Pin.OUT)
relay_kill_gen = machine.Pin(33, machine.Pin.OUT)

# Initialize relays to safe state (off)
relay_start_gen.value(0)
relay_kill_gen.value(0)

# WiFi Access Point Setup
ap = network.WLAN(network.AP_IF)
ap.config(hostname='gencontroller')
ap.config(essid='GenController', password='westinghouse', authmode=network.AUTH_WPA_WPA2_PSK)
ap.active(True)

# Wait for AP to be active
while not ap.active():
    time.sleep(0.1)
print('AP active, IP:', ap.ifconfig()[0])
print('Connect to: http://gencontroller.local')

# Import Microdot after WiFi is initialized
from microdot import Microdot, Response, send_file

# Set up Microdot
app = Microdot()
Response.default_content_type = 'application/json'

class GeneratorState:
    IDLE = "idle"                          # Not running, monitoring for requests/maintenance
    STARTING = "starting"                   # Activating start relay (pulse)
    CONFIRM_STARTED = "confirm_started"     # Waiting to see if generator started after pulse
    RUNNING = "running"                     # Running (normal or maintenance)
    COOL_DOWN = "cool_down"                 # Running but no request; waiting to stop
    STOPPING = "stopping"                   # Activating kill relay with delay

class GeneratorController:
    def __init__(self, sensor_manager, config):
        self.sensor_manager = sensor_manager
        self.maintenance_interval_days = config.get("maintenance_interval_days", 7)
        self.maintenance_duration_minutes = config.get("maintenance_duration_minutes", 10)
        self.maintenance_duration = self.maintenance_duration_minutes * 60 * 1000
        self.cool_down_duration_minutes = config.get("cool_down_duration_minutes", 15)
        self.cool_down_duration = self.cool_down_duration_minutes * 60 * 1000
        self.maintenance_start_hour = config.get("maintenance_start_hour", 12)
        self.maintenance_start_minute = config.get("maintenance_start_minute", 0)

        # State variables
        self.days_until_maintenance = self.maintenance_interval_days
        self.maintenance_check_time = time.ticks_ms()
        self.maintenance_end = 0
        self.maintenance_active = False
        self.cool_down_end = 0
        self.cool_down_active = False
        self.kill_gen = False

        # Statistics and tracking
        self.start_attempts = 0
        self.detected_runs = 0
        self.last_start_request = 0
        self.last_kill_action = 0
        self.last_run_sense_start = 0
        self.last_run_sense_end = 0

        # Timing and relays
        self.start_relay_end_time = 0
        self.pulse_cooldown = 0

        self.state_log = []
        self.max_log_entries = 50

        self.prev_state = {
            'running': False,
            'run_request': False,
            'cool_down': False,
            'maintenance': False,
            'days': self.maintenance_interval_days,
            'kill_relay': False,
            'start_relay': False,
            'maintenance_reset': False
        }

        self.current_state = None
        self.state_map = {
            GeneratorState.IDLE: IdleState(self),
            GeneratorState.STARTING: StartingState(self),
            GeneratorState.CONFIRM_STARTED: ConfirmStartedState(self),
            GeneratorState.RUNNING: RunningState(self),
            GeneratorState.COOL_DOWN: CoolDownState(self),
            GeneratorState.STOPPING: StoppingState(self),
        }
        self.transition_to(GeneratorState.IDLE)

    def log_state_change(self, event, details=''):
        """Log a state transition with timestamp"""
        entry = (time.ticks_ms(), event, details)
        self.state_log.append(entry)
        if len(self.state_log) > self.max_log_entries:
            self.state_log.pop(0)
        print(f"[{entry[0]}] {event}: {details}")

    def transition_to(self, state):
        if self.current_state:
            self.current_state.on_exit()
        self.current_state = self.state_map[state]
        self.current_state.on_enter()

    def get_status_generator(self):
        current_time = time.ticks_ms()
        current_minutes = get_current_minutes()
        configured_minutes = self.maintenance_start_hour * 60 + self.maintenance_start_minute
        minutes_until_start = (configured_minutes - current_minutes + 1440) % 1440
        total_minutes = self.days_until_maintenance * 1440 + minutes_until_start

        yield '{'
        yield '"running":' + ('true' if self.sensor_manager.is_running_debounced() else 'false')
        yield ',"run_request":' + ('true' if self.sensor_manager.is_request_run() else 'false')
        yield ',"cool_down":' + ('true' if self.cool_down_active else 'false')
        yield ',"cool_down_remaining":' + str(max(0, self.cool_down_end - current_time))
        yield ',"maintenance":' + ('true' if self.maintenance_active else 'false')
        yield ',"maintenance_remaining":' + str(max(0, self.maintenance_end - current_time))
        yield ',"maintenance_countdown":{"days":' + str(total_minutes // 1440)
        yield ',"hours":' + str((total_minutes % 1440) // 60)
        yield ',"minutes":' + str(total_minutes % 60)
        yield ',"total_minutes":' + str(total_minutes) + '}'
        yield ',"current_time_minutes":' + str(current_minutes)
        yield ',"rtc_synced":true'
        yield ',"start_attempts":' + str(self.start_attempts)
        yield ',"detected_runs":' + str(self.detected_runs)
        yield ',"last_start_request":' + str(self.last_start_request)
        yield ',"last_kill_action":' + str(self.last_kill_action)
        yield ',"last_run_sense_start":' + str(self.last_run_sense_start)
        yield ',"last_run_sense_end":' + str(self.last_run_sense_end)
        yield '}'

    def is_maintenance_starting(self):
        if self.days_until_maintenance > 0:
            return False
        current_minutes = get_current_minutes()
        configured_minutes = self.maintenance_start_hour * 60 + self.maintenance_start_minute
        return current_minutes >= configured_minutes

    def update(self):
        # Update pulse cooldown
        if self.pulse_cooldown > 0:
            self.pulse_cooldown -= 1
        self.current_state.update()

class State:
    def __init__(self, controller):
        self.controller = controller

    def on_enter(self):
        pass

    def update(self):
        pass

    def on_exit(self):
        pass

class IdleState(State):
    def update(self):
        # Check for maintenance start
        if self.controller.is_maintenance_starting():
            self.controller.maintenance_end = time.ticks_add(time.ticks_ms(), self.controller.maintenance_duration)
            self.controller.days_until_maintenance = self.controller.maintenance_interval_days
            self.controller.maintenance_active = True
            self.controller.log_state_change('Maintenance', f'Started ({self.controller.maintenance_duration_minutes} min)')
            self.controller.transition_to(GeneratorState.STARTING)
        # Check for run request, only if cooldown expired
        elif self.controller.sensor_manager.is_request_run() and not self.controller.sensor_manager.is_running_debounced() and self.controller.pulse_cooldown == 0:
            self.controller.transition_to(GeneratorState.STARTING)
        # If already running (e.g., startup), go to running
        elif self.controller.sensor_manager.is_running_debounced():
            self.controller.transition_to(GeneratorState.RUNNING)

class StartingState(State):
    def on_enter(self):
        self.controller.start_attempts += 1
        relay_start_gen.value(1)
        self.controller.start_relay_end_time = time.ticks_add(time.ticks_ms(), 1000)
        self.controller.pulse_cooldown = 400  # 20 seconds
        self.controller.log_state_change('Start Relay', 'Activated (starting generator)')
        self.controller.prev_state['start_relay'] = True

    def update(self):
        if self.controller.sensor_manager.is_running_debounced():
            self.controller.transition_to(GeneratorState.RUNNING)
        elif time.ticks_diff(self.controller.start_relay_end_time, time.ticks_ms()) <= 0:
            # Pulse complete, deactivate
            relay_start_gen.value(0)
            self.controller.log_state_change('Start Relay', 'Deactivated (pulse complete)')
            self.controller.prev_state['start_relay'] = False
            # Now wait for the generator to start or cooldown to expire
            self.controller.confirm_started_start_time = time.ticks_ms()
            self.controller.transition_to(GeneratorState.CONFIRM_STARTED)

class ConfirmStartedState(State):
    def on_enter(self):
        # Start waiting for generator to start, or for cooldown to expire
        self.wait_start_time = time.ticks_ms()

    def update(self):
        # If generator started, go to running
        if self.controller.sensor_manager.is_running_debounced():
            self.controller.transition_to(GeneratorState.RUNNING)
        # Wait for cooldown to expire before allowing another attempt
        elif self.controller.pulse_cooldown == 0:
            self.controller.transition_to(GeneratorState.IDLE)

class RunningState(State):
    def on_enter(self):
        # Reset maintenance if from request
        if not self.controller.prev_state['maintenance_reset']:
            self.controller.days_until_maintenance = self.controller.maintenance_interval_days
            self.controller.maintenance_check_time = time.ticks_ms()
            self.controller.prev_state['maintenance_reset'] = True
            self.controller.log_state_change('Maintenance Reset', f'Countdown reset to {self.controller.days_until_maintenance} days (generator running from request)')

    def update(self):
        if self.controller.maintenance_active:
            # End maintenance when time is up
            if time.ticks_diff(self.controller.maintenance_end, time.ticks_ms()) <= 0:
                self.controller.maintenance_active = False
                self.controller.log_state_change('Maintenance', 'Finished')
                self.controller.transition_to(GeneratorState.STOPPING)
        else:
            if not self.controller.sensor_manager.is_request_run():
                self.controller.transition_to(GeneratorState.COOL_DOWN)

class CoolDownState(State):
    def on_enter(self):
        self.controller.cool_down_active = True
        self.controller.cool_down_end = time.ticks_add(time.ticks_ms(), self.controller.cool_down_duration)
        self.controller.log_state_change('Cool Down', f'Started ({self.controller.cool_down_duration_minutes} min)')

    def update(self):
        if time.ticks_diff(self.controller.cool_down_end, time.ticks_ms()) <= 0:
            self.controller.cool_down_active = False
            self.controller.days_until_maintenance = self.controller.maintenance_interval_days
            self.controller.log_state_change('Cool Down', 'Finished')
            self.controller.transition_to(GeneratorState.STOPPING)

    def on_exit(self):
        # Reset maintenance at the end of cool-down
        self.controller.days_until_maintenance = self.controller.maintenance_interval_days
        self.controller.log_state_change('Maintenance', 'Reset after cool-down')

class StoppingState(State):
    def on_enter(self):
        relay_kill_gen.value(1)
        self.controller.stopping_waiting_for_stop = True
        self.controller.kill_relay_delay_timer = None  # Renamed for clarity
        self.controller.last_kill_action = time.ticks_ms()  # Update last_kill_action
        self.controller.log_state_change('Kill Relay', 'Activated')
        self.controller.prev_state['kill_relay'] = True

    def update(self):
        if self.controller.stopping_waiting_for_stop:
            if not self.controller.sensor_manager.is_running_debounced():
                # Generator has stopped, start 2s timer
                self.controller.kill_relay_delay_timer = time.ticks_ms()
                self.controller.stopping_waiting_for_stop = False
        else:
            # Already stopped, count 2 seconds
            if time.ticks_diff(time.ticks_ms(), self.controller.kill_relay_delay_timer) >= 2000:
                relay_kill_gen.value(0)
                self.controller.last_kill_action = time.ticks_ms()  # Update last_kill_action
                self.controller.log_state_change('Kill Relay', 'Deactivated (delay complete)')
                self.controller.prev_state['kill_relay'] = False
                self.controller.transition_to(GeneratorState.IDLE)


class SensorManager:
    def __init__(self, in_run_sense_pin, in_run_request_pin):
        self.in_run_sense = in_run_sense_pin
        self.in_run_request = in_run_request_pin
        self.test_override_running = None
        self.test_override_request = None
        self.debounce_timer = 0
        self.debounced_running = False
        self.previous_debounced_running = False
        self.became_running = False
        self.stopped_running = False

    def set_override_running(self, override):
        self.test_override_running = override

    def set_override_request(self, override):
        self.test_override_request = override

    def _is_running_raw(self):
        if self.test_override_running is not None:
            return self.test_override_running
        return not self.in_run_sense.value()

    def is_request_run(self):
        if self.test_override_request is not None:
            return self.test_override_request
        return not self.in_run_request.value()

    def update_debounce(self):
        sensor_running = self._is_running_raw()
        if sensor_running != self.debounced_running:
            self.debounce_timer += 1
            if self.debounce_timer >= 10:  # 2 seconds debounce (10 * 200ms)
                self.debounced_running = sensor_running
                self.debounce_timer = 0
        else:
            self.debounce_timer = 0

    def is_running_debounced(self):
        return self.debounced_running

    def update_transitions(self):
        """Updates internal transition flags. Should be called once per loop."""
        self.became_running = self.debounced_running and not self.previous_debounced_running
        self.stopped_running = not self.debounced_running and self.previous_debounced_running
        self.previous_debounced_running = self.debounced_running

# Instantiate SensorManager
sensor_manager = SensorManager(in_run_sense, in_run_request)

# Instantiate the controller
controller = GeneratorController(sensor_manager, config)

async def manage_start_stop():
    # Initialize maintenance check time
    controller.maintenance_check_time = time.ticks_ms()
    ms_per_day = 24 * 60 * 60 * 1000  # one day in milliseconds
    previous_request = False

    loop_count = 0

    # Log startup
    controller.log_state_change('System Start', 'Generator controller initialized')

    while True:
        loop_count += 1

        # Check if a day has passed for maintenance countdown
        current_time = time.ticks_ms()
        time_since_check = time.ticks_diff(current_time, controller.maintenance_check_time)
        if time_since_check >= ms_per_day:
            if controller.days_until_maintenance > 0:
                controller.days_until_maintenance -= 1
            controller.maintenance_check_time = current_time

        # Update sensor debouncing
        controller.sensor_manager.update_debounce()
        debounced_running = controller.sensor_manager.is_running_debounced()

        # Count runs based on debounced signal
        controller.sensor_manager.update_transitions()
        if controller.sensor_manager.became_running:
            controller.detected_runs += 1
            controller.last_run_sense_start = time.ticks_ms()
        elif controller.sensor_manager.stopped_running:
            controller.last_run_sense_end = time.ticks_ms()

        if controller.sensor_manager.became_running:
            controller.pulse_cooldown = 100  # 20 seconds cooldown after start

        request = controller.sensor_manager.is_request_run()
        if request and not previous_request:
            controller.last_start_request = time.ticks_ms()
        previous_request = request

        # Update controller (handles state machine)
        controller.update()

        # Log state changes
        current_running = debounced_running
        current_request = controller.sensor_manager.is_request_run()

        if current_running != controller.prev_state['running']:
            controller.log_state_change('Generator Running', 'Yes' if current_running else 'No')
            controller.prev_state['running'] = current_running

        if current_request != controller.prev_state['run_request']:
            controller.log_state_change('Run Request', 'Active' if current_request else 'Inactive')
            controller.prev_state['run_request'] = current_request

        # Log days until maintenance changes
        if controller.days_until_maintenance != controller.prev_state['days']:
            controller.log_state_change('Maintenance Countdown', f'{controller.days_until_maintenance} days remaining')
            controller.prev_state['days'] = controller.days_until_maintenance

        # Profiling
        if loop_count % 100 == 0:
            gc.collect()

        await asyncio.sleep_ms(200)

async def update_leds():
    while True:
        led_running.value(controller.sensor_manager.is_running_debounced())
        led_run_request.value(controller.sensor_manager.is_request_run())
        led_cool_down.value(controller.cool_down_active)
        led_maintenance.value(controller.maintenance_active)
        await asyncio.sleep_ms(500)

# Web server routes
@app.route('/')
def index(request):
    try:
        return send_file('index.html')
    except Exception as e:
        print('[ERROR] / route:', e)
        return Response(body='Error', status_code=500)

@app.route('/script.js')
def script_js(request):
    try:
        return send_file('script.js')
    except Exception as e:
        print('[ERROR] /script.js route:', e)
        return Response(body='Error', status_code=500)

@app.route('/status')
def get_status(request):
    try:
        return controller.get_status_generator(), 200, {'Content-Type': 'application/json'}
    except Exception as e:
        print('[ERROR] /status route:', e)
        return {'error': str(e)}

@app.route('/uptime')
def get_uptime(request):
    try:
        return {'uptime_ms': time.ticks_ms()}
    except Exception as e:
        print('[ERROR] /uptime route:', e)
        return {'error': str(e)}


@app.route('/log')
def get_log(request):
    try:
        def generate_log():
            yield '{"log":['
            for i, (ts, ev, det) in enumerate(controller.state_log):
                if i > 0:
                    yield ','
                yield ujson.dumps({'timestamp': ts, 'event': ev, 'details': det})
            yield '],"uptime_ms":' + str(time.ticks_ms()) + '}'
        return generate_log(), 200, {'Content-Type': 'application/json'}
    except Exception as e:
        print('[ERROR] /log route:', e)
        return {'error': str(e)}

@app.route('/logpage')
def log_page(request):
    try:
        return send_file('log.html')
    except Exception as e:
        print('[ERROR] /logpage route:', e)
        return Response(body='Error', status_code=500)

@app.route('/config')
def config_page(request):
    try:
        return send_file('config.html')
    except Exception as e:
        print('[ERROR] /config route:', e)
        return Response(body='Error', status_code=500)

@app.route('/config/data')
def get_config(request):
    try:
        return config
    except Exception as e:
        print('[ERROR] /config/data route:', e)
        return {'error': str(e)}

def parse_form_data(body):
    data = {}
    for pair in body.decode().split('&'):
        if '=' in pair:
            k, v = pair.split('=', 1)
            data[k] = v
    return data

@app.route('/config/update', methods=['POST'])
def update_config_route(request):
    try:
        data = parse_form_data(request.body)
        data = {k: int(v) for k, v in data.items()}
        old_start_hour = config.get("maintenance_start_hour", 12)
        old_start_minute = config.get("maintenance_start_minute", 0)
        config.update(data)
        save_config(config)

        # Update controller from new config
        controller.maintenance_interval_days = config["maintenance_interval_days"]
        controller.maintenance_duration_minutes = config["maintenance_duration_minutes"]
        controller.maintenance_duration = controller.maintenance_duration_minutes * 60 * 1000
        controller.cool_down_duration_minutes = config["cool_down_duration_minutes"]
        controller.cool_down_duration = controller.cool_down_duration_minutes * 60 * 1000
        controller.maintenance_start_hour = config["maintenance_start_hour"]
        controller.maintenance_start_minute = config["maintenance_start_minute"]

        if 'current_minutes' in data:
            global rtc_base_minutes, rtc_base_ticks, rtc_synced
            host_minutes = data['current_minutes']
            current_minutes = get_current_minutes()
            if abs(current_minutes - host_minutes) > 1:
                rtc_base_minutes = host_minutes
                rtc_base_ticks = time.ticks_ms()
                rtc_synced = True

        # Reset countdown if start time changed
        if controller.maintenance_start_hour != old_start_hour or controller.maintenance_start_minute != old_start_minute:
            controller.days_until_maintenance = controller.maintenance_interval_days
            controller.maintenance_check_time = time.ticks_ms()
        return {'status': 'ok'}
    except Exception as e:
        print('[ERROR] /config/update route:', e)
        return {'error': str(e)}

@app.route('/ping')
def ping(request):
    try:
        return {'status': 'ok', 'message': 'Server is running'}
    except Exception as e:
        print('[ERROR] /ping route:', e)
        return {'error': str(e)}

# Testing endpoints
@app.route('/test/force_maintenance', methods=['POST'])
def test_force_maintenance(request):
    try:
        controller.days_until_maintenance = 0
        controller.log_state_change('TEST', 'Forced maintenance countdown to 0')
        return {'status': 'ok', 'message': 'Maintenance will start in next cycle'}
    except Exception as e:
        print('[ERROR] /test/force_maintenance route:', e)
        return {'error': str(e)}

@app.route('/test/override_running', methods=['POST'])
def test_override_running_endpoint(request):
    try:
        data = request.json
        override = data.get('override')  # True, False, or None
        if override == 'none' or override is None:
            sensor_manager.set_override_running(None)
            controller.log_state_change('TEST', 'Cleared running override (using sensor)')
        else:
            sensor_manager.set_override_running(bool(override))
            controller.log_state_change('TEST', f'Override running = {bool(override)}')
        return {'status': 'ok', 'override': sensor_manager.test_override_running}
    except Exception as e:
        print('[ERROR] /test/override_running route:', e)
        return {'error': str(e)}

@app.route('/test/override_request', methods=['POST'])
def test_override_request_endpoint(request):
    try:
        data = request.json
        override = data.get('override')  # True, False, or None
        if override == 'none' or override is None:
            sensor_manager.set_override_request(None)
            controller.log_state_change('TEST', 'Cleared request override (using sensor)')
        else:
            sensor_manager.set_override_request(bool(override))
            controller.log_state_change('TEST', f'Override request = {bool(override)}')
        return {'status': 'ok', 'override': sensor_manager.test_override_request}
    except Exception as e:
        print('[ERROR] /test/override_request route:', e)
        return {'error': str(e)}

async def main():
    print('Starting Generator Controller...')
    t1 = asyncio.create_task(manage_start_stop())
    t2 = asyncio.create_task(update_leds())
    print('AP config:', ap.ifconfig())
    print('Starting web server on http://gencontroller.local or http://' + ap.ifconfig()[0])
    try:
        await app.start_server(host='0.0.0.0', port=80)
    except Exception as e:
        print('Error in start_server:', e)
        raise

try:
    print('Starting async event loop...')
    asyncio.run(main())
except Exception as e:
    import sys
    sys.print_exception(e)
    print('Error starting server:', e)
