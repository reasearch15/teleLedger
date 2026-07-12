"""Telethon adapters for payment ingestion and cashout reaction sync.

This package init stays empty on purpose: importing lightweight helpers such as
``app.telegram.peer_ids`` must not pull listener, event, or reaction modules.
Import concrete modules from their own paths when needed.
"""
