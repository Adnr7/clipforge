"""
ClipForge — AI-Powered Short-Form Video Clip Studio
Entry point: launches Flask backend + pywebview desktop window
"""
import os
import sys
import threading
from backend.app import create_app
from backend.config import ensure_media_runtime, ensure_ytdlp_runtime

def start_server(app, port):
    """Run Flask in a background thread."""
    from waitress import serve
    serve(app, host='127.0.0.1', port=port, threads=4)

def main():
    port = int(os.environ.get('CLIPFORGE_PORT', '5174'))
    app = create_app()
    # Re-resolve after create_app loads the project's and data directory's .env.
    # On supported Linux/WSL systems this performs the first-run, user-space
    # bootstrap before the server accepts import/render requests.
    media_runtime = ensure_media_runtime()
    ytdlp_ready = ensure_ytdlp_runtime()
    if media_runtime['state'] == 'ready':
        print(f"Media runtime ready ({media_runtime['source']}).", flush=True)
    else:
        print(f"Media runtime unavailable: {media_runtime['message']}", file=sys.stderr, flush=True)
    if not ytdlp_ready:
        print('yt-dlp is unavailable. YouTube import will remain disabled until the next setup retry.',
              file=sys.stderr, flush=True)

    # --web mode: serve the app in the browser only (no desktop window)
    if '--web' in sys.argv:
        print(f'ClipForge web server running at http://127.0.0.1:{port}', flush=True)
        start_server(app, port)
        return

    # Start Flask in background thread
    import webview
    server_thread = threading.Thread(
        target=start_server,
        args=(app, port),
        daemon=True
    )
    server_thread.start()

    # Create pywebview window
    window = webview.create_window(
        title='ClipForge',
        url=f'http://127.0.0.1:{port}',
        width=1400,
        height=900,
        min_size=(1024, 700),
        resizable=True,
        text_select=True,
    )

    webview.start(debug=('--debug' in sys.argv))

if __name__ == '__main__':
    main()
