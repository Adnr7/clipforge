"""Project-scoped AI recommendations. Proposals never mutate candidates or queue renders."""

from threading import BoundedSemaphore

from flask import Blueprint, jsonify

from backend.database import get_db
from backend.routes.analysis import analysis_connection, transcript_context
from backend.routes.transcription import _RouteError, _get_project, _json_object
from backend.services import llm_service
from backend.services.editing_brief import validate_editing_brief, editing_brief_fingerprint
from backend.services.clip_selection_service import validate_audience_brief, EditorialResponseError
from backend.services.visual_analysis_service import analyze_video, VisualCapabilityError


ai_edit_bp = Blueprint('ai_edit', __name__)
_AI_SLOTS = BoundedSemaphore(2)


def _brief(data):
    try:
        return validate_editing_brief(data.get('brief', ''))
    except ValueError as exc:
        raise _RouteError(str(exc)) from exc


@ai_edit_bp.route('/projects/<project_id>/ai-edit/recommendations', methods=['POST'])
def visual_recommendations(project_id):
    try:
        db = get_db()
        project = _get_project(db, project_id)
        data = _json_object()
        if set(data) - {'brief', 'audienceBrief'}:
            raise _RouteError('Only brief and audienceBrief are accepted')
        brief = _brief(data)
        try:
            audience_brief = validate_audience_brief(data.get('audienceBrief'))
        except ValueError as exc:
            raise _RouteError(str(exc)) from exc
        provider, profile = analysis_connection(db, {})
        text, transcript_data = transcript_context(db, project_id)
        if not _AI_SLOTS.acquire(blocking=False):
            raise _RouteError('AI requests are busy. Please retry.', 429)
        try:
            result = analyze_video(project['source_path'], provider, profile=profile,
                                   brief=brief, transcript_text=text or None,
                                    transcript_data=transcript_data or None,
                                    capability_fallback='transcript',
                                    audience_brief=audience_brief)
            return jsonify({**result, 'model': llm_service.provider_model(provider, profile),
                            'provider': provider,
                            'provenance': {'version': 1,
                                           'briefFingerprint': editing_brief_fingerprint(brief, audience_brief)}})
        except VisualCapabilityError as exc:
            return jsonify({'error': str(exc)}), 422
        except (llm_service.AnalysisProviderError, EditorialResponseError) as exc:
            return jsonify({'error': str(exc)}), 502
        except Exception:
            # A provider can echo entire requests: do not relay paths, images or secrets.
            return jsonify({'error': 'Visual recommendation failed. Check the image-capable '
                            'model, provider connection and quota, then retry.'}), 502
        finally:
            _AI_SLOTS.release()
    except _RouteError as exc:
        return jsonify({'error': str(exc)}), exc.status_code
