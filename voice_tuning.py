"""Bounded wake settings. This cannot alter capture, ASR gating or media policy."""
import json
import math
from pathlib import Path

DEFAULTS = {'keyword_score':2.2, 'keyword_threshold':.22,
            'quiet_foreground_ratio':1.15, 'quiet_min_rms':32}
LIMITS = {'keyword_score':(1.5,3), 'keyword_threshold':(.18,.5),
          'quiet_foreground_ratio':(1.1,1.5), 'quiet_min_rms':(30,60)}


def validate(settings):
    if not isinstance(settings,dict) or set(settings)!=set(DEFAULTS):
        raise ValueError('Use only the four supported wake sensitivity settings.')
    for key,value in settings.items():
        lower,upper=LIMITS[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not lower<=value<=upper:
            raise ValueError(f'{key} must be between {lower} and {upper}.')
    return dict(settings)


def load():
    try:
        return validate(json.loads(Path(__file__).with_name('wake_tuning.json').read_text(encoding='utf-8')))
    except (OSError,ValueError):
        return dict(DEFAULTS)
