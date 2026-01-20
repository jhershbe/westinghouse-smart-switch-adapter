import machine
import time
import asyncio
import network
import ujson

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

# Use config values
maintenance_interval_days = config["maintenance_interval_days"]
maintenance_duration_minutes = config["maintenance_duration_minutes"]
maintenance_duration = maintenance_duration_minutes * 60 * 1000
cool_down_duration_minutes = config["cool_down_duration_minutes"]
cool_down_duration = cool_down_duration_minutes * 60 * 1000
maintenance_start_hour = config["maintenance_start_hour"]
maintenance_start_minute = config["maintenance_start_minute"]

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
ap.active(True)
ap.config(essid='GenController', password='westinghouse', authmode=network.AUTH_WPA_WPA2_PSK)
ap.config(hostname='gencontroller')

# Wait for AP to be active
while not ap.active():
    time.sleep(0.1)
print('AP active, IP:', ap.ifconfig()[0])
print('Connect to: http://gencontroller.local')

# Import Microdot after WiFi is initialized
from microdot import Microdot, Response
from enum import Enum

# Set up Microdot
app = Microdot()
Response.default_content_type = 'application/json'

class GeneratorController:
    def __init__(self):
        self.sensor_manager = sensor_manager
        self.maintenance_interval_days = maintenance_interval_days
        self.maintenance_duration_minutes = maintenance_duration_minutes
        self.maintenance_duration = maintenance_duration
        self.cool_down_duration_minutes = cool_down_duration_minutes
        self.cool_down_duration = cool_down_duration
        self.maintenance_start_hour = maintenance_start_hour
        self.maintenance_start_minute = maintenance_start_minute
        self.days_until_maintenance = days_until_maintenance
        self.maintenance_check_time = maintenance_check_time
        self.maintenance_end = maintenance_end
        self.maintenance_active = maintenance_active
        self.cool_down_end = cool_down_end
        self.cool_down_active = cool_down_active
        self.kill_gen = kill_gen
        self.start_attempts = start_attempts
        self.detected_runs = detected_runs
        self.last_start_request = last_start_request
        self.last_kill_action = last_kill_action
        self.last_run_sense_start = last_run_sense_start
        self.last_run_sense_end = last_run_sense_end
        self.start_relay_end_time = start_relay_end_time
        self.pulse_cooldown = pulse_cooldown
        self.kill_relay_delay_active = kill_relay_delay_active
        self.kill_relay_delay_timer = kill_relay_delay_timer
        self.prev_state = prev_state
        self.state_log = state_log
        self.stopping_waiting_for_stop = True
        self.stopping_stopped_time = None
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

    def transition_to(self, state):
        if self.current_state:
            self.current_state.on_exit()
        self.current_state = self.state_map[state]
        self.current_state.on_enter()

    def is_maintenance_starting(self):
        return is_maintenance_starting()

    def update(self):
        # Update pulse cooldown
        if self.pulse_cooldown > 0:
            self.pulse_cooldown -= 1
        self.current_state.update()

# Instantiate the controller
controller = GeneratorController()

class GeneratorState(Enum):
    IDLE = "idle"                          # Not running, monitoring for requests/maintenance
    STARTING = "starting"                   # Activating start relay (pulse)
    CONFIRM_STARTED = "confirm_started"     # Waiting to see if generator started after pulse
    RUNNING = "running"                     # Running (normal or maintenance)
    COOL_DOWN = "cool_down"                 # Running but no request; waiting to stop
    STOPPING = "stopping"                   # Activating kill relay with delay

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
            log_state_change('Maintenance', f'Started ({self.controller.maintenance_duration_minutes} min)')
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
        log_state_change('Start Relay', 'Activated (starting generator)')
        self.controller.prev_state['start_relay'] = True

    def update(self):
        if self.controller.sensor_manager.is_running_debounced():
            self.controller.transition_to(GeneratorState.RUNNING)
        elif time.ticks_diff(self.controller.start_relay_end_time, time.ticks_ms()) <= 0:
            # Pulse complete, deactivate
            relay_start_gen.value(0)
            log_state_change('Start Relay', 'Deactivated (pulse complete)')
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
            log_state_change('Maintenance Reset', f'Countdown reset to {self.controller.days_until_maintenance} days (generator running from request)')

    def update(self):
        if not self.controller.sensor_manager.is_request_run():
            self.controller.transition_to(GeneratorState.COOL_DOWN)

