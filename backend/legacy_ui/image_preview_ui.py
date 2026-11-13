"""Consistent compact previews without changing source images or downloads."""
import streamlit as st


PREVIEW_WIDTH = 320


def image_preview(image, *, width=None, **kwargs):
    """Keep smaller thumbnails and cap large/stretch previews to a compact width.

    Pass the original image through so Streamlit's fullscreen viewer can still
    show its detail. Streamlit also fits this width to narrower parent columns.
    """
    width = min(width, PREVIEW_WIDTH) if isinstance(width, int) else PREVIEW_WIDTH
    return st.image(image, width=width, **kwargs)
