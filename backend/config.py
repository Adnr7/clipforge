import os
import shutil
from flask import current_app, has_app_context

class Config:
    @staticmethod
    def get_data_dir():
        if has_app_context() and 'DATA_DIR' in current_app.config:
            return current_app.config['DATA_DIR']
        return Config.get_environment_data_dir()

    @staticmethod
    def get_environment_data_dir():
        return os.path.abspath(os.path.expanduser(os.environ.get('DATA_DIR') or '~/.clipforge'))

    @staticmethod
    def get_llm_provider():
        return os.environ.get('LLM_PROVIDER', 'deepseek').lower()

    @staticmethod
    def has_ffmpeg():
        return shutil.which('ffmpeg') is not None

    @staticmethod
    def has_ffprobe():
        return shutil.which('ffprobe') is not None

    @staticmethod
    def has_ytdlp():
        return shutil.which('yt-dlp') is not None

    @staticmethod
    def get_env_status():
        return {
            'dataDir': Config.get_data_dir(),
            'hasFfmpeg': Config.has_ffmpeg(),
            'hasFfprobe': Config.has_ffprobe(),
            'hasDeepgramKey': bool(os.environ.get('DEEPGRAM_API_KEY', '').strip()),
            'hasDeepseekKey': bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()),
            'hasAnthropicKey': bool(os.environ.get('ANTHROPIC_API_KEY', '').strip()),
            'hasGeminiKey': bool(os.environ.get('GEMINI_API_KEY', '').strip()),
            'hasOpenaiKey': bool(os.environ.get('OPENAI_API_KEY', '').strip()),
            'hasOpenrouterKey': bool(os.environ.get('OPENROUTER_API_KEY', '').strip()),
            'hasGroqKey': bool(os.environ.get('GROQ_API_KEY', '').strip()),
            'hasCustomProvider': bool(os.environ.get('CUSTOM_BASE_URL', '').strip()) and \
                                 bool(os.environ.get('CUSTOM_API_KEY', '').strip()),
            'llmProvider': Config.get_llm_provider(),
            'hasYtdlp': Config.has_ytdlp(),
        }
