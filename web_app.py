#!/usr/bin/env python3
"""BirdNET Web Interface - View detections, play audio, configure settings."""
import os
import json
import sqlite3
import subprocess
import re
import time
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string, send_from_directory, abort, request, Response

try:
    import docker
    docker_client = docker.from_env()
except:
    docker_client = None

app = Flask(__name__)

DB_PATH = os.environ.get('DB_PATH', '/data/birdnet.db')
AUDIO_DIR = os.environ.get('AUDIO_DIR', '/data')
CONFIG_PATH = os.environ.get('CONFIG_PATH', '/data/config.json')

DEFAULT_CONFIG = {
    'yamnet_threshold': 0.25,
    'min_confidence': 0.25,
    'sf_thresh': 0.10,
    'chunk_duration': 5,
    'latitude': 0.0,
    'longitude': 0.0,
    'week': 1
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BirdNET Monitor</title>
    <style>
        :root {
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-card: #0f3460;
            --text-primary: #eee;
            --text-secondary: #aaa;
            --accent: #e94560;
            --accent-hover: #ff6b6b;
            --success: #4ecca3;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--bg-card);
            flex-wrap: wrap;
            gap: 15px;
        }
        h1 { font-size: 1.8rem; display: flex; align-items: center; gap: 10px; }
        h1::before { content: "🐦"; }
        .header-buttons { display: flex; gap: 10px; }
        .btn {
            padding: 8px 16px;
            border-radius: 20px;
            border: none;
            cursor: pointer;
            font-size: 0.9rem;
            transition: background 0.2s;
        }
        .btn-primary { background: var(--accent); color: white; }
        .btn-primary:hover { background: var(--accent-hover); }
        .btn-secondary { background: var(--bg-card); color: var(--text-primary); }
        .btn-secondary:hover { background: var(--bg-secondary); }
        .status {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            background: var(--bg-secondary);
            border-radius: 20px;
            font-size: 0.9rem;
        }
        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--success);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: var(--bg-card);
            padding: 15px;
            border-radius: 12px;
            text-align: center;
        }
        .stat-value { font-size: 2rem; font-weight: bold; color: var(--accent); }
        .stat-label { color: var(--text-secondary); margin-top: 5px; font-size: 0.85rem; }
        .tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--bg-card);
            padding-bottom: 10px;
        }
        .tab {
            padding: 10px 20px;
            background: transparent;
            border: none;
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 1rem;
            border-radius: 8px 8px 0 0;
            transition: all 0.2s;
        }
        .tab.active { background: var(--bg-card); color: var(--text-primary); }
        .tab:hover { color: var(--text-primary); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .section-title {
            font-size: 1.2rem;
            margin-bottom: 15px;
            color: var(--text-secondary);
        }
        .detections { display: grid; gap: 15px; }
        .detection-card {
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 15px;
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 15px;
            align-items: center;
            transition: transform 0.2s;
        }
        .detection-card:hover { transform: translateX(5px); }
        .bird-info h3 { font-size: 1.2rem; margin-bottom: 5px; }
        .bird-meta {
            display: flex;
            gap: 15px;
            color: var(--text-secondary);
            font-size: 0.85rem;
            flex-wrap: wrap;
        }
        .confidence {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 15px;
            font-weight: bold;
            font-size: 0.85rem;
        }
        .confidence.high { background: var(--success); color: #000; }
        .confidence.medium { background: #f39c12; color: #000; }
        .confidence.low { background: var(--accent); color: #fff; }
        .audio-player { display: flex; flex-direction: column; align-items: center; gap: 8px; }
        .play-btn {
            width: 45px;
            height: 45px;
            border-radius: 50%;
            background: var(--accent);
            border: none;
            color: white;
            font-size: 1rem;
            cursor: pointer;
            transition: background 0.2s;
        }
        .play-btn:hover { background: var(--accent-hover); }
        .play-btn:disabled { background: #666; cursor: not-allowed; }
        .audio-status { font-size: 0.7rem; color: var(--text-secondary); }
        audio { display: none; }
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: var(--text-secondary);
        }
        .empty-state::before {
            content: "🔇";
            font-size: 4rem;
            display: block;
            margin-bottom: 20px;
        }
        /* Settings Panel */
        .settings-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
        }
        .setting-group {
            background: var(--bg-secondary);
            padding: 20px;
            border-radius: 12px;
        }
        .setting-group h3 {
            margin-bottom: 15px;
            color: var(--accent);
            font-size: 1rem;
        }
        .setting-item {
            margin-bottom: 15px;
        }
        .setting-item:last-child { margin-bottom: 0; }
        .setting-item label {
            display: block;
            margin-bottom: 5px;
            color: var(--text-secondary);
            font-size: 0.85rem;
        }
        .setting-item input, .setting-item select {
            width: 100%;
            padding: 10px;
            border-radius: 8px;
            border: 1px solid var(--bg-card);
            background: var(--bg-primary);
            color: var(--text-primary);
            font-size: 1rem;
        }
        .setting-item input:focus, .setting-item select:focus {
            outline: none;
            border-color: var(--accent);
        }
        .setting-hint {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }
        .save-status {
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            display: none;
        }
        .save-status.success { display: block; background: rgba(78, 204, 163, 0.2); color: var(--success); }
        .save-status.error { display: block; background: rgba(233, 69, 96, 0.2); color: var(--accent); }
        .refresh-info {
            text-align: center;
            color: var(--text-secondary);
            font-size: 0.85rem;
            margin-top: 30px;
        }
        /* Status indicator states */
        .status-dot.listening { background: var(--success); animation: pulse 2s infinite; }
        .status-dot.stale { background: #f39c12; animation: pulse 1s infinite; }
        .status-dot.offline { background: var(--accent); animation: none; }
        /* Logs Panel */
        .log-container {
            background: #0a0a12;
            border-radius: 12px;
            padding: 15px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-size: 0.8rem;
            max-height: 500px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .log-line { padding: 2px 0; border-bottom: 1px solid #1a1a2e; }
        .log-line:last-child { border-bottom: none; }
        .log-line.info { color: #4ecca3; }
        .log-line.warning { color: #f39c12; }
        .log-line.error { color: #e94560; }
        .log-line.debug { color: #888; }
        .log-controls {
            display: flex;
            gap: 10px;
            margin-bottom: 15px;
            align-items: center;
            flex-wrap: wrap;
        }
        .log-controls select {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--bg-card);
            background: var(--bg-primary);
            color: var(--text-primary);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>BirdNET Monitor</h1>
            <div class="status" id="detector-status">
                <div class="status-dot offline" id="status-dot"></div>
                <span id="status-text">Checking...</span>
            </div>
        </header>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-value" id="today-count">-</div>
                <div class="stat-label">Today</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="week-count">-</div>
                <div class="stat-label">This Week</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="species-count">-</div>
                <div class="stat-label">Species</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" id="total-count">-</div>
                <div class="stat-label">All Time</div>
            </div>
        </div>
        
        <div class="tabs">
            <button class="tab active" data-tab="detections">Recent Detections</button>
            <button class="tab" data-tab="logs">Logs</button>
            <button class="tab" data-tab="settings">Settings</button>
        </div>
        
        <div id="detections-tab" class="tab-content active">
            <div class="detections" id="detections">
                <div class="empty-state">Loading detections...</div>
            </div>
        </div>

        <div id="logs-tab" class="tab-content">
            <div class="log-controls">
                <select id="log-lines">
                    <option value="50">Last 50 lines</option>
                    <option value="100" selected>Last 100 lines</option>
                    <option value="200">Last 200 lines</option>
                    <option value="500">Last 500 lines</option>
                </select>
                <button class="btn btn-secondary" onclick="loadLogs()">Refresh</button>
                <label style="margin-left: auto; display: flex; align-items: center; gap: 5px;">
                    <input type="checkbox" id="auto-scroll" checked> Auto-scroll
                </label>
            </div>
            <div class="log-container" id="log-container">
                <div class="log-line debug">Loading logs...</div>
            </div>
        </div>

        <div id="settings-tab" class="tab-content">
            <div class="settings-grid">
                <div class="setting-group">
                    <h3>Detection Thresholds</h3>
                    <div class="setting-item">
                        <label for="yamnet_threshold">YAMNet Pre-filter Threshold</label>
                        <input type="number" id="yamnet_threshold" min="0" max="1" step="0.05" value="0.25">
                        <div class="setting-hint">Higher = fewer false positives, may miss quiet birds (0.1-0.5)</div>
                    </div>
                    <div class="setting-item">
                        <label for="min_confidence">BirdNET Minimum Confidence</label>
                        <input type="number" id="min_confidence" min="0" max="1" step="0.05" value="0.25">
                        <div class="setting-hint">Detections below this won't be saved (0.1-0.5)</div>
                    </div>
                    <div class="setting-item">
                        <label for="sf_thresh">Species Frequency Threshold</label>
                        <input type="number" id="sf_thresh" min="0" max="1" step="0.01" value="0.10">
                        <div class="setting-hint">Filter by species occurrence in your area (0.01-0.25)</div>
                    </div>
                </div>
                
                <div class="setting-group">
                    <h3>Location</h3>
                    <div class="setting-item">
                        <label for="latitude">Latitude</label>
                        <input type="number" id="latitude" min="-90" max="90" step="0.00001" value="0.0">
                    </div>
                    <div class="setting-item">
                        <label for="longitude">Longitude</label>
                        <input type="number" id="longitude" min="-180" max="180" step="0.00001" value="0.0">
                    </div>
                    <div class="setting-item">
                        <label for="week">Week of Year</label>
                        <input type="number" id="week" min="1" max="52" value="1">
                        <div class="setting-hint">Used for seasonal species filtering</div>
                    </div>
                </div>
                
                <div class="setting-group">
                    <h3>Audio Processing</h3>
                    <div class="setting-item">
                        <label for="chunk_duration">Chunk Duration (seconds)</label>
                        <input type="number" id="chunk_duration" min="3" max="15" value="5">
                        <div class="setting-hint">Length of audio clips to analyze (3-15s)</div>
                    </div>
                </div>
            </div>
            
            <div style="margin-top: 20px; text-align: center;">
                <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
                <button class="btn btn-secondary" onclick="restartDetector()" style="margin-left: 10px;">Restart Detector</button>
            </div>
            <div id="save-status" class="save-status"></div>
            <p class="refresh-info">Note: Detector must be restarted for changes to take effect</p>
        </div>
        
        <p class="refresh-info" id="refresh-info">Auto-refreshes every 30 seconds</p>
    </div>
    
    <script>
        // Tab switching
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                document.getElementById(tab.dataset.tab + '-tab').classList.add('active');
            });
        });
        
        function formatTime(isoString) {
            const date = new Date(isoString);
            return date.toLocaleString();
        }
        
        function getConfidenceClass(conf) {
            if (conf >= 0.7) return 'high';
            if (conf >= 0.4) return 'medium';
            return 'low';
        }
        
        async function checkAudio(url) {
            try {
                const resp = await fetch(url, { method: 'HEAD' });
                return resp.ok;
            } catch {
                return false;
            }
        }
        
        function createDetectionCard(det) {
            const card = document.createElement('div');
            card.className = 'detection-card';
            card.innerHTML = `
                <div class="bird-info">
                    <h3>${det.common_name}</h3>
                    <div class="bird-meta">
                        <span>${formatTime(det.timestamp)}</span>
                        <span class="confidence ${getConfidenceClass(det.confidence)}">
                            ${(det.confidence * 100).toFixed(1)}%
                        </span>
                    </div>
                </div>
                <div class="audio-player">
                    <button class="play-btn" data-audio="${det.audio_url || ''}" ${!det.audio_url ? 'disabled' : ''}>▶</button>
                    <span class="audio-status">${det.audio_url ? 'Play' : 'No audio'}</span>
                    <audio preload="none"></audio>
                </div>
            `;
            
            const btn = card.querySelector('.play-btn');
            const audio = card.querySelector('audio');
            const status = card.querySelector('.audio-status');
            
            if (det.audio_url) {
                btn.addEventListener('click', async () => {
                    if (audio.src) {
                        if (audio.paused) {
                            audio.play();
                            btn.textContent = '⏸';
                        } else {
                            audio.pause();
                            btn.textContent = '▶';
                        }
                    } else {
                        status.textContent = 'Loading...';
                        const available = await checkAudio(det.audio_url);
                        if (available) {
                            audio.src = det.audio_url;
                            audio.play();
                            btn.textContent = '⏸';
                            status.textContent = 'Playing';
                        } else {
                            btn.disabled = true;
                            status.textContent = 'Pruned';
                        }
                    }
                });
                
                audio.addEventListener('ended', () => {
                    btn.textContent = '▶';
                    status.textContent = 'Play';
                });
            }
            
            return card;
        }
        
        async function loadData() {
            try {
                const [statsResp, detectionsResp] = await Promise.all([
                    fetch('/api/stats'),
                    fetch('/api/detections?limit=50')
                ]);
                
                const stats = await statsResp.json();
                const detections = await detectionsResp.json();
                
                document.getElementById('today-count').textContent = stats.today || 0;
                document.getElementById('week-count').textContent = stats.week || 0;
                document.getElementById('species-count').textContent = stats.species || 0;
                document.getElementById('total-count').textContent = stats.total || 0;
                
                const container = document.getElementById('detections');
                container.innerHTML = '';
                
                if (detections.length === 0) {
                    container.innerHTML = '<div class="empty-state">No detections yet. Waiting for birds...</div>';
                } else {
                    detections.forEach(det => {
                        container.appendChild(createDetectionCard(det));
                    });
                }
            } catch (err) {
                console.error('Failed to load data:', err);
            }
        }
        
        async function loadSettings() {
            try {
                const resp = await fetch('/api/config');
                const config = await resp.json();
                document.getElementById('yamnet_threshold').value = config.yamnet_threshold;
                document.getElementById('min_confidence').value = config.min_confidence;
                document.getElementById('sf_thresh').value = config.sf_thresh;
                document.getElementById('chunk_duration').value = config.chunk_duration;
                document.getElementById('latitude').value = config.latitude;
                document.getElementById('longitude').value = config.longitude;
                document.getElementById('week').value = config.week;
            } catch (err) {
                console.error('Failed to load settings:', err);
            }
        }
        
        async function saveSettings() {
            const config = {
                yamnet_threshold: parseFloat(document.getElementById('yamnet_threshold').value),
                min_confidence: parseFloat(document.getElementById('min_confidence').value),
                sf_thresh: parseFloat(document.getElementById('sf_thresh').value),
                chunk_duration: parseInt(document.getElementById('chunk_duration').value),
                latitude: parseFloat(document.getElementById('latitude').value),
                longitude: parseFloat(document.getElementById('longitude').value),
                week: parseInt(document.getElementById('week').value)
            };
            
            try {
                const resp = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                
                const status = document.getElementById('save-status');
                if (resp.ok) {
                    status.className = 'save-status success';
                    status.textContent = 'Settings saved! Restart detector to apply changes.';
                } else {
                    status.className = 'save-status error';
                    status.textContent = 'Failed to save settings';
                }
                setTimeout(() => { status.className = 'save-status'; }, 5000);
            } catch (err) {
                console.error('Failed to save settings:', err);
            }
        }
        
        async function restartDetector() {
            if (!confirm('Restart the BirdNET detector? This will briefly interrupt detection.')) return;
            try {
                const resp = await fetch('/api/restart', { method: 'POST' });
                const status = document.getElementById('save-status');
                if (resp.ok) {
                    status.className = 'save-status success';
                    status.textContent = 'Detector restart initiated';
                } else {
                    status.className = 'save-status error';
                    status.textContent = 'Failed to restart detector';
                }
                setTimeout(() => { status.className = 'save-status'; }, 5000);
            } catch (err) {
                console.error('Failed to restart:', err);
            }
        }
        
        // Load logs
        async function loadLogs() {
            const lines = document.getElementById('log-lines').value;
            try {
                const resp = await fetch(`/api/logs?lines=${lines}`);
                const data = await resp.json();
                const container = document.getElementById('log-container');
                container.innerHTML = '';

                data.logs.forEach(line => {
                    const div = document.createElement('div');
                    div.className = 'log-line';
                    if (line.includes('[ERROR]') || line.includes('Error') || line.includes('error')) {
                        div.className += ' error';
                    } else if (line.includes('[WARNING]') || line.includes('Warning')) {
                        div.className += ' warning';
                    } else if (line.includes('[INFO]')) {
                        div.className += ' info';
                    } else {
                        div.className += ' debug';
                    }
                    div.textContent = line;
                    container.appendChild(div);
                });

                if (document.getElementById('auto-scroll').checked) {
                    container.scrollTop = container.scrollHeight;
                }
            } catch (err) {
                console.error('Failed to load logs:', err);
            }
        }

        // Check detector status
        async function checkStatus() {
            try {
                const resp = await fetch('/api/detector-status');
                const data = await resp.json();
                const dot = document.getElementById('status-dot');
                const text = document.getElementById('status-text');

                dot.className = 'status-dot ' + data.status;
                if (data.status === 'listening') {
                    text.textContent = 'Listening';
                } else if (data.status === 'stale') {
                    text.textContent = `Stale (${data.minutes_ago}m ago)`;
                } else {
                    text.textContent = 'Offline';
                }
            } catch (err) {
                document.getElementById('status-dot').className = 'status-dot offline';
                document.getElementById('status-text').textContent = 'Unknown';
            }
        }

        loadData();
        loadSettings();
        loadLogs();
        checkStatus();
        setInterval(loadData, 30000);
        setInterval(checkStatus, 15000);
        setInterval(() => {
            if (document.getElementById('logs-tab').classList.contains('active')) {
                loadLogs();
            }
        }, 10000);
    </script>
</body>
</html>
'''

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/stats')
def stats():
    conn = get_db()
    cur = conn.cursor()
    
    today = datetime.utcnow().strftime('%Y-%m-%d')
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    
    cur.execute("SELECT COUNT(*) FROM detections WHERE timestamp >= ?", (today,))
    today_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM detections WHERE timestamp >= ?", (week_ago,))
    week_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(DISTINCT species_code) FROM detections")
    species_count = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM detections")
    total_count = cur.fetchone()[0]
    
    conn.close()
    
    return jsonify({
        'today': today_count,
        'week': week_count,
        'species': species_count,
        'total': total_count
    })

@app.route('/api/detections')
def detections():
    limit = min(int(request.args.get('limit', 50)), 500)
    since = request.args.get('since')

    conn = get_db()
    cur = conn.cursor()
    if since:
        cur.execute('''
            SELECT id, timestamp, common_name, species_code, confidence, audio_file
            FROM detections
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (since, limit))
    else:
        cur.execute('''
            SELECT id, timestamp, common_name, species_code, confidence, audio_file
            FROM detections
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))

    rows = cur.fetchall()
    conn.close()

    results = []
    for row in rows:
        audio_file = row['audio_file']
        audio_url = None
        if audio_file:
            audio_url = f"/audio/{os.path.basename(audio_file)}"

        results.append({
            'id': row['id'],
            'timestamp': row['timestamp'],
            'common_name': row['common_name'],
            'species_code': row['species_code'],
            'confidence': row['confidence'],
            'audio_url': audio_url
        })

    return jsonify(results)

@app.route('/api/config', methods=['GET', 'POST'])
def config():
    if request.method == 'GET':
        return jsonify(load_config())
    else:
        new_config = request.get_json()
        current = load_config()
        current.update(new_config)
        save_config(current)
        return jsonify({'status': 'ok'})

@app.route('/api/restart', methods=['POST'])
def restart():
    try:
        if docker_client is None:
            return jsonify({'status': 'error', 'message': 'Docker client not available'}), 500

        container = docker_client.containers.get('birdnet-detector')
        container.restart()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/audio/<filename>')
def serve_audio(filename):
    if not filename.endswith('.wav') or '/' in filename or '\\\\' in filename:
        abort(400)
    
    filepath = os.path.join(AUDIO_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    
    return send_from_directory(AUDIO_DIR, filename, mimetype='audio/wav')

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/api/logs')
def logs():
    lines = min(int(request.args.get('lines', 100)), 1000)
    try:
        if docker_client is None:
            return jsonify({'logs': ['Docker client not available']}), 500

        container = docker_client.containers.get('birdnet-detector')
        log_output = container.logs(tail=lines, timestamps=False).decode('utf-8', errors='replace')
        log_lines = log_output.strip().split('\n') if log_output.strip() else []
        return jsonify({'logs': log_lines})
    except Exception as e:
        return jsonify({'logs': [f'Error: {str(e)}']}), 500

@app.route('/api/detector-status')
def detector_status():
    try:
        if docker_client is None:
            return jsonify({'status': 'offline', 'error': 'Docker client not available'})

        container = docker_client.containers.get('birdnet-detector')

        # Check container status first
        if container.status != 'running':
            return jsonify({'status': 'offline', 'container_status': container.status})

        # Get last few log lines and check timestamp
        log_output = container.logs(tail=20, timestamps=False).decode('utf-8', errors='replace')

        # Parse timestamps from log lines (format: 2026-01-10 17:09:07 [INFO])
        timestamps = re.findall(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', log_output)

        if not timestamps:
            return jsonify({'status': 'offline', 'last_activity': None})

        # Get most recent timestamp
        last_ts = timestamps[-1]
        last_dt = datetime.strptime(last_ts, '%Y-%m-%d %H:%M:%S')
        now = datetime.utcnow()
        diff = now - last_dt
        minutes_ago = int(diff.total_seconds() / 60)

        if minutes_ago < 2:
            return jsonify({'status': 'listening', 'minutes_ago': minutes_ago, 'last_activity': last_ts})
        elif minutes_ago < 10:
            return jsonify({'status': 'stale', 'minutes_ago': minutes_ago, 'last_activity': last_ts})
        else:
            return jsonify({'status': 'offline', 'minutes_ago': minutes_ago, 'last_activity': last_ts})

    except Exception as e:
        return jsonify({'status': 'offline', 'error': str(e)})

RTSP_URL = os.environ.get('RTSP_URL', '')

@app.route('/api/audio-stream')
def audio_stream():
    """Stream unfiltered audio from RTSP as MP3 for browser playback."""
    if not RTSP_URL:
        abort(503, 'RTSP_URL not configured')

    def generate():
        cmd = [
            'ffmpeg', '-rtsp_transport', 'tcp', '-i', RTSP_URL,
            '-vn', '-f', 'mp3', '-acodec', 'libmp3lame',
            '-ac', '1', '-ar', '16000', '-b:a', '64k',
            '-'
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.kill()

    return Response(generate(), mimetype='audio/mpeg',
                    headers={'Cache-Control': 'no-cache'})

TUNER_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Audio Filter Tuner</title>
    <style>
        :root {
            --bg-primary: #1a1a2e;
            --bg-secondary: #16213e;
            --bg-card: #0f3460;
            --text-primary: #eee;
            --text-secondary: #aaa;
            --accent: #e94560;
            --success: #4ecca3;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { font-size: 1.5rem; margin-bottom: 20px; }
        .controls {
            display: grid;
            gap: 20px;
            margin-bottom: 20px;
        }
        .filter-group {
            background: var(--bg-secondary);
            padding: 20px;
            border-radius: 12px;
        }
        .filter-group h3 {
            color: var(--accent);
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .filter-row {
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 10px;
        }
        .filter-row label {
            min-width: 80px;
            color: var(--text-secondary);
            font-size: 0.85rem;
        }
        .filter-row input[type="range"] {
            flex: 1;
            accent-color: var(--accent);
        }
        .filter-row .value {
            min-width: 70px;
            text-align: right;
            font-family: monospace;
            font-size: 1rem;
            color: var(--success);
        }
        .filter-row input[type="checkbox"] {
            width: 20px;
            height: 20px;
            accent-color: var(--accent);
        }
        canvas {
            width: 100%;
            height: 300px;
            background: #0a0a12;
            border-radius: 12px;
            display: block;
            margin-bottom: 20px;
        }
        .btn {
            padding: 12px 24px;
            border-radius: 20px;
            border: none;
            cursor: pointer;
            font-size: 1rem;
            transition: background 0.2s;
        }
        .btn-primary { background: var(--accent); color: white; }
        .btn-primary:hover { background: #ff6b6b; }
        .btn-primary.active { background: var(--success); }
        .btn-secondary { background: var(--bg-card); color: var(--text-primary); }
        .btn-secondary:hover { background: var(--bg-secondary); }
        .button-row {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            align-items: center;
        }
        .status-text {
            color: var(--text-secondary);
            font-size: 0.85rem;
        }
        .output-box {
            background: #0a0a12;
            padding: 15px;
            border-radius: 12px;
            font-family: monospace;
            font-size: 0.9rem;
            color: var(--success);
        }
        a { color: var(--accent); }
    </style>
</head>
<body>
    <div class="container">
        <h1>Audio Filter Tuner</h1>
        <p style="color: var(--text-secondary); margin-bottom: 20px;">
            Adjust filters in real-time to find the right cutoff values.
            <a href="/">Back to dashboard</a>
        </p>

        <div class="button-row">
            <button class="btn btn-primary" id="start-btn">Start Listening</button>
            <span class="status-text" id="status">Stopped</span>
        </div>

        <canvas id="spectrum"></canvas>

        <div class="controls">
            <div class="filter-group">
                <h3>Highpass Filter <span style="font-size: 0.8rem; color: var(--text-secondary);">Removes low rumble</span></h3>
                <div class="filter-row">
                    <input type="checkbox" id="hp-enabled" checked>
                    <label>Cutoff</label>
                    <input type="range" id="hp-freq" min="50" max="1000" value="200" step="10">
                    <span class="value" id="hp-value">200 Hz</span>
                </div>
            </div>
            <div class="filter-group">
                <h3>Lowpass Filter <span style="font-size: 0.8rem; color: var(--text-secondary);">Removes high hiss</span></h3>
                <div class="filter-row">
                    <input type="checkbox" id="lp-enabled">
                    <label>Cutoff</label>
                    <input type="range" id="lp-freq" min="2000" max="7500" value="7500" step="100">
                    <span class="value" id="lp-value">7500 Hz</span>
                </div>
            </div>
        </div>

        <div class="output-box" id="output">
            FFmpeg filter: highpass=f=200
        </div>
    </div>

    <script>
        let audioCtx, sourceNode, hpFilter, lpFilter, analyser, animId;
        let audioEl;
        let running = false;

        const hpFreq = document.getElementById('hp-freq');
        const lpFreq = document.getElementById('lp-freq');
        const hpValue = document.getElementById('hp-value');
        const lpValue = document.getElementById('lp-value');
        const hpEnabled = document.getElementById('hp-enabled');
        const lpEnabled = document.getElementById('lp-enabled');
        const startBtn = document.getElementById('start-btn');
        const statusEl = document.getElementById('status');
        const output = document.getElementById('output');
        const canvas = document.getElementById('spectrum');
        const canvasCtx = canvas.getContext('2d');

        function updateOutput() {
            const parts = [];
            if (hpEnabled.checked) parts.push(`highpass=f=${hpFreq.value}`);
            if (lpEnabled.checked) parts.push(`lowpass=f=${lpFreq.value}`);
            output.textContent = parts.length
                ? `FFmpeg filter: ${parts.join(',')}`
                : 'FFmpeg filter: (none)';
        }

        function updateFilters() {
            if (hpFilter) {
                hpFilter.frequency.setValueAtTime(
                    hpEnabled.checked ? parseFloat(hpFreq.value) : 1,
                    audioCtx.currentTime
                );
            }
            if (lpFilter) {
                lpFilter.frequency.setValueAtTime(
                    lpEnabled.checked ? parseFloat(lpFreq.value) : audioCtx.sampleRate / 2,
                    audioCtx.currentTime
                );
            }
            hpValue.textContent = hpFreq.value + ' Hz';
            lpValue.textContent = lpFreq.value + ' Hz';
            updateOutput();
        }

        hpFreq.addEventListener('input', updateFilters);
        lpFreq.addEventListener('input', updateFilters);
        hpEnabled.addEventListener('change', updateFilters);
        lpEnabled.addEventListener('change', updateFilters);

        function drawSpectrum() {
            if (!running) return;
            animId = requestAnimationFrame(drawSpectrum);
            if (!analyser) return;

            const dpr = window.devicePixelRatio || 1;
            const rect = canvas.getBoundingClientRect();
            canvas.width = rect.width * dpr;
            canvas.height = rect.height * dpr;
            const w = canvas.width;
            const h = canvas.height;

            const bufLen = analyser.frequencyBinCount;
            const data = new Uint8Array(bufLen);
            analyser.getByteFrequencyData(data);

            canvasCtx.fillStyle = '#0a0a12';
            canvasCtx.fillRect(0, 0, w, h);

            // Only show 0-8kHz (audio source is 16kHz, nothing above 8k)
            const nyquist = audioCtx ? audioCtx.sampleRate / 2 : 24000;
            const maxDisplayHz = 8000;
            const maxBin = Math.ceil((maxDisplayHz / nyquist) * bufLen);
            const hzPerBin = nyquist / bufLen;

            // Draw frequency bars (linear scale within 0-8kHz)
            const barW = Math.max(1, w / maxBin);
            for (let i = 0; i < maxBin; i++) {
                const v = data[i] / 255;
                const barH = v * h * 0.9;
                const hue = 200 + v * 160;
                canvasCtx.fillStyle = `hsl(${hue}, 80%, ${40 + v * 30}%)`;
                canvasCtx.fillRect(i * barW, h - barH - 20 * dpr, barW + 1, barH);
            }

            // Frequency axis labels
            canvasCtx.fillStyle = '#666';
            canvasCtx.font = `${11 * dpr}px monospace`;
            const labelFreqs = [100, 200, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000];
            for (const f of labelFreqs) {
                if (f > maxDisplayHz) break;
                const x = (f / maxDisplayHz) * w;
                canvasCtx.fillText(f >= 1000 ? (f/1000) + 'k' : f + '', x, h - 4 * dpr);
                canvasCtx.fillRect(x, h - 18 * dpr, 1, 4 * dpr);
            }

            // Helper to map Hz to canvas x
            function hzToX(hz) { return (hz / maxDisplayHz) * w; }

            // Draw filter cutoff lines
            if (hpEnabled.checked) {
                const x = hzToX(parseFloat(hpFreq.value));
                canvasCtx.strokeStyle = '#e94560';
                canvasCtx.lineWidth = 2 * dpr;
                canvasCtx.setLineDash([5 * dpr, 5 * dpr]);
                canvasCtx.beginPath();
                canvasCtx.moveTo(x, 0);
                canvasCtx.lineTo(x, h - 20 * dpr);
                canvasCtx.stroke();
                canvasCtx.setLineDash([]);
                canvasCtx.fillStyle = '#e94560';
                canvasCtx.font = `bold ${12 * dpr}px sans-serif`;
                canvasCtx.fillText('HP ' + hpFreq.value + ' Hz', x + 4 * dpr, 16 * dpr);
            }
            if (lpEnabled.checked) {
                const x = hzToX(parseFloat(lpFreq.value));
                canvasCtx.strokeStyle = '#4ecca3';
                canvasCtx.lineWidth = 2 * dpr;
                canvasCtx.setLineDash([5 * dpr, 5 * dpr]);
                canvasCtx.beginPath();
                canvasCtx.moveTo(x, 0);
                canvasCtx.lineTo(x, h - 20 * dpr);
                canvasCtx.stroke();
                canvasCtx.setLineDash([]);
                canvasCtx.fillStyle = '#4ecca3';
                canvasCtx.font = `bold ${12 * dpr}px sans-serif`;
                canvasCtx.fillText('LP ' + lpFreq.value + ' Hz', x + 4 * dpr, 16 * dpr);
            }
        }

        async function stop() {
            running = false;
            cancelAnimationFrame(animId);
            if (audioEl) { audioEl.pause(); audioEl.srcObject = null; audioEl.src = ''; }
            if (audioCtx) { await audioCtx.close(); }
            audioCtx = null; sourceNode = null; hpFilter = null; lpFilter = null; analyser = null;
            startBtn.textContent = 'Start Listening';
            startBtn.classList.remove('active');
            statusEl.textContent = 'Stopped';
        }

        async function start() {
            if (running) { await stop(); return; }

            statusEl.textContent = 'Connecting...';
            startBtn.disabled = true;

            try {
                audioCtx = new AudioContext();
                audioEl = new Audio();
                audioEl.src = '/api/audio-stream';

                // Wait for audio to have enough data
                await new Promise((resolve, reject) => {
                    audioEl.addEventListener('canplay', resolve, { once: true });
                    audioEl.addEventListener('error', () => reject(new Error('Stream failed to load')), { once: true });
                    setTimeout(() => reject(new Error('Timeout connecting to stream')), 15000);
                });

                sourceNode = audioCtx.createMediaElementSource(audioEl);

                hpFilter = audioCtx.createBiquadFilter();
                hpFilter.type = 'highpass';
                hpFilter.frequency.value = hpEnabled.checked ? parseFloat(hpFreq.value) : 1;

                lpFilter = audioCtx.createBiquadFilter();
                lpFilter.type = 'lowpass';
                lpFilter.frequency.value = lpEnabled.checked ? parseFloat(lpFreq.value) : audioCtx.sampleRate / 2;

                analyser = audioCtx.createAnalyser();
                analyser.fftSize = 2048;
                analyser.smoothingTimeConstant = 0.8;

                // Chain: source -> highpass -> lowpass -> analyser -> speakers
                sourceNode.connect(hpFilter);
                hpFilter.connect(lpFilter);
                lpFilter.connect(analyser);
                analyser.connect(audioCtx.destination);

                await audioEl.play();
                running = true;
                startBtn.textContent = 'Stop';
                startBtn.classList.add('active');
                statusEl.textContent = `Streaming (${audioCtx.sampleRate} Hz)`;
                drawSpectrum();
            } catch (e) {
                statusEl.textContent = 'Error: ' + e.message;
                if (audioCtx) { await audioCtx.close(); audioCtx = null; }
            } finally {
                startBtn.disabled = false;
            }
        }

        startBtn.addEventListener('click', start);
    </script>
</body>
</html>
'''

@app.route('/tuner')
def tuner():
    return render_template_string(TUNER_HTML)

@app.route('/api/events')
def events():
    """SSE endpoint — streams new detections as they appear in the DB."""
    def generate():
        # Start from the latest ID currently in the DB
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT MAX(id) FROM detections')
        row = cur.fetchone()
        last_id = row[0] or 0
        conn.close()

        heartbeat_interval = 15
        poll_interval = 2
        last_heartbeat = time.time()

        while True:
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute('''
                    SELECT id, timestamp, common_name, species_code, confidence, audio_file
                    FROM detections
                    WHERE id > ?
                    ORDER BY id ASC
                ''', (last_id,))
                rows = cur.fetchall()
                conn.close()

                for r in rows:
                    audio_file = r['audio_file']
                    audio_url = f"/audio/{os.path.basename(audio_file)}" if audio_file else None
                    data = json.dumps({
                        'id': r['id'],
                        'timestamp': r['timestamp'],
                        'common_name': r['common_name'],
                        'species_code': r['species_code'],
                        'confidence': r['confidence'],
                        'audio_url': audio_url
                    })
                    yield f"data: {data}\n\n"
                    last_id = r['id']

                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    yield "event: heartbeat\ndata: ping\n\n"
                    last_heartbeat = now

            except Exception:
                pass

            time.sleep(poll_interval)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

LIVE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BirdNET Live</title>
    <style>
        :root {
            --bg: #fafafa;
            --card-bg: #ffffff;
            --card-border: #e8e8e8;
            --text: #2c2c2c;
            --text-dim: #888888;
            --accent: #2d8a68;
            --highlight: #f0f8f4;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Georgia', 'Times New Roman', serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            padding: 0;
        }
        .container {
            max-width: 480px;
            margin: 0 auto;
            padding: 20px 16px;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--card-border);
        }
        .header-title {
            font-size: 1.1rem;
            font-weight: normal;
            color: var(--text-dim);
            letter-spacing: 0.5px;
        }
        .header-title .dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            background: var(--accent);
            border-radius: 50%;
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        .header-title .dot.disconnected {
            background: #c0392b;
            animation: none;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
        }
        .header-time {
            font-family: -apple-system, sans-serif;
            font-size: 0.8rem;
            color: var(--text-dim);
        }
        .now-hearing {
            text-align: center;
            padding: 20px;
            margin-bottom: 20px;
            background: var(--highlight);
            border-radius: 12px;
            border: 1px solid rgba(45, 138, 104, 0.2);
            transition: opacity 0.8s;
        }
        .now-hearing.silent {
            opacity: 0.3;
        }
        .now-hearing .label {
            font-family: -apple-system, sans-serif;
            font-size: 0.7rem;
            text-transform: uppercase;
            letter-spacing: 2px;
            color: var(--accent);
            margin-bottom: 8px;
        }
        .now-hearing .species {
            font-size: 1.6rem;
            font-style: italic;
            margin-bottom: 4px;
        }
        .now-hearing .meta {
            font-family: -apple-system, sans-serif;
            font-size: 0.8rem;
            color: var(--text-dim);
        }
        .cards {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        .card {
            display: flex;
            gap: 14px;
            padding: 14px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            transition: opacity 0.8s, transform 0.4s;
            animation: fadeInDown 0.4s ease-out;
        }
        .card.newest {
            border-color: rgba(45, 138, 104, 0.3);
            background: linear-gradient(135deg, var(--card-bg) 0%, var(--highlight) 100%);
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        .card-img {
            width: 56px;
            height: 56px;
            border-radius: 8px;
            object-fit: cover;
            background: #f0f0f0;
            flex-shrink: 0;
        }
        .card-body { flex: 1; min-width: 0; }
        .card-name {
            font-size: 1.05rem;
            font-style: italic;
            margin-bottom: 4px;
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }
        .card-name .count {
            font-family: -apple-system, sans-serif;
            font-style: normal;
            font-size: 0.75rem;
            color: var(--accent);
            background: rgba(45, 138, 104, 0.15);
            padding: 2px 8px;
            border-radius: 10px;
        }
        .card-meta {
            font-family: -apple-system, sans-serif;
            font-size: 0.78rem;
            color: var(--text-dim);
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .confidence { font-weight: 600; }
        .confidence.high { color: var(--accent); }
        .confidence.med { color: #b8860b; }
        .confidence.low { color: #c0392b; }
        .play-btn {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: rgba(45, 138, 104, 0.15);
            border: 1px solid rgba(45, 138, 104, 0.3);
            color: var(--accent);
            font-size: 0.65rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            align-self: center;
        }
        .play-btn:hover { background: rgba(45, 138, 104, 0.3); }
        .footer {
            margin-top: 24px;
            padding-top: 16px;
            border-top: 1px solid var(--card-border);
            font-family: -apple-system, sans-serif;
            font-size: 0.8rem;
            color: var(--text-dim);
            text-align: center;
        }
        @keyframes fadeInDown {
            from { opacity: 0; transform: translateY(-20px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @media (min-width: 768px) {
            .container { max-width: 520px; padding: 30px 24px; }
            .now-hearing .species { font-size: 2rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title"><span class="dot" id="status-dot"></span>Backyard &middot; Listening</div>
            <div class="header-time" id="clock"></div>
        </div>

        <div class="now-hearing silent" id="now-hearing">
            <div class="label">Now Hearing</div>
            <div class="species" id="now-species">&#8212;</div>
            <div class="meta" id="now-meta">Waiting for birds&hellip;</div>
        </div>

        <div class="cards" id="cards"></div>

        <div class="footer" id="footer">
            Today: 0 detections &middot; 0 species
        </div>
    </div>

<script>
(function() {
    // State: species_code -> { common_name, species_code, count, bestConfidence, latestTimestamp, latestAudioUrl, latestId, imgUrl }
    const speciesMap = {};
    const EXPIRY_MS = 60 * 60 * 1000; // 1 hour
    const NOW_HEARING_FADE_MS = 2 * 60 * 1000; // fade "now hearing" after 2 min of silence
    let lastDetectionTime = 0;
    let totalToday = 0;
    let speciesSeenToday = new Set();
    let evtSource = null;

    // --- Image cache using localStorage ---
    const IMG_CACHE_KEY = 'birdnet_img_cache';
    const IMG_CACHE_TTL = 7 * 24 * 60 * 60 * 1000; // 7 days

    function getImgCache() {
        try {
            return JSON.parse(localStorage.getItem(IMG_CACHE_KEY) || '{}');
        } catch { return {}; }
    }
    function setImgCache(code, url) {
        try {
            const cache = getImgCache();
            cache[code] = { url: url, ts: Date.now() };
            localStorage.setItem(IMG_CACHE_KEY, JSON.stringify(cache));
        } catch {}
    }
    function getCachedImg(code) {
        const cache = getImgCache();
        const entry = cache[code];
        if (entry && (Date.now() - entry.ts) < IMG_CACHE_TTL) return entry.url;
        return null;
    }

    // Generic bird silhouette SVG as fallback
    const FALLBACK_IMG = 'data:image/svg+xml,' + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" fill="%232d8a68" opacity="0.3">' +
        '<ellipse cx="45" cy="55" rx="25" ry="18"/>' +
        '<circle cx="30" cy="45" r="10"/>' +
        '<polygon points="20,45 8,42 20,48"/>' +
        '<polygon points="65,60 90,55 88,65"/>' +
        '</svg>'
    );

    async function fetchBirdImage(commonName) {
        const cached = getCachedImg(commonName);
        if (cached) return cached;
        try {
            // Use Wikipedia API to find the bird's image
            const title = commonName.replace(/ /g, '_');
            const url = 'https://en.wikipedia.org/api/rest_v1/page/summary/' + encodeURIComponent(title);
            const resp = await fetch(url);
            if (resp.ok) {
                const data = await resp.json();
                if (data.thumbnail && data.thumbnail.source) {
                    const imgUrl = data.thumbnail.source;
                    setImgCache(commonName, imgUrl);
                    return imgUrl;
                }
            }
        } catch {}
        setImgCache(commonName, FALLBACK_IMG);
        return FALLBACK_IMG;
    }

    // --- Clock ---
    function updateClock() {
        document.getElementById('clock').textContent =
            new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }
    updateClock();
    setInterval(updateClock, 30000);

    // --- Formatting helpers ---
    function confClass(c) { return c >= 0.7 ? 'high' : c >= 0.4 ? 'med' : 'low'; }
    function fmtTime(ts) {
        const d = new Date(ts);
        return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }
    function ageOpacity(ts) {
        const age = Date.now() - new Date(ts).getTime();
        const frac = age / EXPIRY_MS;
        if (frac < 0.25) return 1;
        if (frac < 0.5) return 0.85;
        if (frac < 0.75) return 0.65;
        return 0.45;
    }

    // --- Process a detection (from backfill or SSE) ---
    function processDetection(det) {
        const code = det.species_code;
        speciesSeenToday.add(code);

        if (speciesMap[code]) {
            const s = speciesMap[code];
            s.count++;
            if (new Date(det.timestamp) > new Date(s.latestTimestamp)) {
                s.latestTimestamp = det.timestamp;
                s.latestAudioUrl = det.audio_url;
                s.latestId = det.id;
            }
            if (det.confidence > s.bestConfidence) {
                s.bestConfidence = det.confidence;
            }
        } else {
            speciesMap[code] = {
                common_name: det.common_name,
                species_code: code,
                count: 1,
                bestConfidence: det.confidence,
                latestTimestamp: det.timestamp,
                latestAudioUrl: det.audio_url,
                latestId: det.id,
                imgUrl: null
            };
            // Fetch image async using common name (Wikipedia lookup)
            fetchBirdImage(det.common_name).then(url => {
                if (speciesMap[code]) {
                    speciesMap[code].imgUrl = url;
                    renderCards();
                }
            });
        }

        lastDetectionTime = Math.max(lastDetectionTime, new Date(det.timestamp).getTime());
        totalToday++;
    }

    // --- Render ---
    function renderCards() {
        // Remove expired species
        const now = Date.now();
        for (const code of Object.keys(speciesMap)) {
            if (now - new Date(speciesMap[code].latestTimestamp).getTime() > EXPIRY_MS) {
                delete speciesMap[code];
            }
        }

        // Sort by latest timestamp descending
        const sorted = Object.values(speciesMap).sort(
            (a, b) => new Date(b.latestTimestamp) - new Date(a.latestTimestamp)
        );

        const container = document.getElementById('cards');
        container.innerHTML = '';

        sorted.forEach((s, i) => {
            const opacity = ageOpacity(s.latestTimestamp);
            const isNewest = i === 0 && (now - new Date(s.latestTimestamp).getTime() < 60000);
            const card = document.createElement('div');
            card.className = 'card' + (isNewest ? ' newest' : '');
            card.style.opacity = opacity;

            const imgSrc = s.imgUrl || FALLBACK_IMG;
            const countBadge = s.count > 1 ? '<span class="count">x' + s.count + '</span>' : '';
            const audioBtn = s.latestAudioUrl
                ? '<button class="play-btn" data-audio="' + s.latestAudioUrl + '">&#9654;</button>'
                : '';

            card.innerHTML =
                '<img class="card-img" src="' + imgSrc + '" alt="' + s.common_name + '" loading="lazy">' +
                '<div class="card-body">' +
                '  <div class="card-name">' + s.common_name + ' ' + countBadge + '</div>' +
                '  <div class="card-meta">' +
                '    <span class="confidence ' + confClass(s.bestConfidence) + '">' + Math.round(s.bestConfidence * 100) + '%</span>' +
                '    <span>' + fmtTime(s.latestTimestamp) + '</span>' +
                '  </div>' +
                '</div>' +
                audioBtn;

            // Audio playback
            const btn = card.querySelector('.play-btn');
            if (btn) {
                btn.addEventListener('click', function() {
                    const url = this.getAttribute('data-audio');
                    const existing = document.getElementById('live-audio');
                    if (existing) { existing.pause(); existing.remove(); }
                    const audio = document.createElement('audio');
                    audio.id = 'live-audio';
                    audio.src = url;
                    audio.style.display = 'none';
                    document.body.appendChild(audio);
                    audio.play();
                    audio.addEventListener('ended', function() { this.remove(); });
                });
            }

            container.appendChild(card);
        });

        // Update "now hearing"
        const nh = document.getElementById('now-hearing');
        if (sorted.length > 0 && (now - lastDetectionTime < NOW_HEARING_FADE_MS)) {
            nh.classList.remove('silent');
            document.getElementById('now-species').textContent = sorted[0].common_name;
            const ago = Math.round((now - lastDetectionTime) / 1000);
            const agoText = ago < 10 ? 'just now' : ago < 60 ? ago + 's ago' : Math.round(ago / 60) + 'm ago';
            document.getElementById('now-meta').textContent =
                Math.round(sorted[0].bestConfidence * 100) + '% confidence \\u00b7 ' + agoText;
        } else {
            nh.classList.add('silent');
            document.getElementById('now-species').textContent = '\\u2014';
            document.getElementById('now-meta').textContent = 'Waiting for birds\\u2026';
        }

        // Footer
        document.getElementById('footer').textContent =
            'Today: ' + totalToday + ' detections \\u00b7 ' + speciesSeenToday.size + ' species';
    }

    // --- Backfill last hour ---
    async function backfill() {
        const since = new Date(Date.now() - EXPIRY_MS).toISOString();
        try {
            const resp = await fetch('/api/detections?limit=500&since=' + encodeURIComponent(since));
            const data = await resp.json();
            // Process oldest first
            data.reverse().forEach(processDetection);
            renderCards();
        } catch (e) {
            console.error('Backfill failed:', e);
        }
    }

    // --- Fetch today's stats for footer ---
    async function fetchTodayStats() {
        try {
            const resp = await fetch('/api/stats');
            const stats = await resp.json();
            totalToday = stats.today || 0;
            renderCards();
        } catch {}
    }

    // --- SSE connection ---
    function connectSSE() {
        if (evtSource) evtSource.close();
        evtSource = new EventSource('/api/events');

        evtSource.onmessage = function(e) {
            try {
                const det = JSON.parse(e.data);
                processDetection(det);
                renderCards();
            } catch {}
        };

        evtSource.onerror = function() {
            document.getElementById('status-dot').classList.add('disconnected');
            // EventSource auto-reconnects
        };

        evtSource.onopen = function() {
            document.getElementById('status-dot').classList.remove('disconnected');
        };
    }

    // --- Periodic cleanup & refresh ---
    setInterval(renderCards, 30000); // Re-render to update ages/opacity

    // --- Init ---
    backfill().then(() => {
        fetchTodayStats();
        connectSSE();
    });
})();
</script>
</body>
</html>
'''

@app.route('/live')
def live():
    return render_template_string(LIVE_HTML)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
