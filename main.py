"""
ClipForge — AI-Powered Short-Form Video Clip Studio
Entry point: launches Flask backend + pywebview desktop window
"""
import os
import sys
import threading
from backend.app import create_app

def start_server(app, port):
    """Run Flask in a background thread."""
    from waitress import serve
    serve(app, host='127.0.0.1', port=port, threads=4)

def main():
    port = int(os.environ.get('CLIPFORGE_PORT', '5174'))
    app = create_app()

    # --web mode: serve the app in the browser only (no desktop window)
    if '--web' in sys.argv:
        print(f'ClipForge web server running at http://127.0.0.1:{port}')
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
