import { useCallback, useState } from 'react'
import { AlertTriangle, ArrowRight, Clapperboard, Film, Plus, Search, Settings } from 'lucide-react'
import { api, errorMessage, type Project } from '../api/client'
import { useProjects } from '../hooks/useProjects'
import { useToast } from '../hooks/useToast'
import Modal from './ui/Modal'
import ThemeSelect from './ui/ThemeSelect'
import ProjectCard from './ProjectCard'
import NewProjectModal from './NewProjectModal'
import WorkflowStep from './ui/WorkflowStep'

export default function Dashboard({ onOpenProject, onOpenSettings, needsOnboarding, onStartOnboarding }: {
  onOpenProject: (id: string) => void; onOpenSettings: () => void; needsOnboarding: boolean; onStartOnboarding: () => void
}) {
  const { projects, loading, error, refresh } = useProjects()
  const { showToast } = useToast()
  const [search, setSearch] = useState('')
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const [editing, setEditing] = useState<{ project: Project; action: 'rename' | 'delete' } | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const filtered = projects.filter((project) => project.name.toLowerCase().includes(search.trim().toLowerCase()))
  const onCreated = useCallback((id: string) => { setNewProjectOpen(false); onOpenProject(id) }, [onOpenProject])
  const submitEdit = async () => {
    if (!editing || busy) return
    setBusy(true)
    try {
      if (editing.action === 'delete') await api.deleteProject(editing.project.id)
      else await api.renameProject(editing.project.id, name.trim())
      showToast('success', editing.action === 'delete' ? 'Project deleted' : 'Project renamed')
      setEditing(null); refresh()
    } catch (error) { showToast('error', errorMessage(error)) }
    finally { setBusy(false) }
  }
  return <div className="app-shell">
    <header className="app-header"><div className="brand"><span className="brand-mark"><Clapperboard size={21} /></span><div><strong>ClipForge</strong><span className="brand-subtitle">THE CLIP STUDIO</span></div></div><nav className="header-actions" aria-label="Workspace settings"><ThemeSelect /><button className="btn-secondary" onClick={onOpenSettings}><Settings size={16} />Settings</button></nav></header>
    <main className="dashboard-main">
      {needsOnboarding && <div className="notice notice-warning"><AlertTriangle size={18} /><span>FFmpeg or FFprobe is missing. Complete setup to import and render media.</span><button className="text-button" onClick={onStartOnboarding}>Run setup <ArrowRight size={14} /></button></div>}
      <section className="dashboard-heading"><div><p className="eyebrow">YOUR WORKSPACE</p><h1>Projects</h1><p className="muted">Long-form stories. Standout moments.</p></div><button className="btn-accent" title="Step 1: Import media" onClick={() => setNewProjectOpen(true)}><WorkflowStep step={1} /><Plus size={17} />New project</button></section>
      <div className="library-toolbar"><div className="inline"><h2>Project library</h2><span className="count-label">{projects.length}</span></div><div className="search-field"><Search size={16} aria-hidden="true" /><label className="sr-only" htmlFor="project-search">Search projects</label><input id="project-search" type="search" placeholder="Search projects" value={search} onChange={(event) => setSearch(event.target.value)} /></div></div>
      {loading && <div className="empty-state" role="status"><span className="spinner" /><p>Loading your projects…</p></div>}
      {error && <div className="notice notice-error" role="alert"><AlertTriangle size={18} /><span>{error}</span><button className="btn-secondary" onClick={refresh}>Retry</button></div>}
      {!loading && !error && projects.length === 0 && <section className="library-empty"><div className="empty-illustration"><Film size={48} strokeWidth={1} /></div><p className="eyebrow">A NEW CUT STARTS HERE</p><h2>Bring your first story in.</h2><p>Import a video, podcast, or interview. Review the transcript, discover the strongest moments, and export your clips.</p><button className="btn-accent" title="Step 1: Import media" onClick={() => setNewProjectOpen(true)}><WorkflowStep step={1} /><Plus size={16} />Create your first project</button><div className="empty-formats">VIDEO & AUDIO <span>MP4 · MOV · MKV · MP3 · WAV</span></div></section>}
      {!loading && projects.length > 0 && filtered.length === 0 && <div className="empty-state"><Search size={28} /><h2>No matching projects</h2><p>Try another name or clear your search.</p><button className="btn-secondary" onClick={() => setSearch('')}>Clear search</button></div>}
      <div className="project-grid">{filtered.map((project) => <ProjectCard key={project.id} project={project} onOpen={() => onOpenProject(project.id)} onRename={() => { setEditing({ project, action: 'rename' }); setName(project.name) }} onDelete={() => setEditing({ project, action: 'delete' })} />)}</div>
      <footer className="dashboard-footer"><span>ClipForge / From source to short.</span><button className="text-button" onClick={onStartOnboarding}>Setup guide <ArrowRight size={14} /></button></footer>
    </main>
    {newProjectOpen && <NewProjectModal onClose={() => setNewProjectOpen(false)} onCreated={onCreated} />}
    {editing && <Modal title={editing.action === 'rename' ? 'Rename project' : 'Delete project'} onClose={() => { if (!busy) setEditing(null) }}><form className="stack" onSubmit={(event) => { event.preventDefault(); void submitEdit() }}>{editing.action === 'rename' ? <label className="field">Project name<input className="input-field" value={name} onChange={(event) => setName(event.target.value)} autoFocus required maxLength={200} disabled={busy} /></label> : <p>Delete <strong>{editing.project.name}</strong> and its project records? This cannot be undone.</p>}<div className="form-actions"><button type="button" className="btn-secondary" disabled={busy} onClick={() => setEditing(null)}>Cancel</button><button className={editing.action === 'delete' ? 'btn-danger' : 'btn-accent'} disabled={busy || (editing.action === 'rename' && !name.trim())}>{busy && <span className="spinner" />}{editing.action === 'rename' ? 'Save name' : 'Delete project'}</button></div></form></Modal>}
  </div>
}
