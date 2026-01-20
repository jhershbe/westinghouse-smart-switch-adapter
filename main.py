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

# Wait for AP to be active
while not ap.active():
    time.sleep(0.1)
print('AP active, IP:', ap.ifconfig()[0])

# Import Microdot after WiFi is initialized
from microdot import Microdot, Response

# Set up Microdot
app = Microdot()
Response.default_content_type = 'application/json'

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

# Testing overrides
test_override_running = None  # None = use actual sensor, True/False = override
test_override_request = None  # None = use actual sensor, True/False = override

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

def is_running():
    if test_override_running is not None:
        return test_override_running
    return not in_run_sense.value()

def is_request_run():
    if test_override_request is not None:
        return test_override_request
    return not in_run_request.value()

def is_cool_down_starting():
    return is_running() and (not is_request_run()) and (not (cool_down_active or maintenance_active)) and (not kill_gen)

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
    global cool_down_active
    global cool_down_end
    global kill_gen
    global days_until_maintenance
    global maintenance_check_time
    global maintenance_active
    global maintenance_end
    global prev_state
    global start_attempts
    global detected_runs
    global last_start_request
    global previous_running
    global previous_request
    global last_kill_action
    global last_run_sense_start
    global last_run_sense_end

    # Initialize maintenance check time
    maintenance_check_time = time.ticks_ms()

    # Log startup
    log_state_change('System Start', 'Generator controller initialized')

    while True:
        # Check if a day has passed for maintenance countdown
        current_time = time.ticks_ms()
        running = is_running()
        if running and not previous_running:
            detected_runs += 1
            last_run_sense_start = time.ticks_ms()
        elif not running and previous_running:
            last_run_sense_end = time.ticks_ms()
        previous_running = running

        request = is_request_run()
        if request and not previous_request:
            last_start_request = time.ticks_ms()
        previous_request = request
        time_since_check = time.ticks_diff(current_time, maintenance_check_time)
        if time_since_check >= ms_per_day:
            if days_until_maintenance > 0:
                days_until_maintenance -= 1
            maintenance_check_time = current_time

        # Check for state changes and log them
        current_running = is_running()
        current_request = is_request_run()

        if current_running != prev_state['running']:
            log_state_change('Generator Running', 'Yes' if current_running else 'No')
            prev_state['running'] = current_running

        if current_request != prev_state['run_request']:
            log_state_change('Run Request', 'Active' if current_request else 'Inactive')
            prev_state['run_request'] = current_request

        # Handle cooldown and maintenance FIRST before normal run request logic
        if is_cool_down_starting():
            cool_down_active = True
            cool_down_end = time.ticks_add(time.ticks_ms(), cool_down_duration)
            log_state_change('Cool Down', f'Started ({cool_down_duration_minutes} min)')
        if is_cool_down_finished():
            cool_down_active = False
            # No need for scheduled maintenance if we needed to run
            days_until_maintenance = maintenance_interval_days
            kill_gen = True
            log_state_change('Cool Down', 'Finished')

        # Log cool down state changes
        if cool_down_active != prev_state['cool_down']:
            prev_state['cool_down'] = cool_down_active

        if is_maintenance_starting():
            maintenance_active = True
            maintenance_end = time.ticks_add(time.ticks_ms(), maintenance_duration)
            days_until_maintenance = maintenance_interval_days
            log_state_change('Maintenance', f'Started ({maintenance_duration_minutes} min)')

        # Maintenance relay control - takes priority
        if maintenance_active:
            if is_running():
                if prev_state['start_relay']:
                    relay_start_gen.value(0)
                    log_state_change('Start Relay', 'Deactivated (maintenance - already running)')
                    prev_state['start_relay'] = False
            else:
                if not prev_state['start_relay']:
                    start_attempts += 1
                    relay_start_gen.value(1)
                    log_state_change('Start Relay', 'Activated (maintenance start)')
                    prev_state['start_relay'] = True
        if is_maintenance_finished():
            maintenance_active = False
            kill_gen = True
            log_state_change('Maintenance', 'Finished')

        # Log maintenance state changes
        if maintenance_active != prev_state['maintenance']:
            prev_state['maintenance'] = maintenance_active

        # Log days until maintenance changes
        if days_until_maintenance != prev_state['days']:
            log_state_change('Maintenance Countdown', f'{days_until_maintenance} days remaining')
            prev_state['days'] = days_until_maintenance

        # Normal run request logic (only if not in maintenance mode)
        if not maintenance_active:
            if is_request_run():
                # Reset maintenance interval when generator is running from a request
                if is_running() and not prev_state['maintenance_reset']:
                    days_until_maintenance = maintenance_interval_days
                    maintenance_check_time = time.ticks_ms()
                    prev_state['maintenance_reset'] = True
                    log_state_change('Maintenance Reset', f'Countdown reset to {days_until_maintenance} days (generator running from request)')

                if is_running():
                    if prev_state['start_relay']:
                        relay_start_gen.value(0)
                        log_state_change('Start Relay', 'Deactivated (already running)')
                        prev_state['start_relay'] = False
                    cool_down_active = False
                else:
                    if not prev_state['start_relay']:
                        start_attempts += 1
                        relay_start_gen.value(1)
                        log_state_change('Start Relay', 'Activated (starting generator)')
                        prev_state['start_relay'] = True
            else:
                # Clear the maintenance reset flag when request stops
                if prev_state['maintenance_reset']:
                    prev_state['maintenance_reset'] = False

                # never started and don't want it anymore
                if not is_running():
                    if prev_state['start_relay']:
                        relay_start_gen.value(0)
                        log_state_change('Start Relay', 'Deactivated (no run request)')
                        prev_state['start_relay'] = False

        # Handle kill relay
        if kill_gen and is_running():
            last_kill_action = time.ticks_ms()
            relay_kill_gen.value(1)
            if not prev_state['kill_relay']:
                log_state_change('Kill Relay', 'Activated')
                prev_state['kill_relay'] = True
        elif kill_gen:
            kill_gen = False
            relay_kill_gen.value(0)
            if prev_state['kill_relay']:
                log_state_change('Kill Relay', 'Deactivated')
                prev_state['kill_relay'] = False

        await asyncio.sleep_ms(50)

async def update_leds():
    while True:
        led_running.value(is_running())
        led_run_request.value(is_request_run())
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
        'running': is_running(),
        'run_request': is_request_run(),
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
    global test_override_running
    data = request.json
    override = data.get('override')  # True, False, or None
    if override == 'none' or override is None:
        test_override_running = None
        log_state_change('TEST', 'Cleared running override (using sensor)')
    else:
        test_override_running = bool(override)
        log_state_change('TEST', f'Override running = {test_override_running}')
    return {'status': 'ok', 'override': test_override_running}

@app.route('/test/override_request', methods=['POST'])
def test_override_request_endpoint(request):
    global test_override_request
    data = request.json
    override = data.get('override')  # True, False, or None
    if override == 'none' or override is None:
        test_override_request = None
        log_state_change('TEST', 'Cleared request override (using sensor)')
    else:
        test_override_request = bool(override)
        log_state_change('TEST', f'Override request = {test_override_request}')
    return {'status': 'ok', 'override': test_override_request}

async def main():
    print('Starting Generator Controller...')
    t1 = asyncio.create_task(manage_start_stop())
    t2 = asyncio.create_task(update_leds())
    print('Starting web server on http://' + ap.ifconfig()[0])
    await app.start_server(host='0.0.0.0', port=80)

try:
    print('Starting async event loop...')
    asyncio.run(main())
except Exception as e:
    import sys
    sys.print_exception(e)
    print('Error starting server:', e)
