#!/usr/bin/env python3
"""BirdNET Web Interface - View detections, play audio, configure settings."""
import os
import json
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template_string, send_from_directory, abort, request

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
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>BirdNET Monitor</h1>
            <div class="status">
                <div class="status-dot"></div>
                <span>Listening</span>
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
            <button class="tab" data-tab="settings">Settings</button>
        </div>
        
        <div id="detections-tab" class="tab-content active">
            <div class="detections" id="detections">
                <div class="empty-state">Loading detections...</div>
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
        
        loadData();
        loadSettings();
        setInterval(loadData, 30000);
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
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT timestamp, common_name, species_code, confidence, audio_file
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
    import subprocess
    try:
        # Signal the detector to restart (via docker)
        subprocess.run(['docker', 'restart', 'birdnet-detector'], check=True, capture_output=True)
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
