let currentUptime = 0;
let devicePowerOnTime = null;  // null means not initialized yet
let sent_sync = false;

function formatDateTime(timestamp) {
    const date = new Date(timestamp);
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    const seconds = String(date.getSeconds()).padStart(2, '0');
    return month + '/' + day + '/' + year + ' ' + hours + ':' + minutes + ':' + seconds;
}

function formatUptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return days + 'd ' + hours + 'h ' + minutes + 'm';
}

function updateStatus() {
    fetch('/status')
        .then(r => {
            if (!r.ok) {
                throw new Error('HTTP ' + r.status);
            }
            // Get the raw text first to see what we're receiving
            return r.text();
        })
        .then(text => {
            // Try to parse as JSON
            var data = JSON.parse(text);
            console.log('Status data:', data);
            document.getElementById('running').innerHTML = 
                '<span class="indicator ' + (data.running ? 'on' : 'off') + '"></span>' + 
                (data.running ? 'Yes' : 'No');
            document.getElementById('request').innerHTML = 
                '<span class="indicator ' + (data.run_request ? 'on' : 'off') + '"></span>' + 
                (data.run_request ? 'Yes' : 'No');
            if (data.cool_down) {
                const remaining_ms = data.cool_down_remaining;
                const minutes = Math.floor(remaining_ms / (60 * 1000));
                const seconds = Math.floor((remaining_ms % (60 * 1000)) / 1000);
                const timeStr = minutes + 'm ' + seconds + 's';
                document.getElementById('cooldown').innerHTML = '<span class="indicator on"></span>Yes (' + timeStr + ' remaining)';
            } else {
                document.getElementById('cooldown').innerHTML = '<span class="indicator off"></span>No';
            }
            if (data.maintenance) {
                const remaining_ms = data.maintenance_remaining;
                const minutes = Math.floor(remaining_ms / (60 * 1000));
                const seconds = Math.floor((remaining_ms % (60 * 1000)) / 1000);
                const timeStr = minutes + 'm ' + seconds + 's';
                document.getElementById('maintenance').innerHTML = '<span class="indicator on"></span>Yes (' + timeStr + ' remaining)';
            } else {
                document.getElementById('maintenance').innerHTML = '<span class="indicator off"></span>No';
            }
            var countdown = data.maintenance_countdown;
            document.getElementById('days').textContent =
                countdown.days + 'd ' + countdown.hours + 'h ' + countdown.minutes + 'm';
            document.getElementById('startAttempts').textContent = data.start_attempts;
            document.getElementById('detectedRuns').textContent = data.detected_runs;
            if (data.last_start_request) {
                const timeStr = formatDateTime(devicePowerOnTime + data.last_start_request);
                document.getElementById('lastStartRequest').textContent = timeStr;
            } else {
                document.getElementById('lastStartRequest').textContent = 'None';
            }
            if (data.last_kill_action) {
                const timeStr = formatDateTime(devicePowerOnTime + data.last_kill_action);
                document.getElementById('lastKillAction').textContent = timeStr;
            } else {
                document.getElementById('lastKillAction').textContent = 'None';
            }
            if (data.last_run_sense_start) {
                const timeStr = formatDateTime(devicePowerOnTime + data.last_run_sense_start);
                document.getElementById('lastRunSenseStart').textContent = timeStr;
            } else {
                document.getElementById('lastRunSenseStart').textContent = 'None';
            }
            if (data.last_run_sense_end) {
                const timeStr = formatDateTime(devicePowerOnTime + data.last_run_sense_end);
                document.getElementById('lastRunSenseEnd').textContent = timeStr;
            } else {
                document.getElementById('lastRunSenseEnd').textContent = 'None';
            }
            // Sync time once per connection
            if (!sent_sync) {
                const now = new Date();
                const current_minutes = now.getHours() * 60 + now.getMinutes();
                fetch('/config/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: `current_minutes=${current_minutes}`
                }).then(() => {
                    sent_sync = true;
                });
            }
        })
        .catch(e => {
            console.error('Error updating status:', e);
            // Show error details on the page
            var errorMsg = 'Error: ' + e.message;
            document.getElementById('running').textContent = errorMsg;
            document.getElementById('request').textContent = 'Check WiFi connection';
            document.getElementById('cooldown').textContent = '';
            document.getElementById('maintenance').textContent = '';
            document.getElementById('days').textContent = '';
        });
}

