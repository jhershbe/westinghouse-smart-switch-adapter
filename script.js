let currentUptime = 0;
let devicePowerOnTime = null;  // null means not initialized yet

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

function updateStatus() {
    fetch('/status')
        .then(r => r.json())
        .then(data => {
            document.getElementById('running').innerHTML = 
                '<span class="indicator ' + (data.running ? 'on' : 'off') + '"></span>' + 
                (data.running ? 'Yes' : 'No');
            document.getElementById('request').innerHTML = 
                '<span class="indicator ' + (data.run_request ? 'on' : 'off') + '"></span>' + 
                (data.run_request ? 'Yes' : 'No');
            document.getElementById('cooldown').innerHTML = 
                '<span class="indicator ' + (data.cool_down ? 'on' : 'off') + '"></span>' + 
                (data.cool_down ? 'Yes' : 'No');
            document.getElementById('maintenance').innerHTML = 
                '<span class="indicator ' + (data.maintenance ? 'on' : 'off') + '"></span>' + 
                (data.maintenance ? 'Yes' : 'No');
            var countdown = data.maintenance_countdown;
            document.getElementById('days').textContent =
                countdown.days + 'd ' + countdown.hours + 'h ' + countdown.minutes + 'm';
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
        })
        .catch(e => console.error('Error updating uptime:', e));
}

function updateLog() {
    const logContainer = document.getElementById('logContainer');
    if (!logContainer) return;  // Not on log page

    fetch('/log')
        .then(r => r.json())
        .then(data => {
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
    updateLog();
    setInterval(updateLog, 2000);
} else {
    // Main page
    updateStatus();
    updateUptime();
    setInterval(updateStatus, 1000);
    setInterval(updateUptime, 5000);
}