class CoolDownState(State):
    def on_enter(self):
        self.controller.cool_down_end = time.ticks_add(time.ticks_ms(), self.controller.cool_down_duration)
        log_state_change('Cool Down', f'Started ({self.controller.cool_down_duration_minutes} min)')

    def update(self):
        if time.ticks_diff(self.controller.cool_down_end, time.ticks_ms()) <= 0:
            self.controller.transition_to(GeneratorState.STOPPING)

class StoppingState(State):
    def on_enter(self):
        relay_kill_gen.value(1)
        self.controller.stopping_waiting_for_stop = True
        self.controller.stopping_stopped_time = None
        log_state_change('Kill Relay', 'Activated')
        self.controller.prev_state['kill_relay'] = True

    def update(self):
        if self.controller.stopping_waiting_for_stop:
            if not self.controller.sensor_manager.is_running_debounced():
                # Generator has stopped, start 2s timer
                self.controller.stopping_stopped_time = time.ticks_ms()
                self.controller.stopping_waiting_for_stop = False
        else:
            # Already stopped, count 2 seconds
            if time.ticks_diff(time.ticks_ms(), self.controller.stopping_stopped_time) >= 2000:
                relay_kill_gen.value(0)
                log_state_change('Kill Relay', 'Deactivated (delay complete)')
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
        if sensor_running:
            if not self.debounced_running:
                self.debounce_timer += 1
                if self.debounce_timer >= 40:  # 2 seconds debounce
                    self.debounced_running = True
                    self.debounce_timer = 0
        else:
            self.debounced_running = False
            self.debounce_timer = 0

    def is_running_debounced(self):
        return self.debounced_running

    def get_running_transition(self):
        """Returns (became_running, stopped_running)"""
        became_running = self.debounced_running and not self.previous_debounced_running
        stopped_running = not self.debounced_running and self.previous_debounced_running
        self.previous_debounced_running = self.debounced_running
        return became_running, stopped_running

# Instantiate SensorManager
sensor_manager = SensorManager(in_run_sense, in_run_request)

cool_down_active = False
cool_down_duration = 15 * 60 * 1000 # 15 minutes
cool_down_end = 0

ms_per_day = 24 * 60 * 60 * 1000  # one day in milliseconds
maintenance_active = False
days_until_maintenance = maintenance_interval_days
maintenance_check_time = 0  # When we last checked for day rollover
maintenance_end = 0

kill_gen = False

start_attempts = 0
detected_runs = 0
last_start_request = 0
previous_running = False
previous_request = False
last_kill_action = 0
last_run_sense_start = 0
last_run_sense_end = 0
start_relay_end_time = 0
pulse_cooldown = 0
kill_relay_delay_active = False
kill_relay_delay_timer = 0

# State transition logging
state_log = []
MAX_LOG_ENTRIES = 50

# Track previous states for change detection
prev_state = {
    'running': False,
    'run_request': False,
    'cool_down': False,
    'maintenance': False,
    'days': maintenance_interval_days,
    'kill_relay': False,
    'start_relay': False,
    'maintenance_reset': False  # Track if we've reset maintenance for current run
}

def log_state_change(event, details=''):
    """Log a state transition with timestamp"""
    global state_log
    entry = {
        'timestamp': time.ticks_ms(),
        'event': event,
        'details': details
    }
    state_log.append(entry)
    # Keep only last MAX_LOG_ENTRIES
    if len(state_log) > MAX_LOG_ENTRIES:
        state_log.pop(0)
    print(f"[{entry['timestamp']}] {event}: {details}")

def is_cool_down_starting():
    return sensor_manager.is_running_debounced() and (not sensor_manager.is_request_run()) and (not (cool_down_active or maintenance_active)) and (not kill_gen)

def is_cool_down_finished():
    return cool_down_active and time.ticks_diff(cool_down_end, time.ticks_ms()) < 0

def is_maintenance_starting():
    if days_until_maintenance > 0:
        return False
    current_minutes = get_current_minutes()
    configured_minutes = maintenance_start_hour * 60 + maintenance_start_minute
    return current_minutes >= configured_minutes

def is_maintenance_finished():
    return maintenance_active and time.ticks_diff(maintenance_end, time.ticks_ms()) < 0