function updateUptime() {
    return fetch('/uptime')
        .then(r => r.json())
        .then(data => {
            currentUptime = data.uptime_ms;
            // Calculate when the device powered on (in browser time)
            devicePowerOnTime = Date.now() - currentUptime;
            console.log('Uptime:', currentUptime, 'Power-on time:', devicePowerOnTime, 'Now:', Date.now());
            // Update uptime display if present
            const uptimeDisplay = document.getElementById('uptimeDisplay');
            if (uptimeDisplay) {
                uptimeDisplay.textContent = formatUptime(currentUptime / 1000);
            }
        })
        .catch(e => console.error('Error updating uptime:', e));
}

function updateLog() {
    const logContainer = document.getElementById('logContainer');
    if (!logContainer) return;  // Not on log page

    fetch('/log')
        .then(r => r.json())
        .then(data => {
            currentUptime = data.uptime_ms / 1000;
            devicePowerOnTime = Date.now() - (currentUptime * 1000);
            const logContainer = document.getElementById('logContainer');

            if (data.log.length === 0) {
                logContainer.innerHTML = '<div class="log-entry">No events logged yet</div>';
                return;
            }
            
            // Reverse to show newest first
            const reversedLog = data.log.slice().reverse();
            logContainer.innerHTML = reversedLog.map(entry => {
                // Convert device timestamp to actual browser time
                const actualTime = devicePowerOnTime + entry.timestamp;
                const timeStr = formatDateTime(actualTime);
                return '<div class="log-entry">' +
                    '<span class="log-time">' + timeStr + '</span>' +
                    '<span class="log-event">' + entry.event + '</span>' +
                    '<span class="log-details">' + entry.details + '</span>' +
                    '</div>';
            }).join('');
        })
        .catch(e => {
            console.error('Error updating log:', e);
            document.getElementById('logContainer').innerHTML = '<div class="log-entry">Error: ' + e.message + '</div>';
        });
}

// Page-specific initialization
if (window.location.pathname === '/logpage') {
    // Log page
    updateUptime();
    updateLog();
    setInterval(updateUptime, 5000);
    setInterval(updateLog, 2000);
} else {
    // Main page
    updateStatus();
    updateUptime();
    setInterval(updateStatus, 1000);
    setInterval(updateUptime, 5000);
}

// Testing functions
function testConnection() {
    fetch('/ping')
        .then(r => r.json())
        .then(data => {
            alert('✓ Connection OK: ' + data.message);
        })
        .catch(e => {
            alert('✗ Connection Failed: ' + e.message + '\n\nMake sure you are connected to GenController WiFi network.');
        });
}

function forceMaintenance() {
    fetch('/test/force_maintenance', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(r => r.json())
    .then(data => {
        alert('Maintenance forced! Check status and log.');
        updateStatus();
        updateLog();
    })
    .catch(e => alert('Error: ' + e));
}

function overrideRunning(value) {
    fetch('/test/override_running', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({override: value === null ? 'none' : value})
    })
    .then(r => r.json())
    .then(data => {
        const msg = value === null ? 'Using sensor' : (value ? 'YES' : 'NO');
        alert('Running override: ' + msg);
        updateStatus();
        updateLog();
    })
    .catch(e => alert('Error: ' + e));
}

function overrideRequest(value) {
    fetch('/test/override_request', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({override: value === null ? 'none' : value})
    })
    .then(r => r.json())
    .then(data => {
        const msg = value === null ? 'Using sensor' : (value ? 'YES' : 'NO');
        alert('Request override: ' + msg);
        updateStatus();
        updateLog();
    })
    .catch(e => alert('Error: ' + e));
}
