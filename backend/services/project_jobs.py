"""Shared pipeline status resolution for project detail, polling, and recovery."""

import uuid

JOB_STATES = {'transcription': 'transcribing', 'analysis': 'analyzing'}
# Legacy extraction state: new extractions use the shared transcription claim.
AUDIO_EXTRACTION_STATE = 'audio_extracting'
INTERRUPTED_ERROR = 'Processing failed or was interrupted. Please retry.'


def _ensure_audio_claims(db):
    # Kept separate from updated_at: renaming/probing a project is not a new job.
    db.execute('''CREATE TABLE IF NOT EXISTS audio_extraction_claims (
        project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
        claim_id TEXT NOT NULL
    )''')


def claim_audio_extraction(db, project_id):
    """Reserve an extraction token inside the caller's availability transaction."""
    _ensure_audio_claims(db)
    claim_id = str(uuid.uuid4())
    db.execute('''INSERT INTO audio_extraction_claims (project_id, claim_id) VALUES (?,?)
                  ON CONFLICT(project_id) DO UPDATE SET claim_id=excluded.claim_id''',
               (project_id, claim_id))
    return claim_id


def owns_audio_extraction(db, project_id, claim_id):
    """Check under BEGIN IMMEDIATE before publishing output or recording failure."""
    return db.execute('''SELECT 1 FROM audio_extraction_claims a
                         JOIN projects p ON p.id=a.project_id
                         WHERE a.project_id=? AND a.claim_id=? AND p.status=?''',
                      (project_id, claim_id, JOB_STATES['transcription'])).fetchone() is not None


def release_audio_extraction(db, project_id, claim_id):
    db.execute('DELETE FROM audio_extraction_claims WHERE project_id=? AND claim_id=?',
               (project_id, claim_id))


def project_job_statuses(project, has_transcript, has_candidates):
    """Resolve each stage independently, including databases predating job errors.

    Outputs survive failed reruns, so a persisted stage error takes precedence
    over output presence. An unknown legacy failure belongs only to the first
    stage missing output; completed stages must not inherit a generic error.
    """
    project = dict(project)
    outputs = {'transcription': has_transcript, 'analysis': has_candidates}
    jobs = {}
    for stage, present in outputs.items():
        error = project.get(f'{stage}_error')
        jobs[stage] = {
            'status': 'error' if error else ('done' if present else 'idle'),
            'error': error or None,
        }

    pending_stage = next((stage for stage, present in outputs.items() if not present), None)
    last_stage = project.get('last_job_stage')
    fallback_stage = last_stage if last_stage in JOB_STATES else pending_stage
    state = project['status']
    active_stage = next((stage for stage, running in JOB_STATES.items() if state == running), None)
    # Explicit extraction prepares transcription and shares its retry/error slot.
    if state == AUDIO_EXTRACTION_STATE:
        active_stage = 'transcription'
    if state == 'processing':
        active_stage = fallback_stage
    if active_stage:
        jobs[active_stage] = {'status': 'processing', 'error': None}
    elif state == 'error' and not any(job['error'] for job in jobs.values()) and fallback_stage:
        jobs[fallback_stage] = {'status': 'error', 'error': INTERRUPTED_ERROR}
    return jobs


def recover_project_jobs(db):
    """Persist interrupted stages before replacing the project's running state.

    Also materialize inferred legacy errors, so starting a different stage
    cannot erase their attribution. Repeated startup leaves saved errors alone.
    """
    _ensure_audio_claims(db)
    # Recovery invalidates tokens, including when a retry claims the same state.
    db.execute('DELETE FROM audio_extraction_claims')
    rows = db.execute('''
        SELECT p.*,
            EXISTS(SELECT 1 FROM transcripts WHERE project_id=p.id) AS has_transcript,
            EXISTS(SELECT 1 FROM candidates WHERE project_id=p.id) AS has_candidates
        FROM projects p WHERE status IN ('audio_extracting', 'transcribing', 'analyzing', 'processing', 'error')
    ''').fetchall()
    for row in rows:
        project = dict(row)
        jobs = project_job_statuses(project, project['has_transcript'], project['has_candidates'])
        for stage, job in jobs.items():
            if job['status'] == 'processing':
                db.execute(f'''UPDATE projects SET last_job_stage=?, {stage}_error=?
                               WHERE id=?''', (stage, INTERRUPTED_ERROR, project['id']))
            elif job['error'] and not project.get(f'{stage}_error'):
                db.execute(f'UPDATE projects SET {stage}_error=? WHERE id=?',
                           (job['error'], project['id']))
        db.execute("UPDATE projects SET status='error' WHERE id=?", (project['id'],))
