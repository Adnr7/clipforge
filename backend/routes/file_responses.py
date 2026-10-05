"""Streaming responses that retain temporary files until WSGI closes the body."""

from flask import send_file
from werkzeug.wsgi import ClosingIterator


def send_temporary_file(file, mimetype, download_name, as_attachment=False):
    try:
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        response = send_file(file, mimetype=mimetype, download_name=download_name,
                             as_attachment=as_attachment, conditional=False, etag=False)
        response.content_length = size
        response.headers['Cache-Control'] = 'no-store'
        # call_on_close alone is bypassed by direct_passthrough send_file bodies.
        response.response = ClosingIterator(response.response, [file.close])
        return response
    except Exception:
        file.close()
        raise
