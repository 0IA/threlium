"""Константы и VO для raw-capture attachment на границе bridge→ingress."""
from __future__ import annotations

from threlium.types._core import _RequiredNonEmpty

_RAW_FILENAME = "threlium-raw-ingress.txt"


class RawIngressCaptureAttachmentFilename(_RequiredNonEmpty):
    """Каноническое ``filename`` для ``text/plain`` attachment с сырым входом."""

    @classmethod
    def canonical(cls) -> RawIngressCaptureAttachmentFilename:
        return cls.require(
            name="RawIngressCaptureAttachmentFilename",
            raw=_RAW_FILENAME,
        )
