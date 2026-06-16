"""Backwards-compatible re-export of the shared encryption field.

The tenant model formerly defined here (``BitrixAccount``) has been replaced by
the provider-agnostic ``apps.accounts.models.Account``. The Bitrix OAuth fields
now live on ``apps.bitrix.models.BitrixConnection``. ``EncryptedTextField`` moved
to ``apps.accounts.fields``; it is re-exported here so existing imports keep
working.
"""

from apps.accounts.fields import EncryptedTextField, _fernet

__all__ = ["EncryptedTextField", "_fernet"]
