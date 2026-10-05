import { useId } from 'react'
import { SunMoon } from 'lucide-react'
import { useTheme, type Theme } from '../../hooks/useTheme'

export default function ThemeSelect() {
  const { theme, setTheme } = useTheme()
  const id = useId()
  return <div className="theme-select">
    <SunMoon size={16} aria-hidden="true" />
    <label className="sr-only" htmlFor={id}>Color theme</label>
    <select id={id} value={theme} onChange={(event) => setTheme(event.target.value as Theme)}>
      <option value="dark">Dark theme</option><option value="light">Light theme</option><option value="nord">Nord theme</option>
    </select>
  </div>
}
