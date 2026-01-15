let currentUptime = 0;
let devicePowerOnTime = 0;

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
            document.getElementById('days').textContent = data.days_until_maintenance;
        });
}

function updateUptime() {
    fetch('/uptime')
        .then(r => r.json())
        .then(data => {
            currentUptime = data.uptime_ms;
            // Calculate when the device powered on (in browser time)
            devicePowerOnTime = Date.now() - currentUptime;
        });
}

function updateLog() {
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
        });
}

updateStatus();
updateUptime();
updateLog();
setInterval(updateStatus, 1000);
setInterval(updateUptime, 5000);
setInterval(updateLog, 2000);
