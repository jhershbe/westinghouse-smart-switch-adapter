import machine
import time
import asyncio

in_run_sense = machine.Pin(12, machine.Pin.IN, machine.Pin.PULL_UP)
in_run_request = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_UP)
led_run_request = machine.Pin(16, machine.Pin.OUT)
led_running = machine.Pin(17, machine.Pin.OUT)
led_cool_down = machine.Pin(18, machine.Pin.OUT)
led_maintenance = machine.Pin(19, machine.Pin.OUT)
relay_start_gen = machine.Pin(32, machine.Pin.OUT)
relay_kill_gen = machine.Pin(33, machine.Pin.OUT)

cool_down_active = False
cool_down_duration = 15 * 60 * 1000 # 15 minutes
cool_down_end = 0

us_per_day = 24 * 60 * 60 * 1000 # one day
maintenance_active = False
maintenance_interval = 7 # maintenance days
days_until_maintenance = maintenance_interval
maintenance_duration = 10 * 60 * 1000 # 10 minutes
maintenance_end = 0

kill_gen = False
def is_running():
    return not in_run_sense.value()

def is_request_run():
    return not in_run_request.value()

def is_cool_down_starting():
    return is_running() and (not is_request_run()) and (not (cool_down_active or maintenance_active))

def is_cool_down_finished():
    return cool_down_active and time.ticks_diff(cool_down_end, time.ticks_ms()) < 0

def is_maintenance_starting():
    return days_until_maintenance == 0

def is_maintenance_finished():
    return maintenance_active and time.ticks_diff(maintenance_end, time.ticks_ms()) < 0

async def manage_start_stop():
    global cool_down_active
    global cool_down_end
    global kill_gen
    global days_until_maintenance
    global maintenance_active
    global maintenance_end
    while True:
        if is_request_run():
            # No need for scheduled maintenance if we needed to run
            days_until_maintenance = maintenance_interval
            if is_running():
                relay_start_gen.value(0)
                cool_down_active = False
            else:
                relay_start_gen.value(1)
        else:
            # never started and don't want it anymore
            if not is_running():
                relay_start_gen.value(0)

        if is_cool_down_starting():
            cool_down_active = True
            cool_down_end = time.ticks_add(time.ticks_ms(), cool_down_duration)
        if is_cool_down_finished():
            cool_down_active = False
            # No need for scheduled maintenance if we needed to run
            days_until_maintenance = maintenance_interval
            kill_gen = True

        if is_maintenance_starting():
            maintenance_active = True
            maintenance_end = time.ticks_add(time.ticks_ms(), maintenance_duration)
            days_until_maintenance = maintenance_interval
        if maintenance_active:
            if is_running():
                relay_start_gen.value(0)
            else:
                relay_start_gen.value(1)
                running = True
        if is_maintenance_finished():
            maintenance_active = False
            kill_gen = True

        if kill_gen and is_running():
            relay_kill_gen.value(1)
        elif kill_gen:
            kill_gen = False
            relay_kill_gen.value(0)

        await asyncio.sleep_ms(50)

async def update_leds():
    while True:
        led_running.value(is_running())
        led_run_request.value(is_request_run())
        led_cool_down.value(cool_down_active)
        led_maintenance.value(maintenance_active)
        await asyncio.sleep_ms(100)

async def wait_days():
    global days_until_maintenance
    while True:
        await asyncio.sleep_ms(us_per_day)
        days_until_maintenance -= 1

async def main():
    t1 = asyncio.create_task(manage_start_stop())
    t2 = asyncio.create_task(update_leds())
    t3 = asyncio.create_task(wait_days())
    await asyncio.gather(t1, t2, t3)

asyncio.run(main())
