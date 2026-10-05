import { ArrowUpRight, Clock, Film, Pencil, Trash2 } from 'lucide-react'
import type { Project } from '../api/client'
import { formatDate, formatTime } from '../api/format'

const labels: Record<string, [string, string]> = {
  imported: ['Imported', 'info'], probed: ['Ready to transcribe', 'info'], audio_extracted: ['Audio ready', 'info'],
  transcribing: ['Transcribing', 'warning'], transcribed: ['Transcript ready', 'info'], analyzing: ['Analyzing', 'warning'],
  analyzed: ['Candidates ready', 'success'], rendering: ['Rendering', 'warning'], rendered: ['Clips ready', 'success'], error: ['Needs attention', 'error'],
}
export default function ProjectCard({ project, onOpen, onRename, onDelete }: {
  project: Project; onOpen: () => void; onRename: () => void; onDelete: () => void
}) {
  const [label, tone] = labels[project.status] || [project.status.replaceAll('_', ' '), 'neutral']
  const filename = project.source_path.split(/[\\/]/).pop()
  return <article className="project-card">
    <button className="project-open" onClick={onOpen} aria-label={`Open project ${project.name}`}>
      <div className="project-art"><Film size={34} strokeWidth={1.25} /><span className="project-art-label">SOURCE MEDIA</span><span className="project-duration"><Clock size={12} />{formatTime(project.source_duration)}</span><ArrowUpRight className="project-open-arrow" size={19} /></div>
      <div className="project-info"><span className={`badge badge-${tone}`}>{label}</span><h3>{project.name}</h3><p className="filename" title={project.source_path}>{filename}</p></div>
    </button>
    <footer className="project-footer"><time dateTime={project.updated_at}>{formatDate(project.updated_at)}</time><div className="inline compact"><button className="btn-ghost" onClick={onRename} aria-label={`Rename ${project.name}`} title="Rename project"><Pencil size={15} /></button><button className="btn-ghost danger" onClick={onDelete} aria-label={`Delete ${project.name}`} title="Delete project"><Trash2 size={15} /></button></div></footer>
  </article>
}