async def manage_start_stop():
    # Initialize maintenance check time
    controller.maintenance_check_time = time.ticks_ms()

    # Log startup
    log_state_change('System Start', 'Generator controller initialized')

    while True:
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
        became_running, stopped_running = controller.sensor_manager.get_running_transition()
        if became_running:
            controller.detected_runs += 1
            controller.last_run_sense_start = time.ticks_ms()
        elif stopped_running:
            controller.last_run_sense_end = time.ticks_ms()

        if became_running:
            controller.pulse_cooldown = 400  # 20 seconds cooldown after start

        request = controller.sensor_manager.is_request_run()
        if request and not previous_request:
            controller.last_start_request = time.ticks_ms()
        previous_request = request

        # Update controller (handles state machine)
        controller.update()

        # Handle cooldown and maintenance (legacy logic, may be moved to states)
        if is_cool_down_starting():
            controller.cool_down_active = True
            controller.cool_down_end = time.ticks_add(time.ticks_ms(), controller.cool_down_duration)
            log_state_change('Cool Down', f'Started ({controller.cool_down_duration_minutes} min)')
        if is_cool_down_finished():
            controller.cool_down_active = False
            controller.days_until_maintenance = controller.maintenance_interval_days
            controller.kill_gen = True
            log_state_change('Cool Down', 'Finished')

        # Log cool down state changes
        if controller.cool_down_active != controller.prev_state['cool_down']:
            controller.prev_state['cool_down'] = controller.cool_down_active

        if is_maintenance_starting():
            controller.maintenance_active = True
            controller.maintenance_end = time.ticks_add(time.ticks_ms(), controller.maintenance_duration)
            controller.days_until_maintenance = controller.maintenance_interval_days
            log_state_change('Maintenance', f'Started ({controller.maintenance_duration_minutes} min)')

        if is_maintenance_finished():
            controller.maintenance_active = False
            controller.kill_gen = True
            log_state_change('Maintenance', 'Finished')

        # Log maintenance state changes
        if controller.maintenance_active != controller.prev_state['maintenance']:
            controller.prev_state['maintenance'] = controller.maintenance_active

        # Log days until maintenance changes
        if controller.days_until_maintenance != controller.prev_state['days']:
            log_state_change('Maintenance Countdown', f'{controller.days_until_maintenance} days remaining')
            controller.prev_state['days'] = controller.days_until_maintenance

        # Handle kill relay (legacy, may be moved to states)
        if controller.kill_gen and debounced_running:
            controller.last_kill_action = time.ticks_ms()
            relay_kill_gen.value(1)
            if not controller.prev_state['kill_relay']:
                log_state_change('Kill Relay', 'Activated')
                controller.prev_state['kill_relay'] = True
        elif controller.kill_gen:
            if not controller.kill_relay_delay_active:
                controller.kill_relay_delay_active = True
                controller.kill_relay_delay_timer = 0
            controller.kill_relay_delay_timer += 1
            if controller.kill_relay_delay_timer >= 40:  # 2 seconds delay
                controller.kill_gen = False
                relay_kill_gen.value(0)
                controller.kill_relay_delay_active = False
                if controller.prev_state['kill_relay']:
                    log_state_change('Kill Relay', 'Deactivated (delay complete)')
                    controller.prev_state['kill_relay'] = False

        await asyncio.sleep_ms(50)

async def update_leds():
    while True:
        led_running.value(sensor_manager.is_running_debounced())
        led_run_request.value(sensor_manager.is_request_run())
        led_cool_down.value(cool_down_active)
        led_maintenance.value(maintenance_active)
        await asyncio.sleep_ms(100)

# Web server routes
@app.route('/')
def index(request):
    with open('index.html') as f:
        html = f.read()
    return Response(body=html, headers={'Content-Type': 'text/html'})

@app.route('/script.js')
def script_js(request):
    with open('script.js') as f:
        js = f.read()
    return Response(body=js, headers={'Content-Type': 'application/javascript'})

