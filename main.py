import machine
import time
import asyncio
import network

in_run_sense = machine.Pin(12, machine.Pin.IN, machine.Pin.PULL_UP)
in_run_request = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_UP)
led_run_request = machine.Pin(16, machine.Pin.OUT)
led_running = machine.Pin(17, machine.Pin.OUT)
led_cool_down = machine.Pin(18, machine.Pin.OUT)
led_maintenance = machine.Pin(19, machine.Pin.OUT)
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
maintenance_interval_days = 7  # maintenance days
days_until_maintenance = maintenance_interval_days
maintenance_check_time = 0  # When we last checked for day rollover
maintenance_duration = 10 * 60 * 1000 # 10 minutes
maintenance_end = 0

kill_gen = False

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
    'start_relay': False
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
    return not in_run_sense.value()

def is_request_run():
    return not in_run_request.value()

def is_cool_down_starting():
    return is_running() and (not is_request_run()) and (not (cool_down_active or maintenance_active)) and (not kill_gen)

def is_cool_down_finished():
    return cool_down_active and time.ticks_diff(cool_down_end, time.ticks_ms()) < 0

def is_maintenance_starting():
    return days_until_maintenance <= 0

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

    # Initialize maintenance check time
    maintenance_check_time = time.ticks_ms()

    # Log startup
    log_state_change('System Start', 'Generator controller initialized')

    while True:
        # Check if a day has passed for maintenance countdown
        current_time = time.ticks_ms()
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
            log_state_change('Cool Down', 'Started (15 min)')
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
            log_state_change('Maintenance', 'Started (10 min)')

        # Maintenance relay control - takes priority
        if maintenance_active:
            if is_running():
                if prev_state['start_relay']:
                    relay_start_gen.value(0)
                    log_state_change('Start Relay', 'Deactivated (maintenance - already running)')
                    prev_state['start_relay'] = False
            else:
                if not prev_state['start_relay']:
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
                # No need for scheduled maintenance if we needed to run
                days_until_maintenance = maintenance_interval_days
                if is_running():
                    if prev_state['start_relay']:
                        relay_start_gen.value(0)
                        log_state_change('Start Relay', 'Deactivated (already running)')
                        prev_state['start_relay'] = False
                    cool_down_active = False
                else:
                    if not prev_state['start_relay']:
                        relay_start_gen.value(1)
                        log_state_change('Start Relay', 'Activated (starting generator)')
                        prev_state['start_relay'] = True
            else:
                # never started and don't want it anymore
                if not is_running():
                    if prev_state['start_relay']:
                        relay_start_gen.value(0)
                        log_state_change('Start Relay', 'Deactivated (no run request)')
                        prev_state['start_relay'] = False

        # Handle kill relay
        if kill_gen and is_running():
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
    # Calculate time remaining in current day
    time_since_check = time.ticks_diff(time.ticks_ms(), maintenance_check_time)
    ms_remaining_today = ms_per_day - time_since_check
    if ms_remaining_today < 0:
        ms_remaining_today = 0

    # Total time remaining
    total_ms = (days_until_maintenance - 1) * ms_per_day + ms_remaining_today if days_until_maintenance > 0 else ms_remaining_today

    # Display days: if we're in the middle of a day, show one less day + hours/minutes
    display_days = days_until_maintenance - 1 if days_until_maintenance > 0 else 0
    hours = ms_remaining_today // (60 * 60 * 1000)
    minutes = (ms_remaining_today % (60 * 60 * 1000)) // (60 * 1000)

    return {
        'running': is_running(),
        'run_request': is_request_run(),
        'cool_down': cool_down_active,
        'maintenance': maintenance_active,
        'maintenance_countdown': {
            'days': display_days,
            'hours': hours,
            'minutes': minutes,
            'total_ms': total_ms
        }
    }

@app.route('/uptime')
def get_uptime(request):
    return {'uptime_ms': time.ticks_ms()}

@app.route('/log')
def get_log(request):
    return {'log': state_log}

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
