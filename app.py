from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
from flask_cors import CORS
import yt_dlp
import os
import uuid
import threading
import time

app = Flask(__name__)
CORS(app)

DOWNLOADS_DIR = os.path.join(os.path.expanduser('~'), 'Downloads', 'EduDownloader')
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR)

active_downloads = {}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/info', methods=['POST'])
def get_info():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            is_playlist = 'entries' in info
            
            if is_playlist:
                entries = list(info['entries'])
                items = []
                for idx, entry in enumerate(entries):
                    if entry:
                        items.append({
                            'id': entry.get('id'),
                            'title': entry.get('title', f'Video {idx+1}'),
                            'duration': entry.get('duration', 0),
                            'url': entry.get('url') or entry.get('webpage_url') or url
                        })
                
                return jsonify({
                    'type': 'playlist',
                    'title': info.get('title', 'Unknown Playlist'),
                    'uploader': info.get('uploader', 'Unknown'),
                    'thumbnail': info.get('thumbnail', ''),
                    'items': items,
                    'count': len(items)
                })
            else:
                return jsonify({
                    'type': 'video',
                    'title': info.get('title', 'Unknown Video'),
                    'uploader': info.get('uploader', 'Unknown'),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', ''),
                    'url': url
                })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

class DownloadLogger:
    def __init__(self, task_id):
        self.task_id = task_id

    def debug(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        active_downloads[self.task_id]['status'] = 'error'
        active_downloads[self.task_id]['error'] = msg

def progress_hook(d, task_id):
    if d['status'] == 'downloading':
        active_downloads[task_id]['progress'] = float(d.get('_percent_str', '0%').replace('%', '').strip() or 0)
        active_downloads[task_id]['status'] = 'downloading'
        filename = d.get('filename', '')
        if filename:
            active_downloads[task_id]['current_file'] = os.path.basename(filename)
            
    elif d['status'] == 'finished':
        active_downloads[task_id]['progress'] = 100

def download_thread(url, format_type, task_id):
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOADS_DIR, '%(title).100s.%(ext)s'),
        'windowsfilenames': True,
        'restrictfilenames': True,
        'logger': DownloadLogger(task_id),
        'progress_hooks': [lambda d: progress_hook(d, task_id)],
    }
    
    if format_type == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts.update({
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'merge_output_format': 'mp4'
        })
        
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Try to get the exact final filename
            final_filepath = None
            if 'requested_downloads' in info and len(info['requested_downloads']) > 0:
                final_filepath = info['requested_downloads'][0].get('filepath')
            
            if not final_filepath:
                final_filepath = ydl.prepare_filename(info)
                
            if not final_filepath or not os.path.exists(final_filepath):
                import glob
                list_of_files = [f for f in glob.glob(os.path.join(DOWNLOADS_DIR, '*')) if os.path.isfile(f)]
                if list_of_files:
                    final_filepath = max(list_of_files, key=os.path.getctime)
            
            active_downloads[task_id]['full_filepath'] = final_filepath
            active_downloads[task_id]['status'] = 'completed_all'
            
    except Exception as e:
        active_downloads[task_id]['status'] = 'error'
        active_downloads[task_id]['error'] = str(e)

@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url')
    format_type = data.get('format', 'video') # video or audio
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
        
    task_id = str(uuid.uuid4())
    active_downloads[task_id] = {
        'status': 'downloading',
        'progress': 0,
        'speed': 0,
        'url': url
    }
    
    thread = threading.Thread(target=download_thread, args=(url, format_type, task_id))
    thread.daemon = True
    thread.start()
    
    return jsonify({'task_id': task_id})

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    if task_id not in active_downloads:
        return jsonify({'error': 'Task not found'}), 404
    return jsonify(active_downloads[task_id])

@app.route('/api/open-folder', methods=['POST'])
def open_folder():
    try:
        os.startfile(DOWNLOADS_DIR)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/download-file/<task_id>', methods=['GET'])
def serve_file(task_id):
    if task_id not in active_downloads:
        return "Task not found", 404
        
    filepath = active_downloads[task_id].get('full_filepath')
    if filepath and os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return "File not ready or not found", 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