@app.route('/status')
def get_status(request):
    current_minutes = get_current_minutes()
    configured_minutes = maintenance_start_hour * 60 + maintenance_start_minute
    minutes_until_start = (configured_minutes - current_minutes + 1440) % 1440
    total_minutes = days_until_maintenance * 1440 + minutes_until_start
    days = total_minutes // 1440
    hours = (total_minutes % 1440) // 60
    minutes = total_minutes % 60

    return {
        'running': sensor_manager.is_running_debounced(),
        'run_request': sensor_manager.is_request_run(),
        'cool_down': cool_down_active,
        'cool_down_remaining': max(0, cool_down_end - time.ticks_ms()) if cool_down_active else 0,
        'maintenance': maintenance_active,
        'maintenance_remaining': max(0, maintenance_end - time.ticks_ms()) if maintenance_active else 0,
        'maintenance_countdown': {
            'days': days,
            'hours': hours,
            'minutes': minutes,
            'total_minutes': total_minutes
        },
        'current_time_minutes': current_minutes,
        'rtc_synced': True,  # Always considered synced
        'start_attempts': start_attempts,
        'detected_runs': detected_runs,
        'last_start_request': last_start_request,
        'last_kill_action': last_kill_action,
        'last_run_sense_start': last_run_sense_start,
        'last_run_sense_end': last_run_sense_end
    }

@app.route('/uptime')
def get_uptime(request):
    return {'uptime_ms': time.ticks_ms()}

@app.route('/log')
def get_log(request):
    return {'log': state_log, 'uptime_ms': time.ticks_ms()}

@app.route('/logpage')
def log_page(request):
    with open('log.html') as f:
        html = f.read()
    return Response(body=html, headers={'Content-Type': 'text/html'})

@app.route('/config')
def config_page(request):
    with open('config.html') as f:
        html = f.read()
    return Response(body=html, headers={'Content-Type': 'text/html'})

@app.route('/config/data')
def get_config(request):
    return config

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
        global maintenance_interval_days, maintenance_duration, cool_down_duration, maintenance_start_hour, maintenance_start_minute, rtc_synced, rtc_base_minutes, rtc_base_ticks, days_until_maintenance, maintenance_check_time
        maintenance_interval_days = config["maintenance_interval_days"]
        maintenance_duration = config["maintenance_duration_minutes"] * 60 * 1000
        cool_down_duration = config["cool_down_duration_minutes"] * 60 * 1000
        maintenance_start_hour = config["maintenance_start_hour"]
        maintenance_start_minute = config["maintenance_start_minute"]
        if 'current_minutes' in data:
            host_minutes = data['current_minutes']
            current_minutes = get_current_minutes()
            if abs(current_minutes - host_minutes) > 1:
                rtc_base_minutes = host_minutes
                rtc_base_ticks = time.ticks_ms()
                rtc_synced = True
        # Reset countdown if start time changed
        if maintenance_start_hour != old_start_hour or maintenance_start_minute != old_start_minute:
            days_until_maintenance = maintenance_interval_days
            maintenance_check_time = time.ticks_ms()
        return {'status': 'ok'}
    except Exception as e:
        return {'error': str(e)}

@app.route('/ping')
def ping(request):
    return {'status': 'ok', 'message': 'Server is running'}

# Testing endpoints
@app.route('/test/force_maintenance', methods=['POST'])
def test_force_maintenance(request):
    global days_until_maintenance
    days_until_maintenance = 0
    log_state_change('TEST', 'Forced maintenance countdown to 0')
    return {'status': 'ok', 'message': 'Maintenance will start in next cycle'}

@app.route('/test/override_running', methods=['POST'])
def test_override_running_endpoint(request):
    data = request.json
    override = data.get('override')  # True, False, or None
    if override == 'none' or override is None:
        sensor_manager.set_override_running(None)
        log_state_change('TEST', 'Cleared running override (using sensor)')
    else:
        sensor_manager.set_override_running(bool(override))
        log_state_change('TEST', f'Override running = {bool(override)}')
    return {'status': 'ok', 'override': sensor_manager.test_override_running}

@app.route('/test/override_request', methods=['POST'])
def test_override_request_endpoint(request):
    data = request.json
    override = data.get('override')  # True, False, or None
    if override == 'none' or override is None:
        sensor_manager.set_override_request(None)
        log_state_change('TEST', 'Cleared request override (using sensor)')
    else:
        sensor_manager.set_override_request(bool(override))
        log_state_change('TEST', f'Override request = {bool(override)}')
    return {'status': 'ok', 'override': sensor_manager.test_override_request}

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
