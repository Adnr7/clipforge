import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
from werkzeug.exceptions import HTTPException
from backend.config import Config

def create_app():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, '.env'))
    # A new app must not inherit storage from an already active app context.
    data_dir = Config.get_environment_data_dir()
    load_dotenv(os.path.join(data_dir, '.env'), override=True)

    app = Flask(
        __name__,
        static_folder='../frontend/dist',
        static_url_path='/'
    )
    app.config['DATA_DIR'] = data_dir
    origins = {'http://127.0.0.1:5173', 'http://localhost:5173'}
    CORS(app, resources={r'/api/*': {'origins': list(origins)}})

    @app.before_request
    def local_api_only():
        if not request.path.startswith('/api/'):
            return
        origin = request.headers.get('Origin')
        if origin and origin not in origins and origin != request.host_url.rstrip('/'):
            return jsonify({'error': 'Origin is not allowed'}), 403
        if not origin and request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify({'error': 'Cross-site API requests are not allowed'}), 403

    # Initialize database
    from backend.database import init_db
    init_db(app)

    # Register route blueprints
    from backend.routes.system import system_bp
    from backend.routes.projects import projects_bp
    from backend.routes.media import media_bp
    from backend.routes.transcription import transcription_bp
    from backend.routes.analysis import analysis_bp
    from backend.routes.rendering import rendering_bp
    from backend.routes.render_settings import render_settings_bp
    from backend.routes.youtube import youtube_bp
    from backend.routes.ai_edit import ai_edit_bp

    app.register_blueprint(system_bp, url_prefix='/api')
    app.register_blueprint(projects_bp, url_prefix='/api')
    app.register_blueprint(media_bp, url_prefix='/api')
    app.register_blueprint(transcription_bp, url_prefix='/api')
    app.register_blueprint(analysis_bp, url_prefix='/api')
    app.register_blueprint(rendering_bp, url_prefix='/api')
    app.register_blueprint(render_settings_bp, url_prefix='/api')
    app.register_blueprint(youtube_bp, url_prefix='/api')
    app.register_blueprint(ai_edit_bp, url_prefix='/api')

    # Serve React frontend for non-API routes
    @app.route('/')
    def serve_frontend():
        return app.send_static_file('index.html')

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api/') or request.path.startswith('/assets/'):
            return jsonify({'error': 'Not found'}), 404
        if not os.path.isfile(os.path.join(app.static_folder, 'index.html')):
            return 'Frontend not built. Run npm install and npm run build in frontend/.', 503
        return app.send_static_file('index.html')

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            response = error.get_response()
            response.data = app.json.dumps({'error': error.description})
            response.content_type = 'application/json'
            return response
        return error

    return app
