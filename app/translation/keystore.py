"""Windows-backed translation credentials, scoped to a profile and recipient."""
from __future__ import annotations

import base64
from hashlib import sha256
import sys

from app import APP_SLUG

if sys.platform == 'win32':
    import pywintypes
    import win32cred
    import win32crypt


def _target(profile: str) -> str:
    digest = sha256(profile.casefold().encode('utf-8')).hexdigest()
    return f'{APP_SLUG}/Translation/{digest}'


def read_key(profile: str, recipient: str) -> str:
    if sys.platform != 'win32':
        return ''
    try:
        item = win32cred.CredRead(_target(profile), win32cred.CRED_TYPE_GENERIC, 0)
        blob = item.get('CredentialBlob')
        if item.get('UserName') == recipient and isinstance(blob, bytes):
            # CredWrite accepts text and CredRead returns its Windows WCHAR bytes.
            return blob.decode('utf-16-le')
    except (pywintypes.error, UnicodeError):
        pass
    return ''


def write_key(profile: str, recipient: str, key: str) -> bool:
    if sys.platform != 'win32':
        return False
    try:
        win32cred.CredWrite({
            'Type': win32cred.CRED_TYPE_GENERIC,
            'TargetName': _target(profile),
            'UserName': recipient,
            'CredentialBlob': key,
            'Persist': win32cred.CRED_PERSIST_LOCAL_MACHINE,
        }, 0)
    except (pywintypes.error, TypeError):
        return False
    return True


def clear_key(profile: str) -> bool:
    if sys.platform != 'win32':
        return True
    try:
        win32cred.CredDelete(_target(profile), win32cred.CRED_TYPE_GENERIC, 0)
    except pywintypes.error as exc:
        return exc.winerror == 1168  # ERROR_NOT_FOUND
    return True


def read_legacy_key(stored: str) -> str:
    """Recover a key saved by the initial Windows translation worktree."""
    if sys.platform != 'win32':
        return ''
    try:
        if stored.startswith('dpapi:'):
            blob = base64.b64decode(stored[6:], validate=True)
            return win32crypt.CryptUnprotectData(blob, None, None, None, 1)[1].decode('utf-8').strip()
        if stored.startswith('b64:'):
            return base64.b64decode(stored[4:], validate=True).decode('utf-8').strip()
    except (pywintypes.error, ValueError):
        pass
    return ''
