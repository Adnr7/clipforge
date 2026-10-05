"""Validation and serialization for render settings shared by the API and FFmpeg.

The frontend can discover the accepted values from ``GET /api/render-settings``.
Keeping validation here prevents arbitrary values from reaching an FFmpeg
filtergraph while retaining the original ``captionStyle`` string API.
"""

import math
import json
import re
from urllib.parse import urlparse


CAPTION_PRESETS = {
    'classic': {
        'fontsize': 48, 'fontcolor': 'white', 'borderw': 3,
        'bordercolor': 'black', 'font': 'Arial', 'shadowx': 2, 'shadowy': 2,
    },
    'neon': {
        'fontsize': 52, 'fontcolor': '#00ff88', 'borderw': 2,
        'bordercolor': '#003322', 'font': 'Arial Bold', 'shadowx': 0, 'shadowy': 0,
    },
    'minimal': {
        'fontsize': 40, 'fontcolor': '#cccccc', 'borderw': 0,
        'bordercolor': 'black', 'font': 'Arial', 'shadowx': 1, 'shadowy': 1,
    },
    'bold': {
        'fontsize': 60, 'fontcolor': 'yellow', 'borderw': 4,
        'bordercolor': 'black', 'font': 'Impact', 'shadowx': 3, 'shadowy': 3,
    },
    # These presets deliberately use common fonts and the same safe drawtext
    # options as the original presets.  Custom settings can change any visual
    # value without requiring another backend release.
    'typewriter': {
        'fontsize': 44, 'fontcolor': '#f5f5f5', 'borderw': 2,
        'bordercolor': '#202020', 'font': 'Courier New', 'shadowx': 1, 'shadowy': 1,
    },
    'sunset': {
        'fontsize': 54, 'fontcolor': '#ffb347', 'borderw': 3,
        'bordercolor': '#4a1d35', 'font': 'Arial Bold', 'shadowx': 2, 'shadowy': 2,
    },
    'mono': {
        'fontsize': 42, 'fontcolor': '#e6e6e6', 'borderw': 1,
        'bordercolor': '#333333', 'font': 'DejaVu Sans Mono', 'shadowx': 1, 'shadowy': 1,
    },
    'bubble': {
        'fontsize': 50, 'fontcolor': 'white', 'borderw': 2,
        'bordercolor': '#5b21b6', 'font': 'Arial Bold', 'shadowx': 2, 'shadowy': 2,
    },
}

# Alias used by existing imports and by code that treats styles as a mapping.
STYLES = CAPTION_PRESETS

PLACEMENTS = ('top', 'center', 'bottom')
CHUNK_MODES = ('chunk', 'word')
FONT_FAMILIES = {
    'default': None,  # Do not emit font=; preserve the legacy fontconfig default.
    'sans': 'DejaVu Sans',
    'serif': 'DejaVu Serif',
    'mono': 'DejaVu Sans Mono',
    'display': 'Ubuntu',
}
_COLOR_NAMES = {
    'black', 'white', 'yellow', 'red', 'green', 'blue', 'cyan', 'magenta',
    'gray', 'grey', 'orange', 'transparent',
}
_HEX_COLOR = re.compile(r'^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$')

CAPTION_OPTION_KEYS = {
    'preset', 'style', 'placement', 'fontSize', 'fontColor', 'outlineWidth',
    'outlineColor', 'shadowEnabled', 'shadowColor', 'shadowX', 'shadowY',
    'backgroundEnabled', 'backgroundColor', 'backgroundOpacity',
    'backgroundPadding', 'chunkMode', 'wordsPerChunk', 'maxGapSeconds',
    'splitOnSpeaker', 'fontFamily', 'verticalPosition', 'enabled', 'uppercase',
}

VIDEO_FILTER_KEYS = {'brightness', 'contrast', 'saturation', 'blur', 'sharpen'}
ASPECT_RATIOS = ('source', '16:9', '9:16', '1:1', '4:5', '4:3', '3:2')
OUTPUT_MODES = ('source', 'manual', 'ai')
OUTPUT_FITS = ('contain', 'crop')
LEGACY_OUTPUT_SETTINGS = {
    'mode': 'manual', 'aspectRatio': '9:16', 'fit': 'crop', 'maxDimension': 1920,
}


def _finite_number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'{name} must be a finite number') from exc
    if not math.isfinite(number):
        raise ValueError(f'{name} must be a finite number')
    if minimum is not None and number < minimum or maximum is not None and number > maximum:
        if minimum is not None and maximum is not None:
            raise ValueError(f'{name} must be between {minimum} and {maximum}')
        raise ValueError(f'{name} is outside the supported range')
    return number


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer between {minimum} and {maximum}')
    return value


