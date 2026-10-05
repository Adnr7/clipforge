import type { EnvironmentStatus } from './client'

interface Provider {
  id: string
  label: string
  setting: string
  statusKey: keyof EnvironmentStatus
}

export const KEY_PROVIDERS: Provider[] = [
  { id: 'deepseek', label: 'DeepSeek', setting: 'DEEPSEEK_API_KEY', statusKey: 'hasDeepseekKey' },
  { id: 'deepgram', label: 'Deepgram', setting: 'DEEPGRAM_API_KEY', statusKey: 'hasDeepgramKey' },
  { id: 'openai', label: 'OpenAI', setting: 'OPENAI_API_KEY', statusKey: 'hasOpenaiKey' },
  { id: 'claude', label: 'Anthropic (Claude)', setting: 'ANTHROPIC_API_KEY', statusKey: 'hasAnthropicKey' },
  { id: 'groq', label: 'Groq', setting: 'GROQ_API_KEY', statusKey: 'hasGroqKey' },
  { id: 'gemini', label: 'Gemini', setting: 'GEMINI_API_KEY', statusKey: 'hasGeminiKey' },
  { id: 'openrouter', label: 'OpenRouter', setting: 'OPENROUTER_API_KEY', statusKey: 'hasOpenrouterKey' },
  { id: 'custom', label: 'Custom provider', setting: 'CUSTOM_API_KEY', statusKey: 'hasCustomProvider' },
]
export const ANALYSIS_PROVIDERS = [
  ...KEY_PROVIDERS.filter((provider) => provider.id !== 'deepgram'),
  { id: 'ollama', label: 'Ollama (local)' },
]
export function enteredKeys(keys: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(keys).filter(([, value]) => value.trim()).map(([key, value]) => [key, value.trim()]))
}