def _color(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a supported color')
    value = value.strip().lower()
    if value not in _COLOR_NAMES and not _HEX_COLOR.fullmatch(value):
        raise ValueError(f'{name} must be a named color or #RRGGBB/#RRGGBBAA')
    return value


def _boolean(value, name):
    if not isinstance(value, bool):
        raise ValueError(f'{name} must be a boolean')
    return value


def validate_caption_settings(settings=None, style_name='classic'):
    """Return a complete, safe caption configuration.

    ``style_name`` is retained for callers using the legacy API.  New callers
    should pass a mapping with a ``preset`` and any supported overrides.
    """
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError('caption settings must be a JSON object')
    unknown = set(settings) - CAPTION_OPTION_KEYS
    if unknown:
        raise ValueError(f'Unknown caption setting: {sorted(unknown)[0]}')

    supplied_presets = [settings[key] for key in ('preset', 'style') if key in settings]
    if supplied_presets and len({str(item) for item in supplied_presets}) > 1:
        raise ValueError('caption preset and style must match')
    preset = supplied_presets[0] if supplied_presets else style_name
    if not isinstance(preset, str) or preset.strip().lower() not in CAPTION_PRESETS:
        raise ValueError('Unknown caption preset')
    preset = preset.strip().lower()
    base = CAPTION_PRESETS[preset]

    result = {
        'preset': preset,
        'placement': 'bottom',
        'fontSize': base['fontsize'],
        'fontColor': base['fontcolor'],
        'outlineWidth': base['borderw'],
        'outlineColor': base['bordercolor'],
        # Existing styles defined shadow offsets but did not emit them. Keep
        # legacy pixels unchanged; shadows are opt-in in the new contract.
        'shadowEnabled': False,
        'shadowColor': 'black',
        'shadowX': base['shadowx'],
        'shadowY': base['shadowy'],
        'backgroundEnabled': False,
        'backgroundColor': 'black',
        'backgroundOpacity': 0.65,
        'backgroundPadding': 12,
        'chunkMode': 'chunk',
        'wordsPerChunk': 2,
        'maxGapSeconds': 1.2,
        'splitOnSpeaker': True,
        'fontFamily': 'default',
        'verticalPosition': None,
        'enabled': True,
        'uppercase': False,
    }

    for key in ('placement', 'chunkMode'):
        if key in settings:
            value = settings[key]
            allowed = PLACEMENTS if key == 'placement' else CHUNK_MODES
            if not isinstance(value, str) or value not in allowed:
                raise ValueError(f'{key} must be one of {", ".join(allowed)}')
            result[key] = value
    for key in ('fontColor', 'outlineColor', 'shadowColor', 'backgroundColor'):
        if key in settings:
            result[key] = _color(settings[key], key)
    if 'fontSize' in settings:
        result['fontSize'] = _integer(settings['fontSize'], 'fontSize', 12, 160)
    if 'outlineWidth' in settings:
        result['outlineWidth'] = _integer(settings['outlineWidth'], 'outlineWidth', 0, 16)
    for key in ('shadowX', 'shadowY'):
        if key in settings:
            result[key] = _integer(settings[key], key, 0, 32)
    if 'backgroundPadding' in settings:
        result['backgroundPadding'] = _integer(settings['backgroundPadding'], 'backgroundPadding', 0, 64)
    if 'wordsPerChunk' in settings:
        result['wordsPerChunk'] = _integer(settings['wordsPerChunk'], 'wordsPerChunk', 1, 8)
    if 'maxGapSeconds' in settings:
        result['maxGapSeconds'] = _finite_number(settings['maxGapSeconds'], 'maxGapSeconds', 0, 10)
    if 'backgroundOpacity' in settings:
        result['backgroundOpacity'] = _finite_number(settings['backgroundOpacity'], 'backgroundOpacity', 0, 1)
    for key in ('shadowEnabled', 'backgroundEnabled', 'splitOnSpeaker', 'enabled', 'uppercase'):
        if key in settings:
            result[key] = _boolean(settings[key], key)
    if 'fontFamily' in settings:
        family = settings['fontFamily']
        if not isinstance(family, str) or family not in FONT_FAMILIES:
            raise ValueError('fontFamily must be default, sans, serif, mono or display')
        result['fontFamily'] = family
    if settings.get('verticalPosition') is not None:
        result['verticalPosition'] = _finite_number(
            settings['verticalPosition'], 'verticalPosition', 10, 85)
    return result


def validate_video_filters(filters=None):
    """Return safe manual FFmpeg video filter settings.

    Defaults are neutral.  Blur and sharpen are bounded and rendered through
    fixed filter names/options, so user input cannot become filtergraph syntax.
    """
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise ValueError('videoFilters must be a JSON object')
    unknown = set(filters) - VIDEO_FILTER_KEYS
    if unknown:
        raise ValueError(f'Unknown video filter: {sorted(unknown)[0]}')
    result = {
        'brightness': 0.0,
        'contrast': 1.0,
        'saturation': 1.0,
        'blur': 0.0,
        'sharpen': 0.0,
    }
    ranges = {
        'brightness': (-1, 1),
        'contrast': (0, 4),
        'saturation': (0, 4),
        'blur': (0, 10),
        'sharpen': (0, 2),
    }
    for key, (minimum, maximum) in ranges.items():
        if key in filters:
            result[key] = _finite_number(filters[key], key, minimum, maximum)
    # Keep serialized values stable for idempotency and API responses.
    return {key: int(value) if value.is_integer() else value for key, value in result.items()}


def build_video_filter_chain(filters):
    """Build only validated, deterministic FFmpeg filter fragments."""
    filters = validate_video_filters(filters)
    chain = []
    if (filters['brightness'], filters['contrast'], filters['saturation']) != (0, 1, 1):
        chain.append(
            'eq=brightness={}:contrast={}:saturation={}'.format(
                filters['brightness'], filters['contrast'], filters['saturation']))
    if filters['blur']:
        chain.append(f"gblur=sigma={filters['blur']}")
    if filters['sharpen']:
        chain.append(
            f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={filters['sharpen']}")
    return chain


def validate_output_settings(settings=None):
    """A resolved AI choice is explicit; rendering never calls a model.

    Source/default/original aliases normalize to source. Fit keeps the whole
    image with black bars when needed; crop fills by trimming the center.
    Neither option stretches the picture. New settings default to source/fit.
    """
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError('outputSettings must be a JSON object')
    unknown = set(settings) - {'mode', 'aspectRatio', 'fit', 'maxDimension'}
    if unknown:
        raise ValueError(f'Unknown output setting: {sorted(unknown)[0]}')
    ratio = settings.get('aspectRatio', 'source')
    if isinstance(ratio, str) and ratio in ('original', 'default'):
        ratio = 'source'
    if not isinstance(ratio, str) or ratio not in ASPECT_RATIOS:
        raise ValueError(f'aspectRatio must be one of {", ".join(ASPECT_RATIOS)}')
    mode = settings.get('mode', 'source' if ratio == 'source' else 'manual')
    if isinstance(mode, str) and mode in ('original', 'default'):
        mode = 'source'
    if not isinstance(mode, str) or mode not in OUTPUT_MODES:
        raise ValueError('output mode must be source, manual or ai')
    if mode == 'source' and ratio != 'source':
        raise ValueError('source mode requires the source aspectRatio')
    if mode == 'manual' and ratio == 'source':
        raise ValueError('manual mode requires a fixed aspectRatio')
    if mode == 'ai' and 'aspectRatio' not in settings:
        raise ValueError('ai mode requires an explicit suggested aspectRatio')
    fit = settings.get('fit', 'contain')
    if not isinstance(fit, str) or fit not in OUTPUT_FITS:
        raise ValueError('fit must be contain or crop')
    maximum = _integer(settings.get('maxDimension', 1920), 'maxDimension', 64, 3840)
    return {'mode': mode, 'aspectRatio': ratio, 'fit': fit,
            'maxDimension': maximum // 2 * 2}


def output_dimensions(settings, metadata=None):
    """Even square-pixel dimensions, bounded on both axes by maxDimension.

    Source mode never upscales. Rounding can differ by less than two pixels per
    axis; contain handles that small difference without stretching. Audio has
    no source ratio, so uses the legacy portrait black canvas.
    """
    settings = validate_output_settings(settings)
    metadata = metadata or {}
    maximum = settings['maxDimension']
    if settings['aspectRatio'] == 'source' and metadata.get('has_video'):
        width = metadata.get('display_width', metadata.get('width'))
        height = metadata.get('display_height', metadata.get('height'))
        width = _finite_number(width, 'source width', 1, 1000000)
        height = _finite_number(height, 'source height', 1, 1000000)
        scale = min(1, maximum / max(width, height))
        return {'width': max(2, int(width * scale) // 2 * 2),
                'height': max(2, int(height * scale) // 2 * 2)}
    ratio = settings['aspectRatio'] if settings['aspectRatio'] != 'source' else '9:16'
    x, y = (int(part) for part in ratio.split(':'))
    unit = maximum // (2 * max(x, y)) * 2
    return {'width': x * unit, 'height': y * unit}


def render_settings_contract():
    """Public, secret-free schema/preset metadata for frontend consumers."""
    presets = []
    for name in CAPTION_PRESETS:
        presets.append({
            'id': name,
            'label': name.replace('-', ' ').title(),
            'settings': validate_caption_settings({'preset': name}),
        })
    return {
        'version': 3,
        'output': {
            'defaults': validate_output_settings(),
            'legacyDefaults': dict(LEGACY_OUTPUT_SETTINGS),
            'modes': list(OUTPUT_MODES),
            'aspectRatios': list(ASPECT_RATIOS),
            'fits': list(OUTPUT_FITS),
            'maxDimension': {'min': 64, 'max': 3840},
            'fitBehavior': 'Fit keeps the whole image with black bars if needed. Crop fills the frame by trimming the center. Neither stretches video.',
            'sourceBehavior': 'Source preserves the displayed ratio, never upscales, and rounds dimensions to even pixels. Audio-only sources use a portrait black canvas.',
        },
        'caption': {
            'presets': presets,
            'placements': list(PLACEMENTS),
            'chunkModes': list(CHUNK_MODES),
            'fontFamilies': list(FONT_FAMILIES),
            'defaults': validate_caption_settings(),
            'booleanFields': ['enabled', 'uppercase', 'shadowEnabled', 'backgroundEnabled', 'splitOnSpeaker'],
            'colors': {'names': sorted(_COLOR_NAMES), 'hex': '#RRGGBB or #RRGGBBAA'},
            'textFit': 'Long/custom chunks wrap and shrink to fit the output safe area.',
            'ranges': {
                'verticalPosition': {'min': 10, 'max': 85, 'nullable': True},
                'fontSize': {'min': 12, 'max': 160},
                'outlineWidth': {'min': 0, 'max': 16},
                'shadowX': {'min': 0, 'max': 32},
                'shadowY': {'min': 0, 'max': 32},
                'backgroundPadding': {'min': 0, 'max': 64},
                'wordsPerChunk': {'min': 1, 'max': 8},
                'maxGapSeconds': {'min': 0, 'max': 10},
                'backgroundOpacity': {'min': 0, 'max': 1},
            },
        },
        'videoFilters': {
            'defaults': validate_video_filters(),
            'ranges': {
                'brightness': {'min': -1, 'max': 1},
                'contrast': {'min': 0, 'max': 4},
                'saturation': {'min': 0, 'max': 4},
                'blur': {'min': 0, 'max': 10},
                'sharpen': {'min': 0, 'max': 2},
            },
            'manualOnly': True,
        },
        'suggestions': {'generationAvailable': True, 'basis': 'transcript', 'autoApply': False},
    }


def resolve_project_settings(project, data=None):
    """Omitted groups inherit persisted values; supplied groups replace them.

    A legacy captionStyle explicitly chooses that preset instead of reusing a
    previously saved preset. Aliases remain accepted for existing render clients.
    """
    data = data or {}
    nested = data.get('renderSettings', {})
    if not isinstance(nested, dict):
        raise ValueError('renderSettings must be a JSON object')
    if set(nested) - {'caption', 'captionSettings', 'videoFilters', 'outputSettings'}:
        raise ValueError('Unknown render setting')

    def supplied(names):
        values = [container[name] for container in (data, nested)
                  for name in names if name in container]
        if len(values) > 1:
            raise ValueError(f'Provide {names[0]} once')
        if values and not isinstance(values[0], dict):
            raise ValueError(f'{names[0]} must be a JSON object')
        return values[0] if values else None

    caption = supplied(('captionSettings', 'caption'))
    filters = supplied(('videoFilters', 'filters'))
    output = supplied(('outputSettings',))
    style = data.get('captionStyle')
    if style is not None:
        # Validate even when the caption object supplies its own preset.
        style = validate_caption_settings({}, style)['preset']
    if caption is None and style is None:
        caption = json.loads(project.get('caption_settings_json') or '{}')
    if filters is None:
        filters = json.loads(project.get('video_filters_json') or '{}')
    if output is None:
        stored = json.loads(project.get('render_settings_json') or '{}')
        output = stored.get('outputSettings', LEGACY_OUTPUT_SETTINGS)
    return {
        'captionSettings': validate_caption_settings(
            caption, style or project.get('caption_style') or 'classic'),
        'videoFilters': validate_video_filters(filters),
        'outputSettings': validate_output_settings(output),
    }


def valid_http_url(value):
    """Validate a provider base URL without accepting credentials or fragments."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError('baseUrl must be a valid HTTP(S) URL')
    parsed = urlparse(value.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('baseUrl must be a valid HTTP(S) URL without credentials')
    if parsed.fragment:
        raise ValueError('baseUrl must not contain a URL fragment')
    return value.strip().rstrip('/')
